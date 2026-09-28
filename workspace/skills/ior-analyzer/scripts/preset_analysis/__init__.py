"""Предметные анализаторы пресетов ИОР с ленивым registry import."""


def get_analyzer(preset: str):
    from .registry import get_analyzer as _get_analyzer
    return _get_analyzer(preset)


def has_analyzer(preset: str) -> bool:
    from .registry import has_analyzer as _has_analyzer
    return _has_analyzer(preset)

__all__ = ["get_analyzer", "has_analyzer"]
