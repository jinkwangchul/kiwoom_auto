"""Run every unittest module in an isolated Python process.

The Production-mutable guard is installed in each child through ``tests.__init__``.
This runner exists because a single long-lived 32-bit PyQt process can terminate
with 0xC0000409 after accumulating GUI test objects across modules.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = Path(__file__).resolve().parent
RAN_RE = re.compile(r"Ran\s+(\d+)\s+tests?\s+in")
COUNT_RE = re.compile(r"(failures|errors|skipped)=(\d+)")
PER_TEST_MODULES: set[str] = set()


def _test_modules() -> list[str]:
    return [
        f"tests.{path.stem}"
        for path in sorted(TEST_ROOT.glob("test_*.py"), key=lambda item: item.name)
    ]


def _result_counts(output: str) -> tuple[int, int, int, int] | None:
    ran_matches = RAN_RE.findall(output)
    if not ran_matches:
        return None
    total = int(ran_matches[-1])
    counts = {"failures": 0, "errors": 0, "skipped": 0}
    for key, value in COUNT_RE.findall(output):
        counts[key] = int(value)
    return total, counts["failures"], counts["errors"], counts["skipped"]


def _isolated_test_ids(module: str, environment: dict[str, str]) -> list[str] | None:
    loader_code = """
import json
import sys
import unittest

def flatten(suite):
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            yield from flatten(item)
        else:
            yield item.id()

suite = unittest.defaultTestLoader.loadTestsFromName(sys.argv[1])
print("ISOLATED_TEST_IDS=" + json.dumps(list(flatten(suite))))
"""
    completed = subprocess.run(
        [sys.executable, "-c", loader_code, module],
        cwd=PROJECT_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    if completed.returncode != 0:
        return None
    for line in completed.stdout.splitlines():
        if line.startswith("ISOLATED_TEST_IDS="):
            values = json.loads(line.split("=", 1)[1])
            return [str(value) for value in values]
    return None


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="backslashreplace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="backslashreplace")
    modules = _test_modules()
    environment = dict(os.environ)
    environment.setdefault("QT_QPA_PLATFORM", "offscreen")

    total = failures = errors = skipped = abnormal = guard_violations = 0
    executed: list[str] = []
    module_results: list[dict[str, int | str]] = []
    for index, module in enumerate(modules, start=1):
        if module in PER_TEST_MODULES:
            test_ids = _isolated_test_ids(module, environment)
            if not test_ids:
                abnormal += 1
                executed.append(module)
                module_results.append(
                    {"module": module, "total": 0, "failures": 0, "errors": 0, "skipped": 0, "abnormal": 1, "guard_violations": 0}
                )
                print(
                    f"[{index}/{len(modules)}] ABNORMAL unable to enumerate {module}",
                    flush=True,
                )
                continue
            module_total = module_failures = module_errors = module_skipped = 0
            module_abnormal = 0
            module_guard_violations = 0
            for test_id in test_ids:
                completed = subprocess.run(
                    [sys.executable, "-m", "unittest", "-q", test_id],
                    cwd=PROJECT_ROOT,
                    env=environment,
                    capture_output=True,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    timeout=300,
                    check=False,
                )
                output = f"{completed.stdout}\n{completed.stderr}"
                item_guard_violations = output.count("test attempted Production mutable")
                guard_violations += item_guard_violations
                module_guard_violations += item_guard_violations
                counts = _result_counts(output)
                if counts is None or completed.returncode not in (0, 1):
                    module_abnormal += 1
                    print(
                        f"[{index}/{len(modules)}] ABNORMAL "
                        f"rc={completed.returncode} {test_id}",
                        flush=True,
                    )
                    print("\n".join(output.splitlines()[-40:]), flush=True)
                    continue
                item_total, item_failures, item_errors, item_skipped = counts
                module_total += item_total
                module_failures += item_failures
                module_errors += item_errors
                module_skipped += item_skipped
                if completed.returncode != 0:
                    print("\n".join(output.splitlines()[-40:]), flush=True)
            executed.append(module)
            total += module_total
            failures += module_failures
            errors += module_errors
            skipped += module_skipped
            abnormal += module_abnormal
            module_results.append(
                {
                    "module": module,
                    "total": module_total,
                    "failures": module_failures,
                    "errors": module_errors,
                    "skipped": module_skipped,
                    "abnormal": module_abnormal,
                    "guard_violations": module_guard_violations,
                }
            )
            status = (
                "OK"
                if not module_failures and not module_errors and not module_abnormal
                else "FAILED"
            )
            print(
                f"[{index}/{len(modules)}] {status} per-test {module}: "
                f"total={module_total} failures={module_failures} "
                f"errors={module_errors} skipped={module_skipped} "
                f"abnormal={module_abnormal}",
                flush=True,
            )
            continue

        command = [sys.executable, "-m", "unittest", "-q", module]
        try:
            completed = subprocess.run(
                command,
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=300,
                check=False,
            )
            output = f"{completed.stdout}\n{completed.stderr}"
            module_guard_violations = output.count("test attempted Production mutable")
            guard_violations += module_guard_violations
        except subprocess.TimeoutExpired as exc:
            abnormal += 1
            executed.append(module)
            module_results.append(
                {"module": module, "total": 0, "failures": 0, "errors": 0, "skipped": 0, "abnormal": 1, "guard_violations": 0}
            )
            print(f"[{index}/{len(modules)}] ABNORMAL timeout {module}", flush=True)
            if exc.stdout:
                print(str(exc.stdout)[-4000:], flush=True)
            if exc.stderr:
                print(str(exc.stderr)[-4000:], flush=True)
            continue

        executed.append(module)
        counts = _result_counts(output)
        if counts is None or completed.returncode not in (0, 1):
            abnormal += 1
            module_results.append(
                {"module": module, "total": 0, "failures": 0, "errors": 0, "skipped": 0, "abnormal": 1, "guard_violations": module_guard_violations}
            )
            print(
                f"[{index}/{len(modules)}] ABNORMAL rc={completed.returncode} {module}",
                flush=True,
            )
            print("\n".join(output.splitlines()[-80:]), flush=True)
            continue

        module_total, module_failures, module_errors, module_skipped = counts
        total += module_total
        failures += module_failures
        errors += module_errors
        skipped += module_skipped
        module_results.append(
            {
                "module": module,
                "total": module_total,
                "failures": module_failures,
                "errors": module_errors,
                "skipped": module_skipped,
                "abnormal": 0,
                "guard_violations": module_guard_violations,
            }
        )
        status = "OK" if completed.returncode == 0 else "FAILED"
        print(
            f"[{index}/{len(modules)}] {status} {module}: "
            f"total={module_total} failures={module_failures} "
            f"errors={module_errors} skipped={module_skipped}",
            flush=True,
        )
        if completed.returncode != 0:
            print("\n".join(output.splitlines()[-80:]), flush=True)

    missing = sorted(set(modules) - set(executed))
    pass_count = max(0, total - failures - errors - skipped)
    print(
        "ISOLATED_SUMMARY "
        f"modules={len(modules)} executed={len(executed)} missing={len(missing)} "
        f"total={total} pass={pass_count} failures={failures} errors={errors} "
        f"skipped={skipped} abnormal={abnormal} guard_violations={guard_violations}",
        flush=True,
    )
    if missing:
        print("MISSING_MODULES " + " ".join(missing), flush=True)
    for result in module_results:
        if (
            int(result["failures"])
            or int(result["errors"])
            or int(result["abnormal"])
            or int(result["guard_violations"])
        ):
            print(
                "ISOLATED_FAILED_MODULE "
                f"module={result['module']} total={result['total']} "
                f"failures={result['failures']} errors={result['errors']} "
                f"skipped={result['skipped']} abnormal={result['abnormal']} "
                f"guard_violations={result['guard_violations']}",
                flush=True,
            )
    return 0 if failures == 0 and errors == 0 and abnormal == 0 and not missing else 1


if __name__ == "__main__":
    raise SystemExit(main())
