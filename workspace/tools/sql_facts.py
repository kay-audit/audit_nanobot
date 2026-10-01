from __future__ import annotations
import json
from typing import ClassVar
from nanobot.agent.tools.base import Tool, tool_parameters
from pydantic import BaseModel
from lib.services.sql_assistant_runtime import SqlAssistantRuntime, structured_error

class SqlFactsConfig(BaseModel): enable: bool=True

@tool_parameters({"type":"object","properties":{"sql":{"type":"string"},"dialect":{"type":"string","enum":["spark","greenplum"]},"table_ids":{"type":"array","items":{}},"example_ids":{"type":"array","items":{}}},"required":["sql","dialect"]})
class SqlFactsTool(Tool):
    config_key: ClassVar[str]="sql_facts"; _plugin_discoverable: ClassVar[bool]=False
    @classmethod
    def config_cls(cls): return SqlFactsConfig
    @classmethod
    def _section(cls,ctx):
        try: value=ctx._settings_ref.gateway.sql_facts; return dict(value) if not isinstance(value,dict) else value
        except (AttributeError,TypeError,ValueError): return {}
    @classmethod
    def enabled(cls,ctx): return bool(cls._section(ctx).get("enable",True))
    @classmethod
    def create(cls,ctx): return cls(config=cls.config_cls()(**cls._section(ctx)))
    def __init__(self,*,config): self.config,self.provider=config,None
    def set_provider(self,provider): self.provider=provider
    @property
    def name(self): return "sql_facts"
    @property
    def description(self): return "Extract deterministic SQL AST facts and enrich them with KB schema and example notes; never calls an LLM."
    async def execute(self,*,sql,dialect,table_ids=None,example_ids=None,**_kwargs):
        try: result=SqlAssistantRuntime(self.provider).facts(sql,dialect=dialect,table_ids=table_ids or [],example_ids=example_ids or [])
        except Exception as exc: result=structured_error(exc)
        return json.dumps(result,ensure_ascii=False,default=str)

