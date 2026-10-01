"""Deterministic verbatim delivery of existing SQL Assistant scripts."""
from __future__ import annotations

import logging
from typing import Any, ClassVar
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from pydantic import BaseModel
from lib.services.sql_assistant_runtime import NOT_FOUND, SqlAssistantRuntime

logger = logging.getLogger(__name__)


class SQLAnalyzerToolConfig(BaseModel):
    enable: bool = True


@tool_parameters({"type": "object", "properties": {"prompt": {"type": "string", "description": "Полный запрос с реальным script_id, km_id, filename или path."}}, "required": ["prompt"]})
class SQLAnalyzerTool(Tool):
    config_key: ClassVar[str] = "sql_analyzer"
    _plugin_discoverable: ClassVar[bool] = False
    @classmethod
    def config_cls(cls): return SQLAnalyzerToolConfig
    @classmethod
    def _section(cls, ctx: Any) -> dict[str, Any]:
        try:
            value = ctx._settings_ref.gateway.sql_analyzer
            return dict(value) if not isinstance(value, dict) else value
        except (AttributeError, TypeError, ValueError): return {}
    @classmethod
    def enabled(cls, ctx: Any) -> bool: return bool(cls._section(ctx).get("enable", True))
    @classmethod
    def create(cls, ctx: Any) -> Tool: return cls(config=cls.config_cls()(**cls._section(ctx)))
    def __init__(self, *, config: SQLAnalyzerToolConfig) -> None: self.config, self.provider = config, None
    def set_provider(self, provider: Any) -> None: self.provider = provider
    @property
    def name(self) -> str: return "sql_analyzer"
    @property
    def description(self) -> str: return "Exact lookup and verbatim delivery of an existing corporate SQL script. Never generates, rewrites, validates away, or executes SQL."
    async def execute(self, *, prompt: str, **_kwargs: Any) -> str | ToolResult:
        try:
            result = SqlAssistantRuntime(self.provider).ready(prompt)
            if result["status"] == "found": return result["content"]
            if result["status"] == "not_found": return NOT_FOUND
            return ToolResult.error(result["message"])
        except Exception as exc:
            logger.exception("[sql_analyzer] ready lookup failed")
            return ToolResult.error(f"SQL Assistant KB недоступна ({type(exc).__name__}): {exc}")

