"""Request-local output directory and exact artifact paths for native IOR runs."""
from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Iterator


_output: ContextVar[tuple[Path, list[Path]] | None] = ContextVar("ior_output", default=None)


@contextmanager
def artifact_scope(directory: Path, paths: list[Path]) -> Iterator[None]:
    token = _output.set((directory.resolve(), paths))
    try:
        yield
    finally:
        _output.reset(token)


def output_directory(default: Path) -> Path:
    current = _output.get()
    return current[0] if current else default


def register_artifact(path: Path) -> None:
    current = _output.get()
    if current is None:
        return
    root, paths = current
    resolved = path.resolve()
    if resolved.is_file() and resolved.is_relative_to(root) and resolved not in paths:
        paths.append(resolved)
