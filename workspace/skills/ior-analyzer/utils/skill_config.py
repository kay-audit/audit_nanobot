"""Skill-local access to the shared nanobot cache configuration."""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

_SKILL_ROOT = Path(__file__).resolve().parent.parent
_PROJECT_ROOT = _SKILL_ROOT.parents[2]

if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

from lib.core import skill_config as _lib  # noqa: E402


_SKILL_NAME = "ior_analyzer"

__all__ = ["get_cache_path", "build_cache_provider"]


def get_cache_path() -> str:
    """Return the gateway-published DuckDB snapshot path."""
    return _lib.get_in_memory_cache_path(_SKILL_ROOT)


def build_cache_provider() -> Any:
    """Build the standard provider for the shared gateway snapshot."""
    return _lib.build_cache_provider(_SKILL_NAME, _SKILL_ROOT)
