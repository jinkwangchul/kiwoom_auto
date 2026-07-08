# -*- coding: utf-8 -*-
"""Read-only Ollama analysis runner for project files."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
import subprocess
import sys
from typing import Iterable


DEFAULT_MODEL = "qwen2.5-coder:3b"
PROMPT_FILES = {
    "impact_analysis": "impact_analysis.md",
    "call_flow": "call_flow.md",
    "dependency": "dependency.md",
}


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _resolve_target(path_text: str, root: Path) -> Path:
    path = Path(path_text)
    if not path.is_absolute():
        path = root / path
    resolved = path.resolve()
    if not resolved.exists():
        raise FileNotFoundError(f"target file does not exist: {path_text}")
    if not resolved.is_file():
        raise ValueError(f"target path is not a file: {path_text}")
    return resolved


def _build_prompt(system_prompt: str, task_prompt: str, files: Iterable[Path], root: Path) -> str:
    sections = [
        system_prompt.strip(),
        "",
        task_prompt.strip(),
        "",
        "Project root:",
        str(root),
        "",
        "Files to analyze:",
    ]
    for file_path in files:
        try:
            relative = file_path.relative_to(root)
        except ValueError:
            relative = file_path
        sections.extend(
            [
                "",
                f"--- FILE: {relative} ---",
                _read_text(file_path),
                f"--- END FILE: {relative} ---",
            ]
        )
    sections.extend(
        [
            "",
            "Return an analysis report only. Do not suggest that you modified files.",
        ]
    )
    return "\n".join(sections)


def _write_report(report_text: str, root: Path, prompt_name: str, model: str) -> Path:
    report_dir = root / "reports" / "ai_analysis"
    report_dir.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_model = model.replace(":", "_").replace("/", "_")
    report_path = report_dir / f"{timestamp}_{prompt_name}_{safe_model}.txt"
    report_path.write_text(report_text, encoding="utf-8")
    return report_path


def run_ollama(model: str, prompt: str) -> str:
    completed = subprocess.run(
        ["ollama", "run", model],
        input=prompt,
        text=True,
        capture_output=True,
        check=False,
        encoding="utf-8",
        errors="replace",
    )
    if completed.returncode != 0:
        stderr = completed.stderr.strip()
        raise RuntimeError(f"ollama failed with exit code {completed.returncode}: {stderr}")
    return completed.stdout


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run read-only Ollama analysis on project files and save a report."
    )
    parser.add_argument("files", nargs="+", help="Target project file path(s) to analyze.")
    parser.add_argument(
        "--model",
        default=DEFAULT_MODEL,
        help=f"Ollama model name. Default: {DEFAULT_MODEL}",
    )
    parser.add_argument(
        "--size",
        choices=("3b", "7b"),
        help="Convenience model selector. Overrides --model when provided.",
    )
    parser.add_argument(
        "--prompt",
        choices=sorted(PROMPT_FILES),
        default="impact_analysis",
        help="Analysis prompt to use.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(list(argv or sys.argv[1:]))
    root = _project_root()
    model = args.model
    if args.size == "3b":
        model = "qwen2.5-coder:3b"
    elif args.size == "7b":
        model = "qwen2.5-coder:7b"

    prompt_dir = Path(__file__).resolve().parent / "prompts"
    system_prompt = _read_text(prompt_dir / "system_readonly.md")
    task_prompt = _read_text(prompt_dir / PROMPT_FILES[args.prompt])
    target_files = [_resolve_target(path_text, root) for path_text in args.files]
    prompt = _build_prompt(system_prompt, task_prompt, target_files, root)

    report_text = run_ollama(model, prompt)
    report_path = _write_report(report_text, root, args.prompt, model)
    print(f"AI analysis report written: {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

