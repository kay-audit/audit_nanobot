from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from workspace.tools.kb_search import KbSearchConfig, KbSearchTool
from workspace.tools.sql_analyzer import SQLAnalyzerTool, SQLAnalyzerToolConfig
from workspace.tools.sql_facts import SqlFactsConfig, SqlFactsTool
from workspace.tools.kb_describe import KbDescribeTool
from workspace.tools.sql_generate import SqlGenerateTool
from workspace.tools.sql_validate import SqlValidateTool

SQL_ASSISTANT_TOOLS = (SQLAnalyzerTool, KbSearchTool, KbDescribeTool, SqlGenerateTool, SqlValidateTool, SqlFactsTool)


class Provider:
    def execute_readonly(self, sql, params=None, max_rows=1000):
        if "kb_examples" in sql:
            row={"id":338,"script_id":338,"km_id":"K","file_name":"x.sql","file_path":"/x.sql","nl":"count","nl_variants":"[]","sql":"SELECT 338;\r\n","script_description":"d","tables":"[]","dialect":"spark","updated_at":"now"}
            cols=list(row); rows=[tuple(row.values())] if not params or str(params[0]).strip("%") in {"338","K","x.sql","/x.sql"} else []
            return {"columns":cols,"rows":rows}
        return {"columns":[],"rows":[]}


@pytest.mark.asyncio
async def test_sql_analyzer_uses_injected_provider_and_returns_verbatim():
    tool=SQLAnalyzerTool(config=SQLAnalyzerToolConfig()); tool.set_provider(Provider())
    result=await tool.execute(prompt="script_id 338")
    assert "SELECT 338;\r\n" in result


@pytest.mark.asyncio
async def test_kb_search_exception_is_structured_json():
    tool=KbSearchTool(config=KbSearchConfig()); tool.set_provider(None)
    result=json.loads(await tool.execute(query="x",corpus="tables"))
    assert result["status"] == "not_ready" and result["error_type"]


@pytest.mark.asyncio
async def test_sql_facts_has_no_llm_and_returns_json():
    tool=SqlFactsTool(config=SqlFactsConfig()); tool.set_provider(Provider())
    result=json.loads(await tool.execute(sql="SELECT 1",dialect="spark"))
    assert result["status"] == "ok" and result["literal_values_verified"] is False


def test_all_tools_can_be_created_from_gateway_sections():
    gateway = SimpleNamespace(**{cls.config_key: {"enable": True} for cls in SQL_ASSISTANT_TOOLS})
    ctx = SimpleNamespace(_settings_ref=SimpleNamespace(gateway=gateway))
    provider = Provider()
    for cls in SQL_ASSISTANT_TOOLS:
        assert cls.enabled(ctx)
        tool = cls.create(ctx)
        tool.set_provider(provider)
        assert tool.provider is provider
        assert tool.name == cls.config_key


def test_all_six_tools_register_through_native_project_loader(tmp_path, monkeypatch):
    from lib.services.runtime_patcher import RuntimePatcher
    from nanobot.agent.tools.registry import ToolRegistry

    names = {cls.config_key for cls in SQL_ASSISTANT_TOOLS}
    source = Path(__file__).resolve().parent.parent / "workspace" / "tools"
    tools_dir = tmp_path / "tools"
    tools_dir.mkdir()
    for name in names:
        shutil.copyfile(source / f"{name}.py", tools_dir / f"{name}.py")
    for name in list(sys.modules):
        if name.startswith("workspace.tools."):
            monkeypatch.delitem(sys.modules, name)
    settings = SimpleNamespace(gateway=SimpleNamespace(**{name: {"enable": True} for name in names}))
    agent = SimpleNamespace(tools=ToolRegistry(), tools_config=SimpleNamespace(), workspace=str(tmp_path))
    provider = Provider()
    try:
        ok, message = RuntimePatcher().patch_project_tools(agent, tmp_path, settings=settings, cache_store=provider)
        assert ok, message
        assert "6 project tools registered" in message
        for name in names:
            assert agent.tools.get(name) is not None, message
            assert agent.tools.get(name).provider is provider
    finally:
        for name in names:
            sys.modules.pop(f"workspace.tools.{name}", None)

