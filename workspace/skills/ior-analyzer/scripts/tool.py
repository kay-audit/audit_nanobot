"""Nanobot tool для работы с анализатором инцидентов операционного риска (ИОР)."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Optional

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
_WORKSPACE = Path(__file__).resolve().parents[3]

for _path in (_SCRIPT_DIR, _WORKSPACE, _PROJECT_ROOT):
    if str(_path) not in sys.path:
        sys.path.insert(0, str(_path))

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

import sys
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)

from ior_reports import run_ior_report


@tool_parameters({
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Текст запроса пользователя по инцидентам операционного риска (ИОР).",
        },
        "preset": {
            "type": "string",
            "enum": [
                "financial_consequences_ior",
                "deleted_ior",
                "vozmeshenie_ior",
                "ior_nonfinancial_consequences",
                "ior_period_pao_sberbank",
                "credit_no_way_collect_debt",
                "report_period_specific_ior",
                "ior_hypothesis",
            ],
            "description": (
                "Необязательный предметный пресет. report_period_specific_ior "
                "используется только для досье одного EVE-ID. Предмет анализа имеет "
                "приоритет над EVE, DRP, периодом и другими фильтрами. SBR не поддерживается. "
                "Уточняющий вопрос — нормальный результат: дождитесь ответа и повторите "
                "исходный prompt, добавив 'Уточнение: <ответ>'. Конкретные виды потерь "
                "запускаются во вкладке специального анализа ИОР Единого рабочего места."
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
class IORAnalyzerTool(BaseTool):
    name = "ior_analyzer"
    description = (
        "Инструмент используется при ЛЮБЫХ запросах по инцидентам операционного риска (ИОР), "
        "финансовым последствиям, потерям, возмещениям, удалённым ИОР, кредитной задолженности "
        "и построению аналитических гипотез. Предмет анализа имеет приоритет над фильтрами. "
        "Досье формируется только для одного EVE; SBR не поддерживается. Уточняющий вопрос "
        "является нормальным результатом: повторите исходный prompt с 'Уточнение: <ответ>'. "
        "Конкретные виды потерь направляются во вкладку специального анализа ИОР Единого рабочего места."
    )

    async def execute(
        self,
        prompt: str,
        preset: str | None = None,
        session_id: str = "webui_session",
    ) -> str:
        logger.info(f"[ior_analyzer] 🛠️ Tool execute called | prompt='{prompt}' | preset='{preset}' | session_id='{session_id}'")
        try:
            res = await run_ior_report(
                preset_name=preset,
                session_id=session_id or "webui_session",
                user_prompt=prompt or "",
            )
            logger.info(f"[ior_analyzer] 🎯 Tool execution finished | response_len={len(res)} chars")
            return res
        except Exception as exc:
            logger.exception("IOR analysis failed")
            return (f"Не удалось выполнить анализ ИОР ({type(exc).__name__}). "
                    "Подробности записаны в журнал.")
