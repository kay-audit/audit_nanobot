"""Универсальная аналитика main-table ИОР и smart QuerySpec analytical source."""
from __future__ import annotations

import pandas as pd

from .common import (
    AnalysisBundle, STANDARD_DIMENSIONS, categorical_breakdown, collapse_detail_to_incidents,
    dual_amount_dimension_breakdown, find_column, format_amount, format_count,
    prepare_standard_views, render_breakdown_table, render_dual_amount_dimensions, render_temporal_breakdown,
    temporal_breakdown, to_numeric_clean,
)

PRESET = "ior_hypothesis"
def prepare_generic(df: pd.DataFrame, preset: str = PRESET) -> AnalysisBundle:
    raw = df.copy()
    incident_all = collapse_detail_to_incidents(raw)
    statuses, approved_detail, approved_incident = prepare_standard_views(raw, incident_all)
    incident_col = find_column(raw, ("incdnt_sid", "идентификатор события", "incdnt_id"))
    event_type = find_column(approved_incident, ("incdnt_type_lvl_1_name", "тип события – уровень 1", "тип события - уровень 1"))
    risk_profile = find_column(approved_incident, ("risk_profile_name", "название цифрового профиля риска"))
    source_col = find_column(approved_incident, ("incdnt_source_name", "название источника"))
    loss_col = find_column(approved_incident, ("incdnt_sum", "общая сумма всех последствий (руб.)", "общая сумма последствий (руб.)"))
    recovery_col = find_column(approved_incident, ("recovery_rub_amt_aggr", "возмещение – итого по инциденту (руб.)", "возмещение - итого по инциденту (руб.)"))
    full_loss_col = find_column(incident_all, ("incdnt_sum", "общая сумма всех последствий (руб.)", "общая сумма последствий (руб.)"))
    full_recovery_col = find_column(incident_all, ("recovery_rub_amt_aggr", "возмещение – итого по инциденту (руб.)", "возмещение - итого по инциденту (руб.)"))
    total_loss = float(to_numeric_clean(approved_incident[loss_col]).sum()) if loss_col else 0.0
    total_recovery = float(to_numeric_clean(approved_incident[recovery_col]).sum()) if recovery_col else 0.0
    full_loss = float(to_numeric_clean(incident_all[full_loss_col]).sum()) if full_loss_col else 0.0
    full_recovery = float(to_numeric_clean(incident_all[full_recovery_col]).sum()) if full_recovery_col else 0.0
    event_rows = categorical_breakdown(approved_incident, event_type)
    risk_rows = categorical_breakdown(approved_incident, risk_profile)
    source_rows = categorical_breakdown(approved_incident, source_col)
    _, time_rows = temporal_breakdown(approved_incident, ("incdnt_entry_dt", "дата ввода (событие)", "incdnt_detection_dt"))
    dimensions = dual_amount_dimension_breakdown(approved_incident, loss_col, recovery_col, STANDARD_DIMENSIONS)
    full = {
        "rows": len(raw),
        "unique_incidents": int(raw[incident_col].nunique()) if incident_col else len(incident_all),
        "consequences": full_loss,
        "recoveries": full_recovery,
    }
    header = (
        "### Общая информация по выгрузке:\n"
        f"- **Количество ИОР в полной выборке**: {format_count(full['unique_incidents'])}\n"
        f"- **Сумма последствий по всей выборке**: {format_amount(full_loss)}\n"
        f"- **Сумма возмещений по всей выборке**: {format_amount(full_recovery)}\n"
    )
    profile = "\n\n".join(filter(None, [
        "### 1. Общая сводка ИОР\n"
        f"- **Уникальных утверждённых ИОР**: {format_count(len(approved_incident))}\n"
        f"- **Сумма последствий по утверждённым ИОР**: {format_amount(total_loss)}\n"
        f"- **Сумма возмещений по утверждённым ИОР**: {format_amount(total_recovery)}",
        render_breakdown_table("Типы событий", event_rows, with_amount=False),
        render_breakdown_table("Цифровые профили риска", risk_rows, with_amount=False),
        render_breakdown_table("Источники ИОР", source_rows, with_amount=False),
        "### 2. Аномалии и динамика\n"
        + (render_temporal_breakdown("Фактическое распределение по времени", time_rows) or "Даты для временного распределения не заполнены.")
        + "\nВременные значения не интерпретируются как причинность или сезонность без сопоставимых периодов.",
        "### 3. Концентрация и системные факторы\n" + (render_dual_amount_dimensions(dimensions) or "Структурированные измерения не заполнены."),
    ]))
    return AnalysisBundle(
        preset=preset, raw_df=raw, incident_all_df=incident_all,
        analysis_detail_df=approved_detail, analysis_incident_df=approved_incident,
        status_counts=statuses, full_metrics=full,
        analysis_metrics={"event_types": event_rows, "risk_profiles": risk_rows, "sources": source_rows, "time": time_rows, "consequences": total_loss, "recoveries": total_recovery},
        full_header=header, profile=profile,
        prompt_rules=(
            "Каждая гипотеза должна опираться на реальное наблюдение. Не придумывай EVE-ID, причины трендов, сезонность или численные пороги. "
            "При малой выборке формулируй кейс-гипотезы, а не статистические выводы о системности."
        ),
        forbidden_metrics=("авторегистрация является проблемой", "доказывает, что причиной", "порог подтверждения"),
        hypothesis_topics=("процессной и типологической структуры", "организационной концентрации", "корневых факторов по описаниям конкретных ИОР"),
        chart_amount_column=loss_col,
        chart_amount_label="Сумма последствий",
        chart_date_candidates=("incdnt_entry_dt", "incdnt_detection_dt"),
    )


def prepare(df: pd.DataFrame) -> AnalysisBundle:
    return prepare_generic(df, PRESET)
