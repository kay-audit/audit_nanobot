import json
from types import SimpleNamespace

import pytest

from workspace.tools.kb_search import KbSearchConfig, KbSearchTool
from workspace.tools.sql_analyzer import SQLAnalyzerTool, SQLAnalyzerToolConfig
from workspace.tools.sql_facts import SqlFactsConfig, SqlFactsTool


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
    ctx=SimpleNamespace(_settings_ref=SimpleNamespace(gateway=SimpleNamespace(kb_search={"enable":True})))
    assert KbSearchTool.enabled(ctx) and KbSearchTool.create(ctx).name == "kb_search"

