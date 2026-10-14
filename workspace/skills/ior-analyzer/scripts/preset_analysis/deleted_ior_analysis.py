"""Предметная аналитика журнала удалений ИОР."""
from __future__ import annotations

import pandas as pd
import re
from collections import Counter

from .common import (
    AnalysisBundle, STANDARD_DIMENSIONS, collapse_detail_to_incidents, dimension_breakdown,
    find_column, format_count, render_dimensions,
    format_amount, to_numeric_clean, known_sum,
)

PRESET = "deleted_ior"


def _deterministic_comment_summary(values: list[str]) -> str:
    cleaned = [re.sub(r"\s+", " ", value).strip() for value in values if value and value.strip()]
    if not cleaned:
        return "- Заполненные комментарии отсутствуют."
    groups = Counter()
    for value in cleaned:
        low = value.lower()
        if any(word in low for word in ("дубл", "повтор")):
            groups["Дублирование или повторная регистрация"] += 1
        elif any(word in low for word in ("ошиб", "некоррект", "неверн")):
            groups["Ошибка или некорректное заведение"] += 1
        elif any(word in low for word in ("тест", "учеб")):
            groups["Тестовая/учебная запись"] += 1
        else:
            groups["Иные фактические формулировки"] += 1
    lines = ["#### Фактические группы причин по комментариям"]
    lines.extend(f"- **{label}**: {count}" for label, count in groups.most_common())
    lines.append("#### Повторяющиеся формулировки")
    lines.extend(f"- «{text[:160]}»: {count}" for text, count in Counter(cleaned).most_common(5))
    return "\n".join(lines)


def prepare(df: pd.DataFrame) -> AnalysisBundle:
    raw = df.copy()
    comment = find_column(raw, ("stts_chng_comment_txt", "комментарий / причина действия", "комментарий / причина", "причина удаления"))
    action_date = find_column(raw, ("stts_chng_action_dttm", "дата и время удаления", "дата удаления"))
    status_at_action = find_column(raw, ("incdnt_status_name_at_action", "статус инцидента на момент действия"))
    loss_col = find_column(raw, ("incdnt_sum", "общая сумма всех последствий (руб.)"))
    recovery_col = find_column(raw, ("recovery_rub_amt_aggr", "возмещение – итого по инциденту (руб.)", "возмещение - итого по инциденту (руб.)"))
    # Main aggregate amounts повторяются на каждой deletion action, поэтому при
    # collapse берём первое incident-level значение, а не SUM detail-строк.
    incident_all = collapse_detail_to_incidents(raw, (), (comment,))
    incident_loss_col = find_column(incident_all, ("incdnt_sum", "общая сумма всех последствий (руб.)"))
    incident_recovery_col = find_column(incident_all, ("recovery_rub_amt_aggr", "возмещение – итого по инциденту (руб.)", "возмещение - итого по инциденту (руб.)"))
    deleted_loss = known_sum(incident_all[incident_loss_col]) if incident_loss_col else None
    deleted_recovery = known_sum(incident_all[incident_recovery_col]) if incident_recovery_col else None
    incident_col = find_column(raw, ("incdnt_sid", "идентификатор события", "incdnt_id"))
    unique_count = int(raw[incident_col].nunique()) if incident_col else len(incident_all)
    action_mask = pd.Series(False, index=raw.index)
    journal_columns={c for c in (comment,action_date,status_at_action) if c}
    journal_columns.update(c for c in raw.columns if str(c).lower().startswith('stts_chng_'))
    for column in journal_columns:
        values=raw[column]
        action_mask |= values.notna() & values.astype(str).str.strip().ne('')
    action_count = int(action_mask.sum())
    empty_comments = 0
    comments: list[str] = []
    if comment:
        empty_comments = int(raw.loc[action_mask, comment].fillna("").astype(str).str.strip().str.lower().isin(("", "nan", "none")).sum())
        comments = [v for v in raw.loc[action_mask, comment].fillna("").astype(str).str.strip().tolist() if v.lower() not in ("", "nan", "none")]
    period = "не определён"
    if action_date:
        dates = pd.to_datetime(raw[action_date], errors="coerce").dropna()
        if not dates.empty:
            period = f"{dates.min():%d.%m.%Y} — {dates.max():%d.%m.%Y}"
    approved_at_deletion = 0
    if status_at_action:
        approved_at_deletion = int(raw.loc[action_mask, status_at_action].astype(str).str.strip().str.lower().isin(("утвержден", "утверждён", "утверждение")).sum())
    dimensions = dimension_breakdown(incident_all, None, STANDARD_DIMENSIONS)
    full = {"journal_rows": action_count, "unique_incidents": unique_count, "period": period}
    header = (
        "### Общая информация по журналу удалений:\n"
        f"- **Количество записей журнала удаления**: {format_count(action_count)}\n"
        f"- **Количество уникальных удалённых ИОР**: {format_count(unique_count)}\n"
        f"- **Период действий удаления**: {period}\n"
    )
    profile = "\n\n".join([
        "### 1. Общая сводка удаления\n"
        f"- **Уникальных удалённых ИОР**: {format_count(unique_count)}\n"
        f"- **Сумма последствий по удалённым ИОР**: {format_amount(deleted_loss)}\n"
        f"- **Сумма возмещений по удалённым ИОР**: {format_amount(deleted_recovery)}\n"
        f"- **Действий удаления**: {format_count(action_count)}\n"
        f"- **Действий над ранее утверждёнными ИОР**: {format_count(approved_at_deletion)}",
        "### 2. Причины и паттерны удаления\n"
        f"- **Действий без заполненного комментария**: {format_count(empty_comments)}\n"
        "Содержательный анализ выполняется только по комментариям к действиям удаления.\n"
        + _deterministic_comment_summary(comments)
        + "\n{{DELETION_QWEN_SUMMARY}}",
        "### 3. Организационная и процессная структура удалений\n" + (render_dimensions(dimensions, with_amount=False) or "Структурированные измерения не заполнены."),
    ])
    return AnalysisBundle(
        preset=PRESET, raw_df=raw, incident_all_df=incident_all,
        analysis_detail_df=raw, analysis_incident_df=incident_all,
        status_counts={}, full_metrics=full,
        analysis_metrics={"empty_comments": empty_comments, "approved_at_deletion": approved_at_deletion, "consequences": deleted_loss, "recoveries": deleted_recovery},
        full_header=header, profile=profile,
        prompt_rules=(
            "Главный источник причин — stts_chng_comment_txt. Не используй обычное описание ИОР вместо комментария. "
            "Не показывай табельные номера. Гипотезы должны быть проверочными и не обвинительными."
        ),
        forbidden_metrics=("группа 1", "группа 2", "группа 3", "табельный номер", "неправомерно удалял"),
        hypothesis_topics=("повторяющихся фактических причин удаления", "удалений ранее утверждённых ИОР", "концентрации действий по процессам и подразделениям"),
        status_summary_enabled=False,
        detail_granularity="действиям удаления",
        chart_enabled=False,
        evidence_column_candidates=("stts_chng_comment_txt", "комментарий / причина действия", "причина удаления"),
    )
