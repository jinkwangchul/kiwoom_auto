# -*- coding: utf-8 -*-
"""
create_routine_packages_from_legacy.py

중앙 stocks 통합 이후, 구형 루틴폴더(_지표추종매매, _등록확인폴더)의 budget.json을
신규 routines/<루틴명>/routine.json 구조로 이관하는 안전 도구입니다.

기본 실행은 DRY-RUN입니다.
실제 생성은 --apply 옵션을 붙여 실행하세요.

사용 예:
    python create_routine_packages_from_legacy.py
    python create_routine_packages_from_legacy.py --apply

정책:
- 구형 루틴폴더는 삭제하지 않습니다.
- stocks/, archived_stocks/ 등 종목 데이터는 건드리지 않습니다.
- routines/<루틴명>/routine.json, routine.py, README.txt, assets/만 생성합니다.
- 이미 존재하는 파일은 기본적으로 덮어쓰지 않습니다.
- 덮어쓰기가 필요하면 --overwrite 옵션을 추가합니다.
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

LEGACY_ROUTINES = [
    {
        "legacy_dir": "_지표추종매매",
        "package_name": "지표추종매매",
        "module_name": "macd_routine",
        "routine_type": "auto_trade",
        "description": "MACD 기반 자동매매 루틴 패키지입니다. 현재는 패키지 자동인식용 더미 routine.py를 포함합니다.",
    },
    {
        "legacy_dir": "_등록확인폴더",
        "package_name": "등록확인루틴",
        "module_name": "register_check_routine",
        "routine_type": "utility",
        "description": "등록 확인 및 테스트용 루틴 패키지입니다. 현재는 패키지 자동인식용 더미 routine.py를 포함합니다.",
    },
]

PROTECTED_NAMES = {
    "stocks",
    "archived_stocks",
    "reports",
    "old_migration_tools",
    "cleanup_backup_20260616_065342",
}


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except Exception as exc:
        return {"_load_error": str(exc)}


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or value == "":
            return default
        return int(float(value))
    except Exception:
        return default


def make_routine_json(meta: Dict[str, str], legacy_budget: Dict[str, Any]) -> Dict[str, Any]:
    total_budget = safe_int(legacy_budget.get("total_budget"), 0)
    used_budget = safe_int(legacy_budget.get("used_budget"), 0)
    available_budget = safe_int(legacy_budget.get("available_budget"), max(total_budget - used_budget, 0))
    reserved_order_budget = safe_int(legacy_budget.get("reserved_order_budget"), 0)

    return {
        "schema_version": "1.0",
        "name": meta["package_name"],
        "enabled": True,
        "version": "0.1.0",
        "routine_type": meta["routine_type"],
        "entry_file": "routine.py",
        "module_name": meta["module_name"],
        "description": meta["description"],
        "source": {
            "created_from": "legacy_routine_folder_budget_json",
            "legacy_dir": meta["legacy_dir"],
            "migrated_at": now_text(),
        },
        "budget": {
            "total_budget": total_budget,
            "used_budget": used_budget,
            "available_budget": available_budget,
            "reserved_order_budget": reserved_order_budget,
            "daily_realized_pnl": safe_int(legacy_budget.get("daily_realized_pnl"), 0),
            "daily_loss_limit_percent": legacy_budget.get("daily_loss_limit_percent", -3),
            "legacy_updated_at": legacy_budget.get("updated_at", ""),
        },
        "safety": {
            "auto_restore_after_missing": False,
            "delete_stock_when_routine_missing": False,
            "exclude_auto_trade_when_missing": True,
            "move_to_review_if_holding_or_pending": True,
        },
        "runtime": {
            "execution_enabled": False,
            "note": "현재 routine.py는 자동 인식 및 구조 전환용 더미입니다. 실제 매매 실행은 메인 엔진 안정화 후 별도 연결합니다.",
        },
    }


def make_routine_py(package_name: str) -> str:
    return f'''# -*- coding: utf-8 -*-
"""
{package_name} routine.py

루틴 패키지 자동 인식용 엔트리 파일입니다.
현재 단계에서는 실제 매매 실행 로직을 직접 수행하지 않습니다.
메인 엔진에서 정책/안전검사/주문집행을 통제하는 구조를 유지합니다.
"""

ROUTINE_NAME = "{package_name}"
ROUTINE_API_VERSION = "0.1"
EXECUTION_ENABLED = False


def get_routine_info():
    """루틴 메타정보 반환."""
    return {{
        "name": ROUTINE_NAME,
        "api_version": ROUTINE_API_VERSION,
        "execution_enabled": EXECUTION_ENABLED,
    }}


def evaluate(context):
    """
    향후 루틴 신호 평가용 인터페이스 자리입니다.

    현재는 실제 매수/매도 신호를 반환하지 않습니다.
    주문 실행은 반드시 메인 엔진 안전로직을 통과해야 합니다.
    """
    return {{
        "signal": "NONE",
        "reason": "routine package scaffold only",
    }}
'''


def make_readme(package_name: str, legacy_dir: str) -> str:
    return f"""# {package_name}\n\n이 폴더는 신규 루틴 패키지 구조 테스트/전환용으로 생성되었습니다.\n\n- 기존 원본: {legacy_dir}/budget.json\n- 신규 메타: routine.json\n- 신규 엔트리: routine.py\n\n현재 routine.py는 실제 매매 실행용이 아니라 자동 인식 구조 확인용 더미입니다.\n실제 주문 판단과 주문 실행은 메인 엔진의 안전로직을 통과해야 합니다.\n"""


def write_text(path: Path, text: str, apply: bool, overwrite: bool, actions: List[str]) -> None:
    if path.exists() and not overwrite:
        actions.append(f"SKIP exists: {path}")
        return
    actions.append(f"WRITE: {path}")
    if apply:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")


def write_json(path: Path, data: Dict[str, Any], apply: bool, overwrite: bool, actions: List[str]) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    write_text(path, text + "\n", apply, overwrite, actions)


def create_package(root: Path, meta: Dict[str, str], apply: bool, overwrite: bool, actions: List[str]) -> None:
    legacy_dir = root / meta["legacy_dir"]
    budget_path = legacy_dir / "budget.json"
    routines_root = root / "routines"
    package_dir = routines_root / meta["package_name"]

    legacy_budget = load_json(budget_path)
    if not budget_path.exists():
        actions.append(f"WARN legacy budget not found: {budget_path}")
    elif "_load_error" in legacy_budget:
        actions.append(f"WARN legacy budget load failed: {budget_path} | {legacy_budget['_load_error']}")

    routine_json = make_routine_json(meta, legacy_budget)

    actions.append(f"PACKAGE: {package_dir}")
    if apply:
        package_dir.mkdir(parents=True, exist_ok=True)
        (package_dir / "assets").mkdir(parents=True, exist_ok=True)

    write_json(package_dir / "routine.json", routine_json, apply, overwrite, actions)
    write_text(package_dir / "routine.py", make_routine_py(meta["package_name"]), apply, overwrite, actions)
    write_text(package_dir / "README.txt", make_readme(meta["package_name"], meta["legacy_dir"]), apply, overwrite, actions)

    if apply:
        # assets 폴더가 비어 있어도 루틴 패키지 부속 파일 자리로 유지한다.
        (package_dir / "assets").mkdir(parents=True, exist_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="구형 루틴폴더 budget.json을 신규 routines/<루틴명>/ 패키지로 이관")
    parser.add_argument("--apply", action="store_true", help="실제 파일 생성")
    parser.add_argument("--overwrite", action="store_true", help="기존 routine.json/routine.py/README.txt 덮어쓰기")
    parser.add_argument("--root", default=".", help="프로젝트 루트 경로. 기본값은 현재 폴더")
    args = parser.parse_args()

    root = Path(args.root).resolve()
    actions: List[str] = []

    print("=" * 70)
    print("루틴 패키지 구조 생성 도구")
    print("=" * 70)
    print(f"모드: {'APPLY' if args.apply else 'DRY-RUN'}")
    print(f"프로젝트 루트: {root}")
    print(f"생성 대상: {root / 'routines'}")
    print(f"덮어쓰기: {'YES' if args.overwrite else 'NO'}")
    print()

    if root.name in PROTECTED_NAMES:
        print(f"[중단] 프로젝트 루트가 보호 폴더로 보입니다: {root}")
        return 2

    for meta in LEGACY_ROUTINES:
        create_package(root, meta, args.apply, args.overwrite, actions)

    print("[작업 예정/결과]")
    for action in actions:
        print(f"- {action}")

    report_name = f"create_routine_packages_{'apply' if args.apply else 'dry_run'}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
    report_path = root / report_name
    report_text = "\n".join([
        "=" * 70,
        "루틴 패키지 구조 생성 도구 보고서",
        "=" * 70,
        f"모드: {'APPLY' if args.apply else 'DRY-RUN'}",
        f"프로젝트 루트: {root}",
        f"생성 대상: {root / 'routines'}",
        f"덮어쓰기: {'YES' if args.overwrite else 'NO'}",
        "",
        "[작업 예정/결과]",
        *[f"- {a}" for a in actions],
        "",
    ])

    if args.apply:
        report_path.write_text(report_text, encoding="utf-8")
        print(f"\n[보고서 생성] {report_path}")
    else:
        print("\n[DRY-RUN 안내]")
        print("- 실제 파일 생성은 수행하지 않았습니다.")
        print("- 위 목록 확인 후 문제가 없을 때 --apply 옵션으로 실행하세요.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
