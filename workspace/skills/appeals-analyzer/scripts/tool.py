"""Nanobot tool для работы с анализатором обращений клиентов и сотрудников."""

from __future__ import annotations

import asyncio
import logging
import sys
import traceback
from pathlib import Path
from typing import Any, Optional

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "appeals-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)

try:
    from nanobot.agent.tools.base import Tool, tool_parameters
except ImportError:
    try:
        from agent.tools.base import Tool, tool_parameters
    except ImportError:
        def tool_parameters(params):
            def decorator(cls):
                cls.args_schema = params
                return cls
            return decorator
        class Tool:
            pass

BaseTool = Tool
logger = logging.getLogger(__name__)

from appeals_reports import run_appeals_report


@tool_parameters({
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": (
                'Новый анализ: JSON с request_type="appeals_analysis" либо legacy '
                '"prd", "s_prd", "chnl", "prompt"; после выгрузки допускается '
                "обычный follow-up."
            ),
        },
        "session_id": {
            "type": "string",
            "description": "Идентификатор сессии пользователя.",
            "default": "webui_session",
        },
    },
    "required": ["prompt"],
})
class AppealsAnalyzerTool(BaseTool):
    name = "appeals_analyzer"
    description = (
        "Инструмент используется при ЛЮБЫХ запросах по обращениям клиентов или сотрудников, "
        "жалобам, сбоям приложений, комиссиям, выпискам и транскрибациям. "
        "Маркер request_type=appeals_analysis однозначно означает вызов этого инструмента."
    )

    async def execute(
        self,
        prompt: str,
        session_id: str = "webui_session",
    ) -> str:
        logger.info(f"[appeals_analyzer] 🛠️ Tool execute called | prompt='{prompt}' | session_id='{session_id}'")
        try:
            res = await run_appeals_report(
                session_id=session_id or "webui_session",
                user_prompt=prompt or "",
            )
            logger.info(f"[appeals_analyzer] 🎯 Tool execution finished | response_len={len(res)} chars")
            return res
        except Exception as exc:
            tb_str = traceback.format_exc()
            logger.error(f"[appeals_analyzer] Error executing Appeals tool: {exc}\nTraceback:\n{tb_str}")
            return f"⚠️ Произошла ошибка при выполнении анализа обращений ({type(exc).__name__}): {exc}\n\nTraceback:\n```\n{tb_str}\n```"
