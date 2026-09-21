"""Подготовка двух уровней данных для пресета ``vozmeshenie_ior``.

Исходная выгрузка хранит одну строку на операцию возмещения (R1/R2/...),
тогда как аналитика должна работать с одной строкой на ИОР. Этот модуль
сохраняет обе гранулярности и не зависит от общих модулей nanobot.
"""
from __future__ import annotations

from typing import Any, Optional

import pandas as pd
from preset_analysis.common import deduplicate_detail_entities


INCIDENT_ID_CANDIDATES = (
    "incdnt_sid",
    "идентификатор события",
    "incdnt_id",
    "идентификационный ключ инцидента операционного риска",
)
RECOVERY_AMOUNT_CANDIDATES = (
    "recovery_rub_amt",
    "сумма возмещения (руб.)",
    "сумма возмещения в рублях",
)
RECOVERY_ID_CANDIDATES = ("recovery_sid", "идентификатор возмещения")
RECOVERY_TYPE_CANDIDATES = ("recovery_type_name", "тип возмещения")


def _find_column(df: pd.DataFrame, candidates: tuple[str, ...]) -> Optional[str]:
    columns = {str(column).strip().lower(): column for column in df.columns}
    return next((columns[candidate] for candidate in candidates if candidate in columns), None)


def _to_number(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0)
    cleaned = series.astype(str).str.replace(r"\s+", "", regex=True)
    cleaned = cleaned.str.replace(",", ".", regex=False)
    cleaned = cleaned.str.replace(r"[^\d.\-]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce").fillna(0)


def _first_not_empty(series: pd.Series) -> Any:
    for value in series:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        if str(value).strip().lower() not in ("", "nan", "none", "nat"):
            return value
    return None


def _join_unique(series: pd.Series) -> str:
    values: list[str] = []
    seen: set[str] = set()
    for value in series:
        if value is None:
            continue
        try:
            if pd.isna(value):
                continue
        except (TypeError, ValueError):
            pass
        text = str(value).strip()
        if not text or text.lower() in ("nan", "none", "nat") or text in seen:
            continue
        seen.add(text)
        values.append(text)
    return " | ".join(values)


def prepare_vozmeshenie_views(df: pd.DataFrame) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Возвращает одну строку на ИОР и метрики полной операционной выгрузки.

    Денежные показатели всегда считаются по исходным строкам. В аналитическом
    DataFrame ``recovery_rub_amt`` содержит сумму всех R-фрагментов конкретного
    ``incdnt_sid``, поэтому общая сумма сохраняется после дедупликации.
    """
    raw = df.copy() if df is not None else pd.DataFrame()
    if raw.empty:
        return raw, {
            "total_rows": 0,
            "unique_incidents": 0,
            "total_recovery": 0.0,
            "incident_id_col": None,
            "recovery_amount_col": None,
            "type_breakdown": [],
        }

    incident_col = _find_column(raw, INCIDENT_ID_CANDIDATES)
    recovery_col = _find_column(raw, RECOVERY_AMOUNT_CANDIDATES)
    recovery_id_col = _find_column(raw, RECOVERY_ID_CANDIDATES)
    recovery_type_col = _find_column(raw, RECOVERY_TYPE_CANDIDATES)

    source_rows = len(raw)
    raw = deduplicate_detail_entities(raw, incident_col, recovery_id_col, recovery_col)
    if recovery_col:
        raw[recovery_col] = _to_number(raw[recovery_col])
        total_recovery = float(raw[recovery_col].sum())
    else:
        total_recovery = 0.0

    if not incident_col:
        metrics = {
            "total_rows": len(raw),
            "source_rows": source_rows,
            "unique_incidents": len(raw),
            "total_recovery": total_recovery,
            "incident_id_col": None,
            "recovery_amount_col": recovery_col,
            "type_breakdown": [],
        }
        return raw, metrics

    # Пустые ID не должны случайно склеиваться в один фиктивный инцидент.
    grouping_col = "__voz_incident_group_key"
    raw[grouping_col] = raw[incident_col].astype("object")
    missing_mask = raw[grouping_col].isna() | raw[grouping_col].astype(str).str.strip().isin(("", "nan", "None"))
    raw.loc[missing_mask, grouping_col] = [f"__missing_{idx}" for idx in raw.index[missing_mask]]

    aggregation: dict[str, Any] = {}
    for column in raw.columns:
        if column == grouping_col:
            continue
        if column == recovery_col:
            aggregation[column] = "sum"
        elif column in (recovery_id_col, recovery_type_col):
            aggregation[column] = _join_unique
        else:
            aggregation[column] = _first_not_empty

    incidents = raw.groupby(grouping_col, sort=False, dropna=False).agg(aggregation).reset_index(drop=True)

    type_breakdown: list[dict[str, Any]] = []
    if recovery_type_col:
        for recovery_type, group in raw.groupby(recovery_type_col, dropna=False):
            if recovery_type is None or str(recovery_type).strip().lower() in ("", "nan", "none"):
                continue
            amount = float(group[recovery_col].sum()) if recovery_col else 0.0
            type_breakdown.append({
                "type": str(recovery_type),
                "unique_incidents": int(group[incident_col].nunique(dropna=True)),
                "amount": amount,
                "amount_pct": (amount / total_recovery * 100.0) if total_recovery else 0.0,
            })
        type_breakdown.sort(key=lambda item: item["amount"], reverse=True)

    metrics = {
        "total_rows": len(raw),
        "source_rows": source_rows,
        "unique_incidents": int(raw[incident_col].nunique(dropna=True)),
        "total_recovery": total_recovery,
        "incident_id_col": incident_col,
        "recovery_amount_col": recovery_col,
        "type_breakdown": type_breakdown,
    }
    return incidents, metrics


def format_vozmeshenie_header(metrics: dict[str, Any]) -> str:
    """Единственное место пользовательского отчёта, где упоминаются строки."""
    total_recovery = float(metrics.get("total_recovery", 0.0))
    formatted_recovery = f"{total_recovery:,.2f} ₽".replace(",", " ")
    rows = f"{int(metrics.get('total_rows', 0)):,}".replace(",", " ")
    incidents = f"{int(metrics.get('unique_incidents', 0)):,}".replace(",", " ")
    return (
        "### Общая информация по выгрузке:\n"
        f"- **Количество строк выгрузки**: {rows}\n"
        f"- **Количество уникальных инцидентов с возмещениями**: {incidents}\n"
        f"- **Общая сумма полученных возмещений**: {formatted_recovery}\n\n"
    )
