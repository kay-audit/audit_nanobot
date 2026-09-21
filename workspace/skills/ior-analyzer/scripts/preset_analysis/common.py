"""Общий контракт подготовки данных для предметных анализаторов ИОР.

В этом модуле живут только технические операции: поиск колонок, нормализация
статусов, разделение detail/incident и форматирование. Бизнес-структура отчётов
остаётся в модулях конкретных пресетов.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Optional
import logging
import re

import pandas as pd

logger = logging.getLogger(__name__)


APPROVED_STATUSES = {"утверждён", "утвержден", "утверждение"}
DRAFT_STATUSES = {"черновик", "исследование"}
DELETED_STATUSES = {"удалён", "удален"}

INCIDENT_ID_CANDIDATES = (
    "incdnt_sid", "идентификатор события", "incdnt_id",
    "идентификационный ключ инцидента операционного риска",
)
STATUS_CANDIDATES = (
    "incdnt_status_name", "статус события", "статус инцидента", "статус", "status",
)


def find_column(df: pd.DataFrame, candidates: Iterable[str]) -> Optional[str]:
    columns = {str(column).strip().lower(): column for column in df.columns}
    return next((columns[str(candidate).strip().lower()] for candidate in candidates if str(candidate).strip().lower() in columns), None)


def get_incident_id_column(df: pd.DataFrame) -> Optional[str]:
    return find_column(df, INCIDENT_ID_CANDIDATES)


def get_status_column(df: pd.DataFrame) -> Optional[str]:
    return find_column(df, STATUS_CANDIDATES)


def normalize_status(value: Any) -> str:
    if value is None:
        return ""
    try:
        if pd.isna(value):
            return ""
    except (TypeError, ValueError):
        pass
    return re.sub(r"\s+", " ", str(value).strip().lower())


def status_group(value: Any) -> Optional[str]:
    normalized = normalize_status(value)
    if normalized in APPROVED_STATUSES:
        return "approved"
    if normalized in DRAFT_STATUSES:
        return "draft"
    if normalized in DELETED_STATUSES:
        return "deleted"
    return None


def to_numeric_clean(series: pd.Series) -> pd.Series:
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0)
    cleaned = series.astype(str).str.replace(r"\s+", "", regex=True)
    cleaned = cleaned.str.replace(",", ".", regex=False)
    cleaned = cleaned.str.replace(r"[^\d.\-]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce").fillna(0)


def first_not_empty(series: pd.Series) -> Any:
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


def join_unique(series: pd.Series) -> str:
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


def deduplicate_detail_entities(
    df: pd.DataFrame,
    incident_col: Optional[str],
    entity_sid_col: Optional[str],
    amount_col: Optional[str] = None,
) -> pd.DataFrame:
    """Приводит detail к контракту ``incident + child SID``.

    Строки без SID сохраняются: без бизнес-ключа нельзя доказать, что это дубль.
    Для повторяющейся пары с конфликтующими суммами детерминированно остаётся
    первая строка, а конфликт фиксируется как data-quality warning.
    """
    work = df.copy() if df is not None else pd.DataFrame()
    if work.empty or not incident_col or not entity_sid_col:
        return work
    if incident_col not in work.columns or entity_sid_col not in work.columns:
        return work
    valid = work[incident_col].notna() & work[entity_sid_col].notna()
    valid &= work[incident_col].astype(str).str.strip().ne("")
    valid &= work[entity_sid_col].astype(str).str.strip().ne("")
    keyed = work.loc[valid]
    if amount_col and amount_col in keyed.columns:
        conflict_count = 0
        for _, group in keyed.groupby([incident_col, entity_sid_col], dropna=False, sort=False):
            if len(group) > 1 and to_numeric_clean(group[amount_col]).nunique(dropna=False) > 1:
                conflict_count += 1
        if conflict_count:
            logger.warning(
                "Обнаружено %s detail-сущностей с конфликтующими значениями %s; "
                "для каждой пары сохранена первая строка",
                conflict_count, amount_col,
            )
    dedup_keyed = keyed.drop_duplicates(subset=[incident_col, entity_sid_col], keep="first")
    return pd.concat([dedup_keyed, work.loc[~valid]], axis=0).sort_index().copy()


def format_amount(value: Any) -> str:
    try:
        number = float(value or 0)
    except (TypeError, ValueError):
        number = 0.0
    return f"{number:,.2f} ₽".replace(",", " ")


def format_count(value: Any) -> str:
    try:
        number = int(value or 0)
    except (TypeError, ValueError):
        number = 0
    return f"{number:,}".replace(",", " ")


def collapse_detail_to_incidents(
    raw_df: pd.DataFrame,
    amount_columns: Iterable[str] = (),
    join_columns: Iterable[str] = (),
) -> pd.DataFrame:
    """Схлопывает detail-набор до одной строки на ИОР без потери дочерних сумм."""
    raw = raw_df.copy()
    if raw.empty:
        return raw
    incident_col = get_incident_id_column(raw)
    if not incident_col:
        return raw

    amounts = {column for column in amount_columns if column and column in raw.columns}
    joins = {column for column in join_columns if column and column in raw.columns}
    for column in amounts:
        raw[column] = to_numeric_clean(raw[column])

    grouping_col = "__analysis_incident_key"
    raw[grouping_col] = raw[incident_col].astype("object")
    missing = raw[grouping_col].isna() | raw[grouping_col].astype(str).str.strip().str.lower().isin(("", "nan", "none"))
    raw.loc[missing, grouping_col] = [f"__missing_{idx}" for idx in raw.index[missing]]

    aggregations: dict[str, Any] = {}
    for column in raw.columns:
        if column == grouping_col:
            continue
        if column in amounts:
            aggregations[column] = "sum"
        elif column in joins:
            aggregations[column] = join_unique
        else:
            aggregations[column] = first_not_empty
    return raw.groupby(grouping_col, sort=False, dropna=False).agg(aggregations).reset_index(drop=True)


def calculate_unique_status_groups(incident_df: pd.DataFrame) -> dict[str, int]:
    result = {"approved": 0, "draft": 0, "deleted": 0, "other": 0}
    if incident_df is None or incident_df.empty:
        return result
    incident_col = get_incident_id_column(incident_df)
    status_col = get_status_column(incident_df)
    if not status_col:
        return result
    work = incident_df.copy()
    work["__status_group"] = work[status_col].map(status_group)
    for key in result:
        if key == "other":
            group = work[work["__status_group"].isna()]
        else:
            group = work[work["__status_group"] == key]
        result[key] = int(group[incident_col].nunique(dropna=True)) if incident_col else len(group)
    return result


def filter_approved_incidents(incident_df: pd.DataFrame) -> pd.DataFrame:
    """Возвращает только approved. При отсутствии статуса возвращает пустой DF."""
    if incident_df is None or incident_df.empty:
        return incident_df.copy()
    status_col = get_status_column(incident_df)
    if not status_col:
        return incident_df.iloc[0:0].copy()
    mask = incident_df[status_col].map(normalize_status).isin(APPROVED_STATUSES)
    return incident_df.loc[mask].copy()


def filter_detail_by_incidents(detail_df: pd.DataFrame, incident_df: pd.DataFrame) -> pd.DataFrame:
    if detail_df is None or detail_df.empty or incident_df is None or incident_df.empty:
        return detail_df.iloc[0:0].copy()
    detail_id = get_incident_id_column(detail_df)
    incident_id = get_incident_id_column(incident_df)
    if not detail_id or not incident_id:
        return detail_df.iloc[0:0].copy()
    approved_ids = set(incident_df[incident_id].dropna().astype(str))
    return detail_df[detail_df[detail_id].astype(str).isin(approved_ids)].copy()


def categorical_breakdown(
    detail_df: pd.DataFrame,
    category_col: Optional[str],
    amount_col: Optional[str] = None,
) -> list[dict[str, Any]]:
    if detail_df is None or detail_df.empty or not category_col or category_col not in detail_df.columns:
        return []
    incident_col = get_incident_id_column(detail_df)
    total_amount = float(to_numeric_clean(detail_df[amount_col]).sum()) if amount_col and amount_col in detail_df.columns else 0.0
    rows: list[dict[str, Any]] = []
    for value, group in detail_df.groupby(category_col, dropna=False):
        label = "NULL" if value is None or str(value).strip().lower() in ("", "nan", "none") else str(value)
        amount = float(to_numeric_clean(group[amount_col]).sum()) if amount_col and amount_col in group.columns else 0.0
        rows.append({
            "label": label,
            "unique_incidents": int(group[incident_col].nunique(dropna=True)) if incident_col else len(group),
            "detail_count": len(group),
            "amount": amount,
            "amount_pct": amount / total_amount * 100.0 if total_amount else 0.0,
        })
    rows.sort(key=lambda row: (row["amount"], row["detail_count"]), reverse=True)
    return rows


def cross_breakdown(
    detail_df: pd.DataFrame,
    left_col: Optional[str],
    right_col: Optional[str],
    limit: int = 10,
) -> list[dict[str, Any]]:
    if detail_df is None or detail_df.empty or not left_col or not right_col:
        return []
    if left_col not in detail_df.columns or right_col not in detail_df.columns:
        return []
    incident_col = get_incident_id_column(detail_df)
    rows = []
    for (left, right), group in detail_df.groupby([left_col, right_col], dropna=False):
        rows.append({
            "left": "NULL" if pd.isna(left) else str(left),
            "right": "NULL" if pd.isna(right) else str(right),
            "detail_count": len(group),
            "unique_incidents": int(group[incident_col].nunique()) if incident_col else len(group),
        })
    return sorted(rows, key=lambda row: (row["detail_count"], row["unique_incidents"]), reverse=True)[:limit]


def temporal_breakdown(df: pd.DataFrame, candidates: Iterable[str], limit: int = 18) -> tuple[Optional[str], list[dict[str, Any]]]:
    column = find_column(df, candidates)
    if not column or df.empty:
        return None, []
    work = df.copy()
    work["__period"] = pd.to_datetime(work[column], errors="coerce").dt.to_period("M").astype(str)
    work = work[~work["__period"].isin(("NaT", "nan", "None"))]
    incident_col = get_incident_id_column(work)
    rows = []
    for period, group in work.groupby("__period", sort=True):
        rows.append({
            "period": period,
            "unique_incidents": int(group[incident_col].nunique()) if incident_col else len(group),
        })
    return column, rows[-limit:]


def render_temporal_breakdown(title: str, rows: list[dict[str, Any]]) -> str:
    if not rows:
        return ""
    lines = [f"#### {title}", "| Месяц | Уникальных ИОР |", "|---|---:|"]
    lines.extend(f"| {row['period']} | {row['unique_incidents']} |" for row in rows)
    if len(rows) == 1:
        lines.append("\nПредставлен один месяц: это фактическое распределение, а не тренд, рост, спад или сезонность.")
    return "\n".join(lines)


def dimension_breakdown(
    incident_df: pd.DataFrame,
    amount_col: Optional[str] = None,
    candidates: Iterable[tuple[Iterable[str], str]] = (),
) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    total_amount = float(to_numeric_clean(incident_df[amount_col]).sum()) if amount_col and amount_col in incident_df.columns else 0.0
    incident_col = get_incident_id_column(incident_df)
    for column_candidates, title in candidates:
        column = find_column(incident_df, column_candidates)
        if not column:
            continue
        values = []
        for value, group in incident_df.groupby(column, dropna=True):
            label = str(value).strip()
            if not label or label.lower() in ("nan", "none") or label.startswith("SBR_"):
                continue
            amount = float(to_numeric_clean(group[amount_col]).sum()) if amount_col and amount_col in group.columns else 0.0
            values.append({
                "label": label,
                "unique_incidents": int(group[incident_col].nunique()) if incident_col else len(group),
                "amount": amount,
                "amount_pct": amount / total_amount * 100.0 if total_amount else 0.0,
            })
        values.sort(key=lambda row: (row["amount"], row["unique_incidents"]), reverse=True)
        if values:
            result.append({"title": title, "rows": values[:5]})
    return result


def dual_amount_dimension_breakdown(
    incident_df: pd.DataFrame,
    consequence_col: Optional[str],
    recovery_col: Optional[str],
    candidates: Iterable[tuple[Iterable[str], str]] = (),
) -> list[dict[str, Any]]:
    """Разрезы generic-аналитики с двумя incident-level денежными мерами."""
    result: list[dict[str, Any]] = []
    incident_col = get_incident_id_column(incident_df)
    for column_candidates, title in candidates:
        column = find_column(incident_df, column_candidates)
        if not column:
            continue
        values: list[dict[str, Any]] = []
        for value, group in incident_df.groupby(column, dropna=True):
            label = str(value).strip()
            if not label or label.lower() in ("nan", "none") or label.startswith("SBR_"):
                continue
            consequences = (
                float(to_numeric_clean(group[consequence_col]).sum())
                if consequence_col and consequence_col in group.columns else 0.0
            )
            recoveries = (
                float(to_numeric_clean(group[recovery_col]).sum())
                if recovery_col and recovery_col in group.columns else 0.0
            )
            values.append({
                "label": label,
                "unique_incidents": int(group[incident_col].nunique()) if incident_col else len(group),
                "consequences": consequences,
                "recoveries": recoveries,
            })
        values.sort(
            key=lambda row: (row["consequences"], row["recoveries"], row["unique_incidents"]),
            reverse=True,
        )
        if values:
            result.append({"title": title, "rows": values[:5]})
    return result


def concentration_metrics(incident_df: pd.DataFrame, amount_col: Optional[str]) -> dict[str, Any]:
    incident_col = get_incident_id_column(incident_df)
    if incident_df is None or incident_df.empty or not incident_col or not amount_col or amount_col not in incident_df.columns:
        return {"top": [], "top10_amount": 0.0, "top10_pct": 0.0, "sigma_count": 0}
    ranked = incident_df[[incident_col, amount_col]].copy()
    ranked[amount_col] = to_numeric_clean(ranked[amount_col])
    ranked = ranked.sort_values(amount_col, ascending=False)
    total = float(ranked[amount_col].sum())
    top10 = float(ranked.head(10)[amount_col].sum())
    threshold = float(ranked[amount_col].mean() + 3 * ranked[amount_col].std(ddof=0))
    return {
        "top": [{"id": str(row[incident_col]), "amount": float(row[amount_col])} for _, row in ranked.head(3).iterrows()],
        "top10_amount": top10,
        "top10_pct": top10 / total * 100.0 if total else 0.0,
        "sigma_count": int((ranked[amount_col] > threshold).sum()),
    }


@dataclass
class AnalysisBundle:
    preset: str
    raw_df: pd.DataFrame
    incident_all_df: pd.DataFrame
    analysis_detail_df: pd.DataFrame
    analysis_incident_df: pd.DataFrame
    status_counts: dict[str, int]
    full_metrics: dict[str, Any]
    analysis_metrics: dict[str, Any]
    full_header: str
    profile: str
    prompt_rules: str
    forbidden_metrics: tuple[str, ...] = ()
    hypothesis_topics: tuple[str, ...] = ()
    hypothesis_count: int = 3
    status_summary_enabled: bool = True
    detail_granularity: Optional[str] = None
    chart_enabled: bool = True
    chart_amount_column: Optional[str] = None
    chart_amount_label: Optional[str] = None
    chart_date_candidates: tuple[str, ...] = ("incdnt_entry_dt", "incdnt_detection_dt")
    evidence_column_candidates: tuple[str, ...] = (
        "incdnt_full_descr_txt", "подробное описание", "incdnt_summary_descr_txt", "предварительное описание",
    )

    @property
    def approved_count(self) -> int:
        incident_col = get_incident_id_column(self.analysis_incident_df)
        return int(self.analysis_incident_df[incident_col].nunique()) if incident_col and not self.analysis_incident_df.empty else len(self.analysis_incident_df)

    @property
    def can_analyze(self) -> bool:
        return not self.analysis_incident_df.empty

    def status_summary(self) -> str:
        if not self.status_summary_enabled:
            return ""
        return (
            "### Распределение инцидентов по статусам:\n"
            f"- **Группа 1: Утверждение**: {format_count(self.status_counts.get('approved', 0))}\n"
            f"- **Группа 2: Черновик/Исследование**: {format_count(self.status_counts.get('draft', 0))}\n"
            f"- **Группа 3: Удален**: {format_count(self.status_counts.get('deleted', 0))}\n"
        )

    def scope_note(self) -> str:
        if not self.status_summary_enabled:
            return ""
        if not self.can_analyze:
            return (
                "В выгрузке нет ИОР со статусом «Утверждён/Утверждение». "
                "Аналитическая часть и гипотезы не формируются."
            )
        detail_note = f" Предметные показатели рассчитаны по {self.detail_granularity} только этих ИОР." if self.detail_granularity else ""
        return (
            f"Дальнейший анализ и аналитические гипотезы сформированы только по "
            f"{format_count(self.approved_count)} уникальным ИОР со статусом «Утверждён/Утверждение». "
            "Инциденты других статусов учитываются только в общей информации по выгрузке "
            f"и распределении по статусам.{detail_note}"
        )

    def deterministic_hypotheses(self) -> str:
        if not self.can_analyze or self.hypothesis_count <= 0:
            return ""
        topics = list(self.hypothesis_topics) or ["структуры данных", "процессной концентрации", "текстовых факторов"]
        lines = ["### 4. Аналитические гипотезы для аудиторской проверки"]
        for idx in range(self.hypothesis_count):
            topic = topics[idx] if idx < len(topics) else topics[-1]
            lines.extend([
                f"\n**Гипотеза {idx + 1}: Проверка {topic}**",
                f"- **Предположение / Суть проблемы**: Наблюдаемая структура {topic} является основанием проверить, не связаны ли различия с неоднородным применением процессов и контрольных процедур.",
                "- **Шаги проверки**:",
                "  1. Сформировать выборку конкретных ИОР и сверить исходные документы и даты обработки.",
                "  2. Сопоставить фактические процедуры между лидирующими категориями и подразделениями.",
                "- **Ожидаемый результат**: Подтверждение либо опровержение проверочной гипотезы на первичных документах без вывода о причинности только по статистической доле.",
            ])
        return "\n".join(lines)

    def deterministic_report(self) -> str:
        parts = [self.full_header.rstrip()]
        if self.status_summary_enabled:
            parts.extend([self.status_summary().rstrip(), self.scope_note()])
        if self.can_analyze:
            parts.append(self.profile.replace("{{DELETION_QWEN_SUMMARY}}", "").rstrip())
            hypotheses = self.deterministic_hypotheses()
            if hypotheses:
                parts.append(hypotheses)
        return "\n\n".join(part for part in parts if part and part.strip())

    def chart_policy(self) -> dict[str, Any]:
        return {
            "amount_column": self.chart_amount_column,
            "amount_label": self.chart_amount_label,
            "date_candidates": self.chart_date_candidates,
            "allow_recovery": self.preset == "vozmeshenie_ior",
        }


def prepare_standard_views(raw_df: pd.DataFrame, incident_df: pd.DataFrame) -> tuple[dict[str, int], pd.DataFrame, pd.DataFrame]:
    statuses = calculate_unique_status_groups(incident_df)
    approved_incident = filter_approved_incidents(incident_df)
    approved_detail = filter_detail_by_incidents(raw_df, approved_incident)
    return statuses, approved_detail, approved_incident


STANDARD_DIMENSIONS = (
    (("org_struct_lvl_3_name", "орг. структура – уровень 3 (блок / тб / пцп)", "орг. структура - уровень 3 (блок / тб / пцп)"), "ТБ / оргструктуре"),
    (("funct_block_lvl_3_name", "функциональный блок – уровень 3", "функциональный блок - уровень 3"), "функциональным блокам"),
    (("process_lvl_4_name", "процесс – уровень 4", "процесс - уровень 4"), "процессам"),
)


def render_breakdown_table(
    title: str,
    rows: list[dict[str, Any]],
    with_amount: bool = True,
    amount_label: str = "Сумма",
    amount_share_label: str = "Доля суммы",
) -> str:
    if not rows:
        return ""
    if with_amount:
        lines = [f"#### {title}", f"| Значение | Уникальных ИОР | Детальных записей | {amount_label} | {amount_share_label} |", "|---|---:|---:|---:|---:|"]
        for row in rows:
            lines.append(f"| {row['label']} | {row['unique_incidents']} | {row['detail_count']} | {format_amount(row['amount'])} | {row['amount_pct']:.1f}% |")
    else:
        lines = [f"#### {title}", "| Значение | Уникальных ИОР | Детальных записей |", "|---|---:|---:|"]
        for row in rows:
            lines.append(f"| {row['label']} | {row['unique_incidents']} | {row['detail_count']} |")
    return "\n".join(lines)


def render_dimensions(
    sections: list[dict[str, Any]],
    with_amount: bool = True,
    amount_label: str = "Сумма",
    amount_share_label: str = "Доля суммы",
) -> str:
    blocks: list[str] = []
    for section in sections:
        if with_amount:
            lines = [f"#### По {section['title']}", f"| Значение | Уникальных ИОР | {amount_label} | {amount_share_label} |", "|---|---:|---:|---:|"]
            for row in section["rows"]:
                lines.append(f"| {row['label']} | {row['unique_incidents']} | {format_amount(row['amount'])} | {row['amount_pct']:.1f}% |")
        else:
            lines = [f"#### По {section['title']}", "| Значение | Уникальных ИОР |", "|---|---:|"]
            for row in section["rows"]:
                lines.append(f"| {row['label']} | {row['unique_incidents']} |")
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


def render_dual_amount_dimensions(sections: list[dict[str, Any]]) -> str:
    blocks: list[str] = []
    for section in sections:
        lines = [
            f"#### По {section['title']}",
            "| Значение | Уникальных ИОР | Сумма последствий | Сумма возмещений |",
            "|---|---:|---:|---:|",
        ]
        for row in section["rows"]:
            lines.append(
                f"| {row['label']} | {row['unique_incidents']} | "
                f"{format_amount(row['consequences'])} | {format_amount(row['recoveries'])} |"
            )
        blocks.append("\n".join(lines))
    return "\n\n".join(blocks)


TECHNICAL_DISPLAY_NAMES = {
    "recovery_type_name": "Вид возмещения",
    "recovery_rub_amt": "Сумма возмещения",
    "recovery_rub_amt_aggr": "Сумма возмещений",
    "fin_impact_rub_amt": "Сумма финансового последствия",
    "fin_impact_type_name": "Тип финансового последствия",
    "fin_impact_kind_name": "Вид финансового последствия",
    "incdnt_sum": "Сумма последствий",
    "nonfin_impact_kind_name": "Вид нефинансового последствия",
    "nonfin_impact_influence_class_name": "Класс влияния нефинансового последствия",
    "org_struct_lvl_3_name": "ТБ / оргструктура",
    "org_struct_lvl_4_name": "Подразделение",
    "funct_block_lvl_3_name": "Функциональный блок",
    "funct_block_lvl_4_name": "Подразделение функционального блока",
    "process_lvl_3_name": "Группа процессов",
    "process_lvl_4_name": "Процесс",
    "risk_profile_id": "Код цифрового профиля риска",
    "risk_profile_name": "Цифровой профиль риска",
    "incdnt_summary_descr_txt": "Предварительное описание ИОР",
    "incdnt_full_descr_txt": "Подробное описание ИОР",
}


def sanitize_generated_text(text: str, forbidden: Iterable[str]) -> str:
    if not text:
        return ""
    for technical_name, display_name in TECHNICAL_DISPLAY_NAMES.items():
        text = re.sub(rf"\b{re.escape(technical_name)}\b", display_name, text, flags=re.IGNORECASE)
    # Последний deterministic guard: неизвестный snake_case идентификатор не
    # должен попасть в пользовательский narrative даже после неудачного retry.
    text = re.sub(r"\b[a-z][a-z0-9]*(?:_[a-z0-9]+){2,}\b", "поле данных", text)
    forbidden_lower = tuple(term.lower() for term in forbidden)
    cleaned = []
    for line in text.splitlines():
        low = line.lower()
        if any(term in low for term in forbidden_lower):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()
