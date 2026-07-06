# -*- coding: utf-8 -*-
"""
cleanup_project_junk_v3.py

키움 자동매매 프로젝트 찌꺼기/백업/중복 파일 정리 도구 v3.

v3 핵심:
- v1/v2 정리도구 및 관련 보고서도 정리 대상 포함
- docs 폴더 기본 보호
- 중복명 파일은 SHA-256 비교 후 동일 파일만 자동 백업 이동
- 내용이 다른 중복명 파일은 REVIEW
- ZIP은 자동 처리하지 않고 REVIEW
- APPLY에서도 삭제가 아니라 cleanup_backup_타임스탬프 폴더로 이동
"""

from __future__ import annotations

import argparse
import hashlib
import re
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Tuple


# ------------------------------------------------------------
# 보호 정책
# ------------------------------------------------------------

PROTECTED_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    "env",
    "stocks",
    "routines",
    "runtime",
    "logs",
    "docs",  # v3: docs 기본 보호
}

PROTECTED_FILE_NAMES = {
    "gui_main.py",
    "gui_windows.py",
    "gui_auto_trade_setting_window.py",
    "gui_operation_environment.py",
    "gui_auto_trade_context_menu.py",
    "gui_auto_trade_close.py",
    "gui_auto_trade_policy.py",
    "gui_auto_trade_timer.py",
    "gui_auto_trade_table_loader.py",
    "gui_auto_trade_unregister.py",
    "gui_stock_register_window.py",
    "gui_routine_assign_window.py",
    "gui_macd_routine_settings_dialog.py",
    "macd_signal_engine.py",
    "routine.py",
    "routine_macd_engine.py",
    "condition_engine.py",
    "routine_condition_engine.py",
    "gui_routine_condition_engine.py",
    "signal_result.py",
    "routine_signal_probe.py",
    "routine_signal_queue.py",
    "cleanup_project_junk_v3.py",
}

PROTECTED_JSON_NAMES = {
    "routine.json",
    "rules.json",
    "config.json",
    "settings.json",
}

# docs 안에서도 명시 옵션으로 정리할 때 보호할 핵심 문서 패턴
DOCS_ALWAYS_PROTECT_PATTERNS = [
    re.compile(r".*MASTER_SPEC.*", re.I),
    re.compile(r".*마스터스펙.*", re.I),
    re.compile(r".*작업재개요약.*", re.I),
    re.compile(r".*운영정책.*", re.I),
    re.compile(r".*프로젝트현재상태.*", re.I),
    re.compile(r".*절대금지.*", re.I),
]


# ------------------------------------------------------------
# 분류 패턴
# ------------------------------------------------------------

CACHE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".mypy_cache"}

ARCHIVE_DIR_NAMES = {
    "archived_stocks",
    "old_migration_tools",
    "_deleted_stocks",
    "_recovery_backup",
    "archive_candidate",
}

ARCHIVE_DIR_PATTERNS = [
    re.compile(r"^cleanup_backup_\d{8}_\d{6}$", re.I),
    re.compile(r"^backup_before_", re.I),
    re.compile(r"^registry_migratio", re.I),
]

STEP_BACKUP_PATTERNS = [
    re.compile(r".*_STEP\d+.*\.py$", re.I),
    re.compile(r".*_BACKUP_.*\.py$", re.I),
    re.compile(r".*_FIX_.*\.py$", re.I),
    re.compile(r".*_patch.*\.py$", re.I),
]

TEST_SCRIPT_PATTERNS = [
    re.compile(r"^test_.*\.py$", re.I),
    re.compile(r"^create_test_.*\.py$", re.I),
    re.compile(r"^cleanup_test_.*\.py$", re.I),
    re.compile(r"^run_mock_.*\.py$", re.I),
    re.compile(r"^debug_.*\.py$", re.I),
    re.compile(r"^enable_test_.*\.py$", re.I),
    re.compile(r"^build_order_queue_step\d+\.py$", re.I),
    re.compile(r"^approve_order_queue_step\d+\.py$", re.I),
    re.compile(r"^apply_.*step\d+.*\.py$", re.I),
    re.compile(r"^create_mock_.*\.py$", re.I),
    re.compile(r"^run_real_order_adapter_stub_step\d+\.py$", re.I),
    re.compile(r"^mock_.*step\d+.*\.py$", re.I),
]

REPORT_PATTERNS = [
    re.compile(r".*검증보고.*\.(txt|log)$", re.I),
    re.compile(r".*보고서.*\.(txt|log)$", re.I),
    re.compile(r".*_report\.(txt|log)$", re.I),
    re.compile(r".*_dry_run_\d{8}_\d{6}\.txt$", re.I),
    re.compile(r".*_dry-run_\d{8}_\d{6}\.txt$", re.I),
    re.compile(r".*_apply_\d{8}_\d{6}\.txt$", re.I),
    re.compile(r"^invalid_items\.log$", re.I),
    re.compile(r"^system_error\.log$", re.I),
]

CLEANUP_TOOL_PATTERNS = [
    re.compile(r"^cleanup_project_junk\.py$", re.I),
    re.compile(r"^cleanup_project_junk_v2\.py$", re.I),
    re.compile(r"^cleanup_project_junk_dry_run_\d{8}_\d{6}\.txt$", re.I),
    re.compile(r"^cleanup_project_junk_v2_dry-run_\d{8}_\d{6}\.txt$", re.I),
    re.compile(r"^cleanup_project_junk_v2_dry_run_\d{8}_\d{6}\.txt$", re.I),
    re.compile(r"^cleanup_project_junk_apply_\d{8}_\d{6}\.txt$", re.I),
    re.compile(r"^cleanup_project_junk_v2_apply_\d{8}_\d{6}\.txt$", re.I),
]

DUPLICATE_SUFFIX_PATTERNS = [
    re.compile(r"^(?P<base>.+)\s*\((?P<num>\d+)\)(?P<ext>\.[^.]+)$"),
    re.compile(r"^(?P<base>.+)\s*-\s*복사본?(?P<ext>\.[^.]+)$", re.I),
    re.compile(r"^(?P<base>.+)_copy(?P<ext>\.[^.]+)$", re.I),
    re.compile(r"^(?P<base>.+)_old(?P<ext>\.[^.]+)$", re.I),
    re.compile(r"^(?P<base>.+)_bak(?P<ext>\.[^.]+)$", re.I),
]


@dataclass
class Item:
    category: str
    path: Path
    reason: str
    size: int


def match_any(name: str, patterns: Iterable[re.Pattern]) -> bool:
    return any(p.match(name) for p in patterns)


def get_size(path: Path) -> int:
    try:
        if path.is_file():
            return path.stat().st_size
        total = 0
        for p in path.rglob("*"):
            try:
                if p.is_file():
                    total += p.stat().st_size
            except OSError:
                pass
        return total
    except OSError:
        return 0


def human_size(n: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    f = float(n)
    for u in units:
        if f < 1024 or u == units[-1]:
            return f"{int(f)}B" if u == "B" else f"{f:.1f}{u}"
        f /= 1024
    return f"{n}B"


def sha256_file(path: Path) -> Optional[str]:
    try:
        h = hashlib.sha256()
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)
        return h.hexdigest()
    except OSError:
        return None


def is_inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def is_docs_core(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root)
    except ValueError:
        return False
    if not rel.parts or rel.parts[0] != "docs":
        return False
    return match_any(path.name, DOCS_ALWAYS_PROTECT_PATTERNS)


def is_protected(path: Path, root: Path, include_docs: bool) -> bool:
    if path == root:
        return True

    try:
        rel = path.relative_to(root)
    except ValueError:
        return True

    parts = rel.parts

    if path.name in CACHE_DIR_NAMES:
        return False

    if parts:
        top = parts[0]
        if top == "docs":
            if not include_docs:
                return True
            if is_docs_core(path, root):
                return True
        elif top in PROTECTED_DIR_NAMES:
            return True

    if path.name in PROTECTED_FILE_NAMES:
        return True

    if path.suffix.lower() == ".json" and path.name in PROTECTED_JSON_NAMES:
        return True

    return False


def duplicate_base_name(name: str) -> Optional[str]:
    for pat in DUPLICATE_SUFFIX_PATTERNS:
        m = pat.match(name)
        if m:
            return f"{m.group('base').strip()}{m.group('ext')}"
    return None


def classify_path(path: Path, root: Path, include_docs: bool) -> Optional[Tuple[str, str]]:
    name = path.name

    if is_protected(path, root, include_docs):
        return None

    if path.is_dir():
        if name in CACHE_DIR_NAMES:
            return ("DELETE", "파이썬/테스트 캐시 디렉터리")
        if name in ARCHIVE_DIR_NAMES:
            return ("ARCHIVE", "이전 백업/보관/마이그레이션 산출물 디렉터리")
        if match_any(name, ARCHIVE_DIR_PATTERNS):
            return ("ARCHIVE", "이전 백업/보관/마이그레이션 산출물 디렉터리")
        return None

    if not path.is_file():
        return None

    if match_any(name, CLEANUP_TOOL_PATTERNS):
        return ("ARCHIVE", "이전 정리도구 v1/v2 또는 관련 보고서")

    if match_any(name, STEP_BACKUP_PATTERNS):
        return ("ARCHIVE", "STEP/BACKUP/FIX/패치 계열 산출물")

    if match_any(name, TEST_SCRIPT_PATTERNS):
        return ("ARCHIVE", "테스트/디버그/임시 생성 스크립트")

    if match_any(name, REPORT_PATTERNS):
        return ("ARCHIVE", "검증보고/로그성 산출물")

    base_candidate = duplicate_base_name(name)
    if base_candidate:
        sibling = path.with_name(base_candidate)
        if sibling.exists() and sibling.is_file():
            h1 = sha256_file(path)
            h2 = sha256_file(sibling)
            if h1 and h2 and h1 == h2:
                return ("DUPLICATE_ARCHIVE", f"동일 해시 중복 파일: 원본 추정 '{base_candidate}'")
            return ("REVIEW", f"중복명이나 내용 다름 또는 해시 불명: 원본 추정 '{base_candidate}'")
        return ("REVIEW", f"중복명 형태이나 원본 추정 파일 없음: '{base_candidate}'")

    if path.suffix.lower() == ".zip":
        return ("REVIEW", "ZIP 압축본: 자동 처리 금지")

    if name.lower().endswith((".bak", ".tmp", ".old")):
        return ("ARCHIVE", "임시/백업 확장자 파일")

    return None


def walk_candidates(root: Path, include_docs: bool) -> List[Item]:
    items: List[Item] = []
    selected_dirs: List[Path] = []

    for path in sorted(root.rglob("*"), key=lambda p: (len(p.parts), str(p).lower())):
        if any(is_inside(path, d) for d in selected_dirs):
            continue

        rel = path.relative_to(root)
        if rel.parts and rel.parts[0].startswith("cleanup_backup_"):
            continue

        result = classify_path(path, root, include_docs)
        if not result:
            continue

        category, reason = result
        item = Item(category=category, path=path, reason=reason, size=get_size(path))
        items.append(item)

        if path.is_dir() and category in {"DELETE", "ARCHIVE", "DUPLICATE_ARCHIVE"}:
            selected_dirs.append(path)

    return items


def write_report(root: Path, backup_root: Path, items: List[Item], apply: bool, include_docs: bool) -> Path:
    ts = backup_root.name.replace("cleanup_backup_", "")
    mode = "APPLY" if apply else "DRY-RUN"
    report_path = root / f"cleanup_project_junk_v3_{mode.lower()}_{ts}.txt"

    categories = ["DELETE", "ARCHIVE", "DUPLICATE_ARCHIVE", "REVIEW"]
    grouped: Dict[str, List[Item]] = {c: [] for c in categories}
    for item in items:
        grouped.setdefault(item.category, []).append(item)

    lines: List[str] = []
    lines.append("=" * 70)
    lines.append("키움 자동매매 프로젝트 찌꺼기/백업/중복 파일 정리 보고서 v3")
    lines.append("=" * 70)
    lines.append(f"모드: {mode}")
    lines.append(f"프로젝트 루트: {root}")
    lines.append(f"백업/보관 위치: {backup_root}")
    lines.append(f"docs 정리 포함: {'YES' if include_docs else 'NO'}")
    lines.append("")
    lines.append("[요약]")
    for cat in categories:
        arr = grouped.get(cat, [])
        lines.append(f"- {cat}: {len(arr)}개 / {human_size(sum(x.size for x in arr))}")
    lines.append("")
    lines.append("[주의]")
    lines.append("- DRY-RUN은 실제 삭제/이동을 하지 않는다.")
    lines.append("- APPLY에서도 DELETE/ARCHIVE/DUPLICATE_ARCHIVE 대상은 cleanup_backup 폴더로 먼저 이동한다.")
    lines.append("- REVIEW 대상은 자동 처리하지 않는다.")
    lines.append("- ZIP은 자동 처리하지 않는다.")
    lines.append("- docs 폴더는 기본 보호한다. docs까지 보려면 --include-docs 옵션 필요.")
    lines.append("- include-docs 사용 시에도 MASTER_SPEC/작업재개요약/운영정책/프로젝트현재상태/절대금지 문서는 보호한다.")
    lines.append("- cleanup_project_junk_v3.py 자기 자신은 보호한다.")
    lines.append("")

    for cat in categories:
        arr = grouped.get(cat, [])
        lines.append(f"[{cat} 대상]")
        if not arr:
            lines.append("- 없음")
        else:
            for item in arr:
                rel = item.path.relative_to(root)
                lines.append(f"- {rel} | {human_size(item.size)} | {item.reason}")
        lines.append("")

    report_path.write_text("\n".join(lines), encoding="utf-8")
    return report_path


def move_to_backup(root: Path, backup_root: Path, item: Item) -> None:
    rel = item.path.relative_to(root)
    dest = backup_root / item.category / rel
    dest.parent.mkdir(parents=True, exist_ok=True)

    if dest.exists():
        stamp = datetime.now().strftime("%H%M%S")
        dest = dest.with_name(f"{dest.stem}_{stamp}{dest.suffix}")

    shutil.move(str(item.path), str(dest))


def main() -> int:
    parser = argparse.ArgumentParser(description="키움 자동매매 프로젝트 정리 도구 v3")
    parser.add_argument("--root", default=None, help="프로젝트 루트 경로. 기본값은 현재 작업 폴더")
    parser.add_argument("--apply", action="store_true", help="실제 백업 이동 실행")
    parser.add_argument("--include-docs", action="store_true", help="docs 폴더의 비핵심 문서/보고서도 분류")
    args = parser.parse_args()

    root = Path(args.root).resolve() if args.root else Path.cwd().resolve()
    if not root.exists() or not root.is_dir():
        print(f"[ERROR] 프로젝트 루트가 올바르지 않음: {root}")
        return 1

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_root = root / f"cleanup_backup_{ts}"

    items = walk_candidates(root, include_docs=args.include_docs)

    if args.apply:
        backup_root.mkdir(parents=True, exist_ok=True)
        for item in items:
            if item.category in {"DELETE", "ARCHIVE", "DUPLICATE_ARCHIVE"} and item.path.exists():
                move_to_backup(root, backup_root, item)

    report = write_report(root, backup_root, items, args.apply, args.include_docs)

    grouped: Dict[str, List[Item]] = {}
    for item in items:
        grouped.setdefault(item.category, []).append(item)

    print("=" * 70)
    print("키움 자동매매 프로젝트 정리 도구 v3")
    print("=" * 70)
    print(f"모드: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"프로젝트 루트: {root}")
    print(f"보고서: {report}")
    print(f"docs 정리 포함: {'YES' if args.include_docs else 'NO'}")
    for cat in ["DELETE", "ARCHIVE", "DUPLICATE_ARCHIVE", "REVIEW"]:
        arr = grouped.get(cat, [])
        print(f"[{cat}] {len(arr)}개 / {human_size(sum(x.size for x in arr))}")

    if args.apply:
        print("[APPLY] DELETE/ARCHIVE/DUPLICATE_ARCHIVE 대상은 백업 폴더로 이동 완료.")
        print("[주의] REVIEW 대상은 처리하지 않음.")
    else:
        print("[DRY-RUN] 실제 삭제/이동 없음. 보고서 확인 후 --apply 실행.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
