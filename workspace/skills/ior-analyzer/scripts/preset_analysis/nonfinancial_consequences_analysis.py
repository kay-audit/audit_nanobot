"""Предметная аналитика нефинансовых последствий."""
from __future__ import annotations

import pandas as pd

from .common import (
    AnalysisBundle, STANDARD_DIMENSIONS, categorical_breakdown, collapse_detail_to_incidents,
    cross_breakdown, deduplicate_detail_entities, dimension_breakdown, find_column, format_count, prepare_standard_views,
    render_breakdown_table, render_dimensions,
)

PRESET = "ior_nonfinancial_consequences"


def prepare_nonfinancial_views(df: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    raw = df.copy()
    sid = find_column(raw, ("nonfin_impact_sid", "идентификатор нефинансового последствия"))
    kind = find_column(raw, ("nonfin_impact_kind_name", "вид качественной потери", "вид нефинансового последствия"))
    influence = find_column(raw, ("nonfin_impact_influence_class_name", "класс влияния нефинансового последствия"))
    incident = collapse_detail_to_incidents(raw, (), (sid, kind, influence))
    incident_id = find_column(raw, ("incdnt_sid", "идентификатор события", "incdnt_id"))
    source_rows = len(raw)
    raw = deduplicate_detail_entities(raw, incident_id, sid)
    incident = collapse_detail_to_incidents(raw, (), (sid, kind, influence))
    return incident, {
        "detail_rows": len(raw), "source_rows": source_rows, "unique_incidents": int(raw[incident_id].nunique()) if incident_id else len(raw),
        "sid_col": sid, "kind_col": kind, "influence_col": influence,
    }


def prepare(df: pd.DataFrame) -> AnalysisBundle:
    source = df.copy()
    incident_col = find_column(source, ("incdnt_sid", "идентификатор события", "incdnt_id"))
    sid_col = find_column(source, ("nonfin_impact_sid", "идентификатор нефинансового последствия"))
    raw = deduplicate_detail_entities(source, incident_col, sid_col)
    incident_all, full = prepare_nonfinancial_views(raw)
    statuses, approved_detail, approved_incident = prepare_standard_views(raw, incident_all)
    kind_rows = categorical_breakdown(approved_detail, full["kind_col"])
    influence_rows = categorical_breakdown(approved_detail, full["influence_col"])
    dimensions = dimension_breakdown(approved_incident, None, STANDARD_DIMENSIONS)
    critical_labels = {"высокий", "очень высокий"}
    critical_ids: set[str] = set()
    critical_detail = approved_detail.iloc[0:0].copy()
    incident_col = find_column(approved_detail, ("incdnt_sid", "идентификатор события", "incdnt_id"))
    if full["influence_col"] and incident_col:
        mask = approved_detail[full["influence_col"]].astype(str).str.strip().str.lower().isin(critical_labels)
        critical_detail = approved_detail.loc[mask].copy()
        critical_ids = set(approved_detail.loc[mask, incident_col].dropna().astype(str))
    combinations = cross_breakdown(approved_detail, full["kind_col"], full["influence_col"])
    critical_kind_rows = categorical_breakdown(critical_detail, full["kind_col"])
    critical_incident = collapse_detail_to_incidents(critical_detail)
    critical_dimensions = dimension_breakdown(critical_incident, None, STANDARD_DIMENSIONS)
    multi_kind = 0
    if incident_col and full["kind_col"] and not approved_detail.empty:
        multi_kind = int((approved_detail.groupby(incident_col)[full["kind_col"]].nunique() > 1).sum())
    combo_lines = [
        f"- **{row['left']} × {row['right']}**: {format_count(row['detail_count'])} последствий, "
        f"{format_count(row['unique_incidents'])} уникальных ИОР"
        for row in combinations[:5]
    ]
    full_header = (
        "### Общая информация по выгрузке нефинансовых последствий:\n"
        f"- **Количество строк нефинансовых последствий**: {format_count(full['detail_rows'])}\n"
        f"- **Количество уникальных ИОР**: {format_count(full['unique_incidents'])}\n"
    )
    profile = "\n\n".join(filter(None, [
        "### 1. Общая сводка нефинансовых последствий\n"
        f"- **Уникальных утверждённых ИОР**: {format_count(len(approved_incident))}\n"
        f"- **Нефинансовых последствий**: {format_count(len(approved_detail))}\n"
        f"- **ИОР с классом «Высокий» или «Очень высокий»**: {format_count(len(critical_ids))}",
        render_breakdown_table("Виды нефинансовых последствий", kind_rows, with_amount=False),
        render_breakdown_table("Классы влияния", influence_rows, with_amount=False),
        "### 2. Географическая и процессная структура нефинансовых последствий\n" + (render_dimensions(dimensions, with_amount=False) or "Структурированные измерения не заполнены."),
        "### 3. Концентрация и профиль критичных последствий\n"
        f"- **ИОР с несколькими различными видами нефинансовых последствий**: {format_count(multi_kind)}\n"
        + ("\n".join(combo_lines) if combo_lines else "Сочетания kind × influence не заполнены.")
        + "\n\n" + (render_breakdown_table("Топ видов среди High/Very High", critical_kind_rows, with_amount=False) or "High/Very High виды не заполнены.")
        + "\n\n" + (render_dimensions(critical_dimensions, with_amount=False) or "Процессы/оргструктуры для High/Very High не заполнены.")
        + "\nДенежная концентрация для этого пресета не применяется.",
    ]))
    return AnalysisBundle(
        preset=PRESET, raw_df=source, incident_all_df=incident_all,
        analysis_detail_df=approved_detail, analysis_incident_df=approved_incident,
        status_counts=statuses, full_metrics=full,
        analysis_metrics={"kind_breakdown": kind_rows, "influence_breakdown": influence_rows, "critical_incidents": len(critical_ids), "critical_kinds": critical_kind_rows, "critical_dimensions": critical_dimensions, "combinations": combinations, "multi_kind_incidents": multi_kind},
        full_header=full_header, profile=profile,
        prompt_rules=(
            "nonfin_impact_kind_name и nonfin_impact_influence_class_name — источники истины. "
            "Не подменяй их risk flags и описаниями. Не используй денежные показатели. "
            "Сформируй гипотезы по kind+influence, процессам/оргструктуре и текстам утверждённых EVE."
        ),
        forbidden_metrics=("сумма потерь", "сумма возмещений", "net loss", "финансовые последствия"),
        hypothesis_topics=("сочетаний видов и классов влияния", "процессно-организационной концентрации", "текстовых факторов утверждённых ИОР"),
        detail_granularity="нефинансовым последствиям",
        chart_enabled=False,
    )
