"""Native nanobot tool for the ``appeals-analyzer`` skill.

The skill-level ``tool.py`` remains a compatibility shim.  Gateway discovers
this module through ``RuntimePatcher.patch_project_tools`` and loads the
heavy retrieval stack only when the tool is invoked.
"""
from __future__ import annotations

import logging
import sys
import importlib.util
from importlib import import_module
from pathlib import Path
from typing import Any, ClassVar

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from pydantic import BaseModel
from lib.services.skill_runtime_mode import (
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
                            'Новый анализ: ровно четыре quoted CSV-поля: '
                            '"продукты", "субпродукты", "каналы", "смысловой запрос". '
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
        return cls(config=config)

    def __init__(self, *, config: AppealsAnalyzerToolConfig) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return "appeals_analyzer"

    @property
    def description(self) -> str:
        return ("Анализирует обращения клиентов и сотрудников: точный product/subproduct/channel prefilter, "
                "BGE semantic retrieval, метрики СВА, Excel-выгрузка и follow-up по выборке.")

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

    @staticmethod
    def _require_shared_db(workspace_dir: Path):
        """Resolve only the gateway-wide workspace/utils/db.py module."""
        expected_utils = (workspace_dir / "utils").resolve()
        shared_utils = import_module("utils")
        loaded_paths = {
            Path(path).resolve()
            for path in getattr(shared_utils, "__path__", ())
        }
        if expected_utils not in loaded_paths:
            raise RuntimeError(
                "Appeals requires the gateway shared workspace/utils package; "
                "another top-level utils package is loaded."
            )
        shared_db = import_module("utils.db")
        if Path(getattr(shared_db, "__file__", "")).resolve() != expected_utils / "db.py":
            raise RuntimeError("Appeals loaded a non-workspace utils.db module.")
        return shared_db

    @classmethod
    def _load_runner(cls):
        workspace_dir = Path(__file__).resolve().parents[1]
        cls._require_shared_db(workspace_dir)
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
            spec = importlib.util.spec_from_file_location(
                package_name,
                package_init,
                submodule_search_locations=[str(skill_dir)],
            )
            if spec is None or spec.loader is None:
                raise RuntimeError("Cannot create isolated appeals runtime package.")
            module = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                sys.modules.pop(package_name, None)
                raise
        else:
            loaded_origin = getattr(sys.modules[package_name], "__file__", None)
            if loaded_origin is None or Path(loaded_origin).resolve() != package_init.resolve():
                raise RuntimeError("Appeals runtime namespace is already occupied by another package.")
        reports = import_module(f"{package_name}.scripts.appeals_reports")
        return reports.run_appeals_report

    async def execute(self, *, prompt: str, session_id: str = "webui_session", **_kwargs: Any) -> str:
        try:
            runtime = log_skill_runtime("appeals-analyzer", logger)
            resolved_session = current_tool_session_id(
                None if session_id == "webui_session" else session_id
            )
            if runtime == "testing":
                runner = load_testing_module("appeals-analyzer", "runner")
                return await runner.run_testing_report(
                    session_id=resolved_session,
                    user_prompt=prompt,
                )
            runner = self._load_runner()
            return await runner(session_id=resolved_session, user_prompt=prompt)
        except Exception as exc:
            logger.exception("Appeals analysis failed")
            return ToolResult.error(
                "Не удалось выполнить анализ обращений "
                f"(ошибка {type(exc).__name__}). Подробности записаны в журнал сервера."
            )
