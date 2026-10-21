"""Native dispatcher and session-scoped media delivery for D7 history."""
from __future__ import annotations

import asyncio
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import ClassVar

from nanobot.agent.tools.base import Tool, tool_parameters
from pydantic import BaseModel, ConfigDict, Field


class HistoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enable: bool = True
    timeout_sec: int = Field(default=60, ge=1, le=120)
    workspace_root: str | None = None


def _inside(path: Path, boundary: Path) -> Path:
    resolved = path.resolve(strict=True)
    if not resolved.is_relative_to(boundary.resolve(strict=True)):
        raise PermissionError("Путь вне текущей сессии")
    return resolved


def _identity():
    from nanobot.agent.tools.context import current_request_context
    ctx = current_request_context()
    if ctx is None or not ctx.session_key or not ctx.sender_id:
        raise PermissionError("identity_unavailable: нужен доверенный session_key и sender_id")
    return ctx


@tool_parameters({
    "type": "object",
    "properties": {
        "inputs": {"type": "array", "items": {"type": "string"}, "description": "DOCX/ZIP или каталог вложений текущей сессии"},
        "target": {"type": "string", "description": "Точное имя/key одного закона"},
        "overview": {"type": "string", "description": "Точное имя/key соответствующего обзора"},
        "action": {"type": "string", "enum": ["start", "status", "resume"], "default": "start"},
        "operation_id": {"type": "string"},
    },
    "additionalProperties": False,
})
class LegalSummarizerHistoryTool(Tool):
    config_key: ClassVar[str] = "legal_summarizer_history"

    def __init__(self, *, config: HistoryConfig | None = None, message_tool=None, profile="test"):
        self.config = config or HistoryConfig()
        self._message_tool = message_tool
        self._profile = profile

    @classmethod
    def config_cls(cls):
        return HistoryConfig

    @classmethod
    def _section(cls, ctx):
        settings = getattr(ctx, "_settings_ref", None)
        section = getattr(getattr(settings, "tools", None), cls.config_key, {})
        return section if isinstance(section, dict) else vars(section)

    @classmethod
    def enabled(cls, ctx):
        return bool(cls._section(ctx).get("enable", True))

    @classmethod
    def create(cls, ctx):
        from nanobot.agent.tools.message import MessageTool
        agent = getattr(ctx, "_agent_ref", None)
        message = agent.tools.get("message") if agent else None
        if message is None:
            message = MessageTool(send_callback=ctx.bus.publish_outbound if ctx.bus else None, workspace=ctx.workspace)
        settings = getattr(ctx, "_settings_ref", None)
        return cls(config=HistoryConfig(**cls._section(ctx)), message_tool=message,
                   profile=getattr(settings, "profile", "test"))

    @property
    def name(self):
        return "legal_summarizer_history"

    @property
    def description(self):
        return ("История изменений одного явно выбранного закона по DOCX и обзору ConsultantPlus. "
                "start запускает фон; status проверяет operation_id и доставляет готовые MD/JSON; "
                "resume продолжает прерванную операцию. Используй только вложения текущей сессии. "
                "При needs_input уточни цель/обзор. Не читай полный отчёт в контекст.")

    async def execute(self, *, inputs=None, target=None, overview=None, action="start", operation_id=None, **kwargs):
        try:
            if kwargs or action not in ("start", "status", "resume"):
                raise ValueError("Неизвестные параметры или action")
            ctx = _identity()
            from workspace.utils.session_key import safe_session_key
            session = safe_session_key(ctx.session_key)
            root = Path(self.config.workspace_root or Path(__file__).resolve().parents[2]).resolve()
            sessions = root / "workspace/data_store/cache/sessions"
            sessions.mkdir(parents=True, exist_ok=True)
            boundary = sessions / session
            boundary.mkdir(exist_ok=True)
            _inside(boundary, sessions)
            if boundary.resolve() != boundary.absolute():
                raise PermissionError("Каталог сессии перенаправлен")
            owner = hashlib.sha256(json.dumps([ctx.session_key, str(ctx.sender_id)], ensure_ascii=False).encode()).hexdigest()
            owner_path = boundary / ".history_owner"
            try:
                with owner_path.open("x", encoding="ascii") as handle:
                    handle.write(owner)
            except FileExistsError:
                _inside(owner_path, boundary)
                if owner_path.read_text(encoding="ascii") != owner:
                    raise PermissionError("Владелец сессии не совпадает") from None
            cli = root / "workspace/skills/legal_summarizer/scripts/cli_history.py"
            argv = [sys.executable, str(cli), "--workspace-root", str(root), "--session-key", session,
                    "--profile", self._profile]
            if action == "start":
                if not isinstance(inputs, list) or not inputs or len(inputs) > 100:
                    raise ValueError("Нужен непустой список inputs (до 100)")
                for value in inputs:
                    path = Path(value)
                    path = _inside(path if path.is_absolute() else root / "workspace" / path, boundary)
                    if path.is_dir():
                        for child in path.iterdir():
                            _inside(child, boundary)
                    argv.extend(["--input", str(path)])
                if target:
                    argv.extend(["--target", target])
                if overview:
                    argv.extend(["--overview", overview])
                argv.append("--background")
            else:
                import re
                if not isinstance(operation_id, str) or not re.fullmatch(r"history_[0-9a-f]{32}", operation_id):
                    raise ValueError("Неверный operation_id")
                argv.extend(["--status" if action == "status" else "--resume", operation_id])
            result = await asyncio.to_thread(self._invoke, argv, root)
            if result.get("status") in ("completed", "partial"):
                media = result.get("media")
                if not isinstance(media, list) or len(media) != 2:
                    raise ValueError("Неверный media envelope")
                allowed = _inside(boundary / "results", boundary)
                paths = [_inside(root / "workspace" / value, allowed) for value in media]
                if any(not p.is_file() or p.suffix not in (".md", ".json") for p in paths):
                    raise ValueError("Неверные файлы результата")
                if self._message_tool is None:
                    result["delivery"] = {"status": "error", "error_type": "delivery_unavailable"}
                else:
                    try:
                        sent = await self._message_tool.execute(content="История изменений готова. Отчёт и исходные факты приложены.",
                                                                media=[str(p) for p in paths])
                        failed = getattr(sent, "is_error", False) or str(sent).startswith("Error:")
                        result["delivery"] = {"status": "error" if failed else "sent"}
                    except Exception:
                        result["delivery"] = {"status": "error", "error_type": "delivery_failed"}
            return json.dumps(result, ensure_ascii=False)
        except PermissionError as exc:
            return json.dumps({"status": "error", "error_type": "session_access_denied", "message": str(exc)}, ensure_ascii=False)
        except subprocess.TimeoutExpired:
            return json.dumps({"status": "error", "error_type": "dispatch_timeout"})
        except (OSError, ValueError, TypeError):
            return json.dumps({"status": "error", "error_type": "invalid_request_or_response"})

    def _invoke(self, argv, root):
        process = subprocess.run(argv, cwd=root, shell=False, capture_output=True, text=True, encoding="utf-8",
                                 timeout=self.config.timeout_sec, env={**os.environ, "PYTHONUTF8": "1"})
        result = json.loads(process.stdout)
        if not isinstance(result, dict) or (process.returncode and result.get("status") != "error"):
            raise ValueError("Неверный ответ CLI")
        return result
