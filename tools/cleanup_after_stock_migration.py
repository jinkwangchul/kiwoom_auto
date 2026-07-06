# -*- coding: utf-8 -*-
"""
cleanup_after_stock_migration.py

중앙 stocks 종목폴더 통합 이후 남은 구형/마이그레이션/테스트 찌꺼기 정리 도구.

기본은 dry-run 입니다.
실제 적용은 반드시 결과를 확인한 뒤 --apply 옵션으로 실행하세요.

사용 예:
    python cleanup_after_stock_migration.py
    python cleanup_after_stock_migration.py --root "C:\\Users\\JIN KWANG CHUL\\Documents\\kiwoom_auto"
    python cleanup_after_stock_migration.py --apply
    python cleanup_after_stock_migration.py --apply --root "C:\\Users\\JIN KWANG CHUL\\Documents\\kiwoom_auto"

정책:
- 중앙 운영 원본인 stocks/, archived_stocks/, 루틴폴더, 정책파일은 절대 건드리지 않는다.
- 삭제/이동 대상은 실행 전 cleanup_backup_날짜시간/ 로 먼저 백업한다.
- 마이그레이션/리셋/테스트 도구는 삭제하지 않고 old_migration_tools/ 로 이동한다.
- review_required.json, 로그, __pycache__, archive_candidate, _deleted_stocks 는 백업 후 삭제한다.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import os
import shutil
from pathlib import Path
from typing import Iterable


# 절대 보호 대상: 실수로 목록에 들어가도 정리하지 않음
PROTECTED_NAMES = {
    "stocks",
    "archived_stocks",
    "_지표추종매매",
    "_등록확인폴더",
    "reports",
    "operation_policy.json",
    "global_schedule.json",
    "stock_library.json",
    "stock_repository.py",
    "gui_main.py",
}

# 백업 후 삭제 후보
DELETE_CANDIDATES = [
    "review_required.json",
    "archive_candidate",
    "_deleted_stocks",
    "invalid_items.log",
    "system_error.log",
]

# 재귀 삭제 후보
DELETE_GLOB_PATTERNS = [
    "**/__pycache__",
]

# old_migration_tools/ 로 이동할 후보
MOVE_TO_OLD_TOOLS = [
    "stock_migration_to_central_stocks.py",
    "stock_migration_manifest.json",
    "stock_migration_apply_report.txt",
    "archive_legacy_routine_stock_dirs.py",
    "legacy_routine_stock_archive_report.txt",
    "reset_all_stock_state.py",
    "reset_all_stock_state_report.txt",
    "registry_migratio",
    "backup_before_full_stock_reset",
    "_recovery_backup",
    "make_sample_orders.py",
    "make_sample_orders_multi_day.py",
    "make_sample_orders_multi_day_with_fee.py",
    "gui_windows_149_manual_ats_status_policy_fix.py",
]


def now_stamp() -> str:
    return _dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def is_protected(path: Path, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root.resolve())
    except ValueError:
        return True
    if not rel.parts:
        return True
    return rel.parts[0] in PROTECTED_NAMES


def path_size(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return path.stat().st_size
    total = 0
    for p in path.rglob("*"):
        if p.is_file():
            try:
                total += p.stat().st_size
            except OSError:
                pass
    return total


def count_items(path: Path) -> int:
    if not path.exists():
        return 0
    if path.is_file():
        return 1
    return sum(1 for _ in path.rglob("*"))


def human_size(size: int) -> str:
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f}{unit}"
        value /= 1024
    return f"{size}B"


def unique_destination(dest: Path) -> Path:
    if not dest.exists():
        return dest
    base = dest
    i = 1
    while True:
        candidate = base.with_name(f"{base.name}_{i}")
        if not candidate.exists():
            return candidate
        i += 1


def copy_to_backup(src: Path, backup_root: Path, root: Path) -> Path:
    rel = src.relative_to(root)
    dest = unique_destination(backup_root / rel)
    dest.parent.mkdir(parents=True, exist_ok=True)
    if src.is_dir():
        shutil.copytree(src, dest)
    else:
        shutil.copy2(src, dest)
    return dest


def remove_path(path: Path) -> None:
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def collect_existing(root: Path) -> tuple[list[Path], list[Path]]:
    delete_targets: list[Path] = []
    move_targets: list[Path] = []

    for name in DELETE_CANDIDATES:
        p = root / name
        if p.exists():
            delete_targets.append(p)

    for pattern in DELETE_GLOB_PATTERNS:
        for p in root.glob(pattern):
            if p.exists():
                delete_targets.append(p)

    for name in MOVE_TO_OLD_TOOLS:
        p = root / name
        if p.exists():
            move_targets.append(p)

    # 중복 제거, 상위 경로가 이미 대상이면 하위 경로 제거
    def normalize(paths: Iterable[Path]) -> list[Path]:
        resolved = []
        for p in paths:
            try:
                rp = p.resolve()
            except OSError:
                continue
            if rp not in resolved:
                resolved.append(rp)
        result = []
        for p in sorted(resolved, key=lambda x: len(str(x))):
            if any(str(p).startswith(str(parent) + os.sep) for parent in result):
                continue
            result.append(p)
        return result

    return normalize(delete_targets), normalize(move_targets)


def write_report(report_path: Path, lines: list[str]) -> None:
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description="중앙 stocks 통합 후 찌꺼기 정리 도구")
    parser.add_argument("--root", default=".", help="kiwoom_auto 프로젝트 루트 경로. 기본값: 현재 폴더")
    parser.add_argument("--apply", action="store_true", help="실제 삭제/이동 실행. 없으면 dry-run")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    if not root.exists() or not root.is_dir():
        print(f"[오류] root 폴더가 없습니다: {root}")
        return 2

    backup_root = root / f"cleanup_backup_{now_stamp()}"
    old_tools_dir = root / "old_migration_tools"

    delete_targets, move_targets = collect_existing(root)

    # 보호 대상 차단
    blocked = []
    safe_delete = []
    safe_move = []
    for p in delete_targets:
        if is_protected(p, root):
            blocked.append(("DELETE", p))
        else:
            safe_delete.append(p)
    for p in move_targets:
        if is_protected(p, root):
            blocked.append(("MOVE", p))
        else:
            safe_move.append(p)

    lines: list[str] = []
    lines.append("=" * 70)
    lines.append("중앙 stocks 통합 후 정리 도구 실행 보고서")
    lines.append("=" * 70)
    lines.append(f"모드: {'APPLY' if args.apply else 'DRY-RUN'}")
    lines.append(f"프로젝트 루트: {root}")
    lines.append(f"백업 예정 위치: {backup_root}")
    lines.append(f"보관 이동 위치: {old_tools_dir}")
    lines.append("")

    lines.append("[백업 후 삭제 예정]")
    if safe_delete:
        for p in safe_delete:
            lines.append(f"- {p.relative_to(root)} | items={count_items(p)} | size={human_size(path_size(p))}")
    else:
        lines.append("- 없음")
    lines.append("")

    lines.append("[백업 후 old_migration_tools 이동 예정]")
    if safe_move:
        for p in safe_move:
            lines.append(f"- {p.relative_to(root)} | items={count_items(p)} | size={human_size(path_size(p))}")
    else:
        lines.append("- 없음")
    lines.append("")

    lines.append("[보호 차단 항목]")
    if blocked:
        for action, p in blocked:
            lines.append(f"- {action}: {p}")
    else:
        lines.append("- 없음")
    lines.append("")

    if not args.apply:
        lines.append("[DRY-RUN 안내]")
        lines.append("- 실제 삭제/이동은 수행하지 않았습니다.")
        lines.append("- 위 목록 확인 후 문제가 없을 때만 --apply 옵션으로 실행하세요.")
        print("\n".join(lines))
        report_path = root / f"cleanup_after_stock_migration_dry_run_{now_stamp()}.txt"
        write_report(report_path, lines)
        print(f"\n[보고서 생성] {report_path}")
        return 0

    backup_root.mkdir(parents=True, exist_ok=True)
    old_tools_dir.mkdir(parents=True, exist_ok=True)

    lines.append("[실행 결과]")
    for p in safe_delete:
        try:
            backup_dest = copy_to_backup(p, backup_root, root)
            remove_path(p)
            lines.append(f"- DELETE 완료: {p.relative_to(root)} | backup={backup_dest.relative_to(root)}")
        except Exception as exc:
            lines.append(f"- DELETE 실패: {p.relative_to(root)} | {exc}")

    for p in safe_move:
        try:
            backup_dest = copy_to_backup(p, backup_root, root)
            dest = unique_destination(old_tools_dir / p.name)
            shutil.move(str(p), str(dest))
            lines.append(f"- MOVE 완료: {p.relative_to(root)} -> {dest.relative_to(root)} | backup={backup_dest.relative_to(root)}")
        except Exception as exc:
            lines.append(f"- MOVE 실패: {p.relative_to(root)} | {exc}")

    report_path = root / f"cleanup_after_stock_migration_apply_{now_stamp()}.txt"
    write_report(report_path, lines)
    print("\n".join(lines))
    print(f"\n[보고서 생성] {report_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
