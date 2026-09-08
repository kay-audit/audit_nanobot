"""scripts.serving - запуск локального LLM-сервера (sglang / ollama) перед стартом Nanobot.

Пакет НЕ является частью runtime Nanobot: используется ТОЛЬКО из bootstrap-цепочки
в ``gateway.py`` и из CLI-скриптов ``start_sglang_server.py`` / ``stop_sglang_server.py``.

Архитектура:
    gateway.py
       └── scripts.serving.serving_bootstrap.ensure_serving()
              ├── scripts.serving.sglang_launcher (если mode=sglang)
              ├── scripts.serving.ollama_launcher (если mode=ollama)
              └── scripts.serving.health_check.wait_for_healthy()

Конфигурация берётся из секции ``serving`` в ``config.json`` либо из переменных
окружения ``SGLANG_*``. Если ни того ни другого нет — bootstrap no-op и Nanobot
стартует штатно (без локального LLM-сервера).

ВАЖНО: этот пакет никак не меняет логику агента. Только поднимает внешний
OpenAI-compatible HTTP API, на который потом смотрит Nanobot (apiBase в providers.vllm).
"""

from __future__ import annotations

__all__: list[str] = []
