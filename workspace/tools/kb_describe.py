from __future__ import annotations
import json
from typing import ClassVar
from nanobot.agent.tools.base import Tool, tool_parameters
from pydantic import BaseModel, Field
from lib.services.sql_assistant_runtime import SqlAssistantRuntime, structured_error

class KbDescribeConfig(BaseModel):
    enable: bool=True
    max_columns: int=Field(default=50,ge=1,le=500)
    live_timeout_sec: float=Field(default=30.0,gt=0)

@tool_parameters({"type":"object","properties":{"table_ids":{"type":"array","items":{}},"group_keys":{"type":"array","items":{"type":"string"}},"example_ids":{"type":"array","items":{}},"detail":{"type":"string","enum":["summary","full"],"default":"summary"},"column_query":{"type":"string"},"max_columns":{"type":"integer"},"live_schema":{"type":"boolean","default":false}}})
class KbDescribeTool(Tool):
    config_key: ClassVar[str]="kb_describe"; _plugin_discoverable: ClassVar[bool]=False
    @classmethod
    def config_cls(cls): return KbDescribeConfig
    @classmethod
    def _section(cls,ctx):
        try: value=ctx._settings_ref.gateway.kb_describe; return dict(value) if not isinstance(value,dict) else value
        except (AttributeError,TypeError,ValueError): return {}
    @classmethod
    def enabled(cls,ctx): return bool(cls._section(ctx).get("enable",True))
    @classmethod
    def create(cls,ctx): return cls(config=cls.config_cls()(**cls._section(ctx)))
    def __init__(self,*,config): self.config,self.provider=config,None
    def set_provider(self,provider): self.provider=provider
    @property
    def name(self): return "kb_describe"
    @property
    def description(self): return "Return compact grounded table cards, selected columns, or full SQL examples by verified KB IDs."
    async def execute(self,*,table_ids=None,group_keys=None,example_ids=None,detail="summary",column_query="",max_columns=None,live_schema=False,**_kwargs):
        try: result=await SqlAssistantRuntime(self.provider).describe(table_ids=table_ids or [],group_keys=group_keys or [],example_ids=example_ids or [],detail=detail,column_query=column_query,max_columns=min(int(max_columns or self.config.max_columns),self.config.max_columns),live_schema=bool(live_schema),live_timeout_sec=self.config.live_timeout_sec)
        except Exception as exc: result=structured_error(exc)
        return json.dumps(result,ensure_ascii=False,default=str)
