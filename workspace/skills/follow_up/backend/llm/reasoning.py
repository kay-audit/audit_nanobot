"""Управление рассуждениями и очистка черновика для Follow Up.

Почему модуль локальный, а не ``lib/services/llm_reasoning.py``:
``tests/test_follow_up_skill.py::test_skill_code_does_not_import_the_project``
запрещает коду навыка импортировать проект. Навык живёт отдельным процессом
и обязан держать границу, поэтому здесь — своя, маленькая копия правил.

Логика обязана совпадать с ``lib/services/llm_reasoning.py``:

* **minimax** — ``reasoning_split``. Проверено вживую: без него
  ``reasoning_content`` пустой, а весь ``<think>``-черновик модели приходит
  **внутри ``content``** и пользователь видит черновик вместо ответа.
* **vllm** — ``chat_template_kwargs.enable_thinking`` (ключ уходит в генератор
  шаблона чата; у встроенной спеки vLLM в nanobot ``thinking_style`` пустой).
* **deepseek / volcengine** — ``thinking.type``.
* Неизвестный эндпоинт — ``reasoning_split``: безвреден там, где поле незнакомо.

Переключатель — переменная окружения ``VLLM_ENABLE_THINKING``, общая со всеми
остальными навыками проекта.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict, Optional

ENV_ENABLE_THINKING = "VLLM_ENABLE_THINKING"
DEFAULT_ENABLE_THINKING = True

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t", "да", "истина"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "n", "f", "нет", "ложь"})

_CHAT_TEMPLATE_PROVIDERS = frozenset({"vllm", "sglang"})
_THINKING_TYPE_PROVIDERS = frozenset({"deepseek", "volcengine", "byteplus"})

# Блоки рассуждения: <think>…</think>, </think> без открывающего (сервер мог
# обрезать начало по max_tokens) и ```xml-think …``` — вариант части Qwen-шаблонов.
_THINK_BLOCKS = re.compile(
    r"<think>.*?</think>"
    r"|\A\s*</think>.*?(?=\n\s*\n|\Z)"
    r"|```xml-think\s*\n.*?```"
    r"|```think\s*\n.*?```",
    re.DOTALL | re.IGNORECASE,
)


def _parse_bool(raw: str, fallback: bool) -> bool:
    value = (raw or "").strip().lower()
    if not value:
        return fallback
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return fallback


def enable_thinking() -> bool:
    """Читает окружение на каждом вызове: переменную выставляет лаунчер."""
    return _parse_bool(os.getenv(ENV_ENABLE_THINKING, ""), DEFAULT_ENABLE_THINKING)


def extra_body_for(provider: Optional[str]) -> Dict[str, Any]:
    """Тело запроса для управления рассуждением у конкретного провайдера."""
    name = (provider or "").strip().lower()
    enabled = enable_thinking()
    if name in _CHAT_TEMPLATE_PROVIDERS:
        return {"chat_template_kwargs": {"enable_thinking": enabled}}
    if name in _THINKING_TYPE_PROVIDERS:
        return {"thinking": {"type": "enabled" if enabled else "disabled"}}
    return {"reasoning_split": enabled}


def sdk_extra_body(provider: Optional[str] = None) -> Dict[str, Any]:
    """То же для OpenAI-совместимого SDK — всё едет в ``extra_body``."""
    return extra_body_for(provider)


def strip_think_blocks(text: Optional[str]) -> str:
    """Вырезать черновик рассуждений из текста ответа.

    Нужна там, где ответ достаётся из ``content`` вслепую: если сервер не
    разделил рассуждение, пользователь иначе видит ``<think>``-наброски вместо
    готового ответа. Если после очистки ничего не осталось — возвращается
    пустая строка, и вызывающий сам решает, что делать.
    """
    if not text:
        return ""
    return _THINK_BLOCKS.sub("", text).strip()