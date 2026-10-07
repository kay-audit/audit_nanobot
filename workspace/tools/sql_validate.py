from __future__ import annotations
import json
from typing import ClassVar
from nanobot.agent.tools.base import Tool, tool_parameters
from pydantic import BaseModel, Field
from lib.services.sql_assistant_runtime import SqlAssistantRuntime, structured_error

class SqlValidateConfig(BaseModel):
    enable: bool=True
    explain_timeout_sec: float=Field(default=60.0,gt=0)

@tool_parameters({"type":"object","properties":{"sql":{"type":"string"},"dialect":{"type":"string","enum":["spark","greenplum"]},"table_ids":{"type":"array","items":{}},"live_analyze":{"type":"boolean","default":False},"generated":{"type":"boolean","default":True}},"required":["sql","dialect"]})
class SqlValidateTool(Tool):
    config_key: ClassVar[str]="sql_validate"; _plugin_discoverable: ClassVar[bool]=False
    @classmethod
    def config_cls(cls): return SqlValidateConfig
    @classmethod
    def _section(cls,ctx):
        try: value=ctx._settings_ref.gateway.sql_validate; return dict(value) if not isinstance(value,dict) else value
        except (AttributeError,TypeError,ValueError): return {}
    @classmethod
    def enabled(cls,ctx): return bool(cls._section(ctx).get("enable",True))
    @classmethod
    def create(cls,ctx): return cls(config=cls.config_cls()(**cls._section(ctx)))
    def __init__(self,*,config): self.config,self.provider=config,None
    def set_provider(self,provider): self.provider=provider
    @property
    def name(self): return "sql_validate"
    @property
    def description(self): return "Statically validate one generated read-only Spark or Greenplum SQL statement, with optional non-action Spark analysis."
    async def execute(self,*,sql,dialect,table_ids=None,live_analyze=False,generated=True,**_kwargs):
        try: result=await SqlAssistantRuntime(self.provider).validate(sql,dialect=dialect,table_ids=table_ids or [],live_analyze=bool(live_analyze),generated=bool(generated),timeout_sec=self.config.explain_timeout_sec)
        except Exception as exc: result=structured_error(exc)
        return json.dumps(result,ensure_ascii=False,default=str)

