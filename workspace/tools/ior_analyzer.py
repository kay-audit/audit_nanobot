"""Native nanobot tool for the ``ior-analyzer`` skill.

The legacy ``workspace/skills/ior-analyzer/tool.py`` is kept as a
compatibility shim for direct skill runs.  AgentLoop discovers this module
through ``RuntimePatcher.patch_project_tools`` and registers the tool using
the current nanobot ``Tool.enabled`` / ``Tool.create`` contract.
"""
from __future__ import annotations

import logging
import sys
from importlib import import_module
from pathlib import Path
from typing import Any, ClassVar

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from nanobot.agent.tools.context import current_request_context, current_request_session_key
from pydantic import BaseModel


logger = logging.getLogger(__name__)

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
                "разрешён только для запроса с конкретным EVE-ID. Для ad-hoc "
                "фильтров по DRP/SBR, периоду, оргструктуре или сумме передавай "
                "ior_hypothesis либо не передавай preset."
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
            "DRP/SBR и период являются ad-hoc фильтрами."
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
        self,
        *,
        prompt: str,
        preset: str | None = None,
        session_id: str | None = None,
        **_kwargs: Any,
    ) -> str:
        try:
            runner = self._load_runner()
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
            with artifact_scope(output, paths):
                report = await runner(
                    preset_name=preset,
                    session_id=resolved_session,
                    user_prompt=prompt,
                )
            if paths:
                # The stock message tool already publishes exact media paths to
                # the active channel.  Its content is the full report because
                # Nanobot suppresses the later final reply after message(...).
                if request_context is not None:
                    if self._message_tool is None:
                        raise RuntimeError("MessageTool is unavailable for IOR artifact delivery")
                    delivery = await self._message_tool.execute(
                        content=report, media=[str(path) for path in paths]
                    )
                    if getattr(delivery, "is_error", False):
                        raise RuntimeError(f"IOR artifact delivery failed: {delivery}")
            return report
        except Exception as exc:
            logger.exception("IOR analysis failed")
            return ToolResult.error(
                f"Не удалось выполнить анализ ИОР ({type(exc).__name__}). "
                "Подробности записаны в журнал."
            )
