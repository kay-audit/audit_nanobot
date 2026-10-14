"""Native OSIRIS Tool using the registered Python runtime and Nanobot session context."""
from __future__ import annotations

import asyncio
import json
import shutil
import uuid
from pathlib import Path
from typing import Any, ClassVar

from loguru import logger
from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context, current_request_session_key
from pydantic import BaseModel, Field

from workspace.utils.osiris_runtime.api import OsirisRuntime
from workspace.utils.osiris_runtime.errors import (
    OsirisBusyError,
    OsirisPayloadError,
    OsirisRequestTimeoutError,
    OsirisResourcesUnavailableError,
    OsirisServiceNotFoundError,
    OsirisStartupTimeoutError,
    OsirisUnavailableError,
    OsirisUnknownOperationError,
)
from workspace.utils.osiris_runtime.protocol import confined, json_bytes
from workspace.utils.osiris_runtime.registry import ServiceRegistry
from workspace.utils.session_key import safe_session_key


class OsirisToolConfig(BaseModel):
    enable: bool = False
    allowed_services: list[str] = Field(default_factory=lambda: ["appeals"])
    allowed_operations: dict[str, list[str]] = Field(default_factory=dict)
    profile_files: list[str] = Field(default_factory=lambda: ["skills/appeals-analyzer/utils/osiris_config.py"])
    allow_stop: bool = False
    admin_session_keys: list[str] = Field(default_factory=list)
    request_timeout_sec: float = Field(default=1800, gt=0, le=86400)
    max_result_chars: int = Field(default=16000, ge=256, le=1000000)


@tool_parameters({
    "type": "object", "additionalProperties": False,
    "properties": {
        "action": {"type": "string", "enum": ["status", "start", "invoke", "stop"]},
        "service": {"type": "string", "description": "Имя зарегистрированного и разрешённого сервиса."},
        "operation": {"type": "string", "description": "Зарегистрированная capability; требуется для invoke."},
        "payload": {"type": "object", "description": "JSON-параметры операции, без Python-объектов и кода."},
        "query": {"type": "string", "description": "Текст поискового запроса, если операция его использует."},
        "artifacts": {"type": "object", "additionalProperties": {"type": "string"},
                      "description": "Имя артефакта → относительный путь файла внутри текущей сессии."},
    },
    "required": ["action", "service"],
})
class OsirisTool(Tool):
    config_key: ClassVar[str] = "osiris"
    _plugin_discoverable: ClassVar[bool] = False

    @classmethod
    def config_cls(cls):
        return OsirisToolConfig

    @classmethod
    def _section(cls, ctx):
        settings = getattr(ctx, "_settings_ref", None)
        tools = settings.get("tools", {}) if isinstance(settings, dict) else getattr(settings, "tools", None)
        section = tools.get("osiris", {}) if isinstance(tools, dict) else getattr(tools, "osiris", {})
        return dict(section or {})

    @classmethod
    def enabled(cls, ctx):
        return bool(cls._section(ctx).get("enable", False))

    @classmethod
    def create(cls, ctx):
        config = cls.config_cls()(**cls._section(ctx))
        workspace = Path(getattr(ctx, "workspace", Path(__file__).resolve().parents[1]))
        registry = ServiceRegistry()
        for file in config.profile_files:
            registry.load(Path(file) if Path(file).is_absolute() else workspace / file)
        for name in config.allowed_services:
            registry.get(name)
        agent = getattr(ctx, "_agent_ref", None)
        message = agent.tools.get("message") if agent is not None else None
        return cls(config=config, runtime=OsirisRuntime(registry), workspace=workspace, message_tool=message)

    def __init__(self, *, config: OsirisToolConfig, runtime=None, workspace=None, message_tool=None):
        self.config = config
        self.runtime = runtime or OsirisRuntime()
        self.workspace = Path(workspace or Path(__file__).resolve().parents[1])
        self.message_tool = message_tool

    @property
    def name(self):
        return "osiris"

    @property
    def description(self):
        return ("Управляет зарегистрированными GPU-сервисами OSIRIS: status, start и invoke. "
                "Вызывает разрешённые операции с JSON-параметрами и файлами текущей сессии. "
                "Stop доступен только административным сессиям по конфигурации. "
                "При недоступности сервиса сообщи пользователю результат; не повторяй вызов бесконечно.")

    def _run(self, action, service, op, payload, query, artifacts, session):
        if action == "status":
            return self.runtime.status(service)
        if action == "start":
            self.runtime.start(service)
            return self.runtime.status(service)
        if action == "stop":
            return {"status": "stopped", "job": self.runtime.stop(service)}
        root = self.workspace / "data_store/cache/sessions" / safe_session_key(session)
        sources = {}
        for name, relative in (artifacts or {}).items():
            if not isinstance(relative, str) or Path(relative).is_absolute():
                raise OsirisPayloadError("Only session-relative artifact paths are accepted")
            sources[name] = confined(root / relative, root, must_exist=True)
        return self.runtime.invoke(service, op, payload, session_id=session, consumer_id="nanobot",
                                   query=query, timeout_sec=self.config.request_timeout_sec,
                                   artifacts=sources, file_root=root)

    def _format_result(self, result, session):
        from workspace.utils.osiris_runtime.json_transport import InvocationResult
        if not isinstance(result, InvocationResult):
            return json.dumps(result, ensure_ascii=False), []
        content = json.dumps({"status": "ok", "request_id": result.request_id, "result": result.value}, ensure_ascii=False)
        files = []
        if result.artifacts or len(content) > self.config.max_result_chars:
            destination = self.workspace / "data_store/cache/sessions" / safe_session_key(session) / "osiris" / uuid.uuid4().hex
            destination.mkdir(parents=True, exist_ok=True)
            if len(content) > self.config.max_result_chars:
                name = "result.json"
                index = 0
                while name in result.artifacts:
                    index += 1
                    name = f"result-{index}.json"
                report = destination / name
                report.write_text(content, encoding="utf-8")
                files.append(report)
            for name, source in result.artifacts.items():
                from workspace.utils.osiris_runtime.protocol import identity
                identity(name, "artifact name")
                target = confined(destination / name, destination)
                shutil.copyfile(source, target)
                files.append(target)
            content = json.dumps({"status": "ok", "request_id": result.request_id,
                                  "artifacts": [file.name for file in files],
                                  "result": result.value if len(content) <= self.config.max_result_chars else None,
                                  "message": "Результат передан в файлах сессии."}, ensure_ascii=False)
            if len(content) > self.config.max_result_chars:
                content = json.dumps({"status": "ok", "request_id": result.request_id,
                                      "artifact_count": len(files), "message": "Результат передан в файлах сессии."}, ensure_ascii=False)
        return content, files

    async def execute(self, *, action: str, service: str, operation: str | None = None,
                      payload: dict | None = None, query: str = "", artifacts: dict | None = None,
                      session_id: str | None = None, **_kwargs: Any):
        result = None
        try:
            if action not in {"status", "start", "invoke", "stop"}:
                raise OsirisPayloadError("Unknown action")
            if service not in self.config.allowed_services:
                raise OsirisServiceNotFoundError("Service is not allowed")
            context = current_request_context()
            current = current_request_session_key()
            if context is not None and not current:
                raise OsirisPayloadError("Request session is missing")
            session = current if context is not None else session_id
            if action == "stop" and (not self.config.allow_stop or context is None
                                     or current not in self.config.admin_session_keys):
                return ToolResult.error("Остановка OSIRIS разрешена только административной сессии.")
            if action == "invoke":
                if not session:
                    raise OsirisPayloadError("A real request session or explicit Python session is required")
                profile = self.runtime.profile(service)
                allowed = self.config.allowed_operations.get(service, list(profile.capabilities))
                if operation not in profile.capabilities or operation not in allowed:
                    raise OsirisUnknownOperationError("Operation is not allowed")
                if not isinstance(payload, dict):
                    raise OsirisPayloadError("Payload must be a JSON object")
                if artifacts is not None and not isinstance(artifacts, dict):
                    raise OsirisPayloadError("Artifacts must be a JSON object")
                json_bytes(payload, profile.max_payload_bytes)
            result = await asyncio.to_thread(self._run, action, service, operation, payload, query, artifacts, session)
            content, files = await asyncio.to_thread(self._format_result, result, session)
            if files and context is not None:
                if self.message_tool is None:
                    raise OsirisPayloadError("Artifact delivery tool is unavailable")
                delivery = await self.message_tool.execute(content=content, media=[str(path) for path in files])
                if getattr(delivery, "is_error", False):
                    raise OsirisPayloadError("Artifact delivery failed")
            from workspace.utils.osiris_runtime.json_transport import InvocationResult
            if isinstance(result, InvocationResult):
                await asyncio.to_thread(self.runtime.cleanup, service,
                                        session_id=session, request_id=result.request_id)
            return content
        except Exception as exc:
            logger.error("OSIRIS Tool failed: {}", type(exc).__name__)
            codes = [(OsirisStartupTimeoutError, "startup_timeout"), (OsirisRequestTimeoutError, "request_timeout"),
                     (OsirisResourcesUnavailableError, "gpu_unavailable"), (OsirisBusyError, "busy"),
                     (OsirisServiceNotFoundError, "unknown_service"), (OsirisUnknownOperationError, "unknown_operation"),
                     (OsirisPayloadError, "invalid_payload"), (OsirisUnavailableError, "startup_failed")]
            code = next((value for error, value in codes if isinstance(exc, error)), "worker_error")
            error = {"error": code, "message": "OSIRIS не выполнил запрос.",
                     "retry_after_sec": self.runtime.profile(service).retry_after_sec if code in {"startup_failed", "startup_timeout", "gpu_unavailable"} else None}
            from workspace.utils.osiris_runtime.json_transport import InvocationResult
            if isinstance(result, InvocationResult):
                error["request_id"] = result.request_id
            return ToolResult.error(json.dumps(error, ensure_ascii=False))
