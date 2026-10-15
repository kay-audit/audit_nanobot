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


# --- plan_widening_presets ----------------------------------------------

def test_wrong_preset_widens_to_base_vitrine():
    """deleted_ior дал ноль строк — идём к базовой витрине инцидентов."""
    assert planner.plan_widening_presets("deleted_ior", has_filters=True) == [
        "deleted_ior",
        "ior_hypothesis",
    ]


def test_financial_preset_widens_to_base_vitrine():
    assert planner.plan_widening_presets(
        "financial_consequences_ior", has_filters=True
    ) == [
        "financial_consequences_ior",
        "ior_hypothesis",
    ]


def test_base_preset_without_filters_has_nothing_to_widen():
    """Пресетный запрос базы уже выполнялся и дал ноль — повтор не нужен."""
    assert planner.plan_widening_presets("ior_hypothesis", has_filters=False) == []


def test_base_preset_after_dynamic_query_retries_without_llm_where():
    """Регрессия живого прогона: LLM-WHERE обнулил выборку, но готовый
    запрос пресета по базе НЕ выполнялся — без него агент заключал,
    что данных нет."""
    assert planner.plan_widening_presets("ior_hypothesis", has_filters=True) == [
        "ior_hypothesis"
    ]


def test_unknown_preset_still_reaches_base():
    """Пресет без физической таблицы не должен блокировать базовый источник."""
    steps = planner.plan_widening_presets("no_such_preset", has_filters=True)
    assert steps[-1] == "ior_hypothesis"


def test_base_never_repeated():
    for preset in (
        "deleted_ior",
        "vozmeshenie_ior",
        "report_period_specific_ior",
        "ior_hypothesis",
    ):
        for has_filters in (True, False):
            steps = planner.plan_widening_presets(preset, has_filters)
            assert steps.count(planner.BASE_PRESET) <= 1, (preset, has_filters)