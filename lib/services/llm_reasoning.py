"""Сквозной переключатель рассуждений для локальных vLLM-моделей.

Единственный параметр — переменная окружения ``VLLM_ENABLE_THINKING``
(``true``/``false``). Она одна на все навыки и на агента: навыки живут
отдельными процессами, и общий конфиг до них не доходит, а вот окружение
наследуют все.

Почему именно ``chat_template_kwargs``, а не ``agents.defaults.reasoningEffort``
и не ``providers.vllm.thinkingStyle``:

* ``reasoningEffort`` у провайдера ``vllm`` бесполезен. Спека vLLM встроенная
  (``nanobot/providers/registry.py``) и имеет пустой ``thinking_style``, а
  ``enable_thinking`` отправляется только для моделей из белого списка
  ``_QWEN_THINKING_MODELS``. Локальная модель названа ``Qwen3.6-27B``, её slug
  в списке отсутствует — выключатель до vLLM не дойдёт никогда.
* ``reasoningEffort`` != ``none`` ещё и выбрасывает ``temperature`` из всех
  запросов агента (``_supports_temperature``), то есть меняет поведение
  навыков, которым это не нужно.
* ``providers.vllm.thinkingStyle`` читается только при создании динамической
  спеки для НЕИЗВЕСТНОГО провайдера (``providers/factory.py``). У ``vllm``
  спека встроенная, значение игнорируется.

``chat_template_kwargs`` — канонический способ vLLM: ключ уходит в генератор
шаблона чата, и ``enable_thinking`` управляет рассуждением у Qwen3 независимо
от того, как названа модель. Для моделей без такого параметра в шаблоне ключ
просто игнорируется — вреда нет.

Модуль без зависимостей: его читают и навыки-скрипты, и агент, и отдельный
процесс Follow Up.
"""

from __future__ import annotations

import os
from typing import Any, Dict

#: Имя переменной окружения — единственного источника истины.
ENV_ENABLE_THINKING = "VLLM_ENABLE_THINKING"

#: Значение по умолчанию. Рассуждение включаем осознанно: у Qwen3 шаблон чата
#: и так по умолчанию рассуждает, поэтому default=True ничего не меняет по
#: сравнению с сегодняшним поведением — он лишь делает его явным и одинаковым
#: во всех навыках.
DEFAULT_ENABLE_THINKING = True

_TRUE_VALUES = frozenset({"1", "true", "yes", "on", "y", "t", "да", "истина"})
_FALSE_VALUES = frozenset({"0", "false", "no", "off", "n", "f", "нет", "ложь"})


def _parse_bool(raw: str, fallback: bool) -> bool:
    """Разбор значения переменной. Неизвестное значение — fallback, не исключение.

    Молчаливый fallback здесь обязателен: навык не должен падать из-за опечатки
    в переменной окружения — он должен взять заданное по умолчанию значение и
    работать дальше.
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


def chat_template_kwargs() -> Dict[str, Any]:
    """Фрагмент тела запроса для vLLM: ``{"chat_template_kwargs": {...}}``."""
    return {"chat_template_kwargs": {"enable_thinking": enable_thinking()}}


def apply_to_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    """Влить переключатель в тело сырого HTTP-запроса (requests/httpx).

    Слияние, а не присваивание: вызывающий мог уже положить свои
    ``chat_template_kwargs``, и молча сносить их нельзя.
    """
    if not isinstance(payload, dict):
        return payload
    kwargs = payload.get("chat_template_kwargs")
    if isinstance(kwargs, dict):
        merged = dict(kwargs)
        merged["enable_thinking"] = enable_thinking()
        payload["chat_template_kwargs"] = merged
    else:
        payload["chat_template_kwargs"] = {"enable_thinking": enable_thinking()}
    return payload


def sdk_extra_body() -> Dict[str, Any]:
    """То же самое для OpenAI-совместимого SDK: параметр ``extra_body``.

    SDK не пропускает неизвестные поля верхнего уровня, поэтому
    ``chat_template_kwargs`` едет именно в ``extra_body``.
    """
    return chat_template_kwargs()