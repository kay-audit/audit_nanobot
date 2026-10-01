"""Exact request-local artifact paths for the native Tool and standalone CLI."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

_output: ContextVar[tuple[Path, list[Path]] | None] = ContextVar("appeals_output", default=None)


def session_results(session_id: str) -> Path:
    from workspace.utils.session_key import safe_session_key

    if not session_id:
        raise ValueError("Appeals requires a session key")
    workspace = Path(__file__).resolve().parents[3]
    return workspace / "data_store" / "cache" / "sessions" / safe_session_key(session_id) / "results"


@contextmanager
def artifact_scope(directory: Path, paths: list[Path]):
    token = _output.set((directory.resolve(), paths))
    try:
        yield
    finally:
        _output.reset(token)


def output_directory(session_id: str) -> Path:
    current = _output.get()
    return current[0] if current else session_results(session_id)


def register_artifact(path: Path) -> None:
    current = _output.get()
    if current is None:
        return
    root, paths = current
    resolved = path.resolve()
    if resolved.is_file() and resolved.is_relative_to(root) and resolved not in paths:
        paths.append(resolved)
