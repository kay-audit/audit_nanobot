from __future__ import annotations
import json
from typing import ClassVar
from nanobot.agent.tools.base import Tool, tool_parameters
from pydantic import BaseModel, Field
from lib.services.sql_assistant_runtime import SqlAssistantRuntime, structured_error

class SqlGenerateConfig(BaseModel):
    enable: bool=True
    max_repairs: int=Field(default=2,ge=0,le=2)
    timeout_sec: float=Field(default=180.0,gt=0)

@tool_parameters({"type":"object","properties":{"question":{"type":"string"},"dialect":{"type":"string","enum":["spark","greenplum"]},"table_ids":{"type":"array","items":{}},"example_ids":{"type":"array","items":{}},"column_query":{"type":"string"},"prior_sql":{"type":"string"},"feedback":{"type":"string"},"max_repairs":{"type":"integer","minimum":0,"maximum":2},"live_analyze":{"type":"boolean","default":false}},"required":["question","dialect","table_ids"]})
class SqlGenerateTool(Tool):
    config_key: ClassVar[str]="sql_generate"; _plugin_discoverable: ClassVar[bool]=False
    @classmethod
    def config_cls(cls): return SqlGenerateConfig
    @classmethod
    def _section(cls,ctx):
        try: value=ctx._settings_ref.gateway.sql_generate; return dict(value) if not isinstance(value,dict) else value
        except (AttributeError,TypeError,ValueError): return {}
    @classmethod
    def enabled(cls,ctx): return bool(cls._section(ctx).get("enable",True))
    @classmethod
    def create(cls,ctx): return cls(config=cls.config_cls()(**cls._section(ctx)))
    def __init__(self,*,config): self.config,self.provider=config,None
    def set_provider(self,provider): self.provider=provider
    @property
    def name(self): return "sql_generate"
    @property
    def description(self): return "Generate grounded read-only SQL through the shared LLM client, validate it, repair at most twice, and return AST facts."
    async def execute(self,*,question,dialect,table_ids,example_ids=None,column_query="",prior_sql="",feedback="",max_repairs=None,live_analyze=False,**_kwargs):
        try: result=await SqlAssistantRuntime(self.provider).generate(question=question,dialect=dialect,table_ids=table_ids,example_ids=example_ids or [],column_query=column_query,prior_sql=prior_sql,feedback=feedback,max_repairs=self.config.max_repairs if max_repairs is None else min(2,max(0,int(max_repairs))),live_analyze=bool(live_analyze),llm_timeout_sec=self.config.timeout_sec)
        except Exception as exc: result=structured_error(exc)
        return json.dumps(result,ensure_ascii=False,default=str)
