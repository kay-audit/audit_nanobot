"""Регрессия на «пустая выгрузка ≠ данных нет» (ior-analyzer).

Инцидент: на вопрос «Сколько ИОР всего в нашей базе и на какую общую сумму
потерь?» навык ушёл в сгенерированный LLM-запрос, тот вернул 0 строк,
предметный пресет разрешился в ``deleted_ior`` (таблица удалённых ИОР) —
снова ноль. Агент сделал вывод «данных ИОР в базе нет», хотя витрина
содержала 10 инцидентов.

Модуль ``source_planner`` чистый: без pandas/Greenplum/LLM, поэтому тесты
не тянут окружение стенда.
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

_SCRIPTS_DIR = Path(__file__).resolve().parents[1] / "scripts"
_spec = importlib.util.spec_from_file_location(
    "ior_source_planner", _SCRIPTS_DIR / "source_planner.py"
)
planner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(planner)  # type: ignore[union-attr]


# --- has_comparative_threshold ------------------------------------------

def test_comparative_without_number_is_not_a_filter():
    """Оборот речи не считается порогом — иначе уходим с пресета."""
    assert planner.has_comparative_threshold(
        "Сколько ИОР всего в нашей базе данных и на какую общую сумму потерь больше всего?"
    ) is False


def test_comparative_with_number_is_a_filter():
    """Настоящий порог «больше 100000» фильтром остаётся."""
    assert planner.has_comparative_threshold(
        "Покажи ИОР с суммой потерь больше 100000"
    ) is True


def test_comparative_with_spaced_number():
    assert planner.has_comparative_threshold("сумма свыше 10 000 000") is True


def test_plain_question_has_no_threshold():
    assert planner.has_comparative_threshold("Сколько ИОР за 2025 год?") is False


def test_empty_prompt_is_safe():
    assert planner.has_comparative_threshold("") is False


# Empty business populations must never be silently expanded.
import pytest

@pytest.mark.parametrize('preset', ['deleted_ior', 'financial_consequences_ior',
                                   'vozmeshenie_ior', 'ior_hypothesis', 'unknown'])
@pytest.mark.parametrize('has_filters', [True, False])
def test_empty_population_never_widens(preset, has_filters):
    assert planner.plan_widening_presets(preset, has_filters) == []
