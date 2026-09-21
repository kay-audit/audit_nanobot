"""Explicit runtime switch for production and external testing skills."""
from __future__ import annotations

import importlib
import importlib.util
import logging
import os
import sys
from pathlib import Path
from types import ModuleType


_ENV_NAME = "NANOBOT_SKILLS_RUNTIME"
_VALID_RUNTIMES = frozenset({"production", "testing"})


def get_skill_runtime() -> str:
    value = os.environ.get(_ENV_NAME, "production").strip().lower() or "production"
    if value not in _VALID_RUNTIMES:
        raise ValueError(
            f"{_ENV_NAME} must be 'production' or 'testing', got {value!r}"
        )
    return value


def is_testing_runtime() -> bool:
    return get_skill_runtime() == "testing"


def log_skill_runtime(skill_name: str, logger: logging.Logger) -> str:
    runtime = get_skill_runtime()
    logger.info("[%s] runtime=%s", skill_name, runtime)
    return runtime


def current_tool_session_id(explicit: str | None = None) -> str:
    """Prefer the request ContextVar; keep webui_session only for local calls."""
    try:
        from nanobot.agent.tools.context import current_request_session_key

        current = current_request_session_key()
        if current:
            return str(current)
    except (ImportError, LookupError, RuntimeError):
        pass
    return explicit or "webui_session"


def load_testing_module(skill_directory: str, module_name: str) -> ModuleType:
    """Load a testing package next to a hyphenated skill in an isolated namespace."""
    root = Path(__file__).resolve().parents[2]
    testing_dir = root / "workspace" / "skills" / skill_directory / "testing"
    package_init = testing_dir / "__init__.py"
    package_name = "_external_testing_" + skill_directory.replace("-", "_")
    if package_name not in sys.modules:
        spec = importlib.util.spec_from_file_location(
            package_name,
            package_init,
            submodule_search_locations=[str(testing_dir)],
        )
        if spec is None or spec.loader is None:
            raise RuntimeError(f"Cannot load testing runtime: {testing_dir}")
        package = importlib.util.module_from_spec(spec)
        sys.modules[package_name] = package
        spec.loader.exec_module(package)
    return importlib.import_module(f"{package_name}.{module_name}")


def build_sql_assistant_runtime(provider=None, **kwargs):
    runtime = get_skill_runtime()
    logging.getLogger(__name__).info("[sql_assistant] runtime=%s", runtime)
    if runtime == "testing":
        from workspace.skills.sql_assistant.testing.runtime import SqlTestingRuntime

        return SqlTestingRuntime(provider)
    from lib.services.sql_assistant_runtime import SqlAssistantRuntime

    return SqlAssistantRuntime(provider, **kwargs)
