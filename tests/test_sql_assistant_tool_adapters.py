from __future__ import annotations

import importlib
import importlib.util
import json
import sys
import unittest
from types import ModuleType
from unittest.mock import AsyncMock, patch

from lib.services import sql_assistant_runtime


class TestToolAdapters(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tool_modules = ("workspace.tools.kb_search", "workspace.tools.sql_validate", "workspace.tools.kb_describe")
        self.previous = {name: sys.modules.get(name) for name in self.tool_modules}
        self.native_patch = None
        if importlib.util.find_spec("nanobot") is None:
            base = ModuleType("nanobot.agent.tools.base")
            base.Tool = type("Tool", (), {})
            base.tool_parameters = lambda parameters: lambda cls: cls
            modules = {name: ModuleType(name) for name in ("nanobot", "nanobot.agent", "nanobot.agent.tools")}
            for module in modules.values():
                module.__path__ = []
            modules["nanobot.agent.tools.base"] = base
            self.native_patch = patch.dict(sys.modules, modules)
            self.native_patch.start()

    def tearDown(self):
        for name, previous in self.previous.items():
            if previous is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = previous
        if self.native_patch is not None:
            self.native_patch.stop()

    async def test_validate_tool_returns_delivery_gate_not_audit_success(self):
        module = importlib.import_module("workspace.tools.sql_validate")
        tool = module.SqlValidateTool(config=module.SqlValidateConfig())
        tool.set_provider(type("Provider", (), {"execute_readonly": lambda *args, **kwargs: {"rows": [], "columns": []}})())
        report = {"status": "invalid", "valid": False, "sql": "SELECT missing", "issues": [{"code": "unknown_column"}], "warnings": []}
        with patch.object(sql_assistant_runtime, "validate_sql", return_value=report):
            result = json.loads(await tool.execute(sql="SELECT missing", dialect="spark"))
        self.assertEqual(result["status"], "invalid")
        self.assertFalse(result["publishable"])
        self.assertEqual(result["sql"], "")

    async def test_kb_search_uses_remote_adapters_not_local_models(self):
        module = importlib.import_module("workspace.tools.kb_search")
        tool = module.KbSearchTool(config=module.KbSearchConfig())
        tool.set_provider(object())
        captured = {}
        class Runtime:
            def __init__(self, provider, **kwargs):
                captured.update(kwargs)
            def search(self, *args, **kwargs):
                return {"status": "ok", "items": []}
        with patch.object(module, "SqlAssistantRuntime", Runtime):
            result = json.loads(await tool.execute(query="monthly", corpus="tables"))
        self.assertEqual(result["status"], "ok")
        self.assertIsInstance(captured["embedder"].__self__, module.OsirisModels)
        self.assertIsInstance(captured["reranker"].__self__, module.OsirisModels)
        self.assertIn("|osiris|", captured["model_key"])

    async def test_describe_exact_table_name_returns_only_kb_columns(self):
        module = importlib.import_module("workspace.tools.kb_describe")
        table = "UVZ_SELFSERVICE_SRC.MV_UVZ_WORK_PLANS"
        class Provider:
            def execute_readonly(self, sql, params=None, max_rows=1000):
                if "lower(table_name) IN" in sql:
                    return {"columns": ["id", "table_name"], "rows": [(10, table)]}
                if "kb_columns" in sql:
                    return {"columns": ["id", "table_id", "column_name", "data_type"], "rows": [(20, 10, "PA_ID", "BIGINT")]}
                return {"columns": [], "rows": []}
        tool = module.KbDescribeTool(config=module.KbDescribeConfig())
        tool.set_provider(Provider())
        result = json.loads(await tool.execute(table_names=[table], detail="full"))
        self.assertEqual(result["tables"][0]["table_name"], table)
        self.assertEqual([row["column_name"] for row in result["tables"][0]["selected_columns"]], ["PA_ID"])

    async def test_columns_tool_does_not_use_models(self):
        module = importlib.import_module("workspace.tools.kb_search")
        tool = module.KbSearchTool(config=module.KbSearchConfig())
        tool.set_provider(object())
        captured = {}
        class Runtime:
            def __init__(self, provider, **kwargs):
                captured.update(kwargs)
            def search(self, *args, **kwargs):
                return {"status": "ok", "items": []}
        with patch.object(module, "SqlAssistantRuntime", Runtime):
            await tool.execute(query="id", corpus="columns")
        self.assertIsNone(captured["embedder"])
        self.assertIsNone(captured["reranker"])


if __name__ == "__main__":
    unittest.main()
