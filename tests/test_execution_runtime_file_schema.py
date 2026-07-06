from __future__ import annotations

import unittest

from execution_runtime_file_schema import (
    ORDER_EXECUTIONS_SCHEMA,
    ORDER_LOCKS_SCHEMA,
    default_order_executions_data,
    default_order_locks_data,
)


class ExecutionRuntimeFileSchemaTest(unittest.TestCase):
    def test_order_executions_default_schema(self) -> None:
        data = default_order_executions_data()

        self.assertEqual(
            data,
            {
                "version": 1,
                "updated_at": None,
                "executions": [],
            },
        )

    def test_order_locks_default_schema(self) -> None:
        data = default_order_locks_data()

        self.assertEqual(
            data,
            {
                "version": 1,
                "updated_at": None,
                "locks": [],
            },
        )

    def test_order_executions_default_is_deepcopy_independent(self) -> None:
        first = default_order_executions_data()
        second = default_order_executions_data()

        first["executions"].append({"execution_id": "EXEC_1"})
        first["version"] = 2

        self.assertEqual(second, ORDER_EXECUTIONS_SCHEMA)
        self.assertEqual(second["executions"], [])

    def test_order_locks_default_is_deepcopy_independent(self) -> None:
        first = default_order_locks_data()
        second = default_order_locks_data()

        first["locks"].append({"lock_id": "LOCK_1"})
        first["version"] = 2

        self.assertEqual(second, ORDER_LOCKS_SCHEMA)
        self.assertEqual(second["locks"], [])

    def test_schema_module_has_no_file_io_api_usage(self) -> None:
        import execution_runtime_file_schema

        module_text = execution_runtime_file_schema.__loader__.get_source(
            execution_runtime_file_schema.__name__
        )

        self.assertNotIn("write_text", module_text)
        self.assertNotIn("mkdir", module_text)
        self.assertNotIn("open(", module_text)
        self.assertNotIn("os.replace", module_text)


if __name__ == "__main__":
    unittest.main()
