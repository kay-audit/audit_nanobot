"""LLM-Wiki adapter using the skill CLI subprocess contract."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, ClassVar

from nanobot.agent.tools.base import Tool, tool_parameters
from pydantic import BaseModel, Field


class LlmWikiToolConfig(BaseModel):
    enable: bool = True
    timeout_sec: int = Field(default=600, ge=1, le=3600)


def _section(value: Any, name: str) -> Any:
    return value.get(name) if isinstance(value, dict) else getattr(value, name, None)


def _safe_json(payload: dict) -> str:
    result = json.dumps(payload, ensure_ascii=False, default=str)
    key = os.environ.get("MINIMAX_API_KEY")
    if key:
        result = result.replace(key, "[REDACTED]")
    return result


@tool_parameters({
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["query", "status", "prepare", "load"], "default": "query"},
        "question": {"type": "string", "description": "Вопрос к локальной базе Wiki/Jira/Confluence."},
        "key": {"type": "string", "description": "Ключ задачи или проекта для load, например DEMO-1001 или DEMO."},
        "dry_run": {"type": "boolean", "default": False}
    },
    "required": ["action"],
    "additionalProperties": False
})
class LlmWikiTool(Tool):
    config_key: ClassVar[str] = "llm_wiki"

    def __init__(self, *, config: LlmWikiToolConfig) -> None:
        self.config = config

    @classmethod
    def config_cls(cls):
        return LlmWikiToolConfig

    @classmethod
    def _read_settings_section(cls, ctx: Any) -> dict:
        section = _section(_section(getattr(ctx, "_settings_ref", None), "tools"), cls.config_key)
        if section is None:
            return {}
        return {name: _section(section, name) for name in ("enable", "timeout_sec")
                if _section(section, name) is not None}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        skill = _section(_section(getattr(ctx, "_settings_ref", None), "skills"), "llm_wiki")
        return bool(cls._read_settings_section(ctx).get("enable", True)) and (
            _section(skill, "enabled") is not False
        )

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        return cls(config=cls.config_cls()(**cls._read_settings_section(ctx)))

    @property
    def name(self) -> str:
        return "llm_wiki"

    @property
    def description(self) -> str:
        return ("Поиск и ответы по локальной LLM-Wiki и JSON Jira/Confluence. "
                "query отвечает без изменения Wiki; status проверяет готовность. "
                "prepare/load пополняют производную поисковую базу только по явному запросу пользователя; "
                "не загружают данные из Jira API и не применяют Proposal.")

    async def execute(self, *, action: str = "query", question: str = "",
                      key: str = "", dry_run: bool = False, **kwargs: Any) -> str:
        if kwargs or action not in {"query", "status", "prepare", "load"}:
            return _safe_json({"status": "error", "error_type": "invalid_arguments",
                               "message": "Неизвестное действие или аргументы."})
        if sys.version_info[:2] != (3, 12):
            return _safe_json({"status": "error", "error_type": "python_version",
                               "message": "Запустите nanobot на Python 3.12."})
        root = Path(__file__).resolve().parents[1] / "skills" / "llm_wiki"
        cli = root / "scripts" / "cli_nanobot.py"
        if not cli.is_file():
            return _safe_json({"status": "error", "error_type": "cli_not_found",
                               "message": "Отсутствует CLI навыка llm_wiki."})
        env = os.environ.copy()
        env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
        try:
            completed = await asyncio.to_thread(
                subprocess.run, [sys.executable, str(cli)], cwd=str(root), env=env,
                input=json.dumps({"action": action, "question": question, "key": key,
                                  "dry_run": dry_run}, ensure_ascii=False),
                capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=self.config.timeout_sec, shell=False, check=False,
            )
        except subprocess.TimeoutExpired:
            return _safe_json({"status": "error", "error_type": "timeout",
                               "message": f"LLM-Wiki превысил таймаут {self.config.timeout_sec} с."})
        except OSError:
            return _safe_json({"status": "error", "error_type": "subprocess_error",
                               "message": "Не удалось запустить CLI LLM-Wiki."})
        try:
            payload = json.loads(completed.stdout)
        except (ValueError, TypeError):
            payload = None
        if isinstance(payload, dict) and (
            (completed.returncode == 0 and payload.get("status") == "ok") or
            (completed.returncode != 0 and payload.get("status") == "error")
        ):
            return _safe_json(payload)
        return _safe_json({"status": "error", "error_type": "cli_failed",
                           "message": f"CLI не вернул ожидаемый JSON (exit={completed.returncode})."})
