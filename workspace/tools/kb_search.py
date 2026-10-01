from __future__ import annotations
import json
from typing import Any, ClassVar
from nanobot.agent.tools.base import Tool, tool_parameters
from pydantic import BaseModel, Field
from lib.services.sql_assistant_runtime import SqlAssistantRuntime, structured_error
from lib.services.hybrid_search import make_bge_embedder, make_bge_reranker

class KbSearchConfig(BaseModel):
    enable: bool = True
    index_root: str = "data_store/sql_assistant_indexes"
    score_floor: float = Field(default=0.4, ge=0.0, le=1.0)
    max_top_k: int = Field(default=20, ge=1, le=100)
    dense_enabled: bool = True
    reranker_enabled: bool = True
    dense_model: str = "BAAI/bge-m3"
    reranker_model: str = "BAAI/bge-reranker-v2-m3"
    device: str = "auto"
    model_cache_dir: str | None = None

@tool_parameters({"type":"object","properties":{"query":{"type":"string"},"corpus":{"type":"string","enum":["tables","examples","columns"]},"top_k":{"type":"integer","default":5},"filters":{"type":"object"},"mode":{"type":"string","enum":["search","rank_ids"],"default":"search"},"ids":{"type":"array","items":{}}},"required":["query","corpus"]})
class KbSearchTool(Tool):
    config_key: ClassVar[str] = "kb_search"; _plugin_discoverable: ClassVar[bool] = False
    @classmethod
    def config_cls(cls): return KbSearchConfig
    @classmethod
    def _section(cls, ctx):
        try: value=ctx._settings_ref.gateway.kb_search; return dict(value) if not isinstance(value,dict) else value
        except (AttributeError,TypeError,ValueError): return {}
    @classmethod
    def enabled(cls,ctx): return bool(cls._section(ctx).get("enable",True))
    @classmethod
    def create(cls,ctx): return cls(config=cls.config_cls()(**cls._section(ctx)))
    def __init__(self,*,config): self.config,self.provider=config,None
    def set_provider(self,provider): self.provider=provider
    @property
    def name(self): return "kb_search"
    @property
    def description(self): return "Search SQL knowledge-base tables, columns, or examples; rank_ids only reranks supplied real IDs."
    async def execute(self,*,query,corpus,top_k=5,filters=None,mode="search",ids=None,**_kwargs):
        try:
            embedder=make_bge_embedder(self.config.dense_model,device=self.config.device,cache_dir=self.config.model_cache_dir) if corpus != "columns" and self.config.dense_enabled else None
            reranker=make_bge_reranker(self.config.reranker_model,device=self.config.device,cache_dir=self.config.model_cache_dir) if corpus != "columns" and self.config.reranker_enabled else None
            model_key=f"{self.config.dense_model}|{self.config.reranker_model}|{self.config.device}|{self.config.dense_enabled}|{self.config.reranker_enabled}|{self.config.model_cache_dir or ''}"
            result=SqlAssistantRuntime(self.provider,index_root=self.config.index_root,score_floor=self.config.score_floor,embedder=embedder,reranker=reranker,model_key=model_key).search(query,corpus=corpus,top_k=min(max(1,int(top_k)),self.config.max_top_k),filters=filters or {},mode=mode,ids=ids)
        except Exception as exc: result=structured_error(exc)
        return json.dumps(result,ensure_ascii=False,default=str)
