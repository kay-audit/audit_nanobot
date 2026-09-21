"""Аналитика досье одного ИОР с защитой от N×M cross join."""
from __future__ import annotations

import pandas as pd

from .common import (
    AnalysisBundle, calculate_unique_status_groups, collapse_detail_to_incidents,
    filter_approved_incidents, find_column, format_amount, format_count, to_numeric_clean,
)

PRESET = "report_period_specific_ior"


def _distinct_entity(df: pd.DataFrame, sid_col: str | None, amount_col: str | None) -> tuple[int, float]:
    if not sid_col or sid_col not in df.columns:
        return 0, 0.0
    distinct = df.dropna(subset=[sid_col]).drop_duplicates(subset=[sid_col])
    amount = float(to_numeric_clean(distinct[amount_col]).sum()) if amount_col and amount_col in distinct.columns else 0.0
    return len(distinct), amount


def prepare_dossier_views(df: pd.DataFrame) -> dict:
    fin_sid = find_column(df, ("fin_impact_sid", "идентификатор финансового последствия"))
    rec_sid = find_column(df, ("recovery_sid", "идентификатор возмещения"))
    fin_amount = find_column(df, ("fin_impact_rub_amt", "сумма финансового последствия (руб.)"))
    rec_amount = find_column(df, ("recovery_rub_amt", "сумма возмещения (руб.)"))
    fin_count, fin_total = _distinct_entity(df, fin_sid, fin_amount)
    rec_count, rec_total = _distinct_entity(df, rec_sid, rec_amount)
    return {"raw_rows": len(df), "fin_count": fin_count, "fin_total": fin_total, "recovery_count": rec_count, "recovery_total": rec_total}


def prepare(df: pd.DataFrame) -> AnalysisBundle:
    raw = df.copy()
    incident_all = collapse_detail_to_incidents(raw)
    statuses = calculate_unique_status_groups(incident_all)
    approved_incident = filter_approved_incidents(incident_all)
    metrics = prepare_dossier_views(raw)
    approved_raw = raw if not approved_incident.empty else raw.iloc[0:0].copy()
    approved_metrics = prepare_dossier_views(approved_raw)
    incident_col = find_column(raw, ("incdnt_sid", "идентификатор события", "incdnt_id"))
    sid = str(raw.iloc[0][incident_col]) if incident_col and not raw.empty else "—"
    header = (
        f"### Досье по инциденту {sid}:\n"
        f"- **Строк в raw-досье (fin impact × recovery)**: {format_count(metrics['raw_rows'])}\n"
        f"- **Уникальных финансовых последствий**: {format_count(metrics['fin_count'])}\n"
        f"- **Уникальных операций возмещения**: {format_count(metrics['recovery_count'])}\n"
    )
    profile = (
        "### 1. Предметная сводка досье\n"
        f"- **Финансовых последствий**: {format_count(approved_metrics['fin_count'])}, сумма: {format_amount(approved_metrics['fin_total'])}\n"
        f"- **Операций возмещения**: {format_count(approved_metrics['recovery_count'])}, сумма: {format_amount(approved_metrics['recovery_total'])}\n\n"
        "### 2. Структура последствий и возмещений\n"
        "Каждый fin_impact_sid и recovery_sid учтён один раз независимо от числа комбинаций cross join.\n\n"
        "### 3. Контроль согласованности досье\n"
        "Проверке подлежат связи между отдельными последствиями, операциями возмещения и первичными документами."
    )
    return AnalysisBundle(
        preset=PRESET, raw_df=raw, incident_all_df=incident_all,
        analysis_detail_df=approved_raw, analysis_incident_df=approved_incident,
        status_counts=statuses, full_metrics=metrics, analysis_metrics=approved_metrics,
        full_header=header, profile=profile,
        prompt_rules="Сформируй две предметные проверочные гипотезы по конкретному досье. Не суммируй повторяющиеся строки N×M и не придумывай EVE-ID.",
        forbidden_metrics=("строк как финансовых последствий", "строк как возмещений"),
        hypothesis_topics=("согласованности финансовых последствий", "полноты и своевременности операций возмещения"),
        hypothesis_count=2,
        detail_granularity="уникальным fin_impact_sid и recovery_sid",
        chart_enabled=False,
    )
