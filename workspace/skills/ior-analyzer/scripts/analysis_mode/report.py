"""Exactly three deterministic report sections, with hypotheses only in the third."""
import pandas as pd

from .anomalies import report_events
from .models import AnalysisData, AnalysisMetrics, AnalysisRequest


def cell(value) -> str:
    if pd.isna(value):
        return "Не указано"
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ").replace("#", "")


def amount(value) -> str:
    return f"{value:,.2f}".replace(",", " ")


def table(frame: pd.DataFrame, mapping: dict[str, str]) -> str:
    if frame.empty:
        return "Нет данных для этого разреза."
    lines = ["| " + " | ".join(mapping.values()) + " |", "| " + " | ".join("---" for _ in mapping) + " |"]
    for row in frame.to_dict("records"):
        values = []
        for key in mapping:
            value = row[key]
            if key.endswith("share"):
                values.append(f"{value:.1%}")
            elif key in {"direct_loss_rub", "average_loss_per_incident"}:
                values.append(amount(value))
            else:
                values.append(cell(value))
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines)


def render_report(request: AnalysisRequest, data: AnalysisData, metrics: AnalysisMetrics, events, hypotheses: str) -> str:
    parts = ["### 1. Общая статистика", f"Период создания финансовых последствий: {request.start}–{request.end} (включительно).",
             f"- Количество уникальных ИОР: {metrics.unique_incidents}\n"
             f"- Общая сумма прямых потерь по всем ИОР: {amount(metrics.total_loss)} руб.\n"
             f"- Общая сумма прямых потерь по утверждённым ИОР: {amount(metrics.approved_loss)} руб.",
             "Распределение количества ИОР по статусам:",
             table(metrics.statuses, {"incdnt_status_name": "Статус", "unique_incidents": "Количество ИОР"})]
    if data.quality_notes:
        parts.append("Качество данных: " + " ".join(data.quality_notes))
    if not data.approved_incident_df.empty:
        parts.extend(["Дальнейший анализ проводится только по утверждённым ИОР.",
                      "**Топ-10 ТБ / оргструктур по сумме прямых потерь**",
                      table(metrics.top_org, {"org_struct_lvl_3_name": "ТБ / оргструктура", "unique_incidents": "Количество ИОР", "direct_loss_rub": "Прямые потери, руб.", "share": "Доля от суммы прямых потерь"}),
                      "**Топ-10 ЦПР по сумме прямых потерь**",
                      table(metrics.top_risk, {"risk_profile_id": "Ключ ЦПР", "risk_profile_name": "Название ЦПР", "unique_incidents": "Количество ИОР", "direct_loss_rub": "Прямые потери, руб.", "share": "Доля от суммы прямых потерь"})])
    parts.append("### 2. Временной анализ и аномалии")
    if data.approved_incident_df.empty:
        parts.append("Временной анализ не сформирован: в выборке нет утверждённых ИОР.")
    else:
        monthly_for_report = metrics.monthly.copy()
        monthly_for_report.loc[monthly_for_report.is_partial_month, "month"] += "*"
        partial_note = ("* Неполный месяц выбранного периода."
                        if metrics.monthly.is_partial_month.any() else "")
        parts.extend([table(monthly_for_report, {"month": "Месяц", "unique_incidents": "Количество ИОР", "direct_loss_rub": "Прямые потери, руб.",
                                                   "average_loss_per_incident": "Средняя потеря на ИОР, руб."}),
                      partial_note,
                      "Один ИОР учитывается один раз в каждом месяце создания его финансовых последствий; поэтому сумма месячных количеств может превышать количество ИОР за весь период.",
                      "Ниже приведены наиболее заметные изменения и закономерности. Они являются основаниями для проверки, а не подтверждёнными причинами.",
                      "\n".join("- " + cell(event.description) for event in report_events(events)) or "Значимых закономерностей и отклонений не выявлено."])
    if data.approved_incident_df.empty:
        parts.extend(["### 3. Гипотезы", "Гипотезы не формируются: в выборке нет утверждённых ИОР."])
    elif hypotheses.lstrip().startswith("### 3. Гипотезы"):
        parts.append(hypotheses.strip())
    else:
        parts.extend(["### 3. Гипотезы", hypotheses])
    return "\n\n".join(parts)
