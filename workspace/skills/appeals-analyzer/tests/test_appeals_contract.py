import json

import numpy as np
import pytest

from utils import bge_search_engine, greenplum_engine


def make_request(**filters):
    base = {
        "prd": [],
        "s_prd": [],
        "chnl": [],
        "date_from": None,
        "date_to": None,
    }
    base.update(filters)
    return json.dumps(
        {"request_type": "appeals_analysis", "filters": base, "prompt": "Найди жалобы"},
        ensure_ascii=False,
    )


def make_canonical(*sections, prompt="Найди жалобы"):
    return "\n\n".join(("Анализ обращений", *sections, f"Запрос:\n{prompt}"))


@pytest.mark.parametrize(
    ("sections", "expected"),
    [
        (("Продукт:\n- Кредиты",), (["Кредиты"], [], [])),
        (
            ("Продукт:\n- Кредиты\n- Вклады",),
            (["Кредиты", "Вклады"], [], []),
        ),
        (
            (
                "Продукт:\n- Кредиты\n- Вклады",
                "Подпродукт:\n- Дебетовая карта",
                "Канал:\n- СБОЛ\n- IVR",
            ),
            (["Кредиты", "Вклады"], ["Дебетовая карта"], ["СБОЛ", "IVR"]),
        ),
        ((), ([], [], [])),
    ],
)
def test_canonical_filter_combinations(sections, expected):
    parsed = greenplum_engine.parse_structured_analytical_request(
        make_canonical(*sections)
    )
    assert (parsed["products"], parsed["subproducts"], parsed["channels"]) == expected
    assert parsed["format"] == "canonical"


@pytest.mark.parametrize(
    ("period", "expected"),
    [
        ("01.01.2025 — 31.07.2026", ("2025-01-01", "2026-07-31")),
        ("с 01.01.2025", ("2025-01-01", None)),
        ("по 31.07.2026", (None, "2026-07-31")),
    ],
)
def test_canonical_dates(period, expected):
    parsed = greenplum_engine.parse_structured_analytical_request(
        make_canonical(f"Период:\n{period}")
    )
    assert parsed["date_range"] == expected


def test_canonical_preserves_multiline_cyrillic_prompt():
    prompt = "Первая строка\n\nВторая строка: проверь жалобы"
    parsed = greenplum_engine.parse_structured_analytical_request(
        make_canonical("Канал:\n- СБОЛ", prompt=prompt)
    )
    assert parsed["query"] == prompt


@pytest.mark.parametrize(
    ("filters", "expected"),
    [
        ({}, ([], [], [])),
        ({"prd": ["Кредиты"]}, (["Кредиты"], [], [])),
        ({"prd": ["Кредиты", "Банковская карта"]}, (["Кредиты", "Банковская карта"], [], [])),
        (
            {"prd": ["Кредиты"], "s_prd": ["Потребительский кредит"], "chnl": ["СБОЛ", "IVR"]},
            (["Кредиты"], ["Потребительский кредит"], ["СБОЛ", "IVR"]),
        ),
    ],
)
def test_json_filter_combinations(filters, expected):
    parsed = greenplum_engine.parse_structured_analytical_request(make_request(**filters))
    assert (parsed["products"], parsed["subproducts"], parsed["channels"]) == expected
    assert parsed["format"] == "json"


@pytest.mark.parametrize(
    ("date_from", "date_to", "expected"),
    [
        (None, None, None),
        ("2025-01-01", None, ("2025-01-01", None)),
        (None, "2026-12-31", (None, "2026-12-31")),
        ("2025-01-01", "2026-07-31", ("2025-01-01", "2026-07-31")),
    ],
)
def test_json_dates(date_from, date_to, expected):
    parsed = greenplum_engine.parse_structured_analytical_request(
        make_request(date_from=date_from, date_to=date_to)
    )
    assert parsed["date_range"] == expected


def test_json_rejects_reversed_dates_and_unknown_catalog_value():
    with pytest.raises(ValueError, match="Дата начала"):
        greenplum_engine.parse_structured_analytical_request(
            make_request(date_from="2026-02-01", date_to="2026-01-01")
        )
    with pytest.raises(ValueError, match="неизвестные значения"):
        greenplum_engine.parse_structured_analytical_request(make_request(prd=["Несуществующий"]))


def test_legacy_csv_still_parses():
    parsed = greenplum_engine.parse_structured_analytical_request(
        '"Кредиты", "Потребительский кредит", "СБОЛ", "жалобы за 2026 год"'
    )
    assert parsed["format"] == "legacy"
    assert parsed["products"] == ["Кредиты"]
    assert parsed["date_range"] is None
    sql, _ = greenplum_engine.build_product_prefilter_sql(
        parsed["products"], parsed["subproducts"], parsed["channels"], years=[2026]
    )
    assert (
        "WHERE (a.prd IN (%s) OR a.s_prd IN (%s) OR a.chnl IN (%s))"
    ) in sql


@pytest.mark.parametrize(
    ("products", "subproducts", "channels", "where"),
    [
        (["Кредиты"], [], [], "(a.prd IN (%s))"),
        ([], ["Дебетовая карта"], [], "(a.s_prd IN (%s))"),
        ([], [], ["СБОЛ"], "(a.chnl IN (%s))"),
        (
            ["Кредиты"],
            ["Дебетовая карта"],
            [],
            "(a.prd IN (%s) OR a.s_prd IN (%s))",
        ),
        (
            ["Кредиты"],
            ["Дебетовая карта"],
            ["СБОЛ"],
            "(a.prd IN (%s) OR a.s_prd IN (%s) OR a.chnl IN (%s))",
        ),
    ],
)
def test_sql_uses_or_between_nonempty_groups(products, subproducts, channels, where):
    sql, _ = greenplum_engine.build_product_prefilter_sql(
        products, subproducts, channels, years=[2026]
    )
    assert f"WHERE {where}" in sql


def test_sql_uses_in_for_multiple_values_inside_every_group():
    sql, params = greenplum_engine.build_product_prefilter_sql(
        ["Кредиты", "Вклады"],
        ["Дебетовая карта", "Потребительский кредит"],
        ["СБОЛ", "IVR"],
        years=[2026],
    )
    assert (
        "WHERE (a.prd IN (%s, %s) OR a.s_prd IN (%s, %s) "
        "OR a.chnl IN (%s, %s))"
    ) in sql
    assert params == [
        "Кредиты", "Вклады", "Дебетовая карта", "Потребительский кредит", "СБОЛ", "IVR",
    ]


def test_sql_applies_date_with_and_to_whole_or_group():
    sql, params = greenplum_engine.build_product_prefilter_sql(
        ["Кредиты", "Вклады"],
        ["Дебетовая карта"],
        ["СБОЛ"],
        years=[2026],
        date_range=("2026-01-01", "2026-07-31"),
    )
    assert (
        "WHERE (a.prd IN (%s, %s) OR a.s_prd IN (%s) OR a.chnl IN (%s)) "
        "AND a.req_reg_date >= %s AND a.req_reg_date < %s"
    ) in sql
    assert params[:4] == ["Кредиты", "Вклады", "Дебетовая карта", "СБОЛ"]
    assert str(params[4]) == "2026-01-01"
    assert str(params[5]) == "2026-08-01"


def test_one_sided_date_masks():
    old_dates = bge_search_engine.req_reg_dates
    old_ids = bge_search_engine.doc_ids
    old_positions = bge_search_engine.id_to_positions
    try:
        bge_search_engine.doc_ids = ["1", "2", "3"]
        bge_search_engine.req_reg_dates = ["2024-12-31", "2025-01-01", "2026-01-01"]
        bge_search_engine.id_to_positions = {"1": [0], "2": [1], "3": [2]}
        assert np.array_equal(
            bge_search_engine.build_allowed_mask(None, ("2025-01-01", None)),
            np.array([False, True, True]),
        )
        assert np.array_equal(
            bge_search_engine.build_allowed_mask(None, (None, "2025-12-31")),
            np.array([True, True, False]),
        )
    finally:
        bge_search_engine.req_reg_dates = old_dates
        bge_search_engine.doc_ids = old_ids
        bge_search_engine.id_to_positions = old_positions


def test_bge_union_candidate_mask_is_combined_with_date_by_and():
    old_dates = bge_search_engine.req_reg_dates
    old_ids = bge_search_engine.doc_ids
    old_positions = bge_search_engine.id_to_positions
    try:
        # IDs 1 и 3 уже являются объединением результатов OR-групп из GP.
        # Период должен оставить из этого union только ID 3.
        bge_search_engine.doc_ids = ["1", "2", "3"]
        bge_search_engine.req_reg_dates = ["2025-12-31", "2026-02-01", "2026-03-01"]
        bge_search_engine.id_to_positions = {"1": [0], "2": [1], "3": [2]}
        mask = bge_search_engine.build_allowed_mask(
            ["1", "3"], ("2026-01-01", "2026-12-31")
        )
        assert np.array_equal(mask, np.array([False, False, True]))
    finally:
        bge_search_engine.req_reg_dates = old_dates
        bge_search_engine.doc_ids = old_ids
        bge_search_engine.id_to_positions = old_positions


def test_empty_structural_groups_with_date_use_date_only_mask():
    old_dates = bge_search_engine.req_reg_dates
    old_ids = bge_search_engine.doc_ids
    try:
        bge_search_engine.doc_ids = ["1", "2"]
        bge_search_engine.req_reg_dates = ["2025-01-01", "2026-01-01"]
        mask = bge_search_engine.build_allowed_mask(None, ("2026-01-01", "2026-12-31"))
        assert np.array_equal(mask, np.array([False, True]))
    finally:
        bge_search_engine.req_reg_dates = old_dates
        bge_search_engine.doc_ids = old_ids


def test_all_empty_filters_leave_bge_mask_unrestricted():
    assert bge_search_engine.build_allowed_mask(None, None) is None
