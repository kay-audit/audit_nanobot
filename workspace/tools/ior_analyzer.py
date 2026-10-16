"""Native nanobot tool for the ``ior-analyzer`` skill.

The legacy ``workspace/skills/ior-analyzer/tool.py`` is kept as a
compatibility shim for direct skill runs.  AgentLoop discovers this module
through ``RuntimePatcher.patch_project_tools`` and registers the tool using
the current nanobot ``Tool.enabled`` / ``Tool.create`` contract.
"""
from __future__ import annotations

import logging
import asyncio
from collections import OrderedDict
import hashlib
import json
import math
import os
import re
import sys
import time
import uuid
from importlib import import_module
from pathlib import Path
from typing import Any, ClassVar

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context, current_request_session_key
from pydantic import BaseModel


logger = logging.getLogger(__name__)
_CLARIFICATION_TTL_SEC = 3600


def _pending_input_kind(text: str, question: str) -> str:
    """Use the expected answer before deciding whether a request is new."""
    low, expected = text.casefold().strip(), question.casefold()
    if re.match(r"уточнение:", low):
        return "answer"
    if re.search(r"\bнов\w*\s+(?:запрос|анализ|выгрузк)|\b(?:начни|начать)\s+заново", low):
        return "new"
    action = bool(re.search(r"\b(?:выгруз\w*|найди|покажи|проанализируй|сделай|сформируй)\b", low))
    domain = bool(re.search(r"\b(?:иор\w*|инцидент\w*|(?:не)?финансов\w*\s+последств\w*|возмещ\w*)\b", low))
    constraints = bool(re.search(r"\b\d{4}\b|\bq[1-4]\b|\b(?:drp|eve)[-_\s]?\d+", low))
    if re.search(r"год", expected) and re.fullmatch(r"(?:год\s*[:—-]?\s*)?\d{4}(?:\s*год\w*)?[.!\s]*", low):
        return "answer"
    if re.search(r"происхожд|ответствен|подраздел|блок", expected) and re.search(
        r"происхожд|ответствен", low
    ) and not action and not constraints and "?" not in low:
        return "answer"
    if "eve" in expected and re.fullmatch(r"(?:иор\s+)?eve[-_\s]?\d+[.!\s]*", low):
        return "answer"
    if re.search(r"вид\w*\s+анализ|какой анализ", expected) and re.fullmatch(
        r"(?:возмещения|удал[её]нные иор|(?:не)?финансовые последствия)(?:\s+по иор)?[.!\s]*", low
    ):
        return "answer"
    if domain and (action or constraints):
        return "new"
    if constraints:
        return "answer" if re.search(r"период|год", expected) and not domain and not action else "ambiguous"
    if not domain and not action and "?" not in low and len(text) <= 1000:
        return "answer"
    return "ambiguous"

_PRESETS = (
    "financial_consequences_ior",
    "deleted_ior",
    "vozmeshenie_ior",
    "ior_nonfinancial_consequences",
    "ior_period_pao_sberbank",
    "credit_no_way_collect_debt",
    "report_period_specific_ior",
    "ior_hypothesis",
)


class IORAnalyzerToolConfig(BaseModel):
    """Configuration in ``gateway.ior_analyzer`` of ``project.json``."""

    enable: bool = True


@tool_parameters({
    "type": "object",
    "properties": {
        "prompt": {
            "type": "string",
            "description": "Полный исходный запрос пользователя по ИОР без сокращения или пересказа.",
        },
        "preset": {
            "type": "string",
            "enum": list(_PRESETS),
            "description": (
                "Необязательный предметный пресет. report_period_specific_ior "
                "разрешён только для досье одного EVE-ID. Предмет анализа имеет "
                "приоритет: финансовые/нефинансовые последствия и возмещения "
                "сохраняют свой preset даже с EVE, DRP, периодом или ПАО. "
                "SBR-фильтрация не поддерживается. Если tool вернул уточняющий "
                "вопрос, задай его пользователю, дождись ответа и повтори вызов, "
                "передав ответ пользователя; исходный запрос сохраняет инструмент. "
                "Конкретные виды потерь запускаются во вкладке специального анализа."
            ),
        },
    },
    "required": ["prompt"],
})
class IORAnalyzerTool(Tool):
    """Выполнить анализ ИОР и вернуть готовый аналитический отчёт."""

    config_key: ClassVar[str] = "ior_analyzer"
    _plugin_discoverable: ClassVar[bool] = False

    @classmethod
    def config_cls(cls):
        return IORAnalyzerToolConfig

    @classmethod
    def _settings_section(cls, ctx: Any) -> dict[str, Any]:
        settings = getattr(ctx, "_settings_ref", None)
        if settings is None:
            return {}
        try:
            section = settings.gateway.ior_analyzer
        except AttributeError:
            return {}
        if isinstance(section, dict):
            return dict(section)
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
            logger.exception("Invalid gateway.ior_analyzer configuration; using defaults")
            config = cls.config_cls()()
        agent = getattr(ctx, "_agent_ref", None)
        message_tool = agent.tools.get("message") if agent is not None else None
        return cls(config=config, message_tool=message_tool)

    def __init__(self, *, config: IORAnalyzerToolConfig, message_tool: Any = None) -> None:
        self.config = config
        self._message_tool = message_tool
        self._followup_requests = OrderedDict()

    @property
    def name(self) -> str:
        return "ior_analyzer"

    @property
    def description(self) -> str:
        return (
            "Анализирует инциденты операционного риска (ИОР): финансовые и "
            "нефинансовые последствия, потери, возмещения, удалённые ИОР, "
            "кредитную задолженность и аналитические гипотезы. Возвращает "
            "готовый отчёт; выгрузки и графики передаются отдельными вложениями. Досье "
            "report_period_specific_ior выбирается только для конкретного EVE-ID; "
            "DRP и период являются фильтрами; SBR-коды не поддерживаются. "
            "Если результат содержит уточняющий вопрос, задай его пользователю "
            "и повтори вызов с ответом пользователя; инструмент сам сохранит исходный запрос "
            "и добавит «Уточнение: <ответ>». "
            "Место происхождения и зона ответственности — разные фильтры. "
            "Для конкретных видов потерь направь пользователя во вкладку специального анализа ИОР."
            " Ошибка follow-up является окончательным результатом текущего оборота: "
            "покажи её пользователю и дождись нового сообщения; не меняй prompt для повторного вызова."
        )

    @staticmethod
    def _prepare_skill_utils_namespace(skill_dir: Path) -> None:
        """Make the skill's ``utils`` submodules visible in the shared process.

        Gateway itself imports ``workspace/utils`` as the top-level package
        ``utils`` before project tools are loaded.  The IOR skill historically
        uses imports such as ``from utils.data_store import get_data_store``;
        merely changing ``sys.path`` cannot override an already cached package.
        Extending its package path preserves gateway modules.  The skill path
        is appended (not prepended), so the shared gateway ``utils.db`` keeps
        priority while IOR-only modules such as ``utils.data_store`` remain
        discoverable.
        """
        skill_utils_dir = skill_dir / "utils"
        if not skill_utils_dir.is_dir():
            raise RuntimeError(f"IOR skill utils directory not found: {skill_utils_dir}")

        utils_module = import_module("utils")
        package_path = getattr(utils_module, "__path__", None)
        if package_path is None:
            raise RuntimeError("Loaded module 'utils' is not a package")
        utils_path = str(skill_utils_dir)
        if utils_path in package_path:
            package_path.remove(utils_path)
        package_path.append(utils_path)

    @classmethod
    def _load_runner(cls):
        """Load the skill lazily, so a missing optional dependency does not stop startup."""
        workspace_dir = Path(__file__).resolve().parents[1]
        skill_dir = workspace_dir / "skills" / "ior-analyzer"
        scripts_dir = skill_dir / "scripts"
        if not scripts_dir.is_dir():
            raise RuntimeError(f"IOR skill scripts directory not found: {scripts_dir}")

        # Import/extend the gateway package before skill paths are placed at
        # the front of sys.path.  This also makes isolated CLI/tool tests use
        # workspace/utils/db.py instead of the skill's historical duplicate.
        workspace_path = str(workspace_dir)
        if workspace_path in sys.path:
            sys.path.remove(workspace_path)
        sys.path.insert(0, workspace_path)
        cls._prepare_skill_utils_namespace(skill_dir)

        for path in (skill_dir, scripts_dir):
            path_str = str(path)
            if path_str in sys.path:
                sys.path.remove(path_str)
            sys.path.insert(0, path_str)
        from ior_reports import run_ior_report

        return run_ior_report

    async def execute(
        self, *, prompt: str, preset: str | None = None,
        session_id: str | None = None, **_kwargs: Any,
    ) -> str:
        context = current_request_context()
        original = getattr(context, "original_user_text", None)
        # Standalone calls and all non-follow-up modes retain their existing behavior.
        if context is None or not isinstance(original, str) or not original.strip():
            return await self._execute_once(prompt=prompt, preset=preset, session_id=session_id)
        try:
            runner = self._load_runner()
            from utils.resolve.request_plan import explicit_followup
        except Exception:
            return await self._execute_once(prompt=prompt, preset=preset, session_id=session_id)
        if not explicit_followup(original):
            return await self._execute_once(prompt=prompt, preset=preset, session_id=session_id, _runner=runner)
        session = current_request_session_key()
        turn = getattr(context, "message_id", None) or getattr(context, "turn_id", None)
        if not session or not turn:
            return ToolResult.error("Для поиска по выгрузке необходимы идентификаторы сессии и сообщения. Повторите запрос.")
        key = (session, getattr(context, "sender_id", None), turn)
        digest = hashlib.sha256(original.encode()).hexdigest()
        state = self._followup_requests.setdefault(key, {"lock": asyncio.Lock(), "hash": digest})
        for old in list(self._followup_requests)[:-64]:
            if old != key and not self._followup_requests[old]["lock"].locked():
                self._followup_requests.pop(old)
        async with state["lock"]:
            if state["hash"] != digest:
                return ToolResult.error("Идентификатор сообщения ИОР повторно использован с другим исходным текстом.")
            if "error" in state:
                return state["error"]
            result = await self._execute_once(prompt=prompt, preset=preset, session_id=session_id, _runner=runner)
            if getattr(result, "is_error", False):
                state["error"] = result
            return result

    async def _execute_once(
        self,
        *,
        prompt: str,
        preset: str | None = None,
        session_id: str | None = None,
        _runner=None,
        **_kwargs: Any,
    ) -> str:
        try:
            runner = _runner if _runner is not None else self._load_runner()
            self._prepare_skill_utils_namespace(Path(__file__).resolve().parents[1] / "skills" / "ior-analyzer")
            from workspace.utils.session_key import safe_session_key
            from utils.ior_artifacts import artifact_scope

            request_context = current_request_context()
            request_session = current_request_session_key()
            if request_context is not None and not request_session:
                raise RuntimeError("Current request has no session key")
            resolved_session = request_session or session_id or "webui_session"
            paths: list[Path] = []
            workspace = Path(__file__).resolve().parents[1]
            output = workspace / "data_store" / "cache" / "sessions" / safe_session_key(resolved_session) / "results"
            runtime_options = {}
            pending_path = None
            clarification = False
            if request_context is not None:
                original = getattr(request_context, "original_user_text", None)
                if not isinstance(original, str) or not original.strip():
                    return ToolResult.error("В текущем запросе отсутствует исходный текст пользователя. Повторите запрос по ИОР.")
                # Scope pending answers by the real session AND sender, never an LLM session_id.
                owner = str(getattr(request_context, "sender_id", None) or "")
                key = hashlib.sha256((resolved_session + "\0" + owner).encode()).hexdigest()[:24]
                pending_path = output.parent / f"ior_clarification_{key}.json"
                turn = getattr(request_context, "turn_id", None) or getattr(request_context, "message_id", None)
                new_request = _pending_input_kind(original, "") == "new" or original.startswith("Анализ ИОР")
                try:
                    pending = json.loads(pending_path.read_text(encoding="utf-8"))
                    if not isinstance(pending, dict) or not isinstance(pending.get("prompt"), str) or pending.get("session") != resolved_session or pending.get("owner") != owner:
                        raise ValueError("Invalid pending clarification")
                except FileNotFoundError:
                    pending = None
                except (ValueError, OSError):
                    if not new_request:
                        return ToolResult.error("Не удалось прочитать состояние уточнения ИОР. Повторите полный исходный запрос.")
                    pending = None
                from utils.resolve.request_plan import explicit_followup
                if pending:
                    created = pending.get("created_at")
                    if not isinstance(created, (int, float)) or isinstance(created, bool) or not math.isfinite(created) or not 0 <= time.time() - created <= _CLARIFICATION_TTL_SEC:
                        pending_path.unlink(missing_ok=True)
                        pending = None
                        if not new_request:
                            return ToolResult.error("Срок ожидания уточнения ИОР истёк. Повторите полный исходный запрос.")
                continuing = False
                if pending and not explicit_followup(original):
                    kind = _pending_input_kind(original, pending.get("question", ""))
                    if turn and pending.get("turn") == turn:
                        prompt = pending["prompt"]
                        continuing = True
                    elif kind == "answer":
                        prefix = "\n" if re.match(r"\s*уточнение:", original, re.I) else "\nУточнение: "
                        prompt = pending["prompt"] + prefix + original
                        continuing = True
                    elif kind == "new":
                        prompt = original
                    else:
                        return "Это ответ на предыдущее уточнение или новый запрос по ИОР? Уточните намерение; исходные условия сохранены. " + pending.get("question", "")
                else:
                    prompt = original
                if re.fullmatch(r"\s*(?:год\s*[:—-]?\s*)?\d{4}(?:\s*год\w*)?[.!\s]*", prompt):
                    return ToolResult.error("Нет ожидающего уточнения ИОР. Сформулируйте полный запрос по ИОР.")
                # An agent-supplied preset is not a trusted source of user intent.
                preset = None

                def save_clarification(question):
                    nonlocal clarification
                    clarification = True
                    pending_path.parent.mkdir(parents=True, exist_ok=True)
                    temporary = pending_path.with_name(pending_path.name + "." + uuid.uuid4().hex + ".tmp")
                    try:
                        temporary.write_text(json.dumps({"session": resolved_session, "owner": owner,
                            "turn": turn, "prompt": prompt, "question": str(question),
                            "created_at": pending["created_at"] if continuing else time.time()}, ensure_ascii=False), encoding="utf-8")
                        os.replace(temporary, pending_path)
                    finally:
                        temporary.unlink(missing_ok=True)

                runtime_options["on_clarification"] = save_clarification
            with artifact_scope(output, paths):
                report = await runner(
                    trusted_preset=False,
                    preset_name=preset,
                    session_id=resolved_session,
                    user_prompt=prompt,
                    **runtime_options,
                )
            if getattr(report, "is_error", False):
                return report
            if paths:
                if request_context is not None:
                    if self._message_tool is None:
                        raise RuntimeError("MessageTool is unavailable for IOR artifact delivery")
                    from lib.utils.verbatim_report import verbatim_report_context
                    with verbatim_report_context(request_context, 'ior_analyzer', report):
                        delivery = await self._message_tool.execute(
                            content=report, media=[str(path) for path in paths]
                        )
                    if getattr(delivery, "is_error", False):
                        raise RuntimeError(f"IOR artifact delivery failed: {delivery}")
            if pending_path is not None and not clarification:
                pending_path.unlink(missing_ok=True)
            return report
        except Exception as exc:
            logger.error("IOR analysis failed: %s", type(exc).__name__)
            return ToolResult.error(
                f"Не удалось выполнить анализ ИОР ({type(exc).__name__}). "
                "Подробности записаны в журнал."
            )
