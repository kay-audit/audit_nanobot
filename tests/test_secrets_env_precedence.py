"""``.secrets.env`` перекрывает ``config.json`` — и об этом нужно помнить.

**Два разных контура конфигурации** (это и было источником путаницы):

1. **Конфиг ядра nanobot** (``agents.*``, ``tools.*``, ``providers.*``,
   ``modelPresets``, ``api``) — читается **только** из ``config.json``
   загрузчиком нанобота (``nanobot.config.loader``). ``.secrets.env`` на него
   **не влияет вообще**. Отсюда были мёртвые правки: ``maxToolIterations``
   и ``failOnToolError``, проставленные в ``config.json``, «не применялись»,
   пока источник искали в ``.secrets.env``.

2. **Конфиг проекта** (``SETTINGS``: ``channels.*``, ``logging.*``,
   ``skills.*``, ``gateway.*``, ``benchmark.*``, ``cli.*``) — собирается как
   ``project.json`` → ``config.json`` → ``.secrets.env``, последний
   выигрывает. Здесь ``.secrets.env`` перекрывает всё.

Отдельно: ``maxToolIterations`` для ``AgentLoop`` вообще берётся не из
``agents.defaults``, а из ``project.json::cli.max_iterations`` —
``ApplicationContext.create()`` передаёт его явно
(``lib/core/application_context.py:280``) и это значение перекрывает
``config.json``.

Тесты ниже фиксируют именно эти правила, чтобы следующая правка не
попала в мёртвый файл.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_JSON = REPO_ROOT / "config.json"
PROJECT_JSON = REPO_ROOT / "project.json"
SECRETS_ENV = REPO_ROOT / ".secrets.env"

# Ключи контура проекта: ``.secrets.env`` перекрывает ``project.json``,
# поэтому молчаливое расхождение меняет рантайм.
SETTINGS_KEYS = (
    "logging.db.enabled",
)

# Ключи контура ядра: authoritative — ``config.json``. Если такой же ключ
# есть в ``.secrets.env``, он НЕ применяется, но читатель будет уверен,
# что применяется. Это и есть ловушка.
CORE_KEYS = (
    "agents.defaults.maxToolIterations",
    "agents.defaults.failOnToolError",
    "agents.defaults.contextWindowTokens",
)

# Реальный (эффективный) источник лимита итераций для AgentLoop.
EFFECTIVE_ITERATION_KEY = "cli.max_iterations"


def _flatten(tree, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for key, val in tree.items():
        name = f"{prefix}__{key}" if prefix else str(key)
        if isinstance(val, dict):
            out.update(_flatten(val, name))
        else:
            out[name] = "" if val is None else str(val)
    return out


def _secrets_tree() -> dict[str, str]:
    if not SECRETS_ENV.exists():
        pytest.skip(f"{SECRETS_ENV.name} нет (свежий клон) — нечего сверять")
    if str(REPO_ROOT) not in sys.path:
        sys.path.insert(0, str(REPO_ROOT))
    from config import load_env

    return _flatten(load_env(SECRETS_ENV))


def _json_value(path: Path, dotted: str) -> str | None:
    if path.suffix == ".jsonc" or path.name == "project.json":
        if str(REPO_ROOT) not in sys.path:
            sys.path.insert(0, str(REPO_ROOT))
        from config import load_config_json

        cur: object = load_config_json(path)
    else:
        cur = json.loads(path.read_text(encoding="utf-8"))
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return None if cur is None else str(cur)


def _norm(v: str) -> str:
    try:
        return str(int(v))
    except (TypeError, ValueError):
        return v.strip().lower()


@pytest.mark.parametrize("dotted", SETTINGS_KEYS)
def test_settings_keys_match_between_project_and_secrets(dotted: str):
    """Для контура проекта ``.secrets.env`` перекрывает ``project.json`` —
    расхождение должно быть явным (обычно его и вносят вместе)."""
    proj = _json_value(PROJECT_JSON, dotted)
    if proj is None:
        pytest.skip(f"{dotted} нет в project.json")
    sec = _secrets_tree()
    key = dotted.replace(".", "__")
    if key not in sec:
        return
    assert _norm(sec[key]) == _norm(proj), (
        f"{dotted}: project.json={proj!r} и .secrets.env={sec[key]!r} расходятся. "
        f"Побеждает .secrets.env — держите оба синхронными."
    )


@pytest.mark.parametrize("dotted", CORE_KEYS)
def test_core_keys_are_not_overridden_in_secrets(dotted: str):
    """Ключи ядра (agents.*) читаются только из ``config.json``.

    Дублирование в ``.secrets.env`` не применится, но вводит в заблуждение
    (именно так «не сработала» правка ``failOnToolError``). Если значения
    разошлись — это ловушка для следующего читателя.
    """
    sec = _secrets_tree()
    key = dotted.replace(".", "__")
    if key not in sec:
        return  # не дублируется — ловушки нет

    cfg_val = _json_value(CONFIG_JSON, dotted)
    if cfg_val is None:
        pytest.skip(f"{dotted} нет в config.json")
    assert _norm(sec[key]) == _norm(cfg_val), (
        f"{dotted}: config.json={cfg_val!r} (эффективно), но в .secrets.env "
        f"лежит {sec[key]!r}, который НЕ применяется к конфигу ядра. "
        f"Уберите дубль из .secrets.env или синхронизируйте — иначе правка "
        f"в .secrets.env будет выглядеть как сработавшая, но не сработает."
    )


def test_effective_iteration_limit_is_bounded():
    """``cli.max_iterations`` — эффективный лимит итераций AgentLoop."""
    n = int(_json_value(PROJECT_JSON, EFFECTIVE_ITERATION_KEY) or 200)
    assert n <= 15, (
        f"project.json cli.max_iterations={n} — agentic loop не "
        f"прерывается вовремя (наблюдалось 16+ итераций за 10 минут)"
    )
    assert n >= 5, f"cli.max_iterations={n} — слишком мало для реальных задач"


def test_core_and_project_iteration_limits_agree():
    """``config.json`` не должен обещать лимит, который перекроет project.json."""
    core = int(_json_value(CONFIG_JSON, "agents.defaults.maxToolIterations") or 0)
    eff = int(_json_value(PROJECT_JSON, EFFECTIVE_ITERATION_KEY) or 0)
    assert core == eff, (
        f"config.json agents.defaults.maxToolIterations={core} != "
        f"project.json cli.max_iterations={eff}. Эффективен второй "
        f"(ApplicationContext передаёт его явно) — держите синхронными."
    )