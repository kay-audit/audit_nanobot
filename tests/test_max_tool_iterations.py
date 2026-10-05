"""Защита от agentic-loop: лимит итераций агента должен быть ≤ 15.

Default upstream — 200 (``nanobot/config/schema.py:129``), что позволяет LLM
крутить tool-вызовы 10+ минут (наблюдалось в ``nanobot_bugfix_stutter``:
после ответа ``ior_analyzer`` агент делал 16+ итераций ``exec``/``read_file``
за 10 минут, пока не вылетел по ``ContextWindowExceededError``).

ВАЖНО, ГДЕ ЖИВЁТ ЭФФЕКТИВНОЕ ЗНАЧЕНИЕ
--------------------------------------
``AgentLoop`` получает лимит из ``ApplicationContext.create()``:

    ``lib/core/application_context.py:280``
        ``max_iterations=ctx.config_service.get_int("cli", "max_iterations", 200)``

то есть из ``project.json`` → ``cli.max_iterations``. Это значение передаётся
в ``AgentLoop`` явно и **перекрывает** ``agents.defaults.maxToolIterations``
из ``config.json`` и ``.secrets.env`` — правки там не влияют на лимит
(проверено: при 10 в обоих конфигах ``my`` tool рапортует 200).
Поэтому тесты ниже смотрят на ``project.json``.
"""
from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
PROJECT_JSON = REPO_ROOT / "project.json"
CONFIG_JSON = REPO_ROOT / "config.json"

MAX_ALLOWED = 15
MIN_ALLOWED = 5


def _project_cli_max_iterations() -> int:
    """``project.json`` — JSONC; читаем штатным загрузчиком ``config.py``."""
    import sys

    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from config import load_config_json

    cfg = load_config_json(PROJECT_JSON)
    return int(cfg["cli"]["max_iterations"])


def test_effective_max_iterations_bounded():
    """Эффективный лимит (project.json → cli.max_iterations) ограничен."""
    n = _project_cli_max_iterations()
    assert n <= MAX_ALLOWED, (
        f"cli.max_iterations={n} — agentic loop не прерывается вовремя; "
        f"наблюдалось 16+ итераций за 10 минут. Должно быть <= {MAX_ALLOWED}."
    )
    assert n != 200, "cli.max_iterations=200 — возврат к upstream-дефолту"


def test_max_iterations_not_too_low():
    """Нижняя граница: слишком мало ломает сложные сценарии."""
    n = _project_cli_max_iterations()
    assert n >= MIN_ALLOWED, (
        f"cli.max_iterations={n} — слишком мало для реальных задач"
    )


def test_agents_defaults_does_not_diverge():
    """``agents.defaults.maxToolIterations`` перекрывается, но не должен
    расходиться с эффективным значением — иначе при следующем рефакторинге
    (``ApplicationContext`` перестанет передавать ``cli.max_iterations``)
    лимит незаметно вернётся к 200.
    """
    eff = _project_cli_max_iterations()
    cfg = json.loads(CONFIG_JSON.read_text(encoding="utf-8"))
    declared = cfg["agents"]["defaults"]["maxToolIterations"]
    assert declared == eff, (
        f"config.json agents.defaults.maxToolIterations={declared} расходится "
        f"с project.json cli.max_iterations={eff}. Эффективен второй — "
        f"держите их синхронными."
    )