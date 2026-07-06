# -*- coding: utf-8 -*-
"""routine_signal_probe.py

STEP 6-B: 루틴 evaluate() 연결 확인용 안전 프로브 + 신호큐 저장본.

역할:
- 현재 선택 루틴의 routine.py를 import한다.
- 연결 종목별로 evaluate(context)를 호출한다.
- 결과를 runtime/routine_signal_probe.log에 기록한다.
- BUY/SELL 신호만 runtime/routine_signals.json 큐에 저장한다.

중요 원칙:
- 주문 실행 없음.
- 예산 처리 없음.
- 청산 처리 없음.
- state.json / config.json / orders.json 수정 없음.
- GUI 상태 컬럼 변경 없음.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any


PROJECT_ROOT = Path(__file__).resolve().parent
RUNTIME_DIR = PROJECT_ROOT / "runtime"
LOG_PATH = RUNTIME_DIR / "routine_signal_probe.log"


try:
    from routine_signal_queue import enqueue_routine_signal
except Exception:  # pragma: no cover
    enqueue_routine_signal = None


def now_text() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _read_json(path: Path) -> Any:
    try:
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_json_dict(path: Path) -> dict[str, Any]:
    data = _read_json(path)
    return data if isinstance(data, dict) else {}


def _append_log(line: str) -> None:
    try:
        RUNTIME_DIR.mkdir(parents=True, exist_ok=True)
        with LOG_PATH.open("a", encoding="utf-8") as file:
            file.write(line.rstrip() + "\n")
    except Exception:
        pass


def _parse_stock_folder_name(stock_dir: Path) -> tuple[str, str]:
    parts = stock_dir.name.split("_", 1)
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", stock_dir.name


def _load_candles_from_stock_dir(stock_dir: Path) -> list[dict[str, Any]]:
    for filename in ("candles.json", "bars.json", "ohlcv.json"):
        data = _read_json(stock_dir / filename)
        if isinstance(data, list):
            return [item for item in data if isinstance(item, dict)]
        if isinstance(data, dict):
            for key in ("candles", "bars", "ohlcv"):
                value = data.get(key)
                if isinstance(value, list):
                    return [item for item in value if isinstance(item, dict)]
    return []


def _load_routine_module(routine_dir: Path):
    routine_file = routine_dir / "routine.py"
    if not routine_file.exists():
        raise FileNotFoundError(f"routine.py 없음: {routine_file}")

    module_name = "runtime_probe_" + routine_dir.name.replace("-", "_").replace(" ", "_")
    spec = importlib.util.spec_from_file_location(module_name, routine_file)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"routine.py 로드 실패: {routine_file}")

    module = importlib.util.module_from_spec(spec)
    routine_path_text = str(routine_dir.resolve())
    added_to_path = False
    if routine_path_text not in sys.path:
        sys.path.insert(0, routine_path_text)
        added_to_path = True
    try:
        spec.loader.exec_module(module)
    finally:
        if added_to_path:
            try:
                sys.path.remove(routine_path_text)
            except ValueError:
                pass
    return module


def _is_trade_watch_target(state: dict[str, Any]) -> bool:
    if bool(state.get("review_required", False)):
        return False
    if not bool(state.get("trade_enabled", False)):
        return False

    status = str(state.get("status", "") or "").upper()
    if status in {"REVIEW_REQUIRED", "STOPPED", "UNREGISTERED"}:
        return False

    return True


def _maybe_enqueue_signal(
    result: dict[str, Any],
    *,
    routine_name: str,
    code: str,
    name: str,
    tick_key: str,
) -> dict[str, Any] | None:
    if not callable(enqueue_routine_signal):
        return None

    try:
        return enqueue_routine_signal(
            result,
            routine=routine_name,
            code=code,
            name=name,
            tick_key=tick_key,
            source="routine_signal_probe",
        )
    except Exception as exc:
        return {
            "status": "error",
            "reason": f"신호큐 저장 예외: {exc}",
        }


def probe_routine_for_stock(
    routine_module: Any,
    routine_name: str,
    stock_dir: Path,
    tick_key: str,
) -> dict[str, Any]:
    code, name = _parse_stock_folder_name(stock_dir)
    state = _read_json_dict(stock_dir / "state.json")
    stock_config = _read_json_dict(stock_dir / "config.json")

    queue_result = None

    if not _is_trade_watch_target(state):
        result = {
            "signal": "SKIP",
            "reason": "감시 대상 아님",
            "routine": routine_name,
            "code": code,
            "name": name,
        }
    else:
        candles = _load_candles_from_stock_dir(stock_dir)
        evaluate = getattr(routine_module, "evaluate", None)
        if not callable(evaluate):
            result = {
                "signal": "ERROR",
                "reason": "evaluate 함수 없음",
                "routine": routine_name,
                "code": code,
                "name": name,
            }
        else:
            context = {
                "routine": routine_name,
                "code": code,
                "name": name,
                "stock_dir": str(stock_dir),
                "state": state,
                "stock_config": stock_config,
                "candles": candles,
                "probe_only": True,
                "tick_key": tick_key,
            }
            try:
                raw_result = evaluate(context)
                result = raw_result if isinstance(raw_result, dict) else {
                    "signal": "ERROR",
                    "reason": f"evaluate 반환 형식 오류: {type(raw_result).__name__}",
                }
                result["code"] = code
                result["name"] = name
                result["routine"] = result.get("routine", routine_name)
                result["candles"] = len(candles)

                queue_result = _maybe_enqueue_signal(
                    result,
                    routine_name=routine_name,
                    code=code,
                    name=name,
                    tick_key=tick_key,
                )
                if isinstance(queue_result, dict):
                    result["queue_status"] = queue_result.get("status")
                    result["queue_id"] = queue_result.get("id", "")

            except Exception as exc:
                result = {
                    "signal": "ERROR",
                    "reason": f"evaluate 예외: {exc}",
                    "routine": routine_name,
                    "code": code,
                    "name": name,
                }

    queue_text = ""
    if isinstance(queue_result, dict):
        queue_text = f" queue={queue_result.get('status')}"

    _append_log(
        f"[{now_text()}] tick={tick_key} routine={routine_name} "
        f"stock={code} {name} signal={result.get('signal')} reason={result.get('reason')}{queue_text}"
    )
    return result


def probe_selected_routine_once(window: Any, tick_key: str = "") -> dict[str, int]:
    routine_dir_func = getattr(window, "current_selected_routine_dir", None)
    routine_name_func = getattr(window, "current_selected_routine_name", None)

    if not callable(routine_dir_func) or not callable(routine_name_func):
        return {"checked": 0, "logged": 0, "error": 1, "skip": 0, "queued": 0}

    routine_dir = routine_dir_func()
    routine_name = str(routine_name_func() or "").strip()

    if routine_dir is None or not routine_name:
        return {"checked": 0, "logged": 0, "error": 0, "skip": 0, "queued": 0}

    try:
        from gui_auto_trade_runtime import get_stock_dirs_in_routine
        stock_dirs = get_stock_dirs_in_routine(routine_dir)
    except Exception:
        stock_dirs = []

    try:
        routine_module = _load_routine_module(Path(routine_dir))
    except Exception as exc:
        _append_log(f"[{now_text()}] tick={tick_key} routine={routine_name} ERROR routine load: {exc}")
        return {"checked": 0, "logged": 0, "error": 1, "skip": 0, "queued": 0}

    checked = 0
    logged = 0
    error = 0
    skip = 0
    queued = 0

    for stock_dir in stock_dirs:
        checked += 1
        result = probe_routine_for_stock(routine_module, routine_name, Path(stock_dir), tick_key)
        signal = str(result.get("signal", "") or "").upper()
        if signal == "SKIP":
            skip += 1
        elif signal == "ERROR":
            error += 1
            logged += 1
        else:
            logged += 1

        if result.get("queue_status") == "queued":
            queued += 1

    return {"checked": checked, "logged": logged, "error": error, "skip": skip, "queued": queued}
