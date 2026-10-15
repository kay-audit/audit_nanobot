"""
ior_profiler.py — Математическое профилирование инцидентов операционного риска (ИОР).
Расчёт Парето (концентрация потерь топ-1% и топ-5%), выявление выбросов 3σ, временные распределения.
"""
from __future__ import annotations
import sys
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)


import logging
from typing import Optional, Tuple
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)


def _to_numeric_clean(series: pd.Series) -> pd.Series:
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    if series.empty:
        return series
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0)
    s = series.astype(str).str.replace(r'\s+', '', regex=True)
    s = s.str.replace(',', '.', regex=False)
    s = s.str.replace(r'[^\d\.\-]', '', regex=True)
    return pd.to_numeric(s, errors='coerce').fillna(0)


def format_loss(val: float) -> str:
    return f"{val:,.2f} ₽".replace(",", " ")


def get_total_and_direct_loss(df: pd.DataFrame) -> Tuple[float, float]:
    """Извлекает общую и прямую сумму потерь из датафрейма ИОР."""
    if df.empty:
        return 0.0, 0.0
        
    total_loss = 0.0
    direct_loss = 0.0
    col_map = {str(c).lower().strip().replace("–", "-"): c for c in df.columns}

    # 1. Если это выгрузка деталей финансовых последствий (1 строка = 1 последствие),
    # считаем суммы строго по колонке последствия fin_impact_rub_amt, а не по incdnt_sum!
    impact_cols = ["fin_impact_rub_amt", "сумма последствия (руб.)", "сумма последствия"]
    impact_col = next((col_map[c] for c in impact_cols if c in col_map), None)

    if impact_col:
        total_loss = _to_numeric_clean(df[impact_col]).sum()
    else:
        total_cols = [
            "incdnt_sum", "общая сумма всех последствий (руб.)", "общая сумма последствий (руб.)", 
            "сумма последствий, ₽", "сумма в рублях"
        ]
        for c_cand in total_cols:
            norm_cand = c_cand.lower().strip().replace("–", "-")
            if norm_cand in col_map:
                total_loss = _to_numeric_clean(df[col_map[norm_cand]]).sum()
                break
        else:
            money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "сумм"))]
            loss_cols = [c for c in money_cols if any(x in str(c).lower() for x in ("loss", "dmg", "потер", "убыт", "sum", "сумм")) and not any(r in str(c).lower() for r in ("rec", "возмещ", "возврат"))]
            if loss_cols:
                total_loss = _to_numeric_clean(df[loss_cols[0]]).sum()

    # 2. Прямые потери
    type_col = next((c for c in df.columns if str(c).lower().strip() in ("fin_impact_type_name", "тип финансового последствия", "тип последствия")), None)
    if type_col and impact_col:
        direct_loss = _to_numeric_clean(df[df[type_col] == "Прямая потеря"][impact_col]).sum()
    else:
        direct_cols = ["incdnt_drct_dmg_sum", "прямая потеря - итого (руб.)", "direct_loss", "прямая потеря"]
        for c_cand in direct_cols:
            norm_cand = c_cand.lower().strip().replace("–", "-")
            if norm_cand in col_map:
                direct_loss = _to_numeric_clean(df[col_map[norm_cand]]).sum()
                break

    return float(total_loss), float(direct_loss)


def get_recovery_column(df: pd.DataFrame) -> Optional[str]:
    """Находит колонку сумм возмещений в датафрейме."""
    col_map = {str(c).lower().strip().replace("–", "-"): c for c in df.columns}
    rec_cols = [
        "recovery", "сумма возмещений", "сумма возмещения", "сумма возмещения (руб.)", 
        "сумма возмещений (руб.)", "recovery_rub_amt", "возмещение - итого по инциденту (руб.)"
    ]
    for c_cand in rec_cols:
        norm_cand = c_cand.lower().strip().replace("–", "-")
        if norm_cand in col_map:
            return col_map[norm_cand]
    return None


def profile_dataframe(df: pd.DataFrame) -> str:
    """Генерирует сводный математический профиль датасета ИОР (Парето, 3σ, динамика)."""
    if df.empty:
        return "Таблица ИОР пуста."

    total_count = len(df)
    total_loss, direct_loss = get_total_and_direct_loss(df)

    lines = [
        "### Математический профиль выгрузки ИОР:",
        f"- Всего инцидентов в выгрузке: {total_count:,} шт.",
        f"- Общая сумма потерь: {format_loss(total_loss)}",
        f"- Сумма прямых потерь: {format_loss(direct_loss)}"
    ]

    # Находим колонку сумм для Парето и 3σ
    col_map = {str(c).lower().strip().replace("–", "-"): c for c in df.columns}
    loss_col = None
    for cand in ["incdnt_sum", "общая сумма всех последствий (руб.)", "fin_impact_rub_amt", "сумма последствий, ₽"]:
        if cand in col_map:
            loss_col = col_map[cand]
            break

    if loss_col:
        s_loss = _to_numeric_clean(df[loss_col])
        if total_loss > 0 and len(s_loss) > 0:
            s_sorted = s_loss.sort_values(ascending=False)
            top1_count = max(1, int(np.ceil(total_count * 0.01)))
            top5_count = max(1, int(np.ceil(total_count * 0.05)))

            top1_sum = s_sorted.iloc[:top1_count].sum()
            top5_sum = s_sorted.iloc[:top5_count].sum()

            top1_pct = (top1_sum / total_loss) * 100
            top5_pct = (top5_sum / total_loss) * 100

            lines.append("\n**Правило Парето (Концентрация потерь):**")
            lines.append(f"- Топ-1% крупнейших инцидентов ({top1_count} шт.): {format_loss(top1_sum)} ({top1_pct:.1f}% всех потерь)")
            lines.append(f"- Топ-5% крупнейших инцидентов ({top5_count} шт.): {format_loss(top5_sum)} ({top5_pct:.1f}% всех потерь)")

            # 3-sigma выбросы
            mean_val = s_loss.mean()
            std_val = s_loss.std()
            if not np.isnan(std_val) and std_val > 0:
                cutoff_3sig = mean_val + 3 * std_val
                outliers_3sig = df[s_loss > cutoff_3sig]
                lines.append(f"\n**Аномальные выбросы (3σ > {format_loss(cutoff_3sig)}):**")
                lines.append(f"- Выявлено аномалий: {len(outliers_3sig)} шт. на общую сумму {format_loss(_to_numeric_clean(outliers_3sig[loss_col]).sum())}")

    return "\n".join(lines)
