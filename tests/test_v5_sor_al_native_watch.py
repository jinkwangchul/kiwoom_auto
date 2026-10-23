"""Offline contract for the isolated V5 broker _AL callback and parity gate."""

import ast
from math import isfinite
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from tests.test_kiwoom_recovery_snapshot_adapter import _Signal, _load_kiwoom_api_module


ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))


def host_method(name):
    tree = ast.parse((ROOT / "gui_market_data_host.py").read_text(encoding="utf-8"))
    host = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == "MarketDataHost")
    method = next(node for node in host.body if isinstance(node, ast.FunctionDef) and node.name == name)
    method.decorator_list = []
    module = ast.Module(body=[method], type_ignores=[])
    return compile(ast.fix_missing_locations(module), str(ROOT / "gui_market_data_host.py"), "exec")


class Control:
    def __init__(self):
        self.calls = []
        self.fids = {20: "090101", 10: "+101", 13: "10", 15: "+1"}

    def dynamicCall(self, signature, *args):
        self.calls.append((signature, args))
        if signature.startswith("GetCommRealData"):
            return self.fids[args[1]]
        return 0


class NativeWatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.saved = {name: sys.modules.get(name) for name in
                     ("PyQt5", "PyQt5.QtCore", "PyQt5.QtWidgets", "PyQt5.QAxContainer", "kiwoom_api")}
        cls.module = _load_kiwoom_api_module()

    @classmethod
    def tearDownClass(cls):
        for name, value in cls.saved.items():
            if value is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = value

    def setUp(self):
        self.api = self.module.KiwoomApi.__new__(self.module.KiwoomApi)
        self.api._control = Control()
        self.api._available = self.api._connected = True
        self.api._login_requested = False
        self.api._login_session_id = "session-7"
        self.api._connection_epoch = 7
        self.api._unavailable_reason = ""
        self.api.last_login_error = 0
        self.api.last_login_message = "connected"
        self.api.v5_al_tick_received = _Signal()
        self.api._screen_allocator = self.module.KiwoomScreenAllocator()
        self.mock_calls = []
        self.api._on_receive_mock_sor_trade = self.mock_calls.append

    def test_registration_isolated_deduplicated_and_removed(self):
        result = self.api.sync_v5_al_realtime_targets(("005930", "005930", "000660"))
        self.assertTrue(result["ok"])
        calls = [args for signature, args in self.api._control.calls if signature.startswith("SetRealReg")]
        self.assertEqual(len(calls), 1)
        self.assertEqual(set(calls[0][1].split(";")), {"005930_AL", "000660_AL"})
        self.api.sync_v5_al_realtime_targets(("000660", "005930"))
        self.assertEqual(sum(signature.startswith("SetRealReg") for signature, _ in self.api._control.calls), 1)
        self.api.sync_v5_al_realtime_targets(())
        self.assertEqual(sum(signature.startswith("SetRealRemove") for signature, _ in self.api._control.calls), 1)

    def test_callback_rejects_unleased_and_stale_then_emits_native(self):
        self.api.sync_v5_al_realtime_targets(("005930",))
        real_type = self.module.REALTIME_EXECUTION_TYPE
        with patch.object(self.module, "datetime") as clock:
            clock.now.return_value = datetime(2026, 10, 23, 9, 1, 2, tzinfo=KST)
            self.api._on_receive_real_data("000660_AL", real_type, "")
            self.api._on_receive_real_data("005930_AL_BAD", real_type, "")
            self.assertEqual(self.api.v5_al_tick_received.values, [])
            self.api._on_receive_real_data("005930_AL", real_type, "")
        self.assertEqual(len(self.api.v5_al_tick_received.values), 1)
        self.assertIn("005930_AL", self.mock_calls)
        tick = self.api.v5_al_tick_received.values[0]
        self.assertEqual(tick["broker_market_identity"], "005930_AL")
        self.assertEqual((tick["connection_epoch"], tick["login_session_id"]), (7, "session-7"))
        self.api._control.fids[20] = "bad"
        self.api._on_receive_real_data("005930_AL", real_type, "")
        self.api._control.fids[20] = "090102"
        self.api._on_receive_real_data("005930_AL", real_type, "")
        self.assertEqual(self.api.v5_al_tick_received.values[-1]["receive_sequence"], 3)
        self.api._connection_epoch = 8
        self.api._on_receive_real_data("005930_AL", real_type, "")
        self.assertEqual(len(self.api.v5_al_tick_received.values), 2)

    def test_more_than_one_screen_uses_independent_batches(self):
        codes = tuple(f"{100000 + index:06d}" for index in range(101))
        self.assertTrue(self.api.sync_v5_al_realtime_targets(codes)["ok"])
        registrations = [args for signature, args in self.api._control.calls
                         if signature.startswith("SetRealReg")]
        self.assertEqual([len(args[1].split(";")) for args in registrations], [100, 1])
        self.assertNotEqual(registrations[0][0], registrations[1][0])
        self.api.clear_v5_al_realtime_targets()
        self.assertEqual(sum(signature.startswith("SetRealRemove")
                             for signature, _ in self.api._control.calls), 2)

    def test_parity_rejects_gap_mismatch_and_session_then_accepts(self):
        namespace = {"isfinite": isfinite}
        exec(host_method("_v5_al_closed_minute_parity"), namespace)
        compare = namespace["_v5_al_closed_minute_parity"]
        native = {"stock_code": "005930", "identity": (7, "session-7"),
                  "minute": "2026-10-23T09:01:00+09:00", "open": 100,
                  "high": 102, "low": 99, "close": 101,
                  "first_sequence": 1, "last_sequence": 3, "tick_count": 3,
                  "continuous": True}
        broker = {"bar_time": native["minute"], "open": 100, "high": 102,
                  "low": 99, "close": 101, "market_data_identity": "005930_AL",
                  "market_source": "INTEGRATED", "is_complete": True}
        self.assertTrue(compare(native, broker, (7, "session-7")))
        self.assertFalse(compare({**native, "continuous": False}, broker, (7, "session-7")))
        self.assertFalse(compare(native, {**broker, "high": 103}, (7, "session-7")))
        self.assertFalse(compare(native, broker, (8, "session-8")))


if __name__ == "__main__":
    unittest.main()
