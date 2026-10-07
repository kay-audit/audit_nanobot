from __future__ import annotations

import json
import sys
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from lib.services import sql_assistant_runtime as module


class Provider:
    def execute_readonly(self, sql, params=None, max_rows=1000):
        return {"columns": ["id", "table_name"], "rows": [(1, "db.orders")]}


def validation(sql, valid=False):
    return {"status": "valid" if valid else "invalid", "valid": valid, "sql": sql,
            "issues": [] if valid else [{"code": "unknown_column", "message": "Unknown column DATETIME_UTC"}],
            "warnings": [], "ast": sql}


class TestDelivery(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runtime = module.SqlAssistantRuntime(Provider())
        self.runtime.store.schema_for_tables = Mock(return_value={"db.orders": {"id": "BIGINT"}})
        self.runtime.describe = AsyncMock(return_value={"tables": [{"id": 1}], "examples": []})
        self.runtime.facts = Mock(return_value={"tables": ["db.orders"]})

    async def test_invalid_validation_withholds_sql_and_ast(self):
        with patch.object(module, "validate_sql", return_value=validation("SELECT missing FROM db.orders")):
            result = await self.runtime.validate("SELECT missing FROM db.orders", dialect="spark", table_ids=[1])
        self.assertFalse(result["publishable"])
        self.assertFalse(result["valid"])
        self.assertEqual(result["sql"], "")
        self.assertNotIn("ast", result)
        self.assertEqual(result["issues"][0]["code"], "unknown_column")
        self.assertEqual(result["delivery"]["action"], "explain_validation_failure")

    async def test_valid_static_validation_is_explicitly_static_only(self):
        with patch.object(module, "validate_sql", return_value=validation("SELECT id FROM db.orders", True)):
            result = await self.runtime.validate("SELECT id FROM db.orders", dialect="spark", table_ids=iter([1]))
        self.assertTrue(result["publishable"])
        self.assertEqual(result["validation_scope"], "static_only")
        self.runtime.store.schema_for_tables.assert_called_once_with([1])

    async def test_live_invalid_and_unavailable_block_delivery(self):
        for status in ("invalid", "unavailable", "timeout"):
            with self.subTest(status=status):
                with patch.object(module, "validate_sql", return_value=validation("SELECT id FROM db.orders", True)), patch.object(module.SparkBackend, "analyze", new=AsyncMock(return_value={"status": status, "error": "analysis failed"})):
                    result = await self.runtime.validate("SELECT id FROM db.orders", dialect="spark", table_ids=[1], live_analyze=True)
                self.assertFalse(result["publishable"])
                self.assertFalse(result["valid"])
                self.assertEqual(result["sql"], "")

    async def test_live_valid_allows_delivery(self):
        with patch.object(module, "validate_sql", return_value=validation("SELECT id FROM db.orders", True)), patch.object(module.SparkBackend, "analyze", new=AsyncMock(return_value={"status": "valid", "plan": "fake"})):
            result = await self.runtime.validate("SELECT id FROM db.orders", dialect="spark", table_ids=[1], live_analyze=True)
        self.assertTrue(result["publishable"])
        self.assertEqual(result["validation_scope"], "spark_analysis")

    async def _generate(self, replies, validator, repairs=2):
        llm = Mock(side_effect=replies)
        with patch.dict(sys.modules, {"lib.services.llm_client": SimpleNamespace(call_llm=llm)}), patch.object(module, "validate_sql", side_effect=validator):
            result = await self.runtime.generate(question="monthly count", dialect="spark", table_ids=[1], llm_config={"model": "fake"}, max_repairs=repairs)
        return result, llm

    async def test_repeated_invalid_repairs_do_not_publish_sql_or_facts(self):
        result, llm = await self._generate(["SELECT missing FROM db.orders"] * 3, lambda sql, **kw: validation(sql))
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["publishable"])
        self.assertEqual(result["sql"], "")
        self.assertEqual(result["validation"]["sql"], "")
        self.assertNotIn("SELECT missing", json.dumps(result))
        self.assertEqual(result["facts"], {})
        self.runtime.facts.assert_not_called()
        self.assertEqual(llm.call_count, 2)

    async def test_zero_repairs_invalid_is_withheld(self):
        result, llm = await self._generate(["SELECT missing FROM db.orders"], lambda sql, **kw: validation(sql), repairs=0)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["sql"], "")
        self.assertEqual(llm.call_count, 1)

    async def test_successful_repair_publishes_only_valid_sql(self):
        result, llm = await self._generate(["SELECT missing FROM db.orders", "SELECT id FROM db.orders"], lambda sql, **kw: validation(sql, "missing" not in sql))
        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["publishable"])
        self.assertEqual(result["sql"], "SELECT id FROM db.orders")
        self.assertNotIn("SELECT missing", json.dumps(result))
        self.assertEqual(llm.call_count, 2)
        self.runtime.facts.assert_called_once()

    async def test_no_selected_table_blocks_llm(self):
        result = await self.runtime.generate(question="x", dialect="spark", table_ids=[])
        self.assertEqual(result["status"], "grounding_error")

    async def test_cli_validation_uses_same_delivery_gate_without_table_ids(self):
        from workspace.skills.sql_assistant.scripts import cli
        args = cli.parser().parse_args(["--mode", "validate", "--sql", "SELECT missing"])
        with patch.object(module, "validate_sql", return_value=validation("SELECT missing")):
            result = await cli.run(args)
        self.assertFalse(result["publishable"])
        self.assertEqual(result["sql"], "")


if __name__ == "__main__":
    unittest.main()
