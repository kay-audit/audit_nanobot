"""Предметная аналитика финансовых последствий (1 detail row = fin_impact_sid)."""
from __future__ import annotations

import pandas as pd

from .common import (
    AnalysisBundle, STANDARD_DIMENSIONS, categorical_breakdown, collapse_detail_to_incidents,
    concentration_metrics, deduplicate_detail_entities, dimension_breakdown, find_column, format_amount, format_count,
    prepare_standard_views, render_breakdown_table, render_dimensions, render_temporal_breakdown,
    temporal_breakdown, to_numeric_clean,
)

PRESET = "financial_consequences_ior"


def prepare_financial_consequences_views(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    raw = df.copy()
    amount = find_column(raw, ("fin_impact_rub_amt", "сумма финансового последствия (руб.)", "сумма последствия (руб.)"))
    sid = find_column(raw, ("fin_impact_sid", "идентификатор финансового последствия"))
    type_col = find_column(raw, ("fin_impact_type_name", "тип последствия"))
    kind_col = find_column(raw, ("fin_impact_kind_name", "вид последствия"))
    incident = collapse_detail_to_incidents(raw, (amount,), (sid, type_col, kind_col))
    incident_id = find_column(raw, ("incdnt_sid", "идентификатор события", "incdnt_id"))
    source_rows = len(raw)
    raw = deduplicate_detail_entities(raw, incident_id, sid, amount)
    incident = collapse_detail_to_incidents(raw, (amount,), (sid, type_col, kind_col))
    return incident, {
        "detail_rows": len(raw),
        "source_rows": source_rows,
        "unique_incidents": int(raw[incident_id].nunique()) if incident_id else len(raw),
        "total_amount": float(to_numeric_clean(raw[amount]).sum()) if amount else 0.0,
        "amount_col": amount, "sid_col": sid, "type_col": type_col, "kind_col": kind_col,
    }


def prepare(df: pd.DataFrame) -> AnalysisBundle:
    source = df.copy()
    incident_col = find_column(source, ("incdnt_sid", "идентификатор события", "incdnt_id"))
    sid_col = find_column(source, ("fin_impact_sid", "идентификатор финансового последствия"))
    source_amount = find_column(source, ("fin_impact_rub_amt", "сумма финансового последствия (руб.)", "сумма последствия (руб.)"))
    raw = deduplicate_detail_entities(source, incident_col, sid_col, source_amount)
    incident_all, full = prepare_financial_consequences_views(raw)
    statuses, approved_detail, approved_incident = prepare_standard_views(raw, incident_all)
    amount = full["amount_col"]
    approved_amount = find_column(approved_incident, ("fin_impact_rub_amt", "сумма финансового последствия (руб.)", "сумма последствия (руб.)"))
    approved_total = float(to_numeric_clean(approved_detail[amount]).sum()) if amount and not approved_detail.empty else 0.0
    type_rows = categorical_breakdown(approved_detail, full["type_col"], amount)
    kind_rows = categorical_breakdown(approved_detail, full["kind_col"], amount)
    monitoring_col = find_column(approved_detail, ("fin_impact_monitoring_flag", "требует мониторинга (последствие)", "признак мониторинга"))
    monitoring_rows = categorical_breakdown(approved_detail, monitoring_col)
    _, time_rows = temporal_breakdown(
        approved_detail,
        ("fin_impact_reg_dt", "fin_impact_detection_dt", "fin_impact_creation_dttm", "incdnt_entry_dt"),
    )
    dimensions = dimension_breakdown(approved_incident, approved_amount, STANDARD_DIMENSIONS)
    concentration = concentration_metrics(approved_incident, approved_amount)
    full_header = (
        "### Общая информация по выгрузке финансовых последствий:\n"
        f"- **Количество строк финансовых последствий**: {format_count(full['detail_rows'])}\n"
        f"- **Количество уникальных ИОР**: {format_count(full['unique_incidents'])}\n"
        f"- **Общая сумма финансовых последствий**: {format_amount(full['total_amount'])}\n"
    )
    profile = "\n\n".join(filter(None, [
        "### 1. Общая сводка финансовых последствий\n"
        f"- **Уникальных утверждённых ИОР**: {format_count(len(approved_incident))}\n"
        f"- **Финансовых последствий**: {format_count(len(approved_detail))}\n"
        f"- **Сумма финансовых последствий**: {format_amount(approved_total)}",
        render_breakdown_table("Распределение по типам финансовых последствий", type_rows, amount_label="Сумма финансовых последствий", amount_share_label="Доля суммы финансовых последствий"),
        render_breakdown_table("Виды финансовых последствий", kind_rows, amount_label="Сумма финансовых последствий", amount_share_label="Доля суммы финансовых последствий"),
        render_breakdown_table("Признак мониторинга финансовых последствий", monitoring_rows, with_amount=False),
        "### 2. Географическая, процессная и временная структура финансовых последствий\n"
        + (render_dimensions(dimensions, amount_label="Сумма финансовых последствий", amount_share_label="Доля суммы финансовых последствий") or "Структурированные измерения не заполнены.")
        + "\n\n" + (render_temporal_breakdown("Регистрация финансовых последствий по месяцам", time_rows) or "Даты финансовых последствий не заполнены."),
        "### 3. Концентрация и крупные финансовые последствия\n"
        f"- **Сумма финансовых последствий по 10 крупнейшим ИОР**: {format_amount(concentration['top10_amount'])} ({concentration['top10_pct']:.1f}%)\n" +
        "\n".join(f"- **{row['id']}**: {format_amount(row['amount'])}" for row in concentration["top"]),
    ]))
    return AnalysisBundle(
        preset=PRESET, raw_df=source, incident_all_df=incident_all,
        analysis_detail_df=approved_detail, analysis_incident_df=approved_incident,
        status_counts=statuses, full_metrics=full,
        analysis_metrics={"total_amount": approved_total, "type_breakdown": type_rows, "kind_breakdown": kind_rows, "monitoring": monitoring_rows, "time": time_rows, "concentration": concentration},
        full_header=full_header, profile=profile,
        prompt_rules=(
            "Используй только fin_impact_rub_amt, fin_impact_type_name и fin_impact_kind_name. "
            "Не используй recovery-поля и не утверждай причинность только из высокой доли. "
            "Сформируй три разные проверочные гипотезы: типы/виды, процессы/подразделения, тексты конкретных EVE."
        ),
        forbidden_metrics=("возмещение", "recovery", "net loss"),
        hypothesis_topics=("структуры типов и видов финансовых последствий", "концентрации по процессам и подразделениям", "причин по описаниям крупнейших ИОР"),
        detail_granularity="финансовым последствиям",
        chart_amount_column=approved_amount,
        chart_amount_label="Сумма финансовых последствий",
        chart_date_candidates=("fin_impact_reg_dt", "fin_impact_detection_dt", "incdnt_entry_dt"),
    )
