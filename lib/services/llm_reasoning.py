"""Сквозной переключатель рассуждений для OpenAI-совместимых провайдеров.

Единственный параметр — переменная окружения ``VLLM_ENABLE_THINKING``
(``true``/``false``). Источник истины один для всех навыков и для агента.

Почему управление рассуждениями различается по провайдерам
----------------------------------------------------------
Управляющее поле у каждого провайдера своё, и отправлять чужое бессмысленно —
сервер его просто проигнорирует, и переключатель молча не сработает:

* **MiniMax** (``api.minimax.io``) — ``reasoning_split``. Проверено вживую:
  без него ``reasoning_content`` пустой, а весь ``<think>``-черновик модели
  приходит **внутри ``content``**. С ``reasoning_split: true`` рассуждение
  уходит в отдельное поле, а ``content`` остаётся готовым ответом.
* **vLLM / sglang** — ``chat_template_kwargs.enable_thinking``: ключ уходит в
  генератор шаблона чата, и рассуждение управляется независимо от имени модели
  (у встроенной спеки vLLM в nanobot ``thinking_style`` пустой, а
  ``enable_thinking`` отправляется только для моделей из
  ``_QWEN_THINKING_MODELS``, куда локальная ``Qwen3.6-27B`` не входит).
* **DeepSeek / VolcEngine** — ``thinking.type``.

Почему не ``agents.defaults.reasoningEffort``
--------------------------------------------
У провайдера ``minimax`` наномит знает стиль ``reasoning_split``, но отправляет
его **только если reasoningEffort задан явно** (``reasoningEffort: null`` — а
именно это стоит в конфиге по умолчанию — означает «не слать ничего»). А
любое значение кроме ``none`` заодно **выбрасывает ``temperature``** из запроса
(``_supports_temperature``). Чтобы получить разделение и не потерять
``temperature``, у провайдера задан ``extraBody`` напрямую.

Почему всё равно вычищаем ``<think>``
-------------------------------------
Даже при правильном разделении часть серверов (и любые прокси/режимы без
разбора ответа) отдаёт рассуждение внутри ``content``. ``strip_think_blocks``
страхует от этого на путях, где нет своего парсера.

Модуль без зависимостей: его читают и навыки-скрипты, и агент, и отдельный
процесс Follow Up.
"""

from __future__ import annotations

import os
import re
from typing import Any, Dict

#: Имя переменной окружения — единственного источника истины.
ENV_ENABLE_THINKING = "VLLM_ENABLE_THINKING"

#: Значение по умолчанию. Рассуждение включаем осознанно и явно: у Qwen3 шаблон
#: чата рассуждает по умолчанию, а у MiniMax-M3 разделение рассуждения нужно
#: задать руками — иначе черновик попадает в ответ.
DEFAULT_ENABLE_THINKING = True

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t", "да", "истина"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "n", "f", "нет", "ложь"})

#: Провайдеры с полем ``chat_template_kwargs`` (vLLM-семейство).
_CHAT_TEMPLATE_PROVIDERS = frozenset({"vllm", "sglang"})

#: Провайдеры с полем ``thinking.type`` (DeepSeek-семейство).
_THINKING_TYPE_PROVIDERS = frozenset({"deepseek", "volcengine", "byteplus"})

# Блоки рассуждения: <think>…</think>, </think> без открывающего (сервер мог
# обрезать начало по max_tokens), и ```xml-think …``` — вариант, который
# встречается у части Qwen-шаблонов.
_THINK_BLOCKS = re.compile(
    r"<think>.*?</think>"
    r"|\A\s*</think>.*?(?=\n\s*\n|\Z)"
    r"|```xml-think\s*\n.*?```"
    r"|```think\s*\n.*?```",
    re.DOTALL | re.IGNORECASE,
)
_LEADING_THINK_LINE = re.compile(r"\A[^\S\n]*think:[^\n]*\n", re.IGNORECASE)


def _parse_bool(raw: str, fallback: bool) -> bool:
    """Разбор значения переменной. Неизвестное значение — fallback, не исключение.

    Молчаливый fallback обязателен: навык не должен падать из-за опечатки в
    переменной окружения — он должен взять значение по умолчанию и работать.
    """
    value = (raw or "").strip().lower()
    if not value:
        return fallback
    if value in _TRUE_VALUES:
        return True
    if value in _FALSE_VALUES:
        return False
    return fallback


def enable_thinking() -> bool:
    """Включены ли рассуждения при следующем обращении к модели.

    Читает окружение на каждом вызове, а не один раз при импорте: переменную
    выставляет лаунчер навыка, и навык импортируется раньше, чем окружение
    дополняется.
    """
    return _parse_bool(os.getenv(ENV_ENABLE_THINKING, ""), DEFAULT_ENABLE_THINKING)


def extra_body_for(provider: str | None) -> Dict[str, Any]:
    """Тело запроса для управления рассуждением у конкретного провайдера.

    Неизвестный OpenAI-совместимый провайдер получает ``reasoning_split``:
    он безвреден там, где поле незнакомо (такие серверы игнорируют лишнее), и
    нужен там, где эндпоинт MiniMax-совместимый.
    """
    name = (provider or "").strip().lower()
    enabled = enable_thinking()
    if name in _CHAT_TEMPLATE_PROVIDERS:
        return {"chat_template_kwargs": {"enable_thinking": enabled}}
    if name in _THINKING_TYPE_PROVIDERS:
        return {"thinking": {"type": "enabled" if enabled else "disabled"}}
    return {"reasoning_split": enabled}


def apply_to_payload(payload: Dict[str, Any], provider: str | None = None) -> Dict[str, Any]:
    """Влить переключатель в тело сырого HTTP-запроса (requests/httpx/urllib).

    Слияние, а не присваивание: вызывающий мог уже положить свои поля
    (``chat_template_kwargs``/``reasoning_split``), и молча сносить их нельзя.
    """
    if not isinstance(payload, dict):
        return payload
    for field, value in extra_body_for(provider).items():
        if field == "chat_template_kwargs" and isinstance(payload.get(field), dict):
            merged = dict(payload[field])
            merged.update(value)
            payload[field] = merged
        elif field not in payload:
            payload[field] = value
        elif isinstance(payload[field], dict) and isinstance(value, dict):
            merged = dict(payload[field])
            merged.update(value)
            payload[field] = merged
        else:
            payload[field] = value
    return payload


def sdk_extra_body(provider: str | None = None) -> Dict[str, Any]:
    """То же для OpenAI-совместимого SDK: параметр ``extra_body``.

    SDK не пропускает неизвестные поля верхнего уровня, поэтому всё едет
    именно в ``extra_body``.
    """
    return extra_body_for(provider)


def strip_think_blocks(text: str | None) -> str:
    """Вырезать черновик рассуждений из текста ответа.

    Нужна там, где ответ достаётся из ``content`` вслепую: если сервер не
    разделил рассуждение, пользователь иначе видит ``<think>``-наброски вместо
    готового ответа. Если после очистки не осталось ничего — возвращается
    пустая строка, и вызывающий сам решает, что делать (обычно взять
    ``reasoning_content``).
    """
    if not text:
        return ""
    cleaned = _THINK_BLOCKS.sub("", text)
    cleaned = _LEADING_THINK_LINE.sub("", cleaned)
    return cleaned.strip()