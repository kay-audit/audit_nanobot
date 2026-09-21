"""Предметная аналитика операций возмещения."""
from __future__ import annotations

import pandas as pd

from vozmeshenie_analysis import prepare_vozmeshenie_views
from .common import (
    AnalysisBundle, STANDARD_DIMENSIONS, categorical_breakdown, concentration_metrics,
    deduplicate_detail_entities, dimension_breakdown, find_column, format_amount, format_count,
    prepare_standard_views, render_breakdown_table, render_dimensions, to_numeric_clean,
)


PRESET = "vozmeshenie_ior"


def prepare(df: pd.DataFrame) -> AnalysisBundle:
    source = df.copy()
    incident_col = find_column(source, ("incdnt_sid", "идентификатор события", "incdnt_id"))
    sid_col = find_column(source, ("recovery_sid", "идентификатор возмещения"))
    source_amount = find_column(source, ("recovery_rub_amt", "сумма возмещения (руб.)", "сумма возмещения в рублях"))
    raw = deduplicate_detail_entities(source, incident_col, sid_col, source_amount)
    incident_all, raw_metrics = prepare_vozmeshenie_views(raw)
    statuses, approved_detail, approved_incident = prepare_standard_views(raw, incident_all)
    amount_col = find_column(raw, ("recovery_rub_amt", "сумма возмещения (руб.)", "сумма возмещения в рублях"))
    type_col = find_column(raw, ("recovery_type_name", "тип возмещения"))
    approved_amount_col = find_column(approved_incident, ("recovery_rub_amt", "сумма возмещения (руб.)", "сумма возмещения в рублях"))
    approved_total = float(to_numeric_clean(approved_detail[amount_col]).sum()) if amount_col and not approved_detail.empty else 0.0
    type_rows = categorical_breakdown(approved_detail, type_col, amount_col)
    dimensions = dimension_breakdown(approved_incident, approved_amount_col, STANDARD_DIMENSIONS)
    concentration = concentration_metrics(approved_incident, approved_amount_col)

    full_header = (
        "### Общая информация по выгрузке:\n"
        f"- **Количество строк выгрузки**: {format_count(raw_metrics.get('total_rows', len(raw)))}\n"
        f"- **Количество уникальных инцидентов с возмещениями**: {format_count(raw_metrics.get('unique_incidents', len(incident_all)))}\n"
        f"- **Общая сумма полученных возмещений**: {format_amount(raw_metrics.get('total_recovery', 0))}\n"
    )
    profile_parts = [
        "### 1. Общая сводка по возмещениям",
        f"- **Уникальных утверждённых ИОР с возмещениями**: {format_count(len(approved_incident))}",
        f"- **Сумма возмещений утверждённой аналитической выборки**: {format_amount(approved_total)}",
        render_breakdown_table("Распределение по видам/источникам возмещений", type_rows, with_amount=True, amount_label="Сумма возмещений", amount_share_label="Доля суммы возмещений"),
        "### 2. Географическая и процессная структура возмещений",
        render_dimensions(dimensions, with_amount=True, amount_label="Сумма возмещений", amount_share_label="Доля суммы возмещений") or "Структурированные поля оргструктуры и процессов в выборке не заполнены.",
        "### 3. Концентрация и крупные возмещения",
        f"- **Сумма возмещений по 10 крупнейшим ИОР**: {format_amount(concentration['top10_amount'])} ({concentration['top10_pct']:.1f}% от суммы аналитической выборки)",
    ]
    for row in concentration["top"]:
        profile_parts.append(f"- **{row['id']}**: {format_amount(row['amount'])}")

    return AnalysisBundle(
        preset=PRESET, raw_df=source, incident_all_df=incident_all,
        analysis_detail_df=approved_detail, analysis_incident_df=approved_incident,
        status_counts=statuses, full_metrics=raw_metrics,
        analysis_metrics={"total_recovery": approved_total, "type_breakdown": type_rows, "concentration": concentration},
        full_header=full_header, profile="\n\n".join(part for part in profile_parts if part),
        prompt_rules=(
            "Внутренне используй recovery_type_name как источник истины, но в пользовательском тексте называй его «Вид возмещения». "
            "Не пересчитывай суммы и количества. Раздел 4 должен содержать три разные проверочные гипотезы: "
            "по механизмам возмещения, по оргструктуре/процессам и по текстам конкретных ИОР."
        ),
        forbidden_metrics=("строк выгрузки", "сумма потерь", "прямые потери", "чистые потери", "net loss", "убыток", "ущерб"),
        hypothesis_topics=("эффективности видов и источников возмещения", "процессных и организационных различий", "факторов по описаниям крупных или типичных ИОР"),
        detail_granularity="операциям возмещения",
        chart_amount_column=approved_amount_col,
        chart_amount_label="Сумма возмещений",
        chart_date_candidates=("recovery_reg_dt", "recovery_creation_dttm", "incdnt_entry_dt"),
    )
