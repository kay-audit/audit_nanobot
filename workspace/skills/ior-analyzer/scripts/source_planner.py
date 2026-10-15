"""Планирование выборки данных ИОР — чистые функции без внешних зависимостей.

Вынесено из ``ior_reports.py``, чтобы правила выбора источника можно было
покрыть обычными юнит-тестами: модуль не тянет pandas/Greenplum/LLM.

Две задачи:

* ``has_comparative_threshold`` — понимает, задаёт ли фраза числовой порог.
  Сравнительные слова сами по себе («на какую сумму больше всего») фильтром
  не являются: уводили навык с корректного пресетного запроса на
  сгенерированный, который возвращал ноль строк.
* ``plan_widening_presets`` — совместимый запрет расширения пустой выборки.
"""

from __future__ import annotations

import re

# Базовый пресет: витрина инцидентов без соединения с таблицами последствий.
BASE_PRESET = "ior_hypothesis"

# Сравнительный порог в свободном тексте: «больше 100000», «свыше 10 млн».
# Без числа после сравнительного слова это просто оборот речи, а не фильтр.
COMPARATIVE_WITH_NUMBER = re.compile(
    r"(?:более|больше|свыше|выше|не\s+менее|от)\s*[-+]?\d[\d\s.,]*",
    re.IGNORECASE,
)


def has_comparative_threshold(user_prompt: str) -> bool:
    """True, если в запросе есть сравнение с числовым порогом."""
    return bool(COMPARATIVE_WITH_NUMBER.search(user_prompt or ""))


def plan_widening_presets(preset: str, has_filters: bool) -> list[str]:
    """Empty is a valid business result; never relax preset or user predicates."""
    return []
