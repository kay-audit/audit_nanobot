"""Offline structured-analysis contracts: synthetic data, no GP or Qwen service."""
import asyncio
import contextlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import pandas as pd

SKILL_ROOT = Path(__file__).resolve().parent.parent
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from analysis_mode.anomalies import (
    CONCENTRATION_SHIFT_MIN_PP, SHARE_DISPROPORTION_MIN_PP, SIGNIFICANT_CONCENTRATION_SHARE,
    detect_anomalies, report_events,
)
from analysis_mode.direct_loss import DirectLossStrategy, MAIN_COLUMNS, FINANCIAL_COLUMNS
from analysis_mode.hypotheses import (
    MAX_DESCRIPTION_CHARS, MAX_FINAL_PROMPT_CHARS, build_evidence_pack,
    build_hypothesis_messages, generate_hypotheses, summarize_evidence_batches, validate_response,
)
from analysis_mode.models import AnalysisRequestError, AnomalyEvent
from analysis_mode.parser import try_parse_analysis_request
from analysis_mode.runner import run_analysis_mode
from analysis_mode.statistics import calculate_metrics


BASE = {"action": "Анализ", "money_filter": "Прямые потери", "org_filter": None,
        "date_range": "2026-01-01:2026-06-30", "export_excel": False}


def canonical(*, money="Прямые потери", org="Все", period="01.01.2026 — 30.06.2026",
              excel="Да", newline="\n"):
    return newline.join((
        "Анализ ИОР", "", "Денежный показатель:", money, "", "Оргструктура:", org,
        "", "Период:", period, "", "Сформировать Excel:", excel,
    ))


def request(**changes):
    return try_parse_analysis_request(json.dumps(BASE | changes, ensure_ascii=False))


def frame(rows=None):
    if rows is None:
        rows = [(1, "F1", 100, "Утверждён", "2026-03-01"),
                (1, "F1", 100, "Утверждён", "2026-03-01"),
                (1, "F2", 200, "Утверждён", "2026-03-03"),
                (1, "F3", 0, "Утверждён", "2026-04-01"),
                (2, "F4", None, "Утвержден", "2026-03-01"),
                (3, "F5", 0, "Утверждение", "2026-04-30 23:59:59"),
                (4, "F6", 900, "Удалён", "2026-03-02"),
                (5, "F7", 500, "Черновик", "2026-03-02")]
    records = []
    for key, sid, amount, status, created in rows:
        record = dict.fromkeys((*MAIN_COLUMNS, *FINANCIAL_COLUMNS))
        record.update(incdnt_id=key, incdnt_sid=f"EVE-{key}", fin_impact_sid=sid,
                      fin_impact_id=sid, fin_impact_rub_amt=amount, incdnt_status_name=status,
                      fin_impact_creation_dttm=created, fin_impact_type_name="Прямая потеря",
                      org_struct_lvl_3_name=f"ТБ-{key % 2}", risk_profile_id=f"DRP-{key % 2}",
                      risk_profile_name="Общий риск", process_lvl_4_name="Процесс платежей",
                      incdnt_summary_descr_txt=f"Описание EVE-{key}", incdnt_full_descr_txt="Проверить документы по операции")
        records.append(record)
    return pd.DataFrame(records)


def prepared(raw=None, req=None):
    data = DirectLossStrategy().prepare(frame() if raw is None else raw)
    metrics = calculate_metrics(data, req or request())
    events = detect_anomalies(data, metrics)
    return data, metrics, events


def concentration_frame(month_specs):
    """One approved incident per category/month with exact loss weights."""
    rows = []
    incident_id = 1
    for month, categories in month_specs.items():
        for category, loss in categories.items():
            rows.append((incident_id, f"F{incident_id}", loss, "Утверждён", f"{month}-15"))
            incident_id += 1
    result = frame(rows)
    categories = [category for values in month_specs.values() for category in values]
    result["org_struct_lvl_3_name"] = [f"ТБ {category}" for category in categories]
    result["risk_profile_id"] = [f"DRP-{category}" for category in categories]
    result["risk_profile_name"] = [f"Риск {category}" for category in categories]
    result["process_lvl_4_name"] = [f"Процесс {category}" for category in categories]
    return result


def valid_response(pack):
    event = pack["events"][0]
    key = event["evidence_keys"][0]
    eve = next(row["incdnt_sid"] for row in pack["incidents"] if str(row["incdnt_id"]) == key)
    blocks = []
    for number, title in enumerate(("Повторяемость обстоятельств", "Изменение структуры потерь", "Процессный фактор"), 1):
        blocks.append(
            f"**Гипотеза {number}: {title}**\n\n"
            f"- **Предположение / Суть проблемы:** Наблюдение по {eve} может отражать повторяемый механизм, требующий проверки.\n"
            "- **Шаги проверки:**\n"
            "  1. Сопоставить обстоятельства выбранных ИОР.\n"
            "  2. Проверить документы и применявшиеся процедуры контроля.\n"
            "- **Ожидаемый результат:** Повторяемые признаки поддержат гипотезу, их отсутствие ослабит её.")
    return "### 3. Гипотезы\n\n" + "\n\n".join(blocks)


class ParserTests(unittest.TestCase):
    def test_canonical_excel_yes_maps_to_existing_request(self):
        parsed = try_parse_analysis_request(canonical(excel="Да"))
        self.assertEqual(parsed.action, "Анализ")
        self.assertEqual(parsed.money_filter, "прямые потери")
        self.assertIsNone(parsed.org_filter)
        self.assertEqual(str(parsed.start), "2026-01-01")
        self.assertEqual(str(parsed.end), "2026-06-30")
        self.assertTrue(parsed.export_excel)

    def test_canonical_excel_no_and_inclusive_end(self):
        parsed = try_parse_analysis_request(canonical(excel="Нет"))
        self.assertFalse(parsed.export_excel)
        self.assertEqual(str(parsed.end), "2026-06-30")
        self.assertEqual(str(parsed.end_exclusive), "2026-07-01")

    def test_canonical_supports_lf_crlf_cyrillic_and_outer_whitespace(self):
        for newline in ("\n", "\r\n"):
            with self.subTest(newline=repr(newline)):
                parsed = try_parse_analysis_request(
                    " \r\n" + canonical(newline=newline) + "\r\n  "
                )
                self.assertEqual(parsed.money_filter, "прямые потери")
                self.assertTrue(parsed.export_excel)

    def test_canonical_labels_do_not_depend_on_fixed_line_indices(self):
        text = "\n".join((
            "Анализ ИОР", "", "Период:", "01.01.2026 — 30.06.2026", "",
            "Сформировать Excel:", "Нет", "", "Денежный показатель:",
            "Прямые потери", "", "Оргструктура:", "Все",
        ))
        parsed = try_parse_analysis_request(text)
        self.assertEqual(str(parsed.start), "2026-01-01")
        self.assertFalse(parsed.export_excel)

    def test_canonical_rejects_unsupported_money_and_org(self):
        for text in (
            canonical(money="Косвенные потери"),
            canonical(org="Московский банк"),
        ):
            with self.subTest(text=text), self.assertRaises(AnalysisRequestError):
                try_parse_analysis_request(text)

    def test_canonical_rejects_missing_label_and_empty_value(self):
        missing = canonical().replace("\n\nОргструктура:\nВсе", "")
        empty = canonical().replace("Денежный показатель:\nПрямые потери", "Денежный показатель:\n")
        for text in (missing, empty):
            with self.subTest(text=text), self.assertRaises(AnalysisRequestError):
                try_parse_analysis_request(text)

    def test_canonical_rejects_missing_invalid_and_reversed_dates(self):
        for period in (
            "01.01.2026",
            "2026-01-01 — 30.06.2026",
            "31.02.2026 — 30.06.2026",
            "01.07.2026 — 30.06.2026",
        ):
            with self.subTest(period=period), self.assertRaises(AnalysisRequestError):
                try_parse_analysis_request(canonical(period=period))

    def test_canonical_rejects_unknown_excel_and_damaged_label(self):
        for text in (
            canonical(excel="0"),
            canonical().replace("Оргструктура:", "Орг структура:"),
        ):
            with self.subTest(text=text), self.assertRaises(AnalysisRequestError):
                try_parse_analysis_request(text)

    def test_canonical_marker_does_not_capture_ordinary_text(self):
        for text in (
            "Проведи анализ ИОР за полугодие",
            "Анализ ИОР за период 2026 года",
            "Покажи прямые потери и сформируй Excel",
        ):
            with self.subTest(text=text):
                self.assertIsNone(try_parse_analysis_request(text))

    def test_json_backward_compatibility_matches_canonical(self):
        canonical_request = try_parse_analysis_request(canonical())
        json_request = try_parse_analysis_request(json.dumps(BASE | {"export_excel": True}, ensure_ascii=False))
        self.assertEqual(canonical_request, json_request)

    def test_valid_normalized_defaults(self):
        obj = BASE | {"action": "  АНАЛИЗ ", "money_filter": " Прямые   ПОТЕРИ ", "org_filter": "  "}
        del obj["export_excel"]
        parsed = try_parse_analysis_request(json.dumps(obj))
        self.assertEqual(parsed.money_filter, "прямые потери")
        self.assertFalse(parsed.export_excel)
        self.assertEqual(str(parsed.end_exclusive), "2026-07-01")

    def test_missing_fields(self):
        for field in ("action", "money_filter", "date_range"):
            with self.subTest(field=field), self.assertRaises(AnalysisRequestError):
                try_parse_analysis_request(json.dumps({k: v for k, v in BASE.items() if k != field}))

    def test_invalid_fields(self):
        for changes in ({"action": "Выгрузка"}, {"action": None}, {"money_filter": "Косвенные потери"},
                        {"org_filter": "Московский банк"}, {"org_filter": 0}, {"export_excel": "true"},
                        {"export_excel": 1}, {"export_excel": None}, {"extra": 1},
                        {"date_range": "2026-02-30:2026-03-01"}, {"date_range": "2026-07-01:2026-01-01"},
                        {"date_range": "2026-1-01:2026-06-30"}, {"date_range": "2026-01-01:9999-12-31"}):
            with self.subTest(changes=changes), self.assertRaises(AnalysisRequestError):
                request(**changes)

    def test_malformed_json_never_returns_none(self):
        for text in ('{"action":"Анализ",', '{"money_filter":', '"action":"Анализ"}',
                     '[{"action":"Анализ"}]', '```json\n{"action":"Анализ"}\n```',
                     '{"action":"Анализ","action":"Анализ"}'):
            with self.subTest(text=text), self.assertRaises(AnalysisRequestError):
                try_parse_analysis_request(text)

    def test_ordinary_text(self):
        self.assertIsNone(try_parse_analysis_request("Покажи прямые потери"))
        self.assertIsNone(try_parse_analysis_request("Анализ"))


class RoutingTests(unittest.IsolatedAsyncioTestCase):
    async def test_canonical_bypasses_all_legacy_paths(self):
        import ior_reports
        with patch.object(ior_reports, "detect_preset_from_prompt", side_effect=AssertionError("legacy detector")), \
             patch.object(ior_reports, "get_session_extract", side_effect=AssertionError("session")), \
             patch.object(ior_reports, "parse_period", side_effect=AssertionError("period")), \
             patch.object(ior_reports, "get_data_store", return_value=object()), \
             patch("analysis_mode.runner.run_analysis_mode", new_callable=AsyncMock, return_value="structured") as run:
            self.assertEqual(await ior_reports.run_ior_report(None, "test", canonical()), "structured")
            self.assertEqual(run.await_count, 1)

    async def test_structured_bypasses_all_legacy_paths(self):
        import ior_reports
        with patch.object(ior_reports, "detect_preset_from_prompt", side_effect=AssertionError("legacy detector")), \
             patch.object(ior_reports, "get_session_extract", side_effect=AssertionError("session")), \
             patch.object(ior_reports, "parse_period", side_effect=AssertionError("period")), \
             patch.object(ior_reports, "get_data_store", return_value=object()), \
             patch("analysis_mode.runner.run_analysis_mode", new_callable=AsyncMock, return_value="structured") as run:
            self.assertEqual(await ior_reports.run_ior_report(None, "test", json.dumps(BASE)), "structured")
            self.assertEqual(run.await_count, 1)

    async def test_validation_before_store_and_detector(self):
        import ior_reports
        for text, preset in ((json.dumps(BASE | {"action": "wrong"}), None), ('{"action":', None),
                             (json.dumps(BASE), "financial_consequences_ior"),
                             (canonical(), "financial_consequences_ior")):
            with self.subTest(text=text, preset=preset), \
                 patch.object(ior_reports, "get_data_store", side_effect=AssertionError("store")), \
                 patch.object(ior_reports, "detect_preset_from_prompt", side_effect=AssertionError("detector")), \
                 self.assertRaises(AnalysisRequestError):
                await ior_reports.run_ior_report(preset, "test", text)

    def test_old_direct_loss_routing(self):
        from ior_reports import detect_preset_from_prompt
        self.assertEqual(detect_preset_from_prompt("Покажи прямые потери"), "financial_consequences_ior")


class DataTests(unittest.TestCase):
    def test_sql_contract(self):
        sql = DirectLossStrategy().build_sql(request(), {"ior": "test_main", "financial_impact": "test_fin"})
        self.assertIn("FROM test_main ior\nINNER JOIN test_fin fi ON ior.incdnt_id = fi.incdnt_id", sql)
        self.assertIn("fi.fin_impact_type_name = 'Прямая потеря'", sql)
        self.assertIn("fi.fin_impact_creation_dttm >= '2026-01-01'", sql)
        self.assertIn("fi.fin_impact_creation_dttm < '2026-07-01'", sql)
        for forbidden in ("LIMIT", "incdnt_entry_dt", "incdnt_detection_dt", "incdnt_start_dt", "fin_impact_account_num", "fin_impact_docum_num"):
            self.assertNotIn(forbidden, sql)

    def test_sql_executes_full_population_and_boundaries(self):
        # SQLite's ISO timestamp ordering exercises the generated backend-neutral SQL.
        import sqlite3
        raw = frame([(i, f"F{i}", amount, "Утверждён", dt) for i, amount, dt in (
            (1, 0, "2026-01-01"), (2, None, "2026-06-30 23:59:59"),
            (3, 1, "2026-07-01"), (4, 1, "2025-12-31"), (5, 100, "2026-03-01"))])
        raw.loc[raw.incdnt_id.eq(5), "fin_impact_type_name"] = "Косвенная потеря"
        with sqlite3.connect(":memory:") as conn:
            raw[list(MAIN_COLUMNS)].to_sql("main", conn, index=False)
            raw[["incdnt_id", *FINANCIAL_COLUMNS]].to_sql("fin", conn, index=False)
            result = pd.read_sql_query(DirectLossStrategy().build_sql(request(), {"ior": "main", "financial_impact": "fin"}), conn)
        self.assertEqual(result.incdnt_id.tolist(), [1, 2])

    def test_dedup_status_and_null(self):
        data, metrics, _ = prepared()
        self.assertEqual(len(data.detail_df), 7)
        self.assertEqual(metrics.unique_incidents, 5)
        self.assertEqual(metrics.total_loss, 1700)
        self.assertEqual(metrics.approved_loss, 300)
        self.assertEqual(set(data.approved_incident_df.incdnt_id), {1, 2, 3})
        self.assertEqual(metrics.statuses.unique_incidents.sum(), 5)
        by_id = data.incident_df.set_index("incdnt_id")
        self.assertEqual(by_id.loc[1, "direct_loss_rub"], 300)
        self.assertEqual(by_id.loc[2, "amount_class"], "null/unknown")
        self.assertEqual(by_id.loc[3, "amount_class"], "zero")
        self.assertTrue(data.detail_df.loc[data.detail_df.incdnt_id.eq(2), "amount_is_null"].all())

    def test_canonical_key_not_display_sid(self):
        raw = frame()
        raw.incdnt_sid = "EVE-SAME"
        data, metrics, _ = prepared(raw)
        self.assertEqual(metrics.unique_incidents, 5)

    def test_missing_sid_is_not_silently_deduplicated(self):
        raw = frame([(1, None, 100, "Утверждён", "2026-03-01"), (1, None, 200, "Утверждён", "2026-03-01")])
        data, metrics, _ = prepared(raw)
        self.assertEqual(len(data.detail_df), 2)
        self.assertEqual(metrics.total_loss, 300)
        self.assertTrue(data.quality_notes)

    def test_conflicting_duplicate_retains_first_including_null(self):
        raw = frame([(1, "F1", None, "Утверждён", "2026-03-01"), (1, "F1", 100, "Утверждён", "2026-03-01")])
        data, _, _ = prepared(raw)
        self.assertEqual(data.incident_df.iloc[0].amount_class, "null/unknown")
        self.assertTrue(data.quality_notes)

    def test_monthly_granularity_and_empty_months(self):
        data, metrics, _ = prepared()
        monthly = data.monthly_incident_df
        self.assertEqual(len(monthly.loc[monthly.incdnt_id.eq(1)]), 2)
        self.assertEqual(monthly.loc[monthly.month.eq("2026-03") & monthly.incdnt_id.eq(1), "direct_loss_rub"].item(), 300)
        self.assertEqual(len(metrics.monthly), 6)
        self.assertEqual(metrics.monthly.loc[metrics.monthly.month.eq("2026-01"), "unique_incidents"].item(), 0)
        march = metrics.monthly.set_index("month").loc["2026-03"]
        self.assertEqual((march.positive, march.zero, march.null_unknown), (1, 0, 1))

    def test_monthly_mixed_amount_classification(self):
        rows = [(1, "F1", 0, "Утверждён", "2026-03-01"), (1, "F2", 100, "Утверждён", "2026-03-02"),
                (2, "F3", None, "Утверждён", "2026-03-01"), (2, "F4", 0, "Утверждён", "2026-03-02")]
        data, _, _ = prepared(frame(rows))
        classes = data.monthly_incident_df.set_index("incdnt_id").amount_class.to_dict()
        self.assertEqual(classes, {1: "positive", 2: "zero"})

    def test_top_dimensions_denominator_and_limit(self):
        raw = frame([(i, f"F{i}", i, "Утверждён", "2026-03-01") for i in range(1, 16)])
        raw.org_struct_lvl_3_name = raw.incdnt_sid
        raw.risk_profile_id = raw.incdnt_sid
        data, metrics, _ = prepared(raw)
        self.assertEqual(len(metrics.top_org), 10)
        self.assertEqual(len(metrics.top_risk), 10)
        self.assertEqual(metrics.top_risk.iloc[0].risk_profile_id, "EVE-15")
        self.assertEqual(metrics.top_org.iloc[0].share, 15 / 120)

    def test_zero_total_top_share(self):
        raw = frame([(1, "F1", 0, "Утверждён", "2026-03-01"), (2, "F2", None, "Утверждён", "2026-03-01")])
        _, metrics, _ = prepared(raw)
        self.assertTrue(metrics.top_org.share.eq(0).all())

    def test_statistics_are_not_truncated_at_100000(self):
        raw = pd.concat([frame().iloc[[0]]] * 100_005, ignore_index=True)
        raw["incdnt_id"] = range(100_005)
        raw["fin_impact_sid"] = raw.incdnt_id.astype(str)
        data = DirectLossStrategy().prepare(raw)
        metrics = calculate_metrics(data, request())
        self.assertEqual(metrics.unique_incidents, 100_005)
        self.assertEqual(metrics.approved_loss, 10_000_500)


class PartialMonthTests(unittest.TestCase):
    def partial_flag(self, date_range, event_date):
        _, metrics, _ = prepared(frame([(1, "F1", 100, "Утверждён", event_date)]), request(date_range=date_range))
        return bool(metrics.monthly.iloc[0].is_partial_month)

    def test_full_month_and_two_day_left_tolerance(self):
        self.assertFalse(self.partial_flag("2026-01-01:2026-01-31", "2026-01-15"))
        self.assertFalse(self.partial_flag("2026-01-03:2026-01-31", "2026-01-15"))

    def test_left_boundary_beyond_tolerance_is_partial(self):
        self.assertTrue(self.partial_flag("2026-01-04:2026-01-31", "2026-01-15"))

    def test_two_day_right_tolerance(self):
        self.assertFalse(self.partial_flag("2026-04-01:2026-04-28", "2026-04-15"))
        self.assertTrue(self.partial_flag("2026-04-01:2026-04-27", "2026-04-15"))

    def test_single_month_is_partial_if_either_boundary_is_partial(self):
        self.assertTrue(self.partial_flag("2026-01-15:2026-01-31", "2026-01-20"))

    def test_partial_full_pair_has_no_mom_or_concentration_shift(self):
        raw = concentration_frame({"2026-01": {"A": 1000, "B": 0}, "2026-02": {"A": 1, "B": 999}})
        _, metrics, events = prepared(raw, request(date_range="2026-01-04:2026-02-28"))
        self.assertTrue(metrics.monthly.set_index("month").loc["2026-01", "is_partial_month"])
        forbidden = {"month_change", "share_shift", "concentration_shift", "new_concentration", "concentration_drop", "leader_change"}
        self.assertFalse(any(event.kind in forbidden and event.selector.get("months") == ["2026-01", "2026-02"] for event in events))

    def test_partial_month_does_not_enter_monthly_iqr(self):
        raw = frame([(index, f"F{index}", loss, "Утверждён", f"2026-{index:02}-15")
                     for index, loss in enumerate([1_000_000, 10, 11, 12, 13, 1000], 1)])
        _, metrics, events = prepared(raw, request(date_range="2026-01-04:2026-06-30"))
        outlier_months = {event.selector["months"][0] for event in events if event.kind == "monthly_outlier" and event.facts["metric"] == "direct_loss_rub"}
        self.assertIn("2026-06", outlier_months)
        self.assertNotIn("2026-01", outlier_months)
        self.assertEqual(metrics.monthly.is_partial_month.tolist(), [True, False, False, False, False, False])

    def test_report_marks_partial_month_without_extra_section(self):
        data, metrics, events = prepared(frame([(1, "F1", 100, "Утверждён", "2026-01-15")]),
                                         request(date_range="2026-01-04:2026-01-31"))
        from analysis_mode.report import render_report
        result = render_report(request(date_range="2026-01-04:2026-01-31"), data, metrics, events, "Три гипотезы")
        self.assertIn("2026-01*", result)
        self.assertIn("Неполный месяц", result)
        self.assertEqual(len(re.findall(r"^### ", result, re.M)), 3)


class AnalyticalSignalTests(unittest.TestCase):
    def test_monthly_average_and_empty_month(self):
        raw = frame([(1, "F1", 100, "Утверждён", "2026-03-01"),
                     (2, "F2", 300, "Утверждён", "2026-03-02")])
        _, metrics, _ = prepared(raw)
        months = metrics.monthly.set_index("month")
        self.assertEqual(months.loc["2026-03", "average_loss_per_incident"], 200)
        self.assertEqual(months.loc["2026-02", "average_loss_per_incident"], 0)

    def divergence(self, first_count, first_total, second_count, second_total):
        rows = []
        key = 1
        for month, count, total in (("2026-03", first_count, first_total), ("2026-04", second_count, second_total)):
            for _ in range(count):
                rows.append((key, f"F{key}", total / count, "Утверждён", f"{month}-15")); key += 1
        return prepared(frame(rows), request(date_range="2026-03-01:2026-04-30"))[2]

    def test_frequency_up_loss_down_and_reverse(self):
        event = next(e for e in self.divergence(100, 100_000_000, 200, 70_000_000)
                     if e.kind == "frequency_severity_divergence")
        self.assertEqual(event.facts["incident_count_change_pct"], 1)
        self.assertAlmostEqual(event.facts["loss_change_pct"], -.3)
        self.assertLess(event.facts["average_loss_change_pct"], 0)
        reverse = next(e for e in self.divergence(200, 100_000_000, 100, 180_000_000)
                       if e.kind == "frequency_severity_divergence")
        self.assertLess(reverse.facts["incident_count_change_pct"], 0)
        self.assertGreater(reverse.facts["loss_change_pct"], 0)
        self.assertGreater(reverse.facts["average_loss_change_pct"], 0)

    def test_share_disproportion_both_directions_and_suppression(self):
        rows = []
        for key in range(1, 9):
            rows.append((key, f"F{key}", 65_000_000 / 8, "Утверждён", "2026-03-15"))
        for key in range(9, 101):
            rows.append((key, f"F{key}", 35_000_000 / 92, "Утверждён", "2026-03-15"))
        raw = frame(rows)
        raw.loc[:7, "org_struct_lvl_3_name"] = "A"
        raw.loc[8:, "org_struct_lvl_3_name"] = "B"
        data, metrics, events = prepared(raw, request(date_range="2026-03-01:2026-03-31"))
        org = [e for e in events if e.kind == "share_disproportion" and e.dimension == "org"]
        self.assertTrue(any(e.facts["gap_pp"] >= SHARE_DISPROPORTION_MIN_PP for e in org))
        self.assertTrue(any(e.facts["gap_pp"] <= -SHARE_DISPROPORTION_MIN_PP for e in org))
        selected = report_events(events, 10)
        disproportion_categories = {(e.dimension, e.category) for e in selected if e.kind == "share_disproportion"}
        self.assertFalse(any(e.kind == "concentration" and (e.dimension, e.category) in disproportion_categories for e in selected))

    def test_persistent_concentration_and_trend_respect_partial_month(self):
        _, _, persistent = prepared(concentration_frame({
            "2026-01": {"A": 55, "B": 45}, "2026-02": {"A": 58, "B": 42}, "2026-03": {"A": 61, "B": 39}}),
            request(date_range="2026-01-01:2026-03-31"))
        self.assertTrue(any(e.kind == "persistent_concentration" and e.dimension == "process" and e.category == "Процесс A" for e in persistent))
        _, _, trend = prepared(concentration_frame({
            "2026-01": {"A": 30, "B": 70}, "2026-02": {"A": 43, "B": 57}, "2026-03": {"A": 59, "B": 41}}),
            request(date_range="2026-01-01:2026-03-31"))
        self.assertTrue(any(e.kind == "multi_month_trend" and e.dimension == "process" and e.category == "Процесс A" for e in trend))
        _, _, partial = prepared(concentration_frame({
            "2026-01": {"A": 30, "B": 70}, "2026-02": {"A": 43, "B": 57}, "2026-03": {"A": 59, "B": 41}}),
            request(date_range="2026-01-01:2026-03-27"))
        self.assertFalse(any(e.kind == "multi_month_trend" and e.category == "Процесс A" for e in partial))

    def test_report_event_month_diversity_is_soft(self):
        events = []
        for index, month in enumerate(["2026-05"] * 5 + ["2026-06"] * 2 + ["2026-04"] * 2):
            events.append(AnomalyEvent(f"signal-{index}", f"kind-{index}", 100 - index,
                                       f"Сигнал {index}", {"x": index}, {"months": [month]}, month=month))
        selected = report_events(events, 8)
        counts = pd.Series([event.month for event in selected]).value_counts()
        self.assertLessEqual(counts["2026-05"], 4)
        self.assertEqual(set(counts.index), {"2026-04", "2026-05", "2026-06"})


class AnomalyTests(unittest.TestCase):
    def test_zero_to_positive_shift_and_evidence_diversity(self):
        raw = frame([(i, f"F{i}-m", 0, "Утверждён", "2026-03-01") for i in range(1, 21)]
                    + [(i, f"F{i}-a", 100, "Утверждён", "2026-04-01") for i in range(1, 21)])
        data, metrics, events = prepared(raw)
        shifts = [e for e in events if e.kind == "share_shift" and e.facts["metric"] == "positive_share"]
        self.assertTrue(any(e.facts["before"] == 0 and e.facts["after"] == 1 for e in shifts))
        pack = build_evidence_pack(data, metrics, events)
        self.assertTrue(any(e.get("dimension") == "process" for e in pack["events"]))
        self.assertTrue(any(e["kind"] == "share_shift" for e in pack["events"]))
        self.assertTrue(all(row["description"] for row in pack["incidents"]))
        self.assertLessEqual(len(pack["incidents"]), 24)
        self.assertLessEqual(len(report_events(events)), 8)

    def test_iqr_large_incident_and_no_claim_for_small_sample(self):
        rows = [(i, f"F{i}", amount, "Утверждён", "2026-03-01") for i, amount in enumerate([1, 2, 3, 4, 5, 6, 7, 1000], 1)]
        _, _, events = prepared(frame(rows))
        self.assertTrue(any(e.kind == "monetary_outlier" and e.selector["incdnt_id"] == 8 for e in events))
        _, _, small = prepared(frame(rows[:3]))
        self.assertFalse(any("outlier" in e.kind for e in small))

    def test_temporal_iqr_and_previous_zero(self):
        raw = frame([(i, f"F{i}", amount, "Утверждён", f"2026-{i:02}-01") for i, amount in enumerate([0, 1, 2, 3, 4, 1000], 1)])
        _, _, events = prepared(raw)
        self.assertTrue(any(e.kind == "monthly_outlier" for e in events))
        self.assertTrue(any("после нуля" in e.description for e in events))
        self.assertFalse(any("infinity" in e.description.lower() for e in events))

    def test_unapproved_incidents_absent_from_all_evidence(self):
        data, metrics, events = prepared()
        pack = build_evidence_pack(data, metrics, events)
        self.assertTrue({r["incdnt_id"] for r in pack["incidents"]} <= {1, 2, 3})
        self.assertNotIn("EVE-4", json.dumps(pack))

    def test_event_driven_evidence_reaches_sixty_without_monopoly(self):
        raw = frame([(key, f"F{key}", key * 10, "Утверждён", "2026-03-15") for key in range(1, 121)])
        raw["org_struct_lvl_3_name"] = [f"Группа {(key - 1) // 12}" for key in range(1, 121)]
        data, metrics, _ = prepared(raw, request(date_range="2026-03-01:2026-03-31"))
        events = [AnomalyEvent(f"signal-{index}", "share_disproportion", 100 - index, f"Группа {index}", {},
                               {"org_struct_lvl_3_name": f"Группа {index}"}, dimension="org", category=f"Группа {index}")
                  for index in range(10)]
        pack = build_evidence_pack(data, metrics, events, request(date_range="2026-03-01:2026-03-31"))
        self.assertEqual(len(pack["incidents"]), 60)
        self.assertEqual(len({row["incdnt_id"] for row in pack["incidents"]}), 60)
        counts = {}
        for row in pack["incidents"]:
            for event_id in row["selected_for"]:
                counts[event_id] = counts.get(event_id, 0) + 1
        self.assertTrue(counts)
        self.assertLessEqual(max(counts.values()), 6)


class MonthlyConcentrationTests(unittest.TestCase):
    SHIFT_KINDS = {"concentration_shift", "new_concentration", "concentration_drop", "leader_change"}

    def events_for(self, specs, date_range="2026-03-01:2026-04-30"):
        return prepared(concentration_frame(specs), request(date_range=date_range))

    def test_growth_is_calculated_for_all_three_dimensions(self):
        _, metrics, events = self.events_for({"2026-03": {"A": 10, "B": 90}, "2026-04": {"A": 60, "B": 40}})
        shifts = [event for event in events if event.kind == "concentration_shift" and event.category and event.category.endswith("A")]
        self.assertEqual({event.dimension for event in shifts}, {"org", "risk", "process"})
        self.assertTrue(all(event.facts["delta_percentage_points"] == 50 for event in shifts))
        self.assertEqual(CONCENTRATION_SHIFT_MIN_PP, 20.0)
        self.assertEqual(SIGNIFICANT_CONCENTRATION_SHARE, 0.30)
        process = metrics.monthly_concentrations["process"]
        self.assertTrue({"unique_incidents", "direct_loss_sum", "share_of_month_loss", "share_of_month_incidents"} <= set(process.columns))

    def test_new_concentration_has_semantic_event_without_infinity(self):
        _, _, events = self.events_for({"2026-03": {"A": 100}, "2026-04": {"A": 55, "C": 45}})
        new_events = [event for event in events if event.kind == "new_concentration" and event.dimension == "process" and event.category == "Процесс C"]
        self.assertEqual(len(new_events), 1)
        self.assertEqual(new_events[0].facts["previous_share"], 0)
        self.assertNotIn("inf", new_events[0].description.lower())

    def test_drop_is_detected(self):
        _, _, events = self.events_for({"2026-03": {"A": 70, "B": 30}, "2026-04": {"A": 25, "B": 75}})
        drops = [event for event in events if event.kind == "concentration_drop" and event.dimension == "org" and event.category == "ТБ A"]
        self.assertEqual(len(drops), 1)
        self.assertEqual(drops[0].facts["delta_percentage_points"], -45)

    def test_meaningful_leader_change_is_detected(self):
        _, _, events = self.events_for({"2026-03": {"A": 65, "B": 35}, "2026-04": {"A": 40, "B": 60}})
        leaders = [event for event in events if event.kind == "leader_change" and event.dimension == "risk"]
        self.assertEqual(len(leaders), 1)
        self.assertEqual(leaders[0].facts["previous_leader"], "DRP-A / Риск A")
        self.assertEqual(leaders[0].facts["current_leader"], "DRP-B / Риск B")

    def test_small_share_noise_is_ignored(self):
        _, _, events = self.events_for({"2026-03": {"A": 20, "B": 21, "C": 59},
                                        "2026-04": {"A": 22, "B": 20, "C": 58}})
        self.assertFalse(any(event.kind in self.SHIFT_KINDS for event in events))

    def test_partial_month_shift_is_ignored(self):
        _, _, events = self.events_for({"2026-03": {"A": 10, "B": 90}, "2026-04": {"A": 60, "B": 40}},
                                       "2026-03-04:2026-04-30")
        self.assertFalse(any(event.kind in self.SHIFT_KINDS for event in events))

    def test_zero_loss_month_has_finite_zero_shares(self):
        _, metrics, events = self.events_for({"2026-03": {"A": 0, "B": 0}, "2026-04": {"A": 60, "B": 40}})
        for result in metrics.monthly_concentrations.values():
            march = result.loc[result.month.eq("2026-03")]
            self.assertTrue(march.share_of_month_loss.eq(0).all())
            self.assertTrue(pd.notna(result[["share_of_month_loss", "share_of_month_incidents"]]).all().all())
        self.assertFalse(any("inf" in event.description.lower() or "nan" in event.description.lower() for event in events))

    def test_report_ranking_keeps_dimension_and_signal_diversity(self):
        _, _, events = self.events_for({"2026-03": {"A": 10, "B": 90}, "2026-04": {"A": 60, "B": 40}})
        selected = report_events(events)
        monthly = [event for event in selected if event.kind in self.SHIFT_KINDS]
        self.assertLessEqual(len(monthly), 3)
        self.assertEqual(len({(event.dimension, event.month) for event in monthly}), len(monthly))
        self.assertFalse(any(event.kind == "concentration" for event in selected))

    def test_shift_event_is_bound_to_real_evidence(self):
        data, metrics, events = self.events_for({"2026-03": {"A": 10, "B": 90}, "2026-04": {"A": 60, "B": 40}})
        event = next(event for event in events if event.kind == "concentration_shift" and event.dimension == "process" and event.category == "Процесс A")
        pack = build_evidence_pack(data, metrics, [event])
        packed = pack["events"][0]
        self.assertEqual(packed["facts"]["previous_share"], 0.1)
        self.assertEqual(packed["facts"]["current_share"], 0.6)
        self.assertTrue(packed["evidence_keys"])
        evidence = {str(row["incdnt_id"]): row for row in pack["incidents"]}
        self.assertTrue(all(evidence[key]["process_lvl_4_name"] == "Процесс A" for key in packed["evidence_keys"]))
        self.assertTrue(all(evidence[key]["description"] for key in packed["evidence_keys"]))


class HypothesisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        data, metrics, events = prepared()
        self.pack = build_evidence_pack(data, metrics, events)

    async def test_valid_response_and_retry(self):
        ask = Mock(side_effect=["Краткая выжимка", "invalid", valid_response(self.pack)])
        result = await generate_hypotheses(self.pack, ask)
        self.assertEqual(ask.call_count, 3)
        self.assertEqual(result.count("**Гипотеза "), 3)
        self.assertIn("ПРЕДЫДУЩИЙ ОТВЕТ НЕ ПРОШЁЛ ПРОВЕРКУ", ask.call_args.args[0][-1]["content"])
        self.assertIn("получены гипотезы", ask.call_args.args[0][-1]["content"])

    async def test_two_invalid_responses_and_unavailable_fallback(self):
        for ask in (Mock(return_value="invalid"), Mock(side_effect=RuntimeError("offline"))):
            result = await generate_hypotheses(self.pack, ask)
            self.assertEqual(result.count("**Гипотеза "), 3)
            self.assertEqual(result.count("**Ожидаемый результат:**"), 3)
            self.assertIn("EVE-", result)
            self.assertTrue(result.startswith("### 3. Гипотезы"))
        self.assertEqual(ask.call_count, 2)

    def test_markdown_needs_no_json_or_internal_ids(self):
        response = valid_response(self.pack)
        accepted, errors = validate_response(response, self.pack)
        self.assertEqual(errors, [])
        self.assertEqual(accepted, response)
        self.assertNotIn("signal_ids", response)
        self.assertNotIn("evidence_ids", response)

    def test_unknown_eve_and_extra_section_are_rejected(self):
        for response, expected in (
            (valid_response(self.pack).replace("EVE-1", "EVE-9999999"), "EVE-9999999"),
            (valid_response(self.pack) + "\n\n### 4. Вывод", "только заголовок"),
        ):
            accepted, errors = validate_response(response, self.pack)
            self.assertIsNone(accepted)
            self.assertIn(expected, " ".join(errors))

    async def test_no_supported_signal_still_has_cautious_real_cases(self):
        data, metrics, _ = prepared()
        pack = build_evidence_pack(data, metrics, [])
        result = await generate_hypotheses(pack, Mock(return_value="invalid"))
        self.assertEqual(result.count("**Гипотеза "), 3)
        self.assertIn("EVE-1", result)

    async def test_second_invalid_response_retries_exactly_once(self):
        ask = Mock(return_value="invalid")
        await generate_hypotheses(self.pack, ask)
        self.assertEqual(ask.call_count, 3)

    async def test_unknown_eve_retries_and_accepts_corrected_markdown(self):
        invalid = valid_response(self.pack).replace("EVE-1", "EVE-9999999")
        ask = Mock(side_effect=["Краткая выжимка", invalid, valid_response(self.pack)])
        with self.assertLogs("analysis_mode.hypotheses", level="INFO") as logs:
            result = await generate_hypotheses(self.pack, ask)
        self.assertEqual(ask.call_count, 3)
        self.assertEqual(result.count("**Гипотеза "), 3)
        self.assertIn("EVE-9999999", ask.call_args.args[0][-1]["content"])
        self.assertIn("hypotheses_source=qwen", "\n".join(logs.output))

    async def test_qwen_source_is_logged_after_retry(self):
        ask = Mock(side_effect=["Краткая выжимка", "invalid", valid_response(self.pack)])
        with self.assertLogs("analysis_mode.hypotheses", level="INFO") as logs:
            await generate_hypotheses(self.pack, ask)
        text = "\n".join(logs.output)
        self.assertIn("reason=ответ должен содержать", text)
        self.assertIn("hypotheses_source=qwen", text)

    def test_two_hypotheses_and_short_steps_are_rejected(self):
        response = valid_response(self.pack)
        third = response.index("**Гипотеза 3:")
        self.assertIsNone(validate_response(response[:third], self.pack)[0])
        response = response.replace("  2. Проверить документы и применявшиеся процедуры контроля.\n", "", 1)
        parsed, errors = validate_response(response, self.pack)
        self.assertIsNone(parsed)
        self.assertIn("минимум два", " ".join(errors))

    def test_compact_prompt_with_forty_long_descriptions(self):
        raw = frame([(i, f"F{i}", i * 100, "Утверждён", "2026-03-01") for i in range(1, 41)])
        raw["incdnt_summary_descr_txt"] = "Длинное описание " * 500
        raw["incdnt_full_descr_txt"] = "Дополнение " * 500
        data, metrics, _ = prepared(raw)
        events = [AnomalyEvent(f"signal-{i}", "case_observation", 10, f"Случай EVE-{i}",
                               {"amount": i * 100}, {"incdnt_id": i}) for i in range(1, 41)]
        pack = build_evidence_pack(data, metrics, events, request())
        messages = build_hypothesis_messages(pack)
        self.assertLessEqual(len(pack["incidents"]), 60)
        self.assertTrue(all(len(row["description"]) <= MAX_DESCRIPTION_CHARS for row in pack["incidents"]))
        self.assertLess(sum(len(message["content"]) for message in messages), 30_000)
        self.assertLessEqual(sum(len(message["content"]) for message in messages), MAX_FINAL_PROMPT_CHARS)

    async def test_prompt_budget_trims_and_still_calls_qwen(self):
        huge = dict(self.pack)
        huge["events"] = [dict(self.pack["events"][0], description="Факт " * 20_000) for _ in range(100)]
        huge["incidents"] = [dict(self.pack["incidents"][0], description="Описание " * 20_000) for _ in range(100)]
        ask = Mock(return_value=valid_response(self.pack))
        await generate_hypotheses(huge, ask)
        prompt_chars = sum(len(message["content"]) for message in ask.call_args.args[0])
        self.assertLessEqual(prompt_chars, MAX_FINAL_PROMPT_CHARS)
        self.assertEqual(ask.call_count, 11)

    def expanded_pack(self, count):
        pack = dict(self.pack)
        template = self.pack["incidents"][0]
        pack["incidents"] = [dict(template, incdnt_id=index, incdnt_sid=f"EVE-{index}")
                             for index in range(1, count + 1)]
        pack["available_incidents"] = count
        return pack

    async def test_batch_sizes_for_sixty_and_twenty_three(self):
        for count, expected in ((60, [10] * 6), (23, [10, 10, 3])):
            ask = Mock(return_value="Краткая аналитическая выжимка")
            summaries = await summarize_evidence_batches(self.expanded_pack(count), ask)
            sizes = [call.args[0][1]["content"].count("\nEVE-") + int(call.args[0][1]["content"].startswith("EVE-"))
                     for call in ask.call_args_list]
            # Batch prompt has a preface, so every incident starts after a newline.
            self.assertEqual(sizes, expected)
            self.assertEqual(len(summaries), len(expected))

    async def test_one_batch_failure_does_not_block_final_call(self):
        pack = self.expanded_pack(23)
        calls = []
        def ask(messages, max_tokens):
            calls.append(max_tokens)
            if max_tokens == 1800 and calls.count(1800) == 2:
                raise TimeoutError("batch timeout")
            return "Краткая выжимка" if max_tokens == 1800 else valid_response(pack)
        with self.assertLogs("analysis_mode.hypotheses", level="INFO") as logs:
            result = await generate_hypotheses(pack, ask)
        self.assertEqual(result.count("**Гипотеза "), 3)
        self.assertEqual(calls.count(1800), 3)
        self.assertEqual(calls.count(8192), 1)
        self.assertIn("failed, continuing", "\n".join(logs.output))

    async def test_final_prompt_uses_summaries_not_all_raw_descriptions(self):
        pack = self.expanded_pack(60)
        for row in pack["incidents"]:
            row["description"] = "СЫРОЕ_ОПИСАНИЕ " * 100
        prompts = []
        def ask(messages, max_tokens):
            prompts.append((max_tokens, messages[1]["content"]))
            return "Выжимка повторяющихся обстоятельств" if max_tokens == 1800 else valid_response(pack)
        await generate_hypotheses(pack, ask)
        final_prompt = next(prompt for tokens, prompt in prompts if tokens == 8192)
        self.assertIn("АНАЛИТИЧЕСКИЕ ВЫЖИМКИ", final_prompt)
        self.assertIn("ОСНОВНЫЕ НАБЛЮДЕНИЯ", final_prompt)
        self.assertLessEqual(len(final_prompt) + 1000, MAX_FINAL_PROMPT_CHARS)
        self.assertLess(final_prompt.count("СЫРОЕ_ОПИСАНИЕ"), 60 * 10)


class HumanReportTests(unittest.TestCase):
    def test_business_labels_and_three_column_monthly_table(self):
        from analysis_mode.report import render_report
        data, metrics, events = prepared()
        result = render_report(request(), data, metrics, events, "Гипотезы")
        required = [
            "Общая сумма прямых потерь по утверждённым ИОР",
            "Распределение количества ИОР по статусам",
            "Дальнейший анализ проводится только по утверждённым ИОР.",
            "Топ-10 ТБ / оргструктур по сумме прямых потерь",
            "Топ-10 ЦПР по сумме прямых потерь",
            "| Месяц | Количество ИОР | Прямые потери, руб. |",
            "Средняя потеря на ИОР, руб.",
        ]
        for phrase in required:
            self.assertIn(phrase, result)
        forbidden = ["approved", "business group", "Доля approved суммы", "Positive",
                     "Доля positive", "Доля zero", "Только NULL", "Реальный ноль",
                     "IQR", "Q1", "Q3", "связанных ИОР"]
        for phrase in forbidden:
            self.assertNotIn(phrase.casefold(), result.casefold())
        self.assertEqual(len(re.findall(r"^### ", result, re.M)), 3)


class RunnerTests(unittest.IsolatedAsyncioTestCase):
    def store(self, raw):
        return SimpleNamespace(tables={"ior": "main", "financial_impact": "fin"}, query_sql=Mock(return_value=raw))

    async def test_export_false_no_files_and_exact_sections(self):
        with tempfile.TemporaryDirectory() as directory:
            result = await run_analysis_mode(request(), self.store(frame()), ask=Mock(return_value="invalid"), output_dir=Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])
        self.assertEqual(re.findall(r"^### .*", result, re.M), ["### 1. Общая статистика", "### 2. Временной анализ и аномалии", "### 3. Гипотезы"])
        self.assertNotIn("TOP-10 процесс", result)
        self.assertNotIn("Гипотеза 1", result.split("### 3. Гипотезы")[0])

    async def test_export_true_all_statuses_and_detail(self):
        with tempfile.TemporaryDirectory() as directory:
            result = await run_analysis_mode(request(export_excel=True), self.store(frame()), ask=Mock(return_value="invalid"), output_dir=Path(directory))
            files = list(Path(directory).iterdir())
            self.assertEqual(len(files), 1)
            self.assertEqual(files[0].suffix, ".xlsx")
            exported = pd.read_excel(files[0])
            self.assertEqual(len(exported), 7)
            self.assertEqual(len(exported.loc[exported.incdnt_id.eq(1)]), 3)
            self.assertEqual(exported.incdnt_status_name.nunique(), 5)
            self.assertIn(files[0].name, result)

    async def test_empty_no_qwen_no_export(self):
        ask = Mock(side_effect=AssertionError("Qwen"))
        with tempfile.TemporaryDirectory() as directory:
            result = await run_analysis_mode(request(export_excel=True), self.store(pd.DataFrame()), ask=ask, output_dir=Path(directory))
            self.assertIn("не найдено", result)
            self.assertNotIn("###", result)
            self.assertEqual(list(Path(directory).iterdir()), [])
            ask.assert_not_called()

    async def test_no_approved_full_export_without_qwen(self):
        ask = Mock(side_effect=AssertionError("Qwen"))
        raw = frame([(1, "F1", 100, "Удалён", "2026-03-01")])
        with tempfile.TemporaryDirectory() as directory:
            result = await run_analysis_mode(request(export_excel=True), self.store(raw), ask=ask, output_dir=Path(directory))
            self.assertIn("Количество уникальных ИОР: 1", result)
            self.assertIn("по утверждённым ИОР: 0.00", result)
            self.assertEqual(result.count("в выборке нет утверждённых ИОР"), 2)
            self.assertEqual(len(list(Path(directory).glob("*.xlsx"))), 1)
            ask.assert_not_called()

    async def test_store_failure_is_not_empty_report(self):
        store = self.store(frame())
        store.query_sql.side_effect = RuntimeError("DB offline")
        with self.assertRaisesRegex(RuntimeError, "DB offline"):
            await run_analysis_mode(request(), store)

    async def test_all_null_approved_population(self):
        raw = frame([(1, "F1", None, "Утверждён", "2026-03-01")])
        result = await run_analysis_mode(request(), self.store(raw), ask=Mock(return_value="invalid"))
        self.assertEqual(result.count("**Гипотеза "), 3)
        self.assertNotIn("в выборке нет утверждённых ИОР", result)


class ExportAndCLITests(unittest.TestCase):
    def test_export_partitions_without_losing_detail_and_preserves_text(self):
        from analysis_mode.export import export_details
        raw = frame()
        raw["incdnt_full_descr_txt"] = "=SUM(A1:A2)"
        data = DirectLossStrategy().prepare(raw)
        with tempfile.TemporaryDirectory() as directory, patch("analysis_mode.export.EXCEL_ROWS_PER_SHEET", 3):
            path = export_details(data.detail_df, Path(directory))
            import openpyxl
            workbook = openpyxl.load_workbook(path)
            self.assertEqual(len(workbook.worksheets), 3)
            self.assertEqual(sum(ws.max_row - 1 for ws in workbook.worksheets), 7)
            self.assertFalse(any(cell.data_type == "f" for ws in workbook for row in ws for cell in row))
            workbook.close()

    def test_cli_json_contract_both_export_options(self):
        import cli
        import ior_reports
        store = SimpleNamespace(tables={"ior": "main", "financial_impact": "fin"}, query_sql=Mock(return_value=frame()))
        for export in (False, True):
            with self.subTest(export=export), tempfile.TemporaryDirectory() as directory, \
                 patch.object(sys, "argv", ["cli.py", "--prompt", json.dumps(BASE | {"export_excel": export}, ensure_ascii=False)]), \
                 patch.object(ior_reports, "get_data_store", return_value=store), \
                 patch("analysis_mode.export.GENERATED_FILES", Path(directory)), \
                 patch("analysis_mode.runner.generate_hypotheses", new_callable=AsyncMock, return_value="Тестовые гипотезы"), \
                 contextlib.redirect_stdout(io.StringIO()) as output:
                cli.main()
                self.assertIn("### 3. Гипотезы", output.getvalue())
                self.assertEqual(len(list(Path(directory).glob("*.xlsx"))), int(export))
                self.assertEqual(list(Path(directory).glob("*.csv")), [])

    def test_cli_validation_error_is_readable_and_precedes_store(self):
        import cli
        import ior_reports
        with patch.object(sys, "argv", ["cli.py", "--prompt", '{"action":"Анализ"}']), \
             patch.object(ior_reports, "get_data_store", side_effect=AssertionError("store")), \
             contextlib.redirect_stderr(io.StringIO()) as error, self.assertRaises(SystemExit) as raised:
            cli.main()
        self.assertEqual(raised.exception.code, 2)
        self.assertIn("money_filter", error.getvalue())


if __name__ == "__main__":
    unittest.main()
