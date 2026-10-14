"""Единый резолв LLM-конфигурации из глобальных SETTINGS.

Подход (вынесен из навыка audit_analyzer, scripts/skill_config.py):
дефолт берётся из ``agents.defaults`` (модель/провайдер) и
``providers.<provider>`` (api_base/api_key) конфигурации агента,
а переопределения (например, ``skills.audit_analyzer.llm_*`` из
project.json) передаются через ``overrides``.

SETTINGS уже прошли резолв ``${VAR}`` в ``config.py``, поэтому
``providers.<provider>.apiKey`` содержит реальный ключ — зависеть
от ``os.environ`` и побочных импортов не нужно.

Используется:
  * навыком audit_analyzer — ``get_llm_config()``;
  * бенчмарками (benchmarks/runner.py) — чтобы брать ТУ ЖЕ LLM,
    что и агент (модель/провайдер/ключ из одних настроек);
  * всеми навыками, которым нужна модель агента целиком (ior-analyzer,
    appeals-analyzer) — через ``agent_endpoint()``.

Главное свойство: провайдер, адрес, ключ, модель И поле управления
рассуждениями берутся из одних и тех же настроек. Чтобы перевести весь стек
с MiniMax на локальный vLLM, достаточно поменять ``agents.defaults.provider``
и ``providers.<name>.apiBase`` — код навыков трогать не нужно: поле
рассуждения выводится из имени провайдера (см. ``llm_reasoning``).

Модуль импортируется БЕЗ nanobot: config.py подключается лениво.
"""

from __future__ import annotations

import os
from typing import Any

from lib.services.llm_reasoning import extra_body_for


def _settings() -> Any:
    """SETTINGS агента, инициализированные при первом обращении.

    В гейтвее их инициализирует ``gateway.py`` до старта каналов, и этот путь
    ничего не делает. Навык, запущенный как standalone-скрипт (тесты, CLI
    навыка), приходит с неинициализированным SETTINGS — тогда инициализируем
    сами тем же профилем, что и у гейтвея. Профиль берётся из
    ``NANOBOT_PROFILE``, иначе ``prod`` (единственный, кто его ставит).
    """
    import config as _config

    try:
        _config.SETTINGS.get("agents", {})
        return _config.SETTINGS
    except Exception:
        profile = (os.getenv("NANOBOT_PROFILE") or "prod").strip() or "prod"
        _config._initialize_settings(profile=profile)
        return _config.SETTINGS


def resolve_llm_config(overrides: dict | None = None) -> dict[str, Any]:
    """Собрать LLM-конфиг (provider/model/api_base/api_key/параметры).

    Args:
        overrides: Специфичные переопределения (например, секция
            ``skills.audit_analyzer`` с ключами ``llm_*``). Перекрывают
            дефолт агента.

    Returns:
        Словарь: provider, model, api_base, api_key, max_tokens, temperature,
        extra_body.

    Raises:
        RuntimeError: если не удаётся определить model или api_base
            (никакой подстановки дефолтных значений нет).
    """
    SETTINGS = _settings()

    _cfg = overrides or {}
    defaults = SETTINGS.get("agents", {}).get("defaults", {}) or {}

    provider = _cfg.get("llm_provider") or defaults.get("provider") or "openai-compatible"
    provider_cfg = SETTINGS.get("providers", {}).get(provider) or {}

    model = _cfg.get("llm_model") or defaults.get("model")
    api_base = _cfg.get("llm_api_base") or provider_cfg.get("apiBase") or ""
    api_key = _cfg.get("llm_api_key") or provider_cfg.get("apiKey") or ""

    if not model:
        raise RuntimeError(
            "resolve_llm_config: не задана модель (agents.defaults.model "
            "или llm_model в overrides)"
        )
    if not api_base:
        raise RuntimeError(
            f"resolve_llm_config: не задан api_base для провайдера {provider!r} "
            "(providers.{provider}.apiBase или llm_api_base в overrides)"
        )

    # Поле рассуждения выводится из ИМЕНИ провайдера, а не из extraBody в
    # конфиге: так переключение провайдера в config.json автоматически
    # меняет и форму управления рассуждениями (minimax -> reasoning_split,
    # vllm -> chat_template_kwargs.enable_thinking). Явный extra_body в
    # overrides — единственный способ переопределить это руками.
    extra_body = _cfg.get("llm_extra_body")
    if extra_body is None:
        extra_body = extra_body_for(provider)

    return {
        "provider": provider,
        "model": model,
        "api_base": str(api_base).rstrip("/"),
        "api_key": api_key,
        "max_tokens": int(_cfg.get("llm_max_tokens", 8192)),
        "temperature": float(_cfg.get("llm_temperature", 0.1)),
        "extra_body": dict(extra_body),
    }


def agent_endpoint() -> dict[str, Any]:
    """Эндпоинт модели агента без переопределений.

    Точка входа для навыков, которые должны ходить ровно туда же, куда ходит
    агент: ior-analyzer, appeals-analyzer. Ничего не знает ни о vLLM, ни о
    MiniMax — только читает ``agents.defaults`` + ``providers.*``.
    """
    return resolve_llm_config(None)


def ensure_llm_env() -> None:
    """Гарантировать ``LLM_API_KEY`` в окружении для резолва ``${...}``.

    Лоадеры конфигурации (nanobot ``resolve_config_env_vars``, config.py)
    подставляют ``${LLM_API_KEY}`` из переменных окружения; без явной
    установки они падают ``ValueError``. Ключ берётся из того же резолва,
    что использует агент и навык, — без побочного импорта config.py.
    """
    import os

    if "LLM_API_KEY" in os.environ:
        return
    key = resolve_llm_config().get("api_key")
    if key:
        os.environ["LLM_API_KEY"] = key
