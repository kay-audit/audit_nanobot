"""Локальный агент для Markdown-базы LLM-Wiki."""

from typing import TYPE_CHECKING, Any
from pathlib import Path
import sys

# Dependencies installed with pip --target; no activation or global changes.
_dependencies = Path(__file__).resolve().parents[1] / ".packages"
if _dependencies.is_dir():
    sys.path.insert(0, str(_dependencies))

from .config import Settings

if TYPE_CHECKING:
    from .api import (
        ApplyResult,
        DoctorResult,
        JiraOperations,
        ProposalPreview,
        WikiAgent,
    )

__all__ = [
    "ApplyResult",
    "DoctorResult",
    "JiraOperations",
    "ProposalPreview",
    "Settings",
    "WikiAgent",
    "open_agent",
]
__version__ = "0.4.0"


def __getattr__(name: str) -> Any:
    """Лениво открыть публичный API без побочных импортов модулей."""

    if name in {
        "ApplyResult",
        "DoctorResult",
        "JiraOperations",
        "ProposalPreview",
        "WikiAgent",
        "open_agent",
    }:
        from . import api

        return getattr(api, name)
    raise AttributeError(name)
