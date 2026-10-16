"""Native nanobot tool for the ``appeals-analyzer`` skill.

The skill-level ``tool.py`` remains a compatibility shim.  Gateway discovers
this module through ``RuntimePatcher.patch_project_tools`` and loads the
Appeals orchestration lazily. Retrieval models and indexes live in Osiris.
"""
from __future__ import annotations

import logging
import asyncio
import hashlib
import json
from collections import OrderedDict
import sys
from importlib import import_module
from pathlib import Path
from typing import Any, ClassVar

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context, current_request_session_key
from pydantic import BaseModel
from workspace.utils.skill_runtime_mode import (
    current_tool_session_id,
    load_testing_module,
    log_skill_runtime,
)

logger = logging.getLogger(__name__)


class AppealsAnalyzerToolConfig(BaseModel):
    """Configuration in ``gateway.appeals_analyzer`` of ``project.json``."""

    enable: bool = True


@tool_parameters({
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": ('Полный исходный запрос пользователя без сокращения. '
                            'Новый анализ: canonical «Анализ обращений», appeals_analysis JSON '
                            'legacy quoted CSV или обычный текст: «найди обращения», '
                            '«проанализируй жалобы клиентов», «покажи обращения по теме». '
                            'Свободный запрос не требует фильтров или специальной вкладки. '
                            'В сессии допускается обычный follow-up вопрос.'),
        },
        "session_id": {
            "type": "string",
            "default": "webui_session",
            "description": "Идентификатор сессии для follow-up по сформированной выгрузке.",
        },
    },
    "required": ["prompt"],
})
class AppealsAnalyzerTool(Tool):
    """Выполнить semantic-анализ обращений или follow-up по текущей выгрузке."""

    config_key: ClassVar[str] = "appeals_analyzer"
    _plugin_discoverable: ClassVar[bool] = False

    @classmethod
    def config_cls(cls):
        return AppealsAnalyzerToolConfig

    @classmethod
    def _settings_section(cls, ctx: Any) -> dict[str, Any]:
        settings = getattr(ctx, "_settings_ref", None)
        if settings is None:
            return {}
        try:
            section = settings.gateway.appeals_analyzer
        except AttributeError:
            return {}
        try:
            return dict(section)
        except (TypeError, ValueError):
            return {"enable": bool(getattr(section, "enable", True))}

    @classmethod
    def enabled(cls, ctx: Any) -> bool:
        return bool(cls._settings_section(ctx).get("enable", True))

    @classmethod
    def create(cls, ctx: Any) -> Tool:
        try:
            config = cls.config_cls()(**cls._settings_section(ctx))
        except Exception:
            logger.exception("Invalid gateway.appeals_analyzer configuration; using defaults")
            config = cls.config_cls()()
        agent = getattr(ctx, "_agent_ref", None)
        message_tool = agent.tools.get("message") if agent is not None else None
        return cls(config=config, message_tool=message_tool)

    def __init__(self, *, config: AppealsAnalyzerToolConfig, message_tool: Any = None) -> None:
        self.config = config
        self._message_tool = message_tool
        self._requests = OrderedDict()

    @property
    def name(self) -> str:
        return "appeals_analyzer"

    @property
    def description(self) -> str:
        return ("Анализирует обращения клиентов и сотрудников: точный product/subproduct/channel prefilter, "
                "гибридный поиск в Osiris, четыре гипотезы, XLSX-вложение и follow-up по выборке. "
                "Принимает обычные просьбы найти обращения или проанализировать жалобы; "
                "без frontend-фильтров ищет по всему индексу. Не предназначен для общих вопросов вне обращений. "
                "После готового отчёта передай его пользователю; не вызывай анализ повторно и не сужай выборку "
                "без нового сообщения пользователя. Повтор в текущем запросе возвращает уже готовый результат.")

    @staticmethod
    def _resolve_skill_dir(workspace_dir: Path) -> Path:
        candidates = [
            workspace_dir / "skills" / "appeals-analyzer",
            workspace_dir / "skills" / "appeals_analyzer",
        ]
        existing = [candidate for candidate in candidates if candidate.is_dir()]
        if len(existing) != 1:
            names = ", ".join(str(candidate) for candidate in candidates)
            if existing:
                raise RuntimeError(f"Ambiguous appeals skill directories: {names}")
            raise RuntimeError(f"Appeals skill directory not found; checked: {names}")
        return existing[0]

    @classmethod
    def _load_runner(cls):
        workspace_dir = Path(__file__).resolve().parents[1]
        skill_dir = cls._resolve_skill_dir(workspace_dir)
        scripts_dir = skill_dir / "scripts"
        package_init = skill_dir / "__init__.py"
        scripts_init = scripts_dir / "__init__.py"
        if not package_init.is_file() or not scripts_init.is_file():
            raise RuntimeError("Appeals isolated package entrypoints are missing.")
        package_name = "appeals_analyzer_runtime"
        if package_name not in sys.modules:
            for module_name in list(sys.modules):
                if module_name.startswith(package_name + "."):
                    sys.modules.pop(module_name, None)
            module = import_module(f"workspace.skills.{skill_dir.name}")
            sys.modules[package_name] = module
        else:
            loaded_origin = getattr(sys.modules[package_name], "__file__", None)
            if loaded_origin is None or Path(loaded_origin).resolve() != package_init.resolve():
                raise RuntimeError("Appeals runtime namespace is already occupied by another package.")
        reports = import_module(f"{package_name}.scripts.appeals_reports")
        return reports.run_appeals_report

    async def execute(self, *, prompt: str, session_id: str = "webui_session", **_kwargs: Any) -> str:
        _kwargs.pop("_request_state", None)
        context = current_request_context()
        session = current_request_session_key()
        message = getattr(context, "message_id", None)
        turn = getattr(context, "turn_id", None)
        original = getattr(context, "original_user_text", None)
        if context is None or not session or not (message or turn):
            return await self._execute_once(prompt=prompt, session_id=session_id, **_kwargs)
        key = (session, getattr(context, "sender_id", None), message or turn)
        digest = hashlib.sha256((original if isinstance(original, str) else prompt).encode()).hexdigest()
        state = self._requests.setdefault(key, {"lock": asyncio.Lock(), "hash": digest})
        # Bound per-tool memory without evicting executions currently in flight.
        for old in list(self._requests)[:-64]:
            if old != key and not self._requests[old]["lock"].locked():
                self._requests.pop(old)
        async with state["lock"]:
            diagnostic = {"session_key": session, "original_message_id": message, "turn_id": turn,
                "original_prompt_hash": digest, "tool_call_id": getattr(context, "attributes", {}).get("tool_call_id"),
                "execution_kind": state.get("execution_kind"), "duplicate_call": "report" in state,
                "report_delivered": state.get("delivered", False), "llm_provider": None, "llm_http_status": None}
            logger.info("appeals_request %s", json.dumps(diagnostic, ensure_ascii=False))
            if state["hash"] != digest:
                return ToolResult.error("Идентификатор запроса обращений повторно использован с другим исходным текстом.")
            if state.get("complete"):
                return state["report"]
            result = await self._execute_once(prompt=prompt, session_id=session_id, _request_state=state, **_kwargs)
            diagnostic.update(execution_kind=state.get("execution_kind"), report_delivered=state.get("delivered", False),
                              delivery_confirmation="message_tool" if state.get("delivered") else None)
            logger.info("appeals_request %s", json.dumps(diagnostic, ensure_ascii=False))
            return result

    async def _execute_once(self, *, prompt: str, session_id: str = "webui_session", _request_state=None, **_kwargs: Any) -> str:
        try:
            runtime = log_skill_runtime("appeals-analyzer", logger)
            if runtime == "testing":
                resolved_session = current_tool_session_id(
                    None if session_id == "webui_session" else session_id
                )
                runner = load_testing_module("appeals-analyzer", "runner")
                return await runner.run_testing_report(
                    session_id=resolved_session,
                    user_prompt=prompt,
                )
            runner = self._load_runner()
            context = current_request_context()
            original = getattr(context, "original_user_text", None)
            if isinstance(original, str) and original.strip():
                prompt = original
            request_session = current_request_session_key()
            if context is not None and not request_session:
                raise RuntimeError("Current request has no session key")
            resolved_session = request_session or session_id or "webui_session"
            if context is not None and self._message_tool is None:
                raise RuntimeError("MessageTool is unavailable for Appeals artifact delivery")
            artifacts = import_module("appeals_analyzer_runtime.utils.appeals_artifacts")
            backend = import_module("appeals_analyzer_runtime.utils.data_store")
            paths: list[Path] = []
            if _request_state is not None and "report" in _request_state:
                report = _request_state["report"]
                paths.extend(_request_state["paths"])
            else:
                if _request_state is not None:
                    router = sys.modules.get("appeals_analyzer_runtime.utils.intent_router")
                    sessions = sys.modules.get("appeals_analyzer_runtime.utils.session_extract_manager")
                    if router is not None and sessions is not None:
                        previous = sessions.get_session_extract(resolved_session)
                        intent = router.route_intent(prompt, bool(previous and previous.get("final_ids")))
                        _request_state["execution_kind"] = "new_search" if intent == router.Intent.NEW_SEARCH else "followup"
                with backend.backend_scope("cache"), artifacts.artifact_scope(
                    artifacts.session_results(resolved_session), paths,
                ):
                    report = await runner(session_id=resolved_session, user_prompt=prompt)
                if _request_state is not None and isinstance(report, str) and "сервис обработки недоступен" not in report:
                    _request_state.update(report=report, paths=list(paths))
            if paths and context is not None:
                from lib.utils.verbatim_report import verbatim_report_context
                with verbatim_report_context(context, 'appeals_analyzer', report):
                    delivery = await self._message_tool.execute(
                        content=report, media=[str(path) for path in paths],
                    )
                if getattr(delivery, "is_error", False):
                    raise RuntimeError("Appeals artifact delivery failed")
                if _request_state is not None:
                    _request_state["delivered"] = True
            if _request_state is not None and "report" in _request_state:
                _request_state["complete"] = True
            return report
        except Exception as exc:
            logger.error("Appeals analysis failed: %s", type(exc).__name__)
            from workspace.utils.appeals_structural_cache import AppealsStructuralCacheError
            if isinstance(exc, AppealsStructuralCacheError):
                return ToolResult.error(str(exc))
            from workspace.utils.osiris_runtime import OsirisUnavailableError
            if isinstance(exc, OsirisUnavailableError):
                loaded_config = sys.modules.get("appeals_analyzer_runtime.utils.osiris_config")
                retry_seconds = getattr(getattr(loaded_config, "SERVICE", None), "retry_after_sec", 900)
                return ToolResult.error(
                    "В данный момент сервис обработки недоступен. "
                    f"Попробуйте повторить запрос через {max(1, round(retry_seconds / 60))} минут."
                )
            osiris_config = sys.modules.get("appeals_analyzer_runtime.utils.osiris_config")
            if osiris_config is not None and isinstance(exc, osiris_config.OsirisWorkerNotReady):
                return ToolResult.error(str(exc))
            return ToolResult.error(
                "Не удалось выполнить анализ обращений "
                f"(ошибка {type(exc).__name__}). Подробности записаны в журнал сервера."
            )
