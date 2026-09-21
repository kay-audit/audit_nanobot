"""Synthetic unit tests предметной аналитики без HDFS и внешних сервисов."""
from __future__ import annotations

import unittest
import importlib.util
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pandas as pd

from preset_analysis.common import calculate_unique_status_groups, filter_approved_incidents, sanitize_generated_text
from preset_analysis.registry import get_analyzer
from preset_analysis.financial_consequences_analysis import prepare_financial_consequences_views
from preset_analysis.nonfinancial_consequences_analysis import prepare_nonfinancial_views
from preset_analysis.report_period_specific_ior_analysis import prepare_dossier_views
from ior_hypothesis import (
    build_analysis_context_text, check_hypotheses_completeness,
    generate_hypothesis_narrative,
)
from ior_reports import (
    FINANCIAL_RENAME, IOR_FULL_SQL_QUERIES, apply_preset_column_filter,
    build_dynamic_sql_from_prompt, build_preset_sql_queries, detect_preset_from_prompt,
    format_excel_inspection_markdown, resolve_preset_for_request, run_ior_report,
)
from utils.data_store import DUCKDB_TABLES, GREENPLUM_TABLES

_QUERY_SPEC_PATH = Path(__file__).resolve().parents[1] / "utils" / "query_spec.py"
_QUERY_SPEC_SPEC = importlib.util.spec_from_file_location("ior_analyzer_query_spec_contract", _QUERY_SPEC_PATH)
_QUERY_SPEC_MODULE = importlib.util.module_from_spec(_QUERY_SPEC_SPEC)
sys.modules[_QUERY_SPEC_SPEC.name] = _QUERY_SPEC_MODULE
_QUERY_SPEC_SPEC.loader.exec_module(_QUERY_SPEC_MODULE)
CompileResult = _QUERY_SPEC_MODULE.CompileResult
CompileContext = _QUERY_SPEC_MODULE.CompileContext
compile_query_spec = _QUERY_SPEC_MODULE.compile_query_spec

_QUERY_TOOL_PATH = Path(__file__).resolve().parents[1] / "utils" / "tools" / "query_spec_tool.py"
_QUERY_TOOL_SPEC = importlib.util.spec_from_file_location("ior_analyzer_query_spec_tool_contract", _QUERY_TOOL_PATH)
_QUERY_TOOL_MODULE = importlib.util.module_from_spec(_QUERY_TOOL_SPEC)
sys.modules[_QUERY_TOOL_SPEC.name] = _QUERY_TOOL_MODULE
_QUERY_TOOL_SPEC.loader.exec_module(_QUERY_TOOL_MODULE)
run_query_spec = _QUERY_TOOL_MODULE.run_query_spec


class _Column:
    def __init__(self, name):
        self.name = name
        self.filled_pct = 100.0


class _Table:
    def __init__(self, columns, foreign_keys=None):
        self.columns = [_Column(c) for c in columns]
        self.foreign_keys = foreign_keys or []

    def column_names(self):
        return [c.name for c in self.columns]


class _Schema:
    def __init__(self):
        self.tables = {
            "d6_base_of_knowledge_ior": _Table([
                "incdnt_id", "incdnt_sid", "incdnt_status_name", "process_lvl_4_name",
                "org_struct_lvl_3_name", "incdnt_source_name", "incdnt_entry_dt",
                "incdnt_sum", "recovery_rub_amt_aggr",
            ]),
            "d6_base_of_knowledge_incident_fin_impact": _Table(
                ["incdnt_id", "fin_impact_rub_amt", "fin_impact_type_name"],
                [{"column": "incdnt_id", "references": "d6_base_of_knowledge_ior.incdnt_id"}],
            ),
        }

    def get(self, name):
        return self.tables.get(name)

    def table_names(self):
        return list(self.tables)


class _State:
    def __init__(self, main, fin):
        self.main = main
        self.fin = fin
        self.dataframes = {}
        self.dataframe_meta = {}
        self.files = {}
        self.session_id = "synthetic-query-spec"
        self._counter = 0

    def register_dataframe(self, df, *args):
        self._counter += 1
        df_id = f"df-{self._counter}"
        self.dataframes[df_id] = df.copy()
        return SimpleNamespace(df_id=df_id)

    def get_df(self, df_id):
        return self.dataframes[df_id]


class _Registry:
    async def execute(self, name, args, state):
        if name == "query":
            source = state.fin if "fin_impact" in args["table"] else state.main
            columns = args.get("columns")
            df = source[[c for c in columns if c in source.columns]].copy() if columns else source.copy()
            return SimpleNamespace(ok=True, output={"df_id": state.register_dataframe(df).df_id}, error=None)
        if name == "join_dfs":
            df = state.get_df(args["left_df"]).merge(state.get_df(args["right_df"]), on=args["on"], how=args["how"])
            return SimpleNamespace(ok=True, output={"df_id": state.register_dataframe(df).df_id}, error=None)
        if name == "group_by":
            df = state.get_df(args["df_id"])
            grouped = df.groupby(args["by"], dropna=False).agg(args["agg"]).reset_index()
            return SimpleNamespace(ok=True, output={"df_id": state.register_dataframe(grouped).df_id}, error=None)
        if name in ("export_excel", "export_csv"):
            return SimpleNamespace(ok=True, output={"file_id": "synthetic.xlsx"}, error=None)
        raise AssertionError(name)


def _query_frames():
    main = pd.DataFrame({
        "incdnt_id": [1, 2, 3], "incdnt_sid": ["EVE-1", "EVE-2", "EVE-3"],
        "incdnt_status_name": ["Утверждён"] * 3,
        "process_lvl_4_name": ["A", "A", "B"],
        "org_struct_lvl_3_name": ["Московский банк"] * 3,
        "incdnt_source_name": ["Мониторинг"] * 3,
        "incdnt_entry_dt": ["2026-01-01", "2026-01-02", "2026-02-01"],
        "incdnt_sum": [10.0, 20.0, 30.0],
        "recovery_rub_amt_aggr": [1.0, 2.0, 3.0],
    })
    fin = pd.DataFrame({
        "incdnt_id": [1, 2, 3], "fin_impact_rub_amt": [100.0, 2_000_000.0, 3_000_000.0],
        "fin_impact_type_name": ["Прямая потеря"] * 3,
    })
    return main, fin


def _join_spec():
    return {
        "source": {"table": "d6_base_of_knowledge_ior", "joins": [{
            "table": "d6_base_of_knowledge_incident_fin_impact", "on": "incdnt_id", "how": "left",
            "pre_aggregate": {"group_by": ["incdnt_id"], "agg": {"fin_impact_rub_amt": {"fn": "sum", "as": "direct_loss"}}},
            "select": ["direct_loss"],
        }]},
        "filters": [{"kind": "range", "column": "direct_loss", "op": "gt", "value": 1_000_000}],
        "select": ["incdnt_sid", "direct_loss"], "output": {"format": "excel"},
    }


class CommonStatusContractTests(unittest.TestCase):
    def test_only_approved_variants_are_selected(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-2", "EVE-3", "EVE-4"],
            "incdnt_status_name": ["Утверждён", "Утверждение", "Удалён", "Черновик"],
        })
        approved = filter_approved_incidents(df)
        self.assertEqual(set(approved["incdnt_sid"]), {"EVE-1", "EVE-2"})
        self.assertEqual(calculate_unique_status_groups(df), {"approved": 2, "draft": 1, "deleted": 1, "other": 0})

    def test_no_status_and_zero_approved_never_fall_back_to_all(self):
        no_status = pd.DataFrame({"incdnt_sid": ["EVE-1", "EVE-2"]})
        self.assertTrue(filter_approved_incidents(no_status).empty)
        only_other = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-2"],
            "incdnt_status_name": ["Удалён", "Черновик"],
        })
        self.assertTrue(filter_approved_incidents(only_other).empty)


class VozmesheniePresetTests(unittest.TestCase):
    def test_full_header_and_approved_analysis_use_different_scopes(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-1", "EVE-2"],
            "recovery_sid": ["EVE-1-R1", "EVE-1-R2", "EVE-2-R1"],
            "incdnt_status_name": ["Утверждён", "Утверждён", "Удалён"],
            "recovery_rub_amt": [100.0, 200.0, 900.0],
            "recovery_type_name": ["Клиент", "Страховая", "Клиент"],
        })
        bundle = get_analyzer("vozmeshenie_ior").prepare(df)
        self.assertEqual(bundle.full_metrics["total_rows"], 3)
        self.assertEqual(bundle.full_metrics["unique_incidents"], 2)
        self.assertEqual(bundle.full_metrics["total_recovery"], 1200.0)
        self.assertEqual(bundle.status_counts["approved"], 1)
        self.assertEqual(bundle.status_counts["deleted"], 1)
        self.assertEqual(bundle.approved_count, 1)
        self.assertEqual(bundle.analysis_metrics["total_recovery"], 300.0)
        breakdown = {row["label"]: row["amount"] for row in bundle.analysis_metrics["type_breakdown"]}
        self.assertEqual(breakdown, {"Страховая": 200.0, "Клиент": 100.0})
        self.assertNotIn("900.00", bundle.profile)
        report = bundle.deterministic_report().lower()
        self.assertEqual(report.count("строк"), 1)
        self.assertNotIn("net loss", report)
        self.assertNotIn("сумма потерь", report)


class FinancialPresetTests(unittest.TestCase):
    def test_fin_impact_granularity_and_no_recovery_metrics(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-1", "EVE-2"],
            "incdnt_status_name": ["Утверждён", "Утверждён", "Удалён"],
            "fin_impact_sid": ["F1", "F2", "F3"],
            "fin_impact_type_name": ["Direct", "Indirect", "Direct"],
            "fin_impact_kind_name": ["A", "B", "C"],
            "fin_impact_rub_amt": [100.0, 50.0, 1000.0],
            "incdnt_sum": [9999.0, 9999.0, 9999.0],
            "recovery_rub_amt": [777.0, 777.0, 777.0],
        })
        incidents, metrics = prepare_financial_consequences_views(df)
        self.assertEqual(len(incidents), 2)
        self.assertEqual(metrics["total_amount"], 1150.0)
        bundle = get_analyzer("financial_consequences_ior").prepare(df)
        self.assertEqual(bundle.analysis_metrics["total_amount"], 150.0)
        self.assertEqual(len(bundle.analysis_detail_df), 2)
        low = bundle.deterministic_report().lower()
        self.assertNotIn("net loss", low)
        self.assertNotIn("сумма возмещ", low)


class NonfinancialPresetTests(unittest.TestCase):
    def test_structured_kind_and_influence_only_for_approved(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-1", "EVE-2"],
            "incdnt_status_name": ["Утверждение", "Утверждение", "Удалён"],
            "nonfin_impact_sid": ["N1", "N2", "N3"],
            "nonfin_impact_kind_name": ["Жалобы", "Регулятор", "СМИ"],
            "nonfin_impact_influence_class_name": ["Высокий", "Средний", "Очень высокий"],
        })
        incidents, full = prepare_nonfinancial_views(df)
        self.assertEqual(len(incidents), 2)
        self.assertEqual(full["detail_rows"], 3)
        bundle = get_analyzer("ior_nonfinancial_consequences").prepare(df)
        self.assertEqual(bundle.approved_count, 1)
        self.assertEqual(len(bundle.analysis_detail_df), 2)
        influences = {row["label"]: row["detail_count"] for row in bundle.analysis_metrics["influence_breakdown"]}
        self.assertEqual(influences, {"Высокий": 1, "Средний": 1})
        self.assertNotIn("СМИ", bundle.profile)


class DeletedPresetTests(unittest.TestCase):
    def test_no_artificial_status_groups_and_comments_are_source(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-1", "EVE-2"],
            "incdnt_status_name": ["Удалён"] * 3,
            "incdnt_status_name_at_action": ["Утверждён", "Утверждён", "Черновик"],
            "stts_chng_comment_txt": ["Дубликат", "Ошибочная запись", "Дубликат"],
            "stts_chng_action_dttm": ["2025-01-01", "2025-01-02", "2025-02-01"],
        })
        bundle = get_analyzer("deleted_ior").prepare(df)
        report = bundle.deterministic_report().lower()
        self.assertEqual(bundle.full_metrics["journal_rows"], 3)
        self.assertEqual(bundle.full_metrics["unique_incidents"], 2)
        self.assertNotIn("группа 1", report)
        self.assertIn("комментариям к действиям удаления", report)

    def test_main_money_is_counted_once_for_repeated_deletion_actions(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-1"],
            "stts_chng_comment_txt": ["Дубликат", "Повтор"],
            "incdnt_sum": [500.0, 500.0],
            "recovery_rub_amt_aggr": [120.0, 120.0],
        })
        bundle = get_analyzer("deleted_ior").prepare(df)
        self.assertEqual(bundle.analysis_metrics["consequences"], 500.0)
        self.assertEqual(bundle.analysis_metrics["recoveries"], 120.0)
        self.assertIn("Сумма последствий по удалённым ИОР", bundle.profile)
        self.assertIn("Сумма возмещений по удалённым ИОР", bundle.profile)


class DossierAndSmallSampleTests(unittest.TestCase):
    def test_cross_join_entities_are_counted_distinctly(self):
        rows = []
        for fin_sid, fin_amount in (("F1", 100.0), ("F2", 50.0)):
            for rec_sid, rec_amount in (("R1", 20.0), ("R2", 30.0)):
                rows.append({
                    "incdnt_sid": "EVE-1", "incdnt_status_name": "Утверждён",
                    "fin_impact_sid": fin_sid, "fin_impact_rub_amt": fin_amount,
                    "recovery_sid": rec_sid, "recovery_rub_amt": rec_amount,
                })
        df = pd.DataFrame(rows)
        metrics = prepare_dossier_views(df)
        self.assertEqual(metrics, {"raw_rows": 4, "fin_count": 2, "fin_total": 150.0, "recovery_count": 2, "recovery_total": 50.0})

    def test_small_approved_sample_keeps_hypotheses_and_zero_approved_disables(self):
        approved = pd.DataFrame({
            "incdnt_sid": [f"EVE-{i}" for i in range(5)],
            "incdnt_status_name": ["Утверждён"] * 5,
        })
        small_bundle = get_analyzer("ior_hypothesis").prepare(approved)
        self.assertTrue(small_bundle.can_analyze)
        self.assertIn("Гипотеза 3", small_bundle.deterministic_hypotheses())
        zero = approved.assign(incdnt_status_name="Черновик")
        zero_bundle = get_analyzer("ior_hypothesis").prepare(zero)
        self.assertFalse(zero_bundle.can_analyze)
        zero_report = zero_bundle.deterministic_report()
        self.assertIn("Аналитическая часть и гипотезы не формируются", zero_report)
        self.assertNotIn("### 1.", zero_report)

    def test_queryspec_contract_exposes_analytical_source(self):
        result = CompileResult(ok=True, df_id="final", analysis_df_id="pre_aggregate", spec_resolved={"source": {}})
        self.assertEqual(result.analysis_df_id, "pre_aggregate")
        self.assertIs(get_analyzer("ior_hypothesis"), get_analyzer("ior_hypothesis_v2"))


class RegisteredRuntimeTests(unittest.IsolatedAsyncioTestCase):
    async def test_zero_approved_runtime_stops_before_analytical_sections(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-2"],
            "incdnt_status_name": ["Удалён", "Черновик"],
        })
        report = await generate_hypothesis_narrative(
            "проверка нулевой approved выборки", df,
            session_id="synthetic", preset_name="ior_hypothesis",
        )
        self.assertIn("Аналитическая часть и гипотезы не формируются", report)
        self.assertNotIn("### 1.", report)
        self.assertNotIn("Гипотеза 1", report)

    async def test_five_approved_runtime_produces_three_case_hypotheses(self):
        df = pd.DataFrame({
            "incdnt_sid": [f"EVE-{i}" for i in range(5)],
            "incdnt_status_name": ["Утверждён"] * 5,
            "incdnt_full_descr_txt": [""] * 5,
        })
        report = await generate_hypothesis_narrative(
            "проверка малой approved выборки", df,
            session_id="synthetic", preset_name="ior_hypothesis",
        )
        self.assertIn("Гипотеза 1", report)
        self.assertIn("Гипотеза 2", report)
        self.assertIn("Гипотеза 3", report)


class QuerySpecPopulationTests(unittest.IsolatedAsyncioTestCase):
    async def test_post_join_filter_limits_analysis_population(self):
        main, fin = _query_frames()
        state = _State(main, fin)
        result = await compile_query_spec(
            CompileContext(state, None, _Schema(), None), _join_spec(), registry=_Registry(),
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(set(state.get_df(result.df_id)["incdnt_sid"]), {"EVE-2", "EVE-3"})
        analysis_df = state.get_df(result.analysis_df_id)
        self.assertEqual(set(analysis_df["incdnt_sid"]), {"EVE-2", "EVE-3"})
        self.assertIn("incdnt_sum", analysis_df.columns)
        self.assertIn("recovery_rub_amt_aggr", analysis_df.columns)

    async def test_post_aggregate_filter_limits_analysis_to_surviving_groups(self):
        main, fin = _query_frames()
        state = _State(main, fin)
        spec = _join_spec()
        spec["filters"] = [{"kind": "range", "column": "total_loss", "op": "gt", "value": 2_500_000}]
        spec["aggregate"] = {
            "group_by": ["process_lvl_4_name"],
            "metrics": [{"source": "direct_loss", "fn": "sum", "as": "total_loss"}],
        }
        spec["select"] = ["process_lvl_4_name", "total_loss"]
        result = await compile_query_spec(
            CompileContext(state, None, _Schema(), None), spec, registry=_Registry(),
        )
        self.assertTrue(result.ok, result.error)
        self.assertEqual(set(state.get_df(result.df_id)["process_lvl_4_name"]), {"B"})
        self.assertEqual(set(state.get_df(result.analysis_df_id)["incdnt_sid"]), {"EVE-3"})

    async def test_query_spec_tool_returns_ready_analysis_narrative(self):
        main, fin = _query_frames()
        state = _State(main, fin)
        spec = _join_spec()
        spec["original_user_intent"] = "ИОР Московского банка с прямыми потерями больше 1 млн"
        result = await run_query_spec(state, spec=spec, _schema=_Schema(), _registry=_Registry())
        self.assertTrue(result.ok, result.error)
        narrative = result.output["analysis_narrative"]
        self.assertIn("Общая информация", narrative)
        self.assertIn("Гипотеза 1", narrative)
        self.assertIn("analysis_narrative", result.summary)


class RoutingAndSqlRegressionTests(unittest.TestCase):
    def test_explicit_presets_survive_user_filters(self):
        cases = [
            ("vozmeshenie_ior", "за 2025 год"),
            ("financial_consequences_ior", "за Q1 2026"),
            ("report_period_specific_ior", "EVE-1234567"),
            ("ior_period_pao_sberbank", "за 2025 год"),
        ]
        for preset, prompt in cases:
            self.assertEqual(resolve_preset_for_request(preset, prompt), preset)

    def test_eve_routes_to_dossier_and_credit_keywords_do_not_route_to_credit(self):
        self.assertEqual(detect_preset_from_prompt("покажи всё про EVE-1234567"), "report_period_specific_ior")
        self.assertNotEqual(detect_preset_from_prompt("кредит задолженность РВПС резерв залог"), "credit_no_way_collect_debt")

    def test_false_dossier_preset_is_rejected_for_drp_ad_hoc_query(self):
        prompt = "Выведи ИОРы за март 2025 года по DRP-10121"
        self.assertIsNone(detect_preset_from_prompt(prompt))
        self.assertEqual(
            resolve_preset_for_request("report_period_specific_ior", prompt),
            "ior_hypothesis",
        )
        sql = build_dynamic_sql_from_prompt(
            prompt,
            preset_name=resolve_preset_for_request("report_period_specific_ior", prompt),
        ).upper()
        self.assertIn("2025-03-01", sql)
        self.assertIn("2025-04-01", sql)
        self.assertIn("UPPER(TRIM(RISK_PROFILE_ID)) = 'DRP-10121'", sql)
        self.assertNotIn("INCIDENT_FIN_IMPACT", sql)
        self.assertNotIn("INCIDENT_RECOVERY", sql)

    def test_dynamic_sql_uses_preset_contracts_and_official_pao_scope(self):
        dossier = build_dynamic_sql_from_prompt("EVE-1234567", preset_name="report_period_specific_ior")
        self.assertIn("fin_impact", dossier)
        self.assertIn("incident_recovery", dossier)
        self.assertIn("fi.fin_impact_id", dossier)
        self.assertIn("fi.fin_impact_docum_num", dossier)
        self.assertIn("r.recovery_doc_num", dossier)
        self.assertIn("UPPER(incdnt_sid) = 'EVE-1234567'", dossier)
        financial = build_dynamic_sql_from_prompt("за Q1 2026", preset_name="financial_consequences_ior")
        self.assertIn("incident_fin_impact", financial)
        self.assertNotIn("incident_recovery", financial)
        pao = build_dynamic_sql_from_prompt("за 2025 год", preset_name="ior_period_pao_sberbank")
        for prefix in ("SBR_", "EXT_", "GRC_", "MON_", "BPS_"):
            self.assertIn(prefix, pao)
        self.assertIn("SUBSTR(UPPER(org_struct_id), 1, 4)", IOR_FULL_SQL_QUERIES["ior_period_pao_sberbank"])
        southwest = build_dynamic_sql_from_prompt("по Юго-Западному банку", preset_name="ior_hypothesis")
        self.assertIn("ЮГО-ЗАПАДНЫЙ", southwest.upper())
        self.assertNotIn("СЕВЕРО-ЗАПАДНЫЙ", southwest.upper())
        approved = build_dynamic_sql_from_prompt("только утверждённые ИОР", preset_name="ior_hypothesis")
        for status in ("УТВЕРЖДЁН", "УТВЕРЖДЕН", "УТВЕРЖДЕНИЕ"):
            self.assertIn(status, approved)

    def test_financial_q2_preset_has_only_period_and_subject_join(self):
        sql = build_dynamic_sql_from_prompt(
            "Детализация финансовых потерь за Q2 2025",
            preset_name="financial_consequences_ior",
        ).upper()
        self.assertIn("INCIDENT_FIN_IMPACT", sql)
        self.assertIn("2025-04-01", sql)
        self.assertIn("2025-07-01", sql)
        self.assertNotIn("LIKE '%ФИНАНС%'", sql)
        self.assertNotIn("LIKE '%ПОТЕР", sql)

    def test_exact_drp_does_not_add_risk_lexical_predicate(self):
        sql = build_dynamic_sql_from_prompt(
            "Найди ИОР по цифровому профилю риска DRP-10121 за 2025 год",
            preset_name="ior_hypothesis",
        ).upper()
        self.assertIn("UPPER(TRIM(RISK_PROFILE_ID)) = 'DRP-10121'", sql)
        self.assertNotIn("LIKE '%РИСК", sql)

    def test_smart_financial_threshold_uses_all_impacts_unless_direct_is_explicit(self):
        general = build_dynamic_sql_from_prompt(
            "Покажи финансовые последствия свыше 1 млн рублей по Юго-Западному банку за Q2 2025",
            preset_name="financial_consequences_ior",
        ).upper()
        self.assertIn("SUM(COALESCE(FIN_IMPACT_RUB_AMT, 0))", general)
        self.assertIn("ЮГО-ЗАПАДНЫЙ", general)
        self.assertNotIn("FIN_IMPACT_TYPE_NAME) = 'ПРЯМАЯ ПОТЕРЯ'", general)
        direct = build_dynamic_sql_from_prompt(
            "Покажи прямые потери свыше 1 млн рублей за Q2 2025",
            preset_name="financial_consequences_ior",
        ).upper()
        self.assertIn("FIN_IMPACT_TYPE_NAME) = 'ПРЯМАЯ ПОТЕРЯ'", direct)

    def test_queryspec_financial_semantics_distinguish_general_and_direct_loss(self):
        base = {
            "source": {"table": "d6_base_of_knowledge_ior"},
            "filters": [{"kind": "range", "column": "incdnt_sum", "op": "gt", "value": 1_000_000}],
        }
        general = _QUERY_SPEC_MODULE.normalize_spec({
            **base,
            "original_user_intent": "финансовые последствия свыше 1 млн",
        })
        general_join = general["source"]["joins"][0]
        general_body = general_join["pre_aggregate"]["agg"]["fin_impact_rub_amt"]
        self.assertEqual(general_body["as"], "financial_consequences_sum")
        self.assertNotIn("filter", general_body)
        self.assertEqual(general["filters"][0]["column"], "financial_consequences_sum")

        direct = _QUERY_SPEC_MODULE.normalize_spec({
            **base,
            "original_user_intent": "прямые потери свыше 1 млн",
        })
        direct_body = direct["source"]["joins"][0]["pre_aggregate"]["agg"]["fin_impact_rub_amt"]
        self.assertEqual(direct_body["as"], "direct_loss")
        self.assertEqual(direct_body["filter"]["fin_impact_type_name"]["eq"], "Прямая потеря")

    def test_queryspec_exact_drp_removes_context_word_filter(self):
        spec = _QUERY_SPEC_MODULE.normalize_spec({
            "source": {"table": "d6_base_of_knowledge_ior"},
            "original_user_intent": "Найди ИОР по цифровому профилю риска DRP-10121 за 2025 год",
            "filters": [
                {"kind": "categorical", "column": "risk_profile_id", "value": "DRP-10121"},
                {"kind": "like", "column": "org_struct_lvl_3_name", "value": "%РИСК%"},
            ],
        })
        self.assertEqual(len(spec["filters"]), 1)
        self.assertEqual(spec["filters"][0]["column"], "risk_profile_id")
        self.assertEqual(spec["filters"][0]["op"], "eq")
        self.assertEqual(spec["filters"][0]["value"], "DRP-10121")


class RunReportRoutingRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_report_keeps_explicit_subject_preset(self):
        class Store:
            def __init__(self):
                self.queries = []

            def query_sql(self, sql):
                self.queries.append(sql)
                return pd.DataFrame()

        cases = [
            ("vozmeshenie_ior", "за 2025 год", "incident_recovery"),
            ("financial_consequences_ior", "за Q1 2026", "incident_fin_impact"),
            ("report_period_specific_ior", "EVE-1234567", "incident_recovery"),
            ("ior_period_pao_sberbank", "за 2025 год", "SUBSTR(UPPER(org_struct_id), 1, 4)"),
        ]
        for preset, prompt, sql_marker in cases:
            store = Store()
            narrative = AsyncMock(return_value="ok")
            with patch("ior_reports.get_session_extract", return_value=None), patch(
                "ior_reports.get_data_store", return_value=store
            ), patch("ior_reports.set_session_extract"), patch(
                "ior_reports.generate_hypothesis_narrative", new=narrative
            ):
                self.assertEqual(await run_ior_report(preset, "routing-test", prompt), "ok")
            self.assertIn(sql_marker, store.queries[0])
            self.assertEqual(narrative.await_args.kwargs["preset_name"], preset)

    async def test_run_report_corrects_false_tool_dossier_for_drp(self):
        class Store:
            tables = GREENPLUM_TABLES

            def __init__(self):
                self.queries = []

            def query_sql(self, sql):
                self.queries.append(sql)
                return pd.DataFrame()

        store = Store()
        narrative = AsyncMock(return_value="ok")
        prompt = "Выведи ИОРы за март 2025 года по DRP-10121"
        with patch("ior_reports.get_session_extract", return_value=None), patch(
            "ior_reports.get_data_store", return_value=store
        ), patch("ior_reports.set_session_extract"), patch(
            "ior_reports.generate_hypothesis_narrative", new=narrative
        ):
            self.assertEqual(
                await run_ior_report("report_period_specific_ior", "routing-test", prompt),
                "ok",
            )
        self.assertEqual(len(store.queries), 1)
        self.assertNotIn("JOIN", store.queries[0].upper())
        self.assertEqual(narrative.await_args.kwargs["preset_name"], "ior_hypothesis")


class GreenplumSqlContractTests(unittest.TestCase):
    ACTIVE_PRESETS = (
        "financial_consequences_ior",
        "deleted_ior",
        "vozmeshenie_ior",
        "ior_nonfinancial_consequences",
        "ior_period_pao_sberbank",
        "report_period_specific_ior",
        "ior_hypothesis",
    )

    def test_active_gp_presets_use_only_target_physical_tables(self):
        queries = build_preset_sql_queries(GREENPLUM_TABLES)
        for preset in self.ACTIVE_PRESETS:
            sql = queries[preset]
            self.assertNotIn("arnsdpsbx_t_team_sva_oarb_4", sql)
            self.assertIn("s_grnplm_ld_audit_da_project_34", sql)
            self.assertIn("t_db_oarb_ior_", sql)

    def test_dynamic_and_preset_sql_share_registry(self):
        preset_sql = build_preset_sql_queries(GREENPLUM_TABLES)[
            "financial_consequences_ior"
        ]
        dynamic_sql = build_dynamic_sql_from_prompt(
            "финансовые последствия за Q1 2025",
            preset_name="financial_consequences_ior",
            tables=GREENPLUM_TABLES,
        )
        for logical_name in ("ior", "financial_impact"):
            physical = GREENPLUM_TABLES[logical_name]
            self.assertIn(physical, preset_sql)
            self.assertIn(physical, dynamic_sql)

    def test_subject_join_contracts(self):
        queries = build_preset_sql_queries(GREENPLUM_TABLES)
        contracts = {
            "financial_consequences_ior": "financial_impact",
            "vozmeshenie_ior": "recovery",
            "ior_nonfinancial_consequences": "nonfinancial_impact",
            "deleted_ior": "status",
        }
        for preset, detail in contracts.items():
            sql = queries[preset]
            self.assertIn(GREENPLUM_TABLES["ior"], sql)
            self.assertIn(GREENPLUM_TABLES[detail], sql)
            self.assertRegex(sql, r"ior\.incdnt_id\s*=\s*\w+\.\w*incdnt_id")

    def test_dossier_keeps_main_financial_and_recovery_details(self):
        sql = build_preset_sql_queries(GREENPLUM_TABLES)[
            "report_period_specific_ior"
        ]
        for logical_name in ("ior", "financial_impact", "recovery"):
            self.assertIn(GREENPLUM_TABLES[logical_name], sql)
        self.assertEqual(sql.upper().count("LEFT JOIN"), 2)

    def test_removed_main_business_area_columns_are_not_required(self):
        queries = build_preset_sql_queries(GREENPLUM_TABLES)
        removed = ("busn_area_id", "busn_area_lvl_1_name", "busn_area_lvl_2_name")
        for sql in queries.values():
            for column in removed:
                self.assertNotIn(f"ior.{column}", sql)
        financial_sql = queries["financial_consequences_ior"]
        self.assertIn("fi.fi_busn_area_id", financial_sql)

    def test_duckdb_registry_builds_local_sql(self):
        queries = build_preset_sql_queries(DUCKDB_TABLES)
        for preset in self.ACTIVE_PRESETS:
            self.assertIn("d6_base_of_knowledge", queries[preset])
            self.assertNotIn("s_grnplm_ld_audit_da_project_34", queries[preset])

    def test_dynamic_builder_has_no_hive_substring_backend_detection(self):
        import inspect

        source = inspect.getsource(build_dynamic_sql_from_prompt)
        self.assertNotIn('"arnsdpsbx" in table_name', source)
        self.assertNotIn("'arnsdpsbx' in table_name", source)


class DetailAndPresentationRegressionTests(unittest.TestCase):
    def test_financial_export_keeps_full_detail_contract(self):
        required = ["fin_impact_sid", "fin_impact_type_name", "fin_impact_kind_name", "fin_impact_rub_amt", "fi_busn_area_id", "fi_org_struct_id"]
        filtered = apply_preset_column_filter(pd.DataFrame([{name: "x" for name in required}]), FINANCIAL_RENAME)
        for name in required:
            self.assertIn(FINANCIAL_RENAME[name], filtered.columns)

    def test_duplicate_child_entities_do_not_double_count(self):
        recovery = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-1"], "incdnt_status_name": ["Утверждён"] * 2,
            "recovery_sid": ["R1", "R1"], "recovery_rub_amt": [100.0, 100.0],
        })
        self.assertEqual(get_analyzer("vozmeshenie_ior").prepare(recovery).full_metrics["total_recovery"], 100.0)
        financial = recovery.rename(columns={"recovery_sid": "fin_impact_sid", "recovery_rub_amt": "fin_impact_rub_amt"})
        self.assertEqual(get_analyzer("financial_consequences_ior").prepare(financial).full_metrics["total_amount"], 100.0)
        nonfin = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-1"], "incdnt_status_name": ["Утверждён"] * 2,
            "nonfin_impact_sid": ["N1", "N1"], "nonfin_impact_kind_name": ["Репутация"] * 2,
            "nonfin_impact_influence_class_name": ["Высокий"] * 2,
        })
        self.assertEqual(len(get_analyzer("ior_nonfinancial_consequences").prepare(nonfin).analysis_detail_df), 1)

    def test_financial_chart_policy_never_selects_recovery(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1"], "incdnt_status_name": ["Утверждён"],
            "fin_impact_sid": ["F1"], "fin_impact_rub_amt": [100.0], "recovery_rub_amt_aggr": [999.0],
        })
        policy = get_analyzer("financial_consequences_ior").prepare(df).chart_policy()
        self.assertEqual(policy["amount_column"], "fin_impact_rub_amt")
        self.assertFalse(policy["allow_recovery"])

    def test_nonfinancial_hides_money_and_deleted_card_uses_two_main_sums(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.xlsx"
            path.touch()
            inspection = {"stats": {"rows": 2, "n_unique_incdnt_sid": 1, "sum_total_loss": 500, "recovery": 300}, "excel_meta": {"name": "x.xlsx", "size": "1 KB"}}
            with patch("utils.excel_inspector.inspect_excel", return_value=inspection):
                nonfinancial = format_excel_inspection_markdown(path, include_loss_metrics=False, preset_name="ior_nonfinancial_consequences").lower()
                self.assertNotIn("сумма последствий", nonfinancial)
                self.assertNotIn("сумма возмещений", nonfinancial)
                deleted = format_excel_inspection_markdown(path, include_loss_metrics=False, preset_name="deleted_ior").lower()
                self.assertIn("сумма последствий", deleted)
                self.assertIn("сумма возмещений", deleted)

    def test_recovery_and_financial_excel_cards_follow_preset_money_policy(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.xlsx"
            path.touch()
            inspection = {"stats": {"rows": 2, "n_unique_incdnt_sid": 1, "sum_total_loss": 500, "financial_impact": 700, "recovery": 300}, "excel_meta": {"name": "x.xlsx", "size": "1 KB"}}
            with patch("utils.excel_inspector.inspect_excel", return_value=inspection):
                recovery = format_excel_inspection_markdown(path, preset_name="vozmeshenie_ior").lower()
                self.assertIn("сумма возмещений", recovery)
                self.assertNotIn("сумма потерь", recovery)
                financial = format_excel_inspection_markdown(path, preset_name="financial_consequences_ior").lower()
                self.assertIn("сумма финансовых последствий", financial)
                self.assertNotIn("сумма возмещений", financial)

    def test_sections_contain_real_structured_analysis(self):
        deleted = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-2"], "stts_chng_comment_txt": ["Дубликат", "Дубликат"],
        })
        self.assertIn("Дублирование или повторная регистрация", get_analyzer("deleted_ior").prepare(deleted).profile)
        nonfin = pd.DataFrame({
            "incdnt_sid": ["EVE-1"], "incdnt_status_name": ["Утверждён"], "nonfin_impact_sid": ["N1"],
            "nonfin_impact_kind_name": ["Репутация"], "nonfin_impact_influence_class_name": ["Высокий"],
        })
        self.assertIn("Репутация × Высокий", get_analyzer("ior_nonfinancial_consequences").prepare(nonfin).profile)
        generic = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-2"], "incdnt_status_name": ["Утверждён"] * 2,
            "incdnt_source_name": ["Мониторинг", "УВА"], "incdnt_entry_dt": ["2026-01-01", "2026-02-01"],
        })
        profile = get_analyzer("ior_hypothesis").prepare(generic).profile
        self.assertIn("Источники ИОР", profile)
        self.assertIn("2026-01", profile)

    def test_generic_money_contract_uses_unique_eve_and_hides_coverage(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1", "EVE-1", "EVE-2"],
            "incdnt_status_name": ["Утверждён", "Утверждён", "Удалён"],
            "incdnt_sum": [1000.0, 1000.0, 500.0],
            "recovery_rub_amt_aggr": [200.0, 200.0, 50.0],
            "org_struct_lvl_3_name": ["Московский банк"] * 3,
        })
        bundle = get_analyzer("ior_hypothesis").prepare(df)
        self.assertEqual(bundle.full_metrics["consequences"], 1500.0)
        self.assertEqual(bundle.full_metrics["recoveries"], 250.0)
        report = bundle.deterministic_report()
        self.assertIn("Сумма последствий по всей выборке", report)
        self.assertIn("Сумма возмещений по всей выборке", report)
        self.assertIn("Сумма последствий по утверждённым ИОР", report)
        self.assertIn("Сумма возмещений по утверждённым ИОР", report)
        self.assertIn("| Значение | Уникальных ИОР | Сумма последствий | Сумма возмещений |", report)
        self.assertNotIn("Заполненность", report)
        self.assertNotIn("Покрытие денежного поля", report)
        self.assertNotIn("3σ", report)

    def test_subject_reports_have_unambiguous_amount_columns_and_no_sigma(self):
        financial = pd.DataFrame({
            "incdnt_sid": ["EVE-1"], "incdnt_status_name": ["Утверждён"],
            "fin_impact_sid": ["F1"], "fin_impact_rub_amt": [100.0],
            "fin_impact_type_name": ["Прямая потеря"], "org_struct_lvl_3_name": ["Московский банк"],
        })
        recovery = pd.DataFrame({
            "incdnt_sid": ["EVE-1"], "incdnt_status_name": ["Утверждён"],
            "recovery_sid": ["R1"], "recovery_rub_amt": [100.0],
            "recovery_type_name": ["Компенсация"], "org_struct_lvl_3_name": ["Московский банк"],
        })
        financial_report = get_analyzer("financial_consequences_ior").prepare(financial).deterministic_report()
        recovery_report = get_analyzer("vozmeshenie_ior").prepare(recovery).deterministic_report()
        self.assertIn("Сумма финансовых последствий", financial_report)
        self.assertIn("Сумма возмещений", recovery_report)
        self.assertNotIn("| Сумма |", financial_report + recovery_report)
        self.assertNotIn("3σ", financial_report + recovery_report)

    def test_technical_column_names_are_humanized(self):
        text = sanitize_generated_text(
            "Проверить recovery_type_name и fin_impact_kind_name в process_lvl_4_name.",
            (),
        )
        self.assertNotIn("recovery_type_name", text)
        self.assertIn("Вид возмещения", text)
        self.assertIn("Вид финансового последствия", text)
        self.assertIn("Процесс", text)


class HypothesisGuardRegressionTests(unittest.IsolatedAsyncioTestCase):
    def test_completeness_requires_premise(self):
        text = get_analyzer("ior_hypothesis").prepare(pd.DataFrame({
            "incdnt_sid": ["EVE-1"], "incdnt_status_name": ["Утверждён"],
        })).deterministic_hypotheses().replace("- **Предположение / Суть проблемы**:", "- **Наблюдение**:", 1)
        complete, _ = check_hypotheses_completeness(text, 3)
        self.assertFalse(complete)

    def test_prefilter_context_explicitly_blocks_false_concentration(self):
        context = build_analysis_context_text("покажи ИОР Московского банка", {"original_user_intent": "покажи ИОР Московского банка"})
        self.assertIn("100% Московского банка", context)
        self.assertIn("не трактовать", context.lower())

    async def test_registered_pipeline_retries_invalid_hypotheses(self):
        df = pd.DataFrame({"incdnt_sid": ["EVE-1"], "incdnt_status_name": ["Утверждён"]})
        valid = get_analyzer("ior_hypothesis").prepare(df).deterministic_hypotheses()
        invalid = valid.replace("- **Предположение / Суть проблемы**:", "- **Наблюдение**:", 1)
        clean_validation = {key: False for key in (
            "autoreg_criticized", "hypotheses_duplicate", "extra_sections", "missing_eve_ids_in_major_incidents",
            "fabricated_thresholds", "numbers_inconsistent", "unfounded_inference_from_null_data", "fields_not_in_dataset",
        )}
        clean_validation["details"] = ""
        with patch("ior_hypothesis.ask_local_qwen", side_effect=[invalid, valid]) as qwen, patch(
            "ior_hypothesis.validate_narrative", new=AsyncMock(return_value=clean_validation)
        ):
            report = await generate_hypothesis_narrative("проверка", df, session_id="retry", preset_name="ior_hypothesis")
        self.assertGreaterEqual(qwen.call_count, 2)
        self.assertTrue(check_hypotheses_completeness(report, 3)[0])

    async def test_registered_qwen_prompt_contains_prefilter_protection(self):
        df = pd.DataFrame({
            "incdnt_sid": ["EVE-1"], "incdnt_status_name": ["Утверждён"],
            "org_struct_lvl_3_name": ["Московский банк"],
        })
        valid = get_analyzer("ior_hypothesis").prepare(df).deterministic_hypotheses()
        clean_validation = {key: False for key in (
            "autoreg_criticized", "hypotheses_duplicate", "extra_sections", "missing_eve_ids_in_major_incidents",
            "fabricated_thresholds", "numbers_inconsistent", "unfounded_inference_from_null_data", "fields_not_in_dataset",
        )}
        clean_validation["details"] = ""
        with patch("ior_hypothesis.ask_local_qwen", return_value=valid) as qwen, patch(
            "ior_hypothesis.validate_narrative", new=AsyncMock(return_value=clean_validation)
        ):
            await generate_hypothesis_narrative(
                "покажи ИОР Московского банка", df, session_id="prefilter", preset_name="ior_hypothesis",
                analysis_context={"original_user_intent": "покажи ИОР Московского банка"},
            )
        prompt_text = qwen.call_args.args[0][1]["content"]
        self.assertIn("100% Московского банка", prompt_text)
        self.assertIn("не трактовать", prompt_text.lower())


if __name__ == "__main__":
    unittest.main()
