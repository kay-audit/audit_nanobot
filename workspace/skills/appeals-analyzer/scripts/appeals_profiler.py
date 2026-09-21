"""
appeals_profiler.py — Аналитическое профилирование и частотный анализ обращений клиентов и сотрудников (для больших массивов Greenplum).
"""
from __future__ import annotations

import logging
import json
from typing import Optional, Dict, Any
import pandas as pd

logger = logging.getLogger(__name__)

_PROFILE_EXCLUDED_COLUMNS = {
    "id", "app_row_id", "source_year", "score", "tasks", "description",
    "short_description", "короткое описание", "транскрибация диалога",
    "msg_pprb_chat", "msg_crm_chat", "msg_sc_chat", "app_content", "req_desc",
    "метрика сва", "sva_metric",
}


def build_loaded_columns_profile(
    df: pd.DataFrame,
    max_columns: int = 15,
    top_values: int = 12,
) -> Dict[str, Any]:
    """Return exact, bounded distributions for useful columns from the full population."""
    total = len(df)
    result: Dict[str, Any] = {"total_appeals": total, "columns": {}}
    if total == 0:
        return result

    priority_tokens = (
        "subj", "subject", "topic", "theme", "тема",
        "prd", "product", "продукт", "grp", "chnl", "channel", "канал", "status", "статус", "exec_dept",
    )
    candidates = []
    for position, column in enumerate(df.columns):
        normalized = str(column).strip().casefold()
        if normalized in _PROFILE_EXCLUDED_COLUMNS:
            continue
        series = df[column]
        if series.map(lambda value: isinstance(value, (dict, list, tuple, set))).any():
            continue
        nonempty = series.dropna().astype(str).str.strip()
        nonempty = nonempty[~nonempty.isin(["", "nan", "None", "null"])]
        if nonempty.empty:
            continue
        unique_count = int(nonempty.nunique())
        # Identifiers and free text have little analytical value and can explode the prompt.
        if unique_count > max(100, total // 2) or float(nonempty.str.len().mean()) > 120:
            continue
        priority = 0 if any(token in normalized for token in priority_tokens) else 1
        candidates.append((priority, unique_count, position, str(column), nonempty))

    for _, unique_count, _, column, nonempty in sorted(candidates)[:max_columns]:
        counts = nonempty.value_counts().head(top_values)
        result["columns"][column] = {
            "nonempty": int(len(nonempty)),
            "missing": int(total - len(nonempty)),
            "unique": unique_count,
            "top_values": [
                {
                    "value": str(value),
                    "count": int(count),
                    "percent_of_all": round(int(count) / total * 100, 2),
                }
                for value, count in counts.items()
            ],
        }
    return result


def format_loaded_columns_profile(profile: Dict[str, Any]) -> str:
    """Serialize the exact profile without relying on LLM arithmetic."""
    return json.dumps(profile, ensure_ascii=False, default=str, indent=2)


def profile_complaints_dataframe(df: pd.DataFrame, df_batch: Optional[pd.DataFrame] = None, start_rank: int = 1, total_db_count: Optional[int] = None) -> str:
    """Генерирует сводный аналитический математический профиль крупного датасета обращений."""
    if df.empty:
        return "Таблица обращений пуста."

    total_count = total_db_count if (total_db_count and total_db_count > 0) else len(df)
    lines = [
        "### Математический и частотный профиль массива обращений",
        f"- Общий объем выгрузки обращений: {total_count} шт."
    ]

    col_map = {str(c).lower().strip(): c for c in df.columns}

    # 1. Продукты и субпродукты
    prd_col = col_map.get("prd") or col_map.get("продукт")
    if prd_col and not df[prd_col].dropna().empty:
        prd_cnt = df[prd_col].dropna().astype(str).value_counts().head(5)
        lines.append("\n**Распределение по ключевым продуктам (Top-5):**")
        for prd_name, cnt in prd_cnt.items():
            pct = (cnt / total_count) * 100
            lines.append(f"- {prd_name}: {cnt:,} из {total_count:,} обращений ({pct:.1f}%)")

    # 2. Группы и тематики обращений
    subj_col = col_map.get("subj") or col_map.get("s_subj")
    if subj_col and not df[subj_col].dropna().empty:
        subj_cnt = df[subj_col].dropna().astype(str).value_counts().head(5)
        lines.append("\n**Распределение по тематикам обращений:**")
        for s_name, cnt in subj_cnt.items():
            pct = (cnt / total_count) * 100
            lines.append(f"- {s_name}: {cnt:,} из {total_count:,} обращений ({pct:.1f}%)")

    # 3. Флаги токсичности и ругательств
    toxic_col = col_map.get("toxic_flag") or col_map.get("toxic_flag_rep")
    if toxic_col:
        toxic_series = df[toxic_col].dropna().astype(str).str.strip()
        toxic_cnt = (toxic_series.isin(["1", "true", "1.0"])).sum()
        if toxic_cnt > 0:
            t_pct = (toxic_cnt / total_count) * 100
            lines.append(f"\n**Обращения с признаками токсичности/ругательств:** {toxic_cnt:,} из {total_count:,} ({t_pct:.1f}%)")

    # 4. Временной разрез по месяцам; пользователю показывается полная дата конца периода.
    date_col = next((col_map[c] for c in ["date", "created", "created_at", "дата"] if c in col_map), None)
    if date_col:
        try:
            temp_df = df.copy()
            temp_df["parsed_date"] = pd.to_datetime(temp_df[date_col], errors='coerce')
            temp_df = temp_df.dropna(subset=["parsed_date"])
            if not temp_df.empty:
                temp_df["period_end"] = (
                    temp_df["parsed_date"].dt.to_period("M").dt.to_timestamp(how="end").dt.strftime("%Y-%m-%d")
                )
                date_dynamics = temp_df.groupby("period_end").size().reset_index(name="count")
                lines.append("\n**Временная динамика поступления обращений:**")
                for _, r in date_dynamics.iterrows():
                    m_pct = (r["count"] / total_count) * 100
                    lines.append(f"- {r['period_end']}: {r['count']:,} из {total_count:,} обращений ({m_pct:.1f}%)")
        except Exception as e:
            logger.debug(f"[appeals_profiler] Date profiling note: {e}")

    # 5. Группы обращений из основной таблицы.
    grp_col = col_map.get("grp")
    if grp_col and not df[grp_col].dropna().empty:
        grp_cnt = df[grp_col].dropna().astype(str).str.strip()
        grp_cnt = grp_cnt[grp_cnt != ""].value_counts().head(5)
        lines.append("\n**Распределение по группам обращений (Top-5):**")
        for grp_name, cnt in grp_cnt.items():
            pct = (cnt / total_count) * 100
            lines.append(f"- {grp_name}: {cnt:,} из {total_count:,} обращений ({pct:.1f}%)")

    return "\n".join(lines)
