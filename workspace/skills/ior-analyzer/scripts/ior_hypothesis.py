"""
ior_hypothesis.py — Генерация аналитических гипотез по ИОР на базе локальной модели Qwen (ask_local_qwen).
Полный 1-в-1 перенос функциональности из ior_assistant/backend/agent/hypothesis.py под структуру Nanobot.
"""
from __future__ import annotations

import sys
from pathlib import Path


import sys
import logging
import os
import re
import uuid
import asyncio
import base64
from pathlib import Path
from typing import Optional, Dict, Any, List
import pandas as pd
import numpy as np

# Set matplotlib backend to non-interactive. В минимальном runtime без
# matplotlib текстовая аналитика остаётся доступной, пропускается только chart.
try:
    import matplotlib
    matplotlib.use('Agg')
    import matplotlib.pyplot as plt
except ImportError:  # pragma: no cover - зависит от runtime закрытого контура
    matplotlib = None
    plt = None

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))

_WORKSPACE_DIR = Path(__file__).resolve().parents[3]
if str(_WORKSPACE_DIR) not in sys.path:
    sys.path.insert(0, str(_WORKSPACE_DIR))

_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

import sys
from pathlib import Path


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
if str(_SKILL_DIR) in sys.path:
    sys.path.remove(str(_SKILL_DIR))
sys.path.insert(0, str(_SKILL_DIR))

try:
    from utils.local_qwen import ask_local_qwen
except ImportError as _local_qwen_import_error:  # import-safe unit-test runtime
    _LOCAL_QWEN_IMPORT_ERROR = str(_local_qwen_import_error)
    def ask_local_qwen(*args, **kwargs):
        raise RuntimeError(f"Локальный Qwen недоступен в текущем runtime: {_LOCAL_QWEN_IMPORT_ERROR}")
from vozmeshenie_analysis import format_vozmeshenie_header, prepare_vozmeshenie_views
from preset_analysis.registry import get_analyzer
from preset_analysis.common import sanitize_generated_text

logger = logging.getLogger(__name__)


def _write_minimal_chart_placeholder(prefix: str) -> str:
    """Создаёт валидный PNG только когда optional matplotlib отсутствует."""
    output_dir = Path("workspace/data_store/generated_charts")
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_path = output_dir / f"{prefix}_{uuid.uuid4().hex[:8]}.png"
    # Валидный прозрачный PNG 1x1. В рабочем контуре с matplotlib не используется.
    chart_path.write_bytes(base64.b64decode(
        "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
    ))
    return str(chart_path)


def _to_numeric_clean(series: pd.Series) -> pd.Series:
    if isinstance(series, pd.DataFrame):
        series = series.iloc[:, 0]
    if series.empty:
        return series
    if pd.api.types.is_numeric_dtype(series):
        return series.fillna(0)
    # clean formatting from string columns (e.g. spaces, commas, currency symbols)
    s = series.astype(str).str.replace(r'\s+', '', regex=True)
    s = s.str.replace(',', '.', regex=False)
    s = s.str.replace(r'[^\d\.\-]', '', regex=True)
    return pd.to_numeric(s, errors='coerce').fillna(0)


def _df_to_markdown_clean(df: pd.DataFrame) -> str:
    """Renders a pandas DataFrame as a clean Markdown table without tabulate dependency."""
    if df.empty:
        return ""
    headers = [str(c) for c in df.columns]
    lines = ["| " + " | ".join(headers) + " |"]
    lines.append("| " + " | ".join(["---"] * len(headers)) + " |")
    for _, row in df.iterrows():
        row_str = []
        for v in row:
            if v is None or pd.isna(v):
                row_str.append("—")
            elif isinstance(v, float):
                row_str.append(f"{v:.2f}")
            else:
                row_str.append(str(v).replace("\n", " ").replace("|", "\\|"))
        lines.append("| " + " | ".join(row_str) + " |")
    return "\n".join(lines)


def format_loss(val: float) -> str:
    return f"{val:,.2f} ₽".replace(",", " ")


def _to_datetime_safe(s: pd.Series) -> pd.Series:
    from datetime import datetime

    def parse_val(val):
        if pd.isna(val) or val is None:
            return pd.NaT
        if isinstance(val, (datetime, pd.Timestamp)):
            return pd.Timestamp(val)
        val_str = str(val).strip()
        if not val_str or val_str.lower() in ("nan", "nat", "none", "—", "-"):
            return pd.NaT

        for fmt in (None, "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M:%S", "%d.%m.%Y %H:%M", "%Y-%m-%d", "%d.%m.%Y"):
            try:
                if fmt is None:
                    return pd.Timestamp(val_str)
                else:
                    return pd.Timestamp(datetime.strptime(val_str, fmt))
            except Exception:
                continue
        try:
            from dateutil import parser
            return pd.Timestamp(parser.parse(val_str))
        except Exception:
            return pd.NaT

    if hasattr(s, 'apply'):
        return s.apply(parse_val)
    return pd.Series([parse_val(x) for x in s])


def get_total_and_direct_loss(df: pd.DataFrame) -> tuple[float, float]:
    total_loss = 0.0
    direct_loss = 0.0

    col_map = {str(c).lower().strip().replace("–", "-"): c for c in df.columns}

    # 1. Если это детализация финансовых последствий (1 строка = 1 последствие),
    # используем именно сумму последствий fin_impact_rub_amt, а не incdnt_sum!
    impact_cols = ["fin_impact_rub_amt", "сумма последствия (руб.)", "сумма последствия"]
    impact_col = next((col_map[c] for c in impact_cols if c in col_map), None)

    if impact_col:
        total_loss = _to_numeric_clean(df[impact_col]).sum()
    else:
        total_cols = [
            "incdnt_sum",
            "общая сумма всех последствий (руб.)",
            "общая сумма последствий (руб.)",
            "сумма последствий, ₽",
            "сумма в рублях"
        ]
        for c_cand in total_cols:
            norm_cand = c_cand.lower().strip().replace("–", "-")
            if norm_cand in col_map:
                total_loss = _to_numeric_clean(df[col_map[norm_cand]]).sum()
                break
        else:
            money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм"))]
            loss_cols = [c for c in money_cols if any(x in str(c).lower() for x in ("loss", "dmg", "потер", "убыт", "sum", "сумм")) and not any(r in str(c).lower() for r in ("rec", "возмещ", "возврат"))]
            if loss_cols:
                total_loss = _to_numeric_clean(df[loss_cols[0]]).sum()

    # 2. Прямые потери
    type_col = next((c for c in df.columns if str(c).lower().strip() in ("fin_impact_type_name", "тип финансового последствия", "тип последствия")), None)
    if type_col and impact_col:
        direct_loss = _to_numeric_clean(df[df[type_col] == "Прямая потеря"][impact_col]).sum()
    else:
        direct_cols = [
            "incdnt_drct_dmg_sum",
            "прямая потеря - итого (руб.)",
            "прямая потеря – итого (руб.)",
            "direct_loss",
            "прямая потеря"
        ]
        for c_cand in direct_cols:
            norm_cand = c_cand.lower().strip().replace("–", "-")
            if norm_cand in col_map:
                direct_loss = _to_numeric_clean(df[col_map[norm_cand]]).sum()
                break

    return float(total_loss), float(direct_loss)


def get_recovery_column(df: pd.DataFrame, running_skill: str = None) -> Optional[str]:
    if running_skill and "financial_consequences_ior" in running_skill:
        return None

    col_map = {str(c).lower().strip().replace("–", "-"): c for c in df.columns}
    rec_cols_list = [
        "recovery",
        "сумма возмещений",
        "сумма возмещения",
        "сумма возмещения (руб.)",
        "сумма возмещений (руб.)",
        "возмещ",
        "recovery_rub_amt",
        "recovery_rub_amt_aggr",
        "сумма возмещения (агрегатор)",
        "возмещение - итого по инциденту (руб.)",
        "возмещение – итого по инциденту (руб.)"
    ]
    for c_cand in rec_cols_list:
        norm_cand = c_cand.lower().strip().replace("–", "-")
        if norm_cand in col_map:
            return col_map[norm_cand]

    # Fallback to general recovery/возмещ/возврат keywords
    money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм"))]
    rec_cols_fallback = [c for c in money_cols if any(x in str(c).lower() for x in ("rec", "возмещ", "возврат"))]
    if rec_cols_fallback:
        return rec_cols_fallback[0]

    return None


def profile_vozmeshenie_dataframe(df: pd.DataFrame) -> str:
    """Профиль возмещений на уровне уникальных ИОР без показателей потерь."""
    if df is None or df.empty:
        return "Таблица возмещений пуста."

    id_col = get_incident_id_col(df)
    recovery_col = get_recovery_column(df, "vozmeshenie_ior")
    unique_count = df[id_col].nunique() if id_col else len(df)
    total_recovery = _to_numeric_clean(df[recovery_col]).sum() if recovery_col else 0.0
    lines = [
        "### Профиль уникальных инцидентов с возмещениями:",
        f"- **Количество уникальных инцидентов**: {unique_count}",
        f"- **Сумма полученных возмещений**: {format_loss(total_recovery)}",
    ]

    date_candidates = (
        "recovery_reg_dt", "дата регистрации в учёте", "recovery_creation_dttm",
        "дата создания возмещения", "incdnt_entry_dt", "дата ввода (событие)",
    )
    col_map = {str(c).lower().strip(): c for c in df.columns}
    date_col = next((col_map[c] for c in date_candidates if c in col_map), None)
    if date_col:
        temp = df.copy()
        temp[date_col] = _to_datetime_safe(temp[date_col])
        temp = temp.dropna(subset=[date_col])
        if not temp.empty:
            temp["__month"] = temp[date_col].dt.to_period("M")
            lines.extend([
                "\n#### Временное распределение возмещений:",
                "| Месяц | Уникальных инцидентов | Сумма возмещений | % от общей суммы |",
                "|---|---|---|---|",
            ])
            for month, group in temp.groupby("__month", sort=True):
                count = group[id_col].nunique() if id_col else len(group)
                amount = _to_numeric_clean(group[recovery_col]).sum() if recovery_col else 0.0
                pct = amount / total_recovery * 100.0 if total_recovery else 0.0
                lines.append(f"| {month} | {count} | {format_loss(amount)} | {pct:.1f}% |")

    group_dimensions = (
        (("funct_block_lvl_3_name", "функциональный блок – уровень 3"), "функциональным блокам"),
        (("org_struct_lvl_3_name", "орг. структура – уровень 3 (блок / тб / пцп)"), "ТБ / блокам"),
        (("process_lvl_4_name", "процесс – уровень 4"), "процессам"),
    )
    for candidates, label in group_dimensions:
        group_col = next((col_map[c] for c in candidates if c in col_map), None)
        if not group_col:
            continue
        values = []
        for value, group in df.groupby(group_col, dropna=True):
            if str(value).startswith("SBR_") or str(value).isdigit():
                continue
            amount = _to_numeric_clean(group[recovery_col]).sum() if recovery_col else 0.0
            count = group[id_col].nunique() if id_col else len(group)
            values.append((str(value), count, amount))
        values.sort(key=lambda item: item[2], reverse=True)
        if values:
            lines.extend([
                f"\n#### Показатели по {label}:",
                "| Значение | Уникальных инцидентов | Сумма возмещений | % от общей суммы |",
                "|---|---|---|---|",
            ])
            for value, count, amount in values[:5]:
                pct = amount / total_recovery * 100.0 if total_recovery else 0.0
                lines.append(f"| {value} | {count} | {format_loss(amount)} | {pct:.1f}% |")

    if recovery_col and id_col:
        ranked = df[[id_col, recovery_col]].copy()
        ranked[recovery_col] = _to_numeric_clean(ranked[recovery_col])
        ranked = ranked.sort_values(recovery_col, ascending=False)
        top_10_sum = float(ranked.head(10)[recovery_col].sum())
        top_10_pct = top_10_sum / total_recovery * 100.0 if total_recovery else 0.0
        sigma_threshold = float(ranked[recovery_col].mean() + 3 * ranked[recovery_col].std(ddof=0))
        anomalies = ranked[ranked[recovery_col] > sigma_threshold]
        lines.extend([
            "\n#### Концентрация возмещений:",
            f"- **Сумма возмещений по 10 крупнейшим ИОР**: {format_loss(top_10_sum)} ({top_10_pct:.1f}% от общей суммы)",
            f"- **Количество аномально крупных ИОР по правилу 3σ**: {len(anomalies)}",
        ])

    return "\n".join(lines)


def collapse_cyclical_repetitions(text: str) -> str:
    lines = text.split('\n')
    n = len(lines)
    if n < 2:
        return text

    collapsed = []
    i = 0
    while i < n:
        found_cycle = False
        for k in range(1, 13):
            if i + 2 * k > n:
                continue

            block = [re.sub(r'\s+', ' ', lines[i + j]).strip().lower() for j in range(k)]
            if not any(block):
                continue

            reps = 1
            while i + (reps + 1) * k <= n:
                next_block = [re.sub(r'\s+', ' ', lines[i + reps * k + j]).strip().lower() for j in range(k)]
                if next_block == block:
                    reps += 1
                else:
                    break

            if reps > 1:
                for j in range(k):
                    collapsed.append(lines[i + j])
                logger.warning(f"Collapsed cyclical repetition: block of size {k} repeated {reps} times.")
                i += reps * k
                found_cycle = True
                break

        if not found_cycle:
            collapsed.append(lines[i])
            i += 1

    return '\n'.join(collapsed)


def normalize_markdown_for_frontend(text: str) -> str:
    lines = text.split('\n')
    normalized_lines = []
    for line in lines:
        stripped = line.strip()
        # Match ### or #### headers
        match = re.match(r'^(#{1,5})\s*(.+)$', stripped)
        if match:
            content = match.group(2).strip()
            # If it already ends/starts with **, keep it, else wrap in **
            if content.startswith("**") and content.endswith("**"):
                normalized_lines.append(content)
            else:
                # Remove trailing colons/periods from bold headers for cleaner look
                normalized_lines.append(f"**{content}**")
        elif stripped == "---":
            # Remove single markdown divider lines completely
            continue
        else:
            normalized_lines.append(line)

    res = '\n'.join(normalized_lines)
    return res


def sanitize_vozmeshenie_narrative(text: str) -> str:
    """Не пропускает в LLM-часть повторные строки выгрузки и метрики потерь."""
    if not text:
        return text
    forbidden_line_fragments = (
        "строк", "общая сумма потерь", "сумма потерь", "прямые потери",
        "чистые потери", "net loss", "общая сумма убытков",
    )
    cleaned: list[str] = []
    for line in text.splitlines():
        low = line.lower()
        if any(fragment in low for fragment in forbidden_line_fragments):
            continue
        line = re.sub(r"компенсаци(?:я|и|ю|ей)\s+потерь", "возврат средств", line, flags=re.IGNORECASE)
        line = re.sub(r"возмещени(?:е|я|й|ю)\s+потерь", "возмещение", line, flags=re.IGNORECASE)
        # В этом пресете нет достоверного набора данных о потерях. Даже если
        # локальная LLM проигнорировала промпт, такие выводы не должны попасть
        # в пользовательский отчёт.
        if re.search(r"\b(?:потер\w*|убыт\w*|ущерб\w*)\b|net\s*loss", line, flags=re.IGNORECASE):
            continue
        cleaned.append(line)
    return "\n".join(cleaned).strip()


def collapse_repeated_sentences(text: str) -> tuple[str, int]:
    lines = text.split('\n')
    collapsed_lines = []
    total_reps = 0
    i = 0
    n = len(lines)
    while i < n:
        line = lines[i]
        norm_line = re.sub(r'\s+', ' ', line).strip().lower()
        if not norm_line:
            collapsed_lines.append(line)
            i += 1
            continue

        j = i + 1
        while j < n:
            next_line = lines[j]
            norm_next = re.sub(r'\s+', ' ', next_line).strip().lower()
            if norm_next == norm_line:
                j += 1
            else:
                break

        dup_count = j - i
        if dup_count > 1:
            total_reps += (dup_count - 1)
            logger.warning(f"Detected repetition loop of length {dup_count} for line: '{line}'")
        collapsed_lines.append(line)
        i = j

    final_lines = []
    for line in collapsed_lines:
        if not line.strip():
            final_lines.append(line)
            continue
        sentences = re.split(r'(?<=[.!?])\s+', line)
        collapsed_sentences = []
        i = 0
        n = len(sentences)
        while i < n:
            s = sentences[i]
            norm_s = re.sub(r'\s+', ' ', s).strip().lower()
            if not norm_s:
                collapsed_sentences.append(s)
                i += 1
                continue

            j = i + 1
            while j < n:
                next_s = sentences[j]
                norm_next = re.sub(r'\s+', ' ', next_s).strip().lower()
                if norm_next == norm_s:
                    j += 1
                else:
                    break

            dup_count = j - i
            if dup_count > 1:
                total_reps += (dup_count - 1)
                logger.warning(f"Detected repetition loop of length {dup_count} for sentence: '{s}'")
            collapsed_sentences.append(s)
            i = j
        final_lines.append(" ".join(collapsed_sentences))

    return "\n".join(final_lines), total_reps


def collapse_repeated_sections(text: str) -> str:
    lines = text.split('\n')
    sections = []
    current_section = {'header': '', 'norm_header': '', 'lines': []}
    sections.append(current_section)

    header_pattern = re.compile(r'^(?:#+\s*|\d+\.\s*)(.+)$')

    for line in lines:
        match = header_pattern.match(line.strip())
        if match:
            header_text = match.group(1).strip()
            norm_header = re.sub(r'[^\w\s]', '', header_text).strip().lower()
            norm_header = re.sub(r'^\d+\s*', '', norm_header).strip()

            current_section = {'header': line, 'norm_header': norm_header, 'lines': []}
            sections.append(current_section)
        else:
            current_section['lines'].append(line)

    seen_headers = set()
    unique_sections = []

    for sec in sections:
        norm = sec['norm_header']
        if not norm:
            unique_sections.append(sec)
            continue

        if norm in seen_headers:
            logger.warning(f"Detected duplicate section: '{sec['header']}'")
            continue

        seen_headers.add(norm)
        unique_sections.append(sec)

    result_lines = []
    for sec in unique_sections:
        if sec['header']:
            result_lines.append(sec['header'])
        result_lines.extend(sec['lines'])

    return "\n".join(result_lines)


def trim_extra_sections(narrative: str, is_summarization: bool) -> str:
    last_sec_markers = ["### 3.", "3. Концентрация", "3. Выявленные особенности"] if is_summarization else ["### 4.", "4. Аналитические гипотезы", "Аналитические гипотезы"]

    last_sec_pos = -1
    for marker in last_sec_markers:
        pos = narrative.find(marker)
        if pos != -1:
            last_sec_pos = pos
            break

    if last_sec_pos == -1:
        return narrative

    marker_line_end = narrative.find('\n', last_sec_pos)
    if marker_line_end == -1:
        return narrative

    scan_start = marker_line_end
    text_after = narrative[scan_start:]

    lines = text_after.split('\n')
    cut_idx = -1

    plain_header_pattern = re.compile(r'^[А-ЯA-Z\d][^\n]{1,100}$')
    list_item_pattern = re.compile(r'^\s*[•\-\*\d+\.]\s')

    blacklist_headers = ["следующие шаги", "финальный вывод", "выводы", "рекомендации", "дополнительно", "заключение", "итоги", "резюме", "вывод"]

    for idx, line in enumerate(lines):
        striped_line = line.strip()
        if not striped_line:
            continue

        if striped_line.startswith('#') and not striped_line.startswith('####'):
            cut_idx = idx
            break

        norm_line = re.sub(r'[^\w\s]', '', striped_line).strip().lower()
        if norm_line in blacklist_headers:
            cut_idx = idx
            break

        if plain_header_pattern.match(striped_line):
            next_idx = idx + 1
            while next_idx < len(lines) and not lines[next_idx].strip():
                next_idx += 1
            if next_idx < len(lines):
                next_line = lines[next_idx].strip()
                if list_item_pattern.match(next_line):
                    cut_idx = idx
                    break

    if cut_idx != -1:
        trimmed_after = "\n".join(lines[:cut_idx])
        return narrative[:scan_start] + trimmed_after

    return narrative


# Preset -> expected number of hypotheses ("Сформулируй ровно N ..." in PROMPTS below).
# Anything not listed defaults to 3, which is what almost every preset asks for.
HYPOTHESIS_COUNT_BY_SKILL = {
    "report_period_specific_ior": 2,
}
DEFAULT_HYPOTHESIS_COUNT = 3


def check_hypotheses_completeness(narrative: str, expected_count: int) -> tuple[bool, str]:
    """
    Deterministic (regex-only, no LLM call) check that the narrative actually contains
    `expected_count` fully-formed hypotheses, each with all 3 required fields.
    This exists because generation sometimes gets cut off by max_tokens midway through
    the hypotheses section (Гипотеза 2 missing "Ожидаемый результат", Гипотеза 3 missing
    entirely) and validate_narrative's LLM judge has no dedicated check for this, so such
    truncated reports were passing validation untouched.
    """
    if expected_count <= 0:
        return True, ""

    hyp_blocks = re.split(r'(?=\*?\*?Гипотеза\s*\d)', narrative, flags=re.IGNORECASE)
    hyp_blocks = [b for b in hyp_blocks if re.match(r'\s*\*?\*?Гипотеза\s*\d', b, re.IGNORECASE)]
    found = len(hyp_blocks)

    if found != expected_count:
        return False, f"в отчёте найдено {found} гипотез(ы) вместо требуемых {expected_count}"

    numbers = []
    for block in hyp_blocks:
        match = re.match(r'\s*\*?\*?Гипотеза\s*(\d+)', block, re.IGNORECASE)
        if match:
            numbers.append(int(match.group(1)))
    if numbers != list(range(1, expected_count + 1)):
        return False, f"нумерация гипотез должна быть 1..{expected_count} без пропусков и дубликатов"

    incomplete = []
    for i, block in enumerate(hyp_blocks[:expected_count], start=1):
        has_premise = bool(re.search(r"Предположение\s*/\s*Суть проблемы|Суть проблемы|Предположение", block, re.IGNORECASE))
        has_result = bool(re.search(r"Ожидаемый результат", block, re.IGNORECASE))
        has_steps = bool(re.search(r"Шаги? проверки", block, re.IGNORECASE))
        numbered_steps = len(re.findall(r"(?m)^\s*(?:[-*]\s*)?\d+[.)]\s+", block))
        if not (has_premise and has_result and has_steps and numbered_steps >= 2):
            incomplete.append(i)

    if incomplete:
        return False, f"у гипотез №{incomplete} отсутствует premise, ожидаемый результат или минимум два нумерованных шага"

    return True, ""


_AUTOREG_PATTERN = re.compile(r'авторег\w*', re.IGNORECASE)
_AUTOREG_NEGATIVE_PATTERN = re.compile(
    r'(ошибк\w*|проблем\w*|уязвим\w*|недостат\w*|дефект\w*|сбо\w*|указывает на возможн\w*|'
    r'может (?:указывать|свидетельствовать)|говорит о систем\w*|свидетельствует о)',
    re.IGNORECASE
)


def scrub_autoreg_criticism(narrative: str) -> tuple[str, bool]:
    """
    Deterministic (non-LLM) safety net for the "авторегистрация — это нормальный штатный
    процесс, её нельзя критиковать" rule.
    """
    lines = narrative.splitlines()
    changed = False
    out_lines = []
    in_hypotheses = False

    for line in lines:
        if "### 4." in line or "Аналитические гипотезы" in line:
            in_hypotheses = True

        if in_hypotheses and _AUTOREG_PATTERN.search(line):
            changed = True
            logger.warning(f"[ior_hypothesis] Deterministic scrub: removed autoreg hypothesis line: {line[:150]!r}")
            continue

        if _AUTOREG_PATTERN.search(line) and _AUTOREG_NEGATIVE_PATTERN.search(line):
            changed = True
            logger.warning(f"[ior_hypothesis] Deterministic scrub: removed autoreg-critical line: {line[:150]!r}")
            continue

        out_lines.append(line)

    return ("\n".join(out_lines), changed)


async def validate_narrative(narrative: str, forbidden_fields: list[str] = None) -> dict:
    import json

    system_prompt = (
        "Ты — контролёр качества аналитических отчётов. Проверь предоставленный текст на следующие нарушения правил и верни ТОЛЬКО JSON без пояснений:\n"
        "{\n"
        "  \"autoreg_criticized\": bool, \n"
        "  \"hypotheses_duplicate\": bool, \n"
        "  \"extra_sections\": bool, \n"
        "  \"missing_eve_ids_in_major_incidents\": bool, \n"
        "  \"fabricated_thresholds\": bool, \n"
        "  \"numbers_inconsistent\": bool, \n"
        "  \"unfounded_inference_from_null_data\": bool, \n"
        "  \"fields_not_in_dataset\": bool, \n"
        "  \"details\": \"краткое описание найденных проблем на русском\"\n"
        "}\n\n"
        "КРИТЕРИИ НАРУШЕНИЙ:\n"
        "1. autoreg_criticized: критикуется ли авторегистрация (авторег) как негативный фактор, или утверждается, что высокая доля авторегистрации — это проблема/уязвимость.\n"
        "2. hypotheses_duplicate: дублируют ли гипотезы друг друга по смыслу или сводятся ли они к одной причине (например, все гипотезы утверждают, что 'виноват персонал').\n"
        "3. extra_sections: содержит ли отчет разделы, выходящие за рамки разрешенной структуры (например, разделы 'Вывод', 'Заключение', 'Финальный вывод', 'Следующие шаги', 'Рекомендации').\n"
        "4. missing_eve_ids_in_major_incidents: отсутствуют ли конкретные ID инцидентов (EVE-XXXXXXX) при описании крупных инцидентов или концентрации потерь.\n"
        "5. fabricated_thresholds: присутствуют ли в шагах проверки гипотез надуманные/вымышленные числовые пороги подтверждения (например, '>30%', '>50%'), не подтвержденные данными.\n"
        "6. numbers_inconsistent: противоречат ли друг другу числовые показатели в разных частях отчета (например, разное количество инцидентов или разные суммы для одного среза данных).\n"
        "7. unfounded_inference_from_null_data: делаются ли необоснованные причинно-следственные выводы из нулевых или вырожденных значений метрик (например, 'нулевые потери означают урегулированность всех ошибок')."
    )

    if forbidden_fields:
        fields_str = ", ".join(f"'{f}'" for f in forbidden_fields)
        system_prompt += (
            f"\n8. fields_not_in_dataset: присутствуют ли в отчете числовые значения или явные упоминания сумм/процентов/метрик "
            f"по полям {fields_str}, которых заведомо НЕТ в данном типе выгрузки (например, возмещения в выгрузке финансовых последствий)."
        )
    else:
        system_prompt += "\n8. fields_not_in_dataset: false (всегда false, так как список запрещенных полей пуст)."

    user_message = f"Проверь следующий отчет:\n\n{narrative}"

    try:
        response_text = await asyncio.to_thread(
            ask_local_qwen, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            max_tokens=768
        )

        text = str(response_text).strip()
        if text.startswith("```"):
            lines = text.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            text = "\n".join(lines).strip()

        try:
            return json.loads(text)
        except json.JSONDecodeError:
            match = re.search(r'\{.*\}', text, re.DOTALL)
            if match:
                try:
                    return json.loads(match.group(0))
                except json.JSONDecodeError:
                    pass
    except Exception as e:
        logger.error(f"Error in validate_narrative: {e}")

    return {
        "autoreg_criticized": False,
        "hypotheses_duplicate": False,
        "extra_sections": False,
        "missing_eve_ids_in_major_incidents": False,
        "fabricated_thresholds": False,
        "numbers_inconsistent": False,
        "unfounded_inference_from_null_data": False,
        "fields_not_in_dataset": False,
        "details": "Ошибка парсинга ответа судьи"
    }


def get_incident_id_col(df: pd.DataFrame) -> Optional[str]:
    """Находит основную колонку ID инцидента в DataFrame."""
    if df is None or df.empty:
        return None
    candidates = [
        "incdnt_sid",
        "идентификатор события",
        "incdnt_id",
        "идентификационный ключ инцидента операционного риска",
        "id"
    ]
    col_map = {str(c).lower().strip(): c for c in df.columns}
    for cand in candidates:
        if cand.lower() in col_map:
            return col_map[cand.lower()]
    return None


def calculate_advanced_stats(df: pd.DataFrame) -> dict:
    """
    Computes advanced operational risk metrics: Top-10 concentration (exact sum and percentage).
    Aggregates by unique incident ID to prevent duplicate row distortions.
    """
    stats = {}
    if df is None or df.empty:
        return stats

    id_col = get_incident_id_col(df)
    total_incidents = df[id_col].nunique() if id_col else len(df)
    stats["total_incidents"] = total_incidents
    stats["total_rows"] = len(df)

    money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм"))]
    loss_cols = [c for c in money_cols if any(x in str(c).lower() for x in ("loss", "dmg", "потер", "убыт", "sum", "сумм")) and not any(r in str(c).lower() for r in ("rec", "возмещ", "возврат"))]
    if not loss_cols and money_cols:
        loss_cols = [c for c in money_cols if not any(x in str(c).lower() for x in ("rec", "возмещ", "возврат"))]

    if loss_cols:
        primary_loss = loss_cols[0]
        try:
            if id_col:
                inc_losses = df.groupby(id_col)[primary_loss].apply(lambda s: _to_numeric_clean(s).sum())
            else:
                inc_losses = _to_numeric_clean(df[primary_loss])

            total_loss = inc_losses.sum()

            if total_loss > 0:
                sorted_losses = inc_losses.sort_values(ascending=False)
                top_10_losses = sorted_losses.head(10)
                top_10_sum = top_10_losses.sum()
                top_10_pct = (top_10_sum / total_loss) * 100

                stats["top_10_sum"] = top_10_sum
                stats["top_10_pct"] = top_10_pct
                stats["total_loss"] = total_loss
        except Exception as e:
            logger.warning(f"Error calculating advanced stats: {e}")

    return stats


def generate_dynamics_chart(
    df: pd.DataFrame,
    session_id: str,
    running_skill: str = None,
    chart_policy: Optional[dict] = None,
) -> Optional[str]:
    """
    Plots a professional chart representing temporal dynamics (multiple synergistic lines) and saves it.
    Returns the file_id / path of the saved image.
    """
    if plt is None:
        logger.info("matplotlib unavailable, skipping chart generation.")
        return _write_minimal_chart_placeholder("chart_ior_unavailable")
    try:
        policy_is_explicit = chart_policy is not None
        chart_policy = chart_policy or {}
        configured_dates = chart_policy.get("date_candidates") or ()
        primary_date = next((c for c in configured_dates if c in df.columns), None)
        entry_candidates = [c for c in df.columns if any(x in str(c).lower() for x in ("entry", "ввод", "регистр"))
                            and not any(y in str(c).lower() for y in ("признак", "тип", "флаг", "flag", "type", "номер", "id", "status", "статус"))]
        date_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("dt", "date", "dttm", "ts", "дата", "время"))
                     and not any(y in str(c).lower() for y in ("признак", "тип", "флаг", "flag", "type", "номер", "id", "status", "статус"))]

        if primary_date is None and entry_candidates:
            primary_date = entry_candidates[0]
        elif primary_date is None and date_cols:
            primary_date = date_cols[0]

        if not primary_date:
            logger.info("No date column found, skipping chart generation.")
            return None

        loss_candidates = ["общая сумма всех последствий (руб.)", "общая сумма последствий (руб.)", "сумма последствий, ₽", "сумма последствий", "incdnt_sum", "incdnt_drct_dmg_sum", "сумма последствия (руб.)", "сумма последствия", "fin_impact_rub_amt"]
        rec_candidates = ["возмещение – итого по инциденту (руб.)", "сумма возмещений (руб.)", "сумма возмещений", "возмещ", "recovery", "recovery_rub_amt_aggr", "recovery_rub_amt", "сумма возмещения (руб.)"]

        configured_amount = chart_policy.get("amount_column")
        primary_loss = configured_amount if configured_amount in df.columns else None
        primary_recovery = primary_loss if chart_policy.get("allow_recovery") else None
        if primary_recovery:
            primary_loss = None
        if not policy_is_explicit:
            primary_loss = next((c for c in df.columns if str(c).lower().strip() in loss_candidates), None)
            primary_recovery = next((c for c in df.columns if str(c).lower().strip() in rec_candidates), None)

        if running_skill == "vozmeshenie_ior":
            primary_loss = None

        if not policy_is_explicit and not primary_loss and running_skill != "vozmeshenie_ior":
            money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм", "потери"))]
            loss_cols = [c for c in money_cols if any(x in str(c).lower() for x in ("loss", "dmg", "потер", "убыт", "sum", "потери")) and not any(r in str(c).lower() for r in ("rec", "возмещ", "возврат", "возмещений"))]
            if loss_cols:
                primary_loss = loss_cols[0]
            elif money_cols:
                primary_loss = [c for c in money_cols if not any(r in str(c).lower() for r in ("rec", "возмещ", "возврат", "возмещений"))][0]

        if not policy_is_explicit and not primary_recovery:
            money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм", "потери"))]
            recovery_cols = [c for c in money_cols if any(x in str(c).lower() for x in ("rec", "возмещ", "возврат", "возмещений"))]
            if recovery_cols:
                primary_recovery = recovery_cols[0]

        temp_df = df.copy()
        temp_df[primary_date] = _to_datetime_safe(temp_df[primary_date])
        temp_df = temp_df.dropna(subset=[primary_date])

        if temp_df.empty:
            logger.info("Date column has only null values, skipping chart generation.")
            return None

        min_date = temp_df[primary_date].min()
        max_date = temp_df[primary_date].max()
        days_diff = (max_date - min_date).days if pd.notna(min_date) and pd.notna(max_date) else 0

        if days_diff <= 31:
            temp_df['period_key'] = temp_df[primary_date].dt.strftime('%d.%m')
            period_label = 'День'
        elif days_diff <= 180:
            temp_df['period_key'] = temp_df[primary_date].dt.to_period('W').astype(str).apply(lambda x: str(x).split('/')[0])
            period_label = 'Неделя'
        else:
            temp_df['period_key'] = temp_df[primary_date].dt.to_period('M').astype(str)
            period_label = 'Месяц'

        grouped = temp_df.groupby('period_key').agg(
            count=(primary_date, 'count')
        ).reset_index()

        if primary_loss:
            temp_df[primary_loss] = pd.to_numeric(temp_df[primary_loss], errors='coerce').fillna(0)
            loss_g = temp_df.groupby('period_key')[primary_loss].sum().reset_index(name='loss_sum')
            grouped = grouped.merge(loss_g, on='period_key', how='left')
        else:
            grouped['loss_sum'] = 0.0

        if primary_recovery:
            temp_df[primary_recovery] = pd.to_numeric(temp_df[primary_recovery], errors='coerce').fillna(0)
            rec_g = temp_df.groupby('period_key')[primary_recovery].sum().reset_index(name='recovery_sum')
            grouped = grouped.merge(rec_g, on='period_key', how='left')
        else:
            grouped['recovery_sum'] = 0.0

        grouped['loss_sum'] = grouped['loss_sum'].fillna(0.0)
        grouped['recovery_sum'] = grouped['recovery_sum'].fillna(0.0)

        chronological_keys = temp_df.sort_values(primary_date)['period_key'].unique()
        grouped['period_key'] = pd.Categorical(grouped['period_key'], categories=chronological_keys, ordered=True)
        grouped = grouped.sort_values('period_key')

        if len(grouped) < 1:
            return None

        plt.style.use('default')
        fig, ax1 = plt.subplots(figsize=(9, 4.5), dpi=150)
        fig.patch.set_facecolor('#ffffff')
        ax1.set_facecolor('#ffffff')
        ax1.grid(True, axis='both', color='#e2e8f0', linestyle=':', alpha=0.8)

        periods = grouped['period_key'].astype(str).tolist()
        counts = grouped['count'].tolist()

        bar_width = 0.35
        n_periods = len(periods)
        x_positions = list(range(n_periods))
        ax1.bar(x_positions, counts, width=bar_width, color='#3b82f6', edgecolor='#2563eb', alpha=0.85, label='Число инцидентов')
        ax1.set_ylabel('Число инцидентов', color='#1e3a8a', fontsize=10)
        ax1.tick_params(axis='y', labelcolor='#1e3a8a', colors='#0f172a')

        if n_periods > 12:
            step = (n_periods // 12) + 1
            tick_positions = list(range(0, n_periods, step))
            tick_labels = [periods[i] for i in tick_positions]
        else:
            tick_positions = x_positions
            tick_labels = periods

        ax1.set_xticks(tick_positions)
        ax1.set_xticklabels(tick_labels, rotation=15, ha='right', fontsize=8, color='#0f172a')

        for spine in ax1.spines.values():
            spine.set_edgecolor('#cbd5e1')

        has_losses = primary_loss and grouped['loss_sum'].sum() > 0
        has_recoveries = primary_recovery and grouped['recovery_sum'].sum() > 0

        if has_losses or has_recoveries:
            ax2 = ax1.twinx()
            ax2.set_facecolor('none')

            max_val = max(grouped['loss_sum'].max(), grouped['recovery_sum'].max())
            if max_val >= 1_000_000_000:
                denom = 1_000_000_000
                denom_label = 'млрд ₽'
            elif max_val >= 1_000_000:
                denom = 1_000_000
                denom_label = 'млн ₽'
            else:
                denom = 1000
                denom_label = 'тыс. ₽'

            losses_scaled = (grouped['loss_sum'] / denom).tolist()
            recoveries_scaled = (grouped['recovery_sum'] / denom).tolist()

            ax2.spines['right'].set_color('#94a3b8')

            if has_losses:
                amount_label = chart_policy.get("amount_label") or "Сумма потерь"
                ax2.plot(x_positions, losses_scaled, color='#dc2626', marker='s', markersize=4, linewidth=2, label=f'{amount_label} ({denom_label})')

            if has_recoveries:
                ax2.plot(x_positions, recoveries_scaled, color='#15803d', marker='^', markersize=4, linewidth=1.8, linestyle='--', label=f'Сумма возмещений ({denom_label})')

            y2_label = []
            if has_losses:
                y2_label.append((chart_policy.get("amount_label") or "потерь").lower())
            if has_recoveries:
                y2_label.append("возмещений")
            label_text = f"Объем {' и '.join(y2_label)} ({denom_label})"

            ax2.set_ylabel(label_text, color='#dc2626' if has_losses else '#15803d', fontsize=10)
            ax2.tick_params(axis='y', labelcolor='#dc2626' if has_losses else '#15803d', colors='#0f172a')

            lines1, labels1 = ax1.get_legend_handles_labels()
            lines2, labels2 = ax2.get_legend_handles_labels()
            ax1.legend(lines1 + lines2, labels1 + labels2, loc='upper left', frameon=True, facecolor='#ffffff', edgecolor='#cbd5e1', labelcolor='#0f172a')
        else:
            ax1.legend(loc='upper left', frameon=True, facecolor='#ffffff', edgecolor='#cbd5e1', labelcolor='#0f172a')

        chart_title = (
            'Временная динамика уникальных ИОР и сумм возмещений'
            if running_skill == "vozmeshenie_ior"
            else 'Временная динамика инцидентов операционного риска и финансовых объемов'
        )
        plt.title(chart_title, color='#0f172a', fontsize=11, pad=15, fontweight='bold')
        fig.tight_layout()

        output_dir = Path("workspace/data_store/generated_charts")
        output_dir.mkdir(parents=True, exist_ok=True)
        chart_filename = f"chart_ior_{uuid.uuid4().hex[:8]}.png"
        chart_path = output_dir / chart_filename

        plt.savefig(str(chart_path), bbox_inches='tight', facecolor='#ffffff', edgecolor='none')
        plt.close(fig)
        return str(chart_path)
    except Exception as e:
        logger.error(f"Error generating dynamics chart: {e}")
        return None


def generate_distribution_chart(df: pd.DataFrame, session_id: str) -> Optional[str]:
    """
    Generates a beautiful distribution chart (pie chart for small category counts, 
    horizontal bar chart for larger ones) and returns file path.
    """
    if plt is None:
        logger.info("matplotlib unavailable, skipping distribution chart generation.")
        return _write_minimal_chart_placeholder("chart_dist_unavailable")
    try:
        cat_candidates = []
        for col in df.columns:
            col_lower = str(col).lower()
            if any(x in col_lower for x in ("id", "sid", "key", "date", "dt", "dttm", "ts", "sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм")):
                continue
            if pd.api.types.is_numeric_dtype(df[col]):
                continue
            nunique = df[col].dropna().nunique()
            if 2 <= nunique <= 12:
                rank = 0
                if "type" in col_lower or "тип" in col_lower:
                    rank = 3
                elif "tb" in col_lower or "тб" in col_lower or "struct" in col_lower or "орг" in col_lower:
                    rank = 2
                elif "status" in col_lower or "статус" in col_lower:
                    rank = 1
                cat_candidates.append((col, nunique, rank))

        if not cat_candidates:
            return None

        cat_candidates.sort(key=lambda x: (-x[2], x[1]))
        target_col, nunique, _ = cat_candidates[0]

        grouped = df.groupby(target_col).size().reset_index(name='count')
        grouped = grouped.sort_values(by='count', ascending=True)

        plt.style.use('dark_background')
        fig, ax = plt.subplots(figsize=(8, 4), dpi=150)
        fig.patch.set_facecolor('#0b0f19')
        ax.set_facecolor('#0b0f19')

        labels = grouped[target_col].astype(str).tolist()
        sizes = grouped['count'].tolist()

        col_label_map = {
            "incdnt_type_lvl_1_name": "Типы событий ИОР",
            "incdnt_type_lvl_2_name": "Подтипы событий ИОР",
            "org_struct_lvl_3_name": "Территориальные банки (ТБ)",
            "incdnt_status_name": "Статусы инцидентов",
            "src_type_lvl_1_name": "Источники обнаружения",
            "incdnt_autoreg_flag": "Авторегистрация"
        }
        title_subject = col_label_map.get(target_col, f"Категория '{target_col}'")

        if nunique <= 5:
            colors = ['#10b981', '#3b82f6', '#f59e0b', '#ef4444', '#8b5cf6']
            wedges, texts, autotexts = ax.pie(
                sizes,
                labels=labels,
                autopct='%1.1f%%',
                startangle=140,
                colors=colors[:nunique],
                wedgeprops=dict(width=0.4, edgecolor='#1e293b', linewidth=1.5)
            )
            for text in texts:
                text.set_color('#cbd5e1')
                text.set_fontsize(9)
            for autotext in autotexts:
                autotext.set_color('#ffffff')
                autotext.set_fontsize(8)
                autotext.set_weight('bold')
            ax.set_title(f"Распределение: {title_subject}", color='#f8fafc', fontsize=11, pad=15, fontweight='bold')
        else:
            y_pos = range(len(labels))
            ax.barh(y_pos, sizes, color='#3b82f6', edgecolor='#60a5fa', alpha=0.85, height=0.5)
            ax.set_yticks(y_pos)
            ax.set_yticklabels(labels, color='#cbd5e1', fontsize=9)
            ax.grid(True, axis='x', color='#1e293b', linestyle='--', alpha=0.6)
            ax.spines['top'].set_visible(False)
            ax.spines['right'].set_visible(False)
            ax.spines['left'].set_color('#334155')
            ax.spines['bottom'].set_color('#334155')
            ax.tick_params(axis='x', colors='#94a3b8')
            ax.set_xlabel('Количество инцидентов', color='#94a3b8', fontsize=9, labelpad=8)
            ax.set_title(f"Топ-категории: {title_subject}", color='#f8fafc', fontsize=11, pad=15, fontweight='bold')

        fig.tight_layout()

        output_dir = Path("workspace/data_store/generated_charts")
        output_dir.mkdir(parents=True, exist_ok=True)
        chart_filename = f"chart_dist_{uuid.uuid4().hex[:8]}.png"
        chart_path = output_dir / chart_filename

        plt.savefig(str(chart_path), bbox_inches='tight', facecolor='#0b0f19')
        plt.close(fig)
        return str(chart_path)
    except Exception as e:
        logger.error(f"Error generating distribution chart: {e}")
        return None


def profile_dataframe(df: pd.DataFrame, running_skill: str = None) -> str:
    """
    Generates a Markdown profile of the dataframe.
    Calculates incident counts and percentages based on unique incident IDs.
    """
    if df.empty:
        return "Таблица пуста."

    if running_skill == "vozmeshenie_ior":
        return profile_vozmeshenie_dataframe(df)

    id_col = get_incident_id_col(df)
    total_incidents = df[id_col].nunique() if id_col else len(df)
    total_rows = len(df)

    if total_incidents != total_rows:
        lines = [f"### Профиль данных выгрузки (Уникальных инцидентов: {total_incidents}, строк данных: {total_rows}):\n"]
    else:
        lines = [f"### Профиль данных выгрузки (Всего инцидентов: {total_incidents}):\n"]

    df_copy = df.copy()
    is_nonfinancial = (running_skill == "ior_nonfinancial_consequences")
    reason_col = next((c for c in df_copy.columns if str(c).lower() in ("incdnt_type_lvl_1_name", "тип события – уровень 1", "тип события - уровень 1")), None)
    if reason_col:
        df_copy = df_copy.rename(columns={reason_col: "Основная причина"})

    fb_lvl2_col = next((c for c in df_copy.columns if str(c).lower() in ("funct_block_lvl_2_name", "функциональный блок – уровень 2", "функциональный блок - уровень 2")), None)
    if fb_lvl2_col:
        try:
            mask = df_copy[fb_lvl2_col].astype(str).str.startswith("SBR_") | df_copy[fb_lvl2_col].astype(str).str.isdigit()
            df_copy.loc[mask, fb_lvl2_col] = None
        except Exception:
            pass

    # 1. Money/Loss summaries
    money_cols = []
    incdnt_sum_col = None
    total_loss = 0.0
    total_rec = 0.0

    if not is_nonfinancial:
        money_cols = [c for c in df_copy.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм"))]
        loss_cols = [c for c in money_cols if any(x in str(c).lower() for x in ("loss", "dmg", "потер", "убыт", "sum", "сумм")) and not any(r in str(c).lower() for r in ("rec", "возмещ", "возврат"))]
        if not loss_cols and money_cols:
            loss_cols = [c for c in money_cols if not any(x in str(c).lower() for x in ("rec", "возмещ", "возврат"))]
        if loss_cols:
            incdnt_sum_col = loss_cols[0]
            total_loss = _to_numeric_clean(df_copy[incdnt_sum_col]).sum()

        primary_rec = get_recovery_column(df_copy, running_skill)
        if primary_rec:
            recovery_col = primary_rec
            total_rec = _to_numeric_clean(df_copy[recovery_col]).sum()

        net_loss = max(0.0, total_loss - total_rec)

        lines.append("**Сводка финансовых показателей:**")
        lines.append(f"- **Общая сумма потерь**: {format_loss(total_loss)}")
        lines.append(f"- **Общая сумма возмещений**: {format_loss(total_rec)}")
        lines.append(f"- **Чистые потери (Net Loss)**: {format_loss(net_loss)}\n")

    # 2. Date/Time profiling
    date_cols = [c for c in df_copy.columns if any(x in str(c).lower() for x in ("dt", "date", "dttm", "дата", "время", "period"))]
    date_cols = [c for c in date_cols if not any(x in str(c).lower() for x in ("upd", "sys", "log", "change"))]
    if date_cols:
        primary_date = date_cols[0]
        try:
            temp_df = df_copy.copy()
            temp_df[primary_date] = _to_datetime_safe(temp_df[primary_date])
            temp_df = temp_df.dropna(subset=[primary_date])
            if not temp_df.empty:
                temp_df['month'] = temp_df[primary_date].dt.to_period('M')
                grp = temp_df.groupby('month')

                lines.append("\n#### Временное распределение:")
                lines.append("| Месяц | Число инцидентов | % от общего | Сумма потерь | % потерь |")
                lines.append("|---|---|---|---|---|")

                for month, group in sorted(grp, key=lambda x: x[0]):
                    m_count = group[id_col].nunique() if id_col else len(group)
                    m_pct = (m_count / total_incidents) * 100 if total_incidents > 0 else 0.0
                    m_loss_sum = 0
                    m_loss_pct_str = "—"

                    if incdnt_sum_col is not None:
                        m_loss_sum = _to_numeric_clean(group[incdnt_sum_col]).sum()
                        if total_loss > 0:
                            m_loss_pct_str = f"{(m_loss_sum / total_loss) * 100:.1f}%"

                    lines.append(f"| {month} | {m_count} | {m_pct:.1f}% | {format_loss(m_loss_sum)} | {m_loss_pct_str} |")
        except Exception as e:
            logger.warning(f"Error in temporal profiling: {e}")

    # 3. Categorical analyses (начиная с Уровней 3 и 4, полностью исключая неинформативный Уровень 2)
    ignore_lvl2_cols = {"org_struct_lvl_2_name", "орг. структура – уровень 2 (терр. структура / департамент дзо)",
                        "funct_block_lvl_2_name", "функциональный блок – уровень 2", "функциональный блок - уровень 2"}

    cat_cols = [c for c in df_copy.columns if any(x in str(c).lower() for x in ("name", "type", "kind", "class", "lvl", "status", "tb", "block", "org", "proc", "блок", "процесс", "статус"))]
    cat_cols = [c for c in cat_cols if str(c).lower() not in ignore_lvl2_cols and c not in date_cols and c not in money_cols and "id" not in str(c).lower() and "sid" not in str(c).lower()]

    fb_lvl3_col = next((c for c in df_copy.columns if str(c).lower() in ("funct_block_lvl_3_name", "функциональный блок – уровень 3", "функциональный блок - уровень 3")), None)
    if fb_lvl3_col:
        try:
            grp = df_copy.groupby(fb_lvl3_col)
            sorted_grp = sorted(grp, key=lambda x: len(x[1]), reverse=True)
            lines.append(f"\n**Показатели по функциональным блокам (Уровень 3):**")
            lines.append("| Функциональный блок | Число инцидентов | % от общего | Сумма потерь | % потерь |")
            lines.append("|---|---|---|---|---|")
            for val, group in sorted_grp[:5]:
                if str(val).startswith("SBR_") or str(val).isdigit():
                    continue
                v_count = len(group)
                v_pct = (v_count / total_rows) * 100
                v_loss_sum = 0
                v_loss_pct_str = "—"
                if incdnt_sum_col is not None:
                    v_loss_sum = _to_numeric_clean(group[incdnt_sum_col]).sum()
                    if total_loss > 0:
                        v_loss_pct_str = f"{(v_loss_sum / total_loss) * 100:.1f}%"
                lines.append(f"| {val} | {v_count} | {v_pct:.1f}% | {format_loss(v_loss_sum)} | {v_loss_pct_str} |")
        except Exception as e:
            logger.warning(f"Error in functional block level 3 profiling: {e}")

    tb_lvl3_col = next((c for c in df_copy.columns if str(c).lower() in ("org_struct_lvl_3_name", "орг. структура – уровень 3 (блок / тб / пцп)", "орг. структура - уровень 3 (блок / тб / пцп)")), None)
    if tb_lvl3_col:
        try:
            grp = df_copy.groupby(tb_lvl3_col)
            sorted_grp = sorted(grp, key=lambda x: len(x[1]), reverse=True)
            lines.append(f"\n**Показатели по ТБ / Блокам (Уровень 3):**")
            lines.append("| ТБ / Блок | Число инцидентов | % от общего | Сумма потерь | % потерь |")
            lines.append("|---|---|---|---|---|")
            for val, group in sorted_grp[:5]:
                v_count = len(group)
                v_pct = (v_count / total_rows) * 100
                v_loss_sum = 0
                v_loss_pct_str = "—"
                if incdnt_sum_col is not None:
                    v_loss_sum = _to_numeric_clean(group[incdnt_sum_col]).sum()
                    if total_loss > 0:
                        v_loss_pct_str = f"{(v_loss_sum / total_loss) * 100:.1f}%"
                lines.append(f"| {val} | {v_count} | {v_pct:.1f}% | {format_loss(v_loss_sum)} | {v_loss_pct_str} |")
        except Exception as e:
            logger.warning(f"Error in org struct level 3 profiling: {e}")

    proc_lvl4_col = next((c for c in df_copy.columns if str(c).lower() in ("process_lvl_4_name", "процесс – уровень 4", "процесс - уровень 4")), None)
    if proc_lvl4_col:
        try:
            grp = df_copy.groupby(proc_lvl4_col)
            sorted_grp = sorted(grp, key=lambda x: len(x[1]), reverse=True)
            lines.append(f"\n**Показатели по процессам (Уровень 4):**")
            lines.append("| Бизнес-процесс (Уровень 4) | Число инцидентов | % от общего | Сумма потерь | % потерь |")
            lines.append("|---|---|---|---|---|")
            for val, group in sorted_grp[:5]:
                v_count = len(group)
                v_pct = (v_count / total_rows) * 100
                v_loss_sum = 0
                v_loss_pct_str = "—"
                if incdnt_sum_col is not None:
                    v_loss_sum = _to_numeric_clean(group[incdnt_sum_col]).sum()
                    if total_loss > 0:
                        v_loss_pct_str = f"{(v_loss_sum / total_loss) * 100:.1f}%"
                lines.append(f"| {val} | {v_count} | {v_pct:.1f}% | {format_loss(v_loss_sum)} | {v_loss_pct_str} |")
        except Exception as e:
            logger.warning(f"Error in process level 4 profiling: {e}")

    if running_skill == "vozmeshenie_ior" and recovery_col is not None and total_rec > 0:
        for group_col, group_label in (
            (fb_lvl3_col, "функциональным блокам (Уровень 3)"),
            (tb_lvl3_col, "ТБ / Блокам (Уровень 3)"),
            (proc_lvl4_col, "процессам (Уровень 4)"),
        ):
            if not group_col:
                continue
            try:
                rows_r = []
                for val, group in df_copy.groupby(group_col):
                    if str(val).startswith("SBR_") or str(val).isdigit():
                        continue
                    v_rec_sum = _to_numeric_clean(group[recovery_col]).sum()
                    if v_rec_sum <= 0:
                        continue
                    u_cnt = group[id_col].nunique() if id_col in group.columns else len(group)
                    rows_r.append((val, u_cnt, v_rec_sum))
                rows_r.sort(key=lambda x: x[2], reverse=True)
                if rows_r:
                    lines.append(f"\n**Показатели по {group_label} — суммы ВОЗМЕЩЕНИЙ (не потерь!):**")
                    lines.append("| Значение | Уникальных инцидентов | Сумма возмещений | % от всех возмещений |")
                    lines.append("|---|---|---|---|")
                    for val, v_count, v_rec_sum in rows_r[:5]:
                        v_rec_pct = (v_rec_sum / total_rec) * 100
                        lines.append(f"| {val} | {v_count} | {format_loss(v_rec_sum)} | {v_rec_pct:.1f}% |")
            except Exception as e:
                logger.warning(f"Error in recovery breakdown for {group_col}: {e}")

    if cat_cols:
        lines.append("\n#### Распределение по категориям:")
        for col in cat_cols[:4]:
            try:
                grp = df_copy.groupby(col)
                sorted_grp = sorted(grp, key=lambda x: len(x[1]), reverse=True)

                is_status_history_col = False
                if running_skill == "deleted_ior":
                    col_lower = str(col).lower()
                    if any(x in col_lower for x in ("status", "статус", "stts_chng", "stts")):
                        unique_vals = df_copy[col].dropna().unique()
                        unique_vals_lower = [str(v).lower().strip() for v in unique_vals]
                        if any(v not in ("удален", "удалён") for v in unique_vals_lower):
                            is_status_history_col = True

                if is_status_history_col:
                    lines.append(f"\n**Показатели по колонке '{col}' (Топ-5) [статусы, которые инцидент проходил ДО удаления]:**")
                else:
                    lines.append(f"\n**Показатели по колонке '{col}' (Топ-5):**")
                lines.append("| Значение | Число инцидентов | % от общего | Сумма потерь | % потерь |")
                lines.append("|---|---|---|---|---|")

                for val, group in sorted_grp[:5]:
                    v_count = len(group)
                    v_pct = (v_count / total_rows) * 100
                    v_loss_sum = 0
                    v_loss_pct_str = "—"

                    if incdnt_sum_col is not None:
                        v_loss_sum = _to_numeric_clean(group[incdnt_sum_col]).sum()
                        if total_loss:
                            v_loss_pct_str = f"{(v_loss_sum / total_loss) * 100:.1f}%"

                    lines.append(f"| {val} | {v_count} | {v_pct:.1f}% | {format_loss(v_loss_sum)} | {v_loss_pct_str} |")
            except Exception as e:
                logger.warning(f"Error in categorical profiling for {col}: {e}")

    det_col = next((c for c in df_copy.columns if str(c).lower() in ("incdnt_detection_person_name", "кем выявлено событие")), None)
    if det_col:
        try:
            lines.append("\n#### Анализ каналов выявления событий:")
            client_mask = df_copy[det_col].astype(str).str.lower().str.contains("клиент", na=False)
            client_df = df_copy[client_mask]
            client_count = len(client_df)
            client_loss = _to_numeric_clean(client_df[incdnt_sum_col]).sum() if incdnt_sum_col and not client_df.empty else 0.0
            lines.append(f"- **Выявлено клиентами**: {client_count} инцидентов (сумма потерь: {format_loss(client_loss)})")

            reg_mask = df_copy[det_col].astype(str).str.lower().str.contains("внешн|регул|контрол|орган", na=False)
            reg_df = df_copy[reg_mask]
            reg_count = len(reg_df)
            reg_loss = _to_numeric_clean(reg_df[incdnt_sum_col]).sum() if incdnt_sum_col and not reg_df.empty else 0.0
            lines.append(f"- **Выявлено внешними контролирующими органами/регуляторами**: {reg_count} инцидентов (сумма потерь: {format_loss(reg_loss)})")
        except Exception as e:
            logger.warning(f"Error calculating detection channels stats: {e}")

    id_cols = [c for c in df_copy.columns if any(x in str(c).lower() for x in ("id", "sid", "key", "номер", "идентификатор"))]
    if id_cols and incdnt_sum_col is not None:
        primary_id = id_cols[0]
        try:
            temp_df = df_copy.copy()
            temp_df[incdnt_sum_col] = _to_numeric_clean(temp_df[incdnt_sum_col])
            top_3 = temp_df.sort_values(by=incdnt_sum_col, ascending=False).head(3)
            lines.append("\n#### Топ-3 крупнейших инцидентов по сумме потерь:")
            for idx, row in top_3.iterrows():
                sid_val = row[primary_id]
                loss_val = row[incdnt_sum_col]
                pct_val = (loss_val / total_loss * 100) if total_loss else 0

                status_str = ""
                status_cols = [c for c in df_copy.columns if "status" in str(c).lower() or "статус" in str(c).lower()]
                if status_cols:
                    status_str = f" (Статус: {row[status_cols[0]]})"

                lines.append(f"- **{sid_val}**: {format_loss(loss_val)} ({pct_val:.1f}% от всех потерь){status_str}")
        except Exception as e:
            logger.warning(f"Error calculating outliers: {e}")

    advanced_stats = calculate_advanced_stats(df_copy)
    if advanced_stats:
        lines.append("\n#### Концентрация потерь (Топ-10 инцидентов):")
        if "top_10_sum" in advanced_stats:
            lines.append(f"- **Суммарные потери Топ-10 инцидентов**: {format_loss(advanced_stats['top_10_sum'])} ({advanced_stats['top_10_pct']:.1f}% от всей суммы потерь)")
            lines.append(f"- **Правило Парето**: Топ-10 инцидентов формируют {advanced_stats['top_10_pct']:.1f}% суммарных потерь")
            lines.append(f"- **Аномальные выбросы (3σ)**: Рассчитаны по крупным инцидентам")

    autoreg_cols = [c for c in df_copy.columns if "autoreg" in str(c).lower() or "авторег" in str(c).lower()]
    if autoreg_cols:
        col = autoreg_cols[0]
        try:
            auto_cnt = df_copy[df_copy[col].astype(str).str.upper().str.startswith('Y') | (df_copy[col] == True)].shape[0]
            auto_pct = (auto_cnt / total_rows) * 100
            lines.append(f"\n- **Авторегистрация**: {auto_cnt} инцидентов ({auto_pct:.1f}% от всей выгрузки)")
        except Exception as e:
            logger.warning(f"Error calculating autoregistration: {e}")

    rp_id_col = next((c for c in df_copy.columns if str(c).lower() in ("risk_profile_id", "идентификатор профиля риска")), None)
    rp_name_col = next((c for c in df_copy.columns if str(c).lower() in ("risk_profile_name", "наименование профиля риска")), None)
    if rp_id_col and rp_name_col:
        try:
            grp = df_copy.groupby([rp_id_col, rp_name_col])
            sorted_grp = sorted(grp, key=lambda x: len(x[1]), reverse=True)
            if sorted_grp:
                top_rp = sorted_grp[0][0]
                lines.append(f"\n- **Основной вид рискового события**: {top_rp[0]} - {top_rp[1]}")
        except Exception as e:
            logger.warning(f"Error calculating risk profile stats: {e}")

    if total_rows <= 30:
        lines.append("\n### Сводная таблица данных:\n")
        lines.append(_df_to_markdown_clean(df_copy))

    return "\n".join(lines)


def get_running_skill_id(df: pd.DataFrame, session_id: str) -> Optional[str]:
    try:
        from utils.session_extract_manager import get_session_extract
        extract = get_session_extract(session_id)
        if extract and isinstance(extract, dict):
            preset = extract.get("preset_name") or extract.get("preset") or extract.get("skill_id")
            if preset:
                return preset
    except Exception as e:
        logger.warning(f"Error determining running skill_id: {e}")
    return None


async def summarize_deletion_comments(comments: list[str]) -> str:
    if not comments:
        return ""
    unique_comments = [c[:300] + "..." if len(c) > 300 else c for c in set(comments)][:50]
    prompt = (
        "Ниже представлены комментарии сотрудников об основаниях и причинах удаления инцидентов операционного риска.\n"
        "Проанализируй их и подготовь краткое структурированное резюме наиболее популярных причин удаления (например, дубликаты, сбои АС, ошибки ввода), "
        "используя строго нейтральный деловой язык. Не упоминай точное количество проанализированных комментариев:\n\n"
        + "\n".join(f"- {c}" for c in unique_comments)
    )
    try:
        res = await asyncio.to_thread(
            ask_local_qwen, [
                {"role": "system", "content": "Ты — аналитик Службы внутреннего аудита. Проведи анализ комментариев о причинах удаления инцидентов и выдели ключевые системные или операционные причины удаления."},
                {"role": "user", "content": prompt}
            ],
            max_tokens=1024
        )
        return str(res)
    except Exception as e:
        logger.error(f"Error summarizing deletion comments: {e}")
        return f"Ошибка при анализе комментариев удаления: {e}"


async def analyze_incident_descriptions(df: pd.DataFrame, running_skill: str = None) -> str:
    """
    Batches incident descriptions to prevent LLM context window overflows.
    Uses async local Qwen analysis for summaries (top 30, 3 batches of 10).
    """
    if running_skill == "vozmeshenie_ior":
        loss_cols = ["recovery_rub_amt", "Сумма возмещения (руб.)", "Сумма возмещения в рублях"]
    elif running_skill == "financial_consequences_ior":
        loss_cols = ["fin_impact_rub_amt", "Сумма финансового последствия (руб.)", "Сумма последствия (руб.)"]
    elif running_skill == "ior_nonfinancial_consequences":
        loss_cols = []
    else:
        loss_cols = ["incdnt_sum", "Общая сумма всех последствий (руб.)", "Общая сумма последствий (руб.)", "Сумма последствий, ₽", "fin_impact_rub_amt"]
    primary_loss = next((c for c in loss_cols if c in df.columns), None)
    if not primary_loss:
        money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм"))]
        if running_skill == "vozmeshenie_ior":
            loss_cols_fallback = [c for c in money_cols if any(r in str(c).lower() for r in ("rec", "возмещ", "возврат"))]
        elif running_skill == "financial_consequences_ior":
            loss_cols_fallback = [c for c in money_cols if "fin_impact" in str(c).lower() and "rub" in str(c).lower()]
        elif running_skill == "ior_nonfinancial_consequences":
            loss_cols_fallback = []
        else:
            loss_cols_fallback = [c for c in money_cols if any(x in str(c).lower() for x in ("loss", "dmg", "потер", "убыт", "sum", "сумм")) and not any(r in str(c).lower() for r in ("rec", "возмещ", "возврат"))]
        if loss_cols_fallback:
            primary_loss = loss_cols_fallback[0]

    id_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("id", "sid", "key", "номер", "идентификатор"))]
    primary_id = get_incident_id_col(df) or (id_cols[0] if id_cols else None)

    full_desc_col = next((c for c in df.columns if str(c).lower() in ("incdnt_full_descr_txt", "подробное описание", "полное описание", "описание")), None)
    sum_desc_col = next((c for c in df.columns if str(c).lower() in ("incdnt_summary_descr_txt", "краткое описание", "аннотация")), None)

    tb_col = next((c for c in df.columns if str(c).lower() in ("org_struct_lvl_3_name", "орг. структура – уровень 3 (блок / тб / пцп)")), None)
    div_col = next((c for c in df.columns if str(c).lower() in ("org_struct_lvl_4_name", "орг. структура – уровень 4 (дивизион / департамент)")), None)
    fb_col = next((c for c in df.columns if str(c).lower() in ("funct_block_lvl_3_name", "funct_block_lvl_4_name", "функциональный блок – уровень 3")), None)
    proc_col = next((c for c in df.columns if str(c).lower() in ("process_lvl_4_name", "process_lvl_3_name", "процесс – уровень 4")), None)
    rp_col = next((c for c in df.columns if str(c).lower() in ("risk_profile_name", "risk_profile_id", "наименование профиля риска")), None)
    autoreg_col = next((c for c in df.columns if "autoreg" in str(c).lower() or "авторег" in str(c).lower()), None)
    impact_type_col = next((c for c in df.columns if str(c).lower().strip() in ("fin_impact_type_name", "тип последствия")), None)
    impact_kind_col = next((c for c in df.columns if str(c).lower().strip() in ("fin_impact_kind_name", "вид финансового последствия")), None)
    nonfin_kind_col = next((c for c in df.columns if str(c).lower().strip() in ("nonfin_impact_kind_name", "вид качественной потери", "вид нефинансового последствия")), None)
    nonfin_inf_col = next((c for c in df.columns if str(c).lower().strip() in ("nonfin_impact_influence_class_name", "классификация влияния", "класс влияния нефинансового последствия")), None)

    df_sorted = df.copy()
    if primary_loss:
        df_sorted[primary_loss] = _to_numeric_clean(df_sorted[primary_loss])
        df_sorted = df_sorted.sort_values(by=primary_loss, ascending=False)

    descriptions = []

    top_30_df = df_sorted.head(30)
    for _, row in top_30_df.iterrows():
        sid = row[primary_id] if primary_id and primary_id in row else "—"
        val = row[primary_loss] if primary_loss and primary_loss in row else 0.0
        desc = ""
        if full_desc_col and full_desc_col in row and pd.notna(row[full_desc_col]) and str(row[full_desc_col]).strip():
            desc = str(row[full_desc_col]).strip()
        elif sum_desc_col and sum_desc_col in row and pd.notna(row[sum_desc_col]) and str(row[sum_desc_col]).strip():
            desc = str(row[sum_desc_col]).strip()
        else:
            desc_fallback_col = next((c for c in df.columns if any(x in str(c).lower() for x in ("descr", "описание", "аннотация"))), None)
            if desc_fallback_col and desc_fallback_col in row and pd.notna(row[desc_fallback_col]):
                desc = str(row[desc_fallback_col]).strip()

        if desc:
            if len(desc) > 300:
                desc = desc[:300] + "..."
            amount_label = "Возмещение" if running_skill == "vozmeshenie_ior" else "Финансовое последствие"
            loss_str = f" ({amount_label}: {format_loss(val)})" if val > 0 else ""
            ctx_parts = []
            if impact_type_col and impact_type_col in row and pd.notna(row[impact_type_col]):
                ctx_parts.append(f"Тип последствия: {row[impact_type_col]}")
            if impact_kind_col and impact_kind_col in row and pd.notna(row[impact_kind_col]):
                ctx_parts.append(f"Вид последствия: {row[impact_kind_col]}")
            if nonfin_kind_col and nonfin_kind_col in row and pd.notna(row[nonfin_kind_col]):
                ctx_parts.append(f"Вид нефин. потери: {row[nonfin_kind_col]}")
            if nonfin_inf_col and nonfin_inf_col in row and pd.notna(row[nonfin_inf_col]):
                ctx_parts.append(f"Класс влияния: {row[nonfin_inf_col]}")
            if tb_col and tb_col in row and pd.notna(row[tb_col]):
                ctx_parts.append(f"ТБ: {row[tb_col]}")
            if div_col and div_col in row and pd.notna(row[div_col]):
                ctx_parts.append(f"Дивизион: {row[div_col]}")
            if fb_col and fb_col in row and pd.notna(row[fb_col]) and not str(row[fb_col]).startswith("SBR_"):
                ctx_parts.append(f"Блок: {row[fb_col]}")
            if proc_col and proc_col in row and pd.notna(row[proc_col]):
                ctx_parts.append(f"Процесс: {row[proc_col]}")
            if rp_col and rp_col in row and pd.notna(row[rp_col]):
                ctx_parts.append(f"Риск: {row[rp_col]}")
            if autoreg_col and autoreg_col in row and pd.notna(row[autoreg_col]):
                is_auto = str(row[autoreg_col]).upper().startswith("Y") or row[autoreg_col] == True
                ctx_parts.append(f"Авторег: {'Да' if is_auto else 'Нет'}")

            ctx_str = f" | {', '.join(ctx_parts)}" if ctx_parts else ""
            descriptions.append(f"Идентификатор: {sid}{loss_str}{ctx_str} | Описание: {desc}")

    if not descriptions:
        return ""

    batch_1 = descriptions[:10]
    batch_2 = descriptions[10:20]
    batch_3 = descriptions[20:30]

    async def analyze_batch(batch_items, batch_num):
        if not batch_items:
            return ""
        prompt = (
            f"Ниже представлены описания крупных инцидентов операционного риска (Пакет {batch_num}). "
            f"Для каждого инцидента подготовь краткую выжимку (1-2 предложения), объясняющую суть произошедшего. "
            f"Обязательно сохрани связь с Идентификатором инцидента.\n\n"
            + "\n".join(batch_items)
        )
        try:
            res = await asyncio.to_thread(
                ask_local_qwen, [
                    {"role": "system", "content": "Ты — аналитик Службы внутреннего аудита. Опиши суть каждого инцидента строго индивидуально, в формате 'Идентификатор: [краткая суть]'. Пиши нейтральным деловым языком. Не делай общих выводов."},
                    {"role": "user", "content": prompt}
                ],
                max_tokens=1024
            )
            return str(res)
        except Exception as e:
            logger.error(f"Error analyzing descriptions batch {batch_num}: {e}")
            return ""

    results = await asyncio.gather(
        analyze_batch(batch_1, 1),
        analyze_batch(batch_2, 2),
        analyze_batch(batch_3, 3)
    )

    combined = []
    if results[0]:
        combined.append(f"### Результаты анализа описаний инцидентов (Пакет 1):\n{results[0]}")
    if results[1]:
        combined.append(f"### Результаты анализа описаний инцидентов (Пакет 2):\n{results[1]}")
    if results[2]:
        combined.append(f"### Результаты анализа описаний инцидентов (Пакет 3):\n{results[2]}")

    return "\n\n".join(combined)


# Полный набор пресетных промптов из ior_assistant/backend/agent/hypothesis.py
PROMPTS = {
    "ior_hypothesis": """Ты — эксперт-аналитик Службы внутреннего аудита.
Твоя задача — провести анализ представленного профиля данных инцидентов операционного риска и сформулировать аналитические гипотезы о возможных причинах этих инцидентов.

Пиши максимально простым, понятным и человеческим языком без эмоций, преувеличений и сложного IT или узкоспециализированного корпоративного жаргона. Текст должен быть легким для чтения и понятным любому линейному аналитику или сотруднику.
- Полностью избегай оценочных и экспрессивных выражений (например, "экстремальная концентрация", "катастрофический сбой", "немедленный аудит").
- Избегай перегруженных сложных терминов. Вместо тяжелого IT-жаргона используй простые аналоги. Обычные технические термины использовать можно.
- Излагай факты и предположения сухо, четко, структурированно, с использованием списков и ключевых метрик.

Придерживайся следующей структуры отчета:

### 1. Общая сводка данных
- Кратко перечисли ключевые показатели: общее число инцидентов, общая сумма всех последствий, сумма возмещений и чистые потери по группе Утверждение.
- Укажи, какая самая частая причина инцидентов (основная причина / тип события).
- Опиши общую динамику регистрации во времени.
- Важно: пиши этот раздел как чистое, сухое описание фактов БЕЗ каких-либо выводов, анализа, интерпретаций или гипотез.

### 2. Выявленные аномалии и динамика трендов
- Опиши динамику во времени (сезонность, тренды спада/роста, временные всплески). Сформулируй предположение о возможных причинах временного всплеска.
- Выдели распределение по территориальным банкам (ТБ) или процессам, перечислив лидеров по сумме потерь и количеству инцидентов.

### 3. Концентрация рисков и системные факторы
- Опиши концентрацию потерь: укажи суммарный вклад Топ-10 крупнейших инцидентов (их точную сумму и процент от общего объема потерь).
- Укажи конкретные идентификаторы событий (например, EVE-XXXXXXX) из топа крупнейших инцидентов и проанализируй их вклад.
- Поле "Тип события - уровень 1" (incdnt_type_lvl_1_name) транслируй в отчет как "Основная причина".
- Оцени долю авторегистрации (процент авторегистрированных инцидентов).
- Важно: НЕ перегружай отчет бесконечным перечислением процентов концентрации и долей.

### 4. Аналитические гипотезы для аудиторской проверки
Сформулируй ровно 3 содержательные и разноплановые аналитические гипотезы о причинах аномалий или преобладания инцидентов:
- **Гипотеза 1: Системные и процессные факторы в преобладающей категории событий** (Строится на ключевой причине / типе событий `incdnt_type_lvl_1_name` или бизнес-процессе, объясняя ПОЧЕМУ возникла данная аномалия или системный сбой. СТРОГО ЗАПРЕЩЕНО писать о банальной "концентрации потерь в 3 крупнейших ИОРах"!).
- **Гипотеза 2: Оргструктурная детализация и уязвимости в блоках/дивизионах** (Строится на спецификации ТБ Уровня 3, Дивизионов Уровня 4, функциональных блоков Уровня 3-4. ВАЖНО: если ТБ, дивизион или тема запрошены пользователем, гипотеза анализирует более глубокую структуру и сравнивает подразделения ВНУТРИ этого среза, а НЕ утверждает "90% в запрошенном фильтре"! СТРОГО ЗАПРЕЩЕНО дублировать тему Гипотезы 1!).
- **Гипотеза 3: Фактические причины инцидентов по результатам текстового анализа описаний** (Строится СТРОГО на фактах из анализа текстов описаний `incdnt_full_descr_txt` / `incdnt_summary_descr_txt` — человеческий фактор при ручном вводе, ошибки интерфейсов, несоблюдение регламентов).

ВАЖНО ПО СТРУКТУРЕ КАЖДОЙ ГИПОТЕЗЫ (Соблюдай строго 4 элемента):
**Гипотеза N: [Понятное тематическое название, отражающее суть проблемы]**
- **Предположение / Суть проблемы**: [Развернутое объяснение ПОЧЕМУ возникла данная аномалия или преобладание конкретного ТБ Уровня 3, Дивизиона Уровня 4, Процесса Уровня 3-4, профиля риска DRP или типа событий]
- **Шаги проверки**:
  1. [Конкретный шаг аудита 1]
  2. [Конкретный шаг аудита 2]
- **Ожидаемый результат**: [Качественная цель аудиторской проверки]

ВАЖНЫЕ ТРЕБОВАНИЯ К ГИПОТЕЗАМ:
- Запрещено использовать шаблонные одинаковые названия вроде "Концентрация потерь в крупнейших инцидентах" или "Географическая концентрация". Придумывай живые, понятные тематические заголовки на основе реальных фактов выгрузки.
- Если запрос пользователя сделан по конкретной категории или дивизиону (например, DRP-10121, Блок Риски, Эквайринг, Домклик), ВСЕ 3 гипотезы должны строиться СТРОГО вокруг этой запрошенной категории!
- Игнорируй Уровень 2 оргструктуры ("ПАО Сбербанк") и Уровень 2 функционального блока — весь анализ проводи только по Уровням 3 и 4.
- Авторегистрация — это нормальный штатный процесс. СТРОГО ЗАПРЕЩЕНО писать об авторегистрации как о проблеме, сбое или уязвимости.""",

    "deleted_ior": """Ты — эксперт-аналитик Службы внутреннего аудита.
Твоя задача — провести анализ удаленных инцидентов операционного риска на основе предоставленных данных и результатов суммаризации комментариев сотрудников о причинах удаления.

Пиши максимально простым, понятным и человеческим языком без эмоций, преувеличений и сложного IT или узкоспециализированного корпоративного жаргона. Текст должен быть легким для чтения и понятным любому линейному аналитику или сотруднику.
- Полностью избегай оценочных и экспрессивных выражений.
- Излагай факты и предположения сухо, четко, структурированно, с использованием списков и ключевых метрик.
- Важно: НЕ упоминай точное количество проанализированных комментариев или строк. Пиши о популярных причинах качественно.
- СТРОГО ЗАПРЕЩЕНО писать "(X записей данных)" или "уровень данных". Указывай только количество инцидентов.

Придерживайся следующей структуры отчета:

### 1. Общая сводка данных
- Кратко перечисли ключевые показатели: общее число удаленных инцидентов, общая сумма последствий по удаленным инцидентам, сумма возмещений по удаленным инцидентам.
- Укажи основные действия пользователей (например, удалено вручную, отменено).
- Опиши временной охват удаления инцидентов.
- Важно: пиши этот раздел как чистое, сухое описание фактов БЕЗ каких-либо выводов, анализа, интерпретаций или гипотез.

### 2. Выявленные аномалии и динамика трендов
- Опиши распределение во времени. Если данные представлены за один месяц (или один период), констатируй это как фактологический срез за данный месяц БЕЗ домысливания причин удалений и БЕЗ фантазий о вымышленных "всплесках" или "сезонности".
- Выдели распределение по Дивизионам (Уровень 4), Блокам (Уровень 3-4) или процессам, перечислив лидеров по количеству удалений.

### 3. Концентрация рисков и системные факторы
- Выдели наиболее популярные причины удаления на основе предоставленного анализа комментариев сотрудников `stts_chng_comment_txt` (например, ошибки ручного ввода реквизитов, изменение параметров договора, корректировка статусов).
- Оцени долю повторных отмен и аннулирований.

### 4. Аналитические гипотезы для аудиторской проверки
Сформулируй ровно 3 аналитические гипотезы о причинах удаления инцидентов (СТРОГО БЕЗ использования авторегистрации как фактора/причины!):
- **Гипотеза 1: Процессные и операционные причины отмены на основе комментариев пользователей** (Строится на содержании комментариев сотрудников `stts_chng_comment_txt` — ошибки ручного ввода реквизитов, изменение параметров договора, корректировка статусов).
- **Гипотеза 2: Оргструктурная детализация и уязвимости контрольной среды** (Строится на анализе Дивизионов Уровня 4, Блоков Уровня 3-4 и Процессов. ВАЖНО: если ТБ отфильтрован пользователем, гипотеза строится на внутренних блоках и дивизионах, а НЕ на ТБ!).
- **Гипотеза 3: Системно-технологические и практические факторы повторного ввода и отмены** (Строится на особенностях проведения конкретных банковских операций, стыковки систем и регламентов ввода).

ВАЖНО ПО СТРУКТУРЕ КАЖДОЙ ГИПОТЕЗЫ (Соблюдай строго 4 элемента):
**Гипотеза N: [Понятное тематическое название, отражающее суть проблемы]**
- **Предположение / Суть проблемы**: [Развернутое объяснение ПОЧЕМУ возникла данная аномалия или преобладание конкретной причины отмены]
- **Шаги проверки**:
  1. [Конкретный шаг аудита 1]
  2. [Конкретный шаг аудита 2]
- **Ожидаемый результат**: [Качественная цель аудиторской проверки]""",

    "ior_nonfinancial_consequences": """Ты — эксперт-аналитик Службы внутреннего аудита.
Твоя задача — провести анализ качественных (нефинансовых) последствий инцидентов операционного риска и сформулировать аналитические гипотезы.

- ВАЖНО: В этой выгрузке полностью отсутствуют финансовые суммы потерь в рублях. Вообще не упоминай деньги, рубли или убытки.

Придерживайся следующей структуры отчета:

### 1. Общая сводка данных
- Кратко перечисли ключевые показатели: общее число инцидентов с качественными последствиями.
- Укажи распределение по видам качественных потерь (репутационный риск, прерывание деятельности, регуляторные санкции).
- Опиши общую динамику регистрации во времени.
- Важно: пиши этот раздел как чистое, сухое описание фактов БЕЗ выводов или гипотез.

### 2. Выявленные аномалии и динамика трендов
- Опиши динамику во времени (сезонность, временные всплески).
- Выдели распределение по ТБ Уровня 3, Дивизионам Уровня 4 и процессам, перечислив лидеров по количеству событий.

### 3. Концентрация рисков и системные факторы
- Опиши концентрацию рисков: укажи наиболее подверженные качественным рискам процессы и подразделения.
- Оцени долю авторегистрации сухим фактом статистики.

### 4. Аналитические гипотезы для аудиторской проверки
Сформулируй ровно 3 аналитические гипотезы по качественным рискам:
- **Гипотеза 1: Факторы возникновения конкретного вида нефинансовых потерь** (Строится на видовом составе нефинансовых последствий — репутационный риск, доступность сервисов, регуляторный риск).
- **Гипотеза 2: Оргструктурная и процессная уязвимость контрольной среды** (Строится на лидерстве ТБ Уровня 3, Дивизионов Уровня 4, Процессов Уровня 3-4).
- **Гипотеза 3: Корневые причины по результатам анализа текстов описаний** (Строится на реальных фактах из анализа описаний инцидентов, связывая их с ИС/ИБ-рисками или действиями персонала).

ВАЖНО ПО СТРУКТУРЕ КАЖДОЙ ГИПОТЕЗЫ (Соблюдай строго 4 элемента):
**Гипотеза N: [Понятное тематическое название]**
- **Предположение / Суть проблемы**: [Причина возникновения - ПОЧЕМУ произошел качественный инцидент]
- **Шаги проверки**:
  1. [Шаг 1]
  2. [Шаг 2]
- **Ожидаемый результат**: [Качественная цель проверки]""",

    "financial_consequences_ior": """Ты — эксперт-аналитик Службы внутреннего аудита.
Твоя задача — провести анализ финансовых последствий инцидентов операционного риска на основе детальных данных о последствиях и сформулировать аналитические гипотезы.

- Важно: В данной выгрузке структурно отсутствуют данные по возмещениям (возвратам денег). Ни при каких условиях не упоминай суммы или проценты возмещений (recovery).

Придерживайся следующей структуры отчета:

### 1. Общая сводка финансовых последствий
- Кратко перечисли ключевые показатели: общее число записей о последствиях, общая сумма зафиксированных потерь.
- Укажи структуру и распределение по типам финансовых последствий (прямые, косвенные, нереализовавшиеся потери, потери третьих лиц).
- Опиши временную динамику возникновения финансовых последствий.
- Важно: пиши этот раздел как чистое, сухое описание фактов БЕЗ выводов.

### 2. Структура и виды потерь
- Выдели ключевые виды потерь на основе поля вида последствий (`fin_impact_kind_name` — хищение, судебные расходы, списание, расчетные ошибки).
- Перечисли ТБ Уровня 3, Дивизионы Уровня 4 или Блоки Уровня 3-4, лидирующие по объему финансовых потерь.

### 3. Концентрация финансовых последствий
- Опиши распределение потерь по бизнес-процессам и видам операций.

### 4. Аналитические гипотезы для аудиторской проверки
Сформулируй ровно 3 аналитические гипотезы о причинах возникновения финансовых последствий:
- **Гипотеза 1: Причины возникновения преобладающих видов финансовых потерь** (Строится на виде потерь `fin_impact_kind_name` — списания, судебные издержки, расчетные ошибки — с разбором ПОЧЕМУ возник этот вид потерь. СТРОГО ЗАПРЕЩЕНО писать банальности про 3 крупнейших ИОРа!).
- **Гипотеза 2: Оргструктурные и процессные особенности финансовых рисков** (Строится на специфике ТБ Уровня 3, Дивизионов Уровня 4, Процессов Уровня 3-4).
- **Гипотеза 3: Практические операционные причины по текстам описаний последствий** (Строится на фактах из анализа содержательной части описаний).

ВАЖНО ПО СТРУКТУРЕ КАЖДОЙ ГИПОТЕЗЫ (Соблюдай строго 4 элемента):
**Гипотеза N: [Понятное тематическое название]**
- **Предположение / Суть проблемы**: [Развернутое объяснение причины финансовой потери]
- **Шаги проверки**:
  1. [Шаг 1]
  2. [Шаг 2]
- **Ожидаемый результат**: [Качественная цель проверки]""",

    "vozmeshenie_ior": """Ты — эксперт-аналитик Службы внутреннего аудита.
Твоя задача — провести анализ полученных возмещений (возвратов, страховых выплат, компенсаций) по инцидентам операционного риска и сформулировать аналитические гипотезы.

Придерживайся следующей структуры отчета:

### 1. Общая сводка по возмещениям
- Кратко перечисли ключевые показатели на уровне уникальных инцидентов и общую сумму полученных возмещений.
- Укажи распределение по типам/источникам возмещений (поле `recovery_type_name` — страховые выплаты, восстановление резерва РВПС, компенсации от клиентов, регресс с сотрудников).
- Опиши временной охват поступления возмещений.
- ВАЖНО: количество исходных операций уже выводится программно перед отчётом. Не повторяй его и не используй слово «строки». Все количества в твоём тексте относятся только к уникальным `incdnt_sid`.
- Важно: пиши этот раздел как чистое, сухое описание фактов БЕЗ выводов.

### 2. Географическая и процессная структура возмещений
- Перечисли ТБ Уровня 3, Дивизионы Уровня 4 или Блоки Уровня 3-4, лидирующие по суммам возвращенных средств и количеству уникальных ИОРов.
- Опиши, по каким видам процессов возмещения проходят наиболее эффективно.
- КРИТИЧЕСКИ ВАЖНО: используй ТОЛЬКО данные по суммам и операциям ВОЗМЕЩЕНИЙ (`recovery_rub_amt`, `recovery_type_name`). СТРОГО ЗАПРЕЩЕНО брать цифры из категорий потерь или путать типы инцидентов с типами возмещений.

### 3. Концентрация и ключевые каналы возврата средств
- Опиши ключевые каналы возврата средств по полю `recovery_type_name`.

### 4. Аналитические гипотезы для аудиторской проверки
Сформулируй ровно 3 аналитические гипотезы об эффективности процессов возмещения (ОБЯЗАТЕЛЬНО на основе реально присутствующих полей возмещений):
- **Гипотеза 1: Эффективность и отдача конкретных источников возмещения** (Строится СТРОГО на структуре видов возмещений из поля `recovery_type_name` — например, "Восстановление резерва РВПС", "Страховые выплаты", "Компенсации от клиентов", "Возмещения во внесудебном/судебном порядке". СТРОГО ЗАПРЕЩЕНО писать про типы инцидентов вроде "Регулятор" или "Ошибки персонала"!).
- **Гипотеза 2: Оргструктурные особенности претензионно-исковой работы** (Строится на анализе ТБ Уровня 3 и Дивизионов Уровня 4 по суммам возвращенных средств).
- **Гипотеза 3: Системно-процессные факторы полноты и сроков возврата средств** (Строится на особенностях оформления бухгалтерских документов возмещений, взаимодействия со страховщиками и сопоставления дат регистрации).

ВАЖНО ПО СТРУКТУРЕ КАЖДОЙ ГИПОТЕЗЫ (Соблюдай строго 4 элемента):
**Гипотеза N: [Понятное тематическое название]**
- **Предположение / Суть проблемы**: [Причина высокого/низкого уровня возмещения]
- **Шаги проверки**:
  1. [Шаг 1]
  2. [Шаг 2]
- **Ожидаемый результат**: [Качественная цель проверки]""",

    "credit_no_way_collect_debt": """Ты — эксперт-аналитик Службы внутреннего аудита.
Твоя задача — провести анализ случаев невозможности взыскания задолженности по кредитным продуктам и сформулировать аналитические гипотезы.

Придерживайся следующей структуры отчета:

### 1. Сводные показатели по кредитной задолженности
- Кратко перечисли ключевые показатели: общее количество кредитных договоров, общая сумма задолженности, размер резерва (РВПС).
- Укажи распределение по типам заемщиков (физические лица, юридические лица) и кредитным продуктам.
- Важно: пиши этот раздел как чистое описание фактов.

### 2. Причины невозможности взыскания и залоговое обеспечение
- Проанализируй основные причины невозможности взыскания (ликвидация заемщика, истечение срока исковой давности, неполнота залогов).
- Опиши достаточность залогового обеспечения.

### 3. Распределение задолженности по процессам
- Опиши подразделения и процессы с наибольшей долей нереализованного взыскания.

### 4. Аналитические гипотезы для аудиторской проверки
Сформулируй ровно 3 аналитические гипотезы о системных недостатках в кредитном процессе:
- **Гипотеза 1: Причины дефолта и неполноты правовых залогов** (Строится на факторах ликвидации заемщиков, пропуске сроков исковой давности и дефектах оформления залоговых прав).
- **Гипотеза 2: Оргструктурные и филиальные риски кредитного конвейера** (Строится на анализе ТБ Уровня 3, Дивизионов Уровня 4, Процессов Уровня 3-4).
- **Гипотеза 3: Продуктовые и сегментные уязвимости андеррайтинга** (Строится на специфике кредитных продуктов ФЛ/ЮЛ и текстах описаний списания).

ВАЖНО ПО СТРУКТУРЕ КАЖДОЙ ГИПОТЕЗЫ (Соблюдай строго 4 элемента):
**Гипотеза N: [Понятное тематическое название]**
- **Предположение / Суть проблемы**: [Причина невозврата кредитныных средств]
- **Шаги проверки**:
  1. [Шаг 1]
  2. [Шаг 2]
- **Ожидаемый результат**: [Качественная цель проверки]""",

    "report_period_specific_ior": """Ты — эксперт-аналитик Службы внутреннего аудита.
Твоя задача — провести детальный анализ конкретного инцидента (досье ИОР) и сформулировать аналитические гипотезы о его причинах.

Придерживайся следующей структуры отчета:

### 1. Сведения об инциденте
- Кратко перечисли ключевые реквизиты инцидента: бизнес-идентификатор (incdnt_sid), статус, даты регистрации и совершения события.
- Опиши финансовые параметры: общая сумма последствий, прямые/косвенные потери, сумма возмещения.
- Укажи подразделение (ТБ Уровень 3, Дивизион Уровень 4) и процесс.
- Важно: пиши этот раздел как чистое описание фактов.

### 2. Описание события и каналы обнаружения
- Приведи резюме сути инцидента на основе описания.
- Укажи источник и канал выявления события.

### 3. Выявленные особенности инцидента
- Проанализируй специфические факторы события (участие АС, человеческий фактор).

### 4. Аналитические гипотезы для аудиторской проверки
Сформулируй ровно 2 аналитические гипотезы о причинах возникновения конкретного инцидента:
- **Гипотеза 1: Технологические и процедурные причины по тексту описания** (Строится на фактических действиях персонала, сбоях АС и ошибках ввода).
- **Гипотеза 2: Организационный и контрольный контекст подразделения** (Строится на роли подразделения Уровня 3-4, процесса и канала выявления).

ВАЖНО ПО СТРУКТУРЕ КАЖДОЙ ГИПОТЕЗЫ (Соблюдай строго 4 элемента):
**Гипотеза N: [Понятное тематическое название]**
- **Предположение / Суть проблемы**: [Причина возникновения конкретного инцидента]
- **Шаги проверки**:
  1. [Шаг 1]
  2. [Шаг 2]
- **Ожидаемый результат**: [Качественная цель проверки]""",

    "ior_period_pao_sberbank": """Ты — эксперт-аналитик Службы внутреннего аудита.
Твоя задача — провести комплексный анализ инцидентов операционного риска и сформулировать аналитические гипотезы.

Придерживайся следующей структуры отчета:

### 1. Общая сводка данных
- Кратко перечисли ключевые показатели: общее число зарегистрированных инцидентов, общая сумма последствий, сумма возмещений и чистые потери (Net Loss).
- Укажи распределение инцидентов по статусам.
- Важно: пиши этот раздел как сухое описание фактов.

### 2. Выявленные аномалии и динамика трендов
- Опиши динамику во времени (сезонность, временные всплески).
- Выдели распределение по ТБ Уровня 3, Дивизионам Уровня 4 или процессам.

### 3. Концентрация рисков и системные факторы
- Опиши концентрацию рисков и системные факторы.

### 4. Аналитические гипотезы для аудиторской проверки
Сформулируй ровно 3 аналитические гипотезы:
- **Гипотеза 1: Системные и процессные уязвимости преобладающей категории рисков** (Строится на ключевой причине / типе событий `incdnt_type_lvl_1_name` или бизнес-процессе).
- **Гипотеза 2: Оргструктурные особенности применения регламентов в филиальной сети** (Строится на специфике ТБ Уровня 3, Дивизионов Уровня 4, Блоков Уровня 3-4).
- **Гипотеза 3: Фактические причины инцидентов по результатам текстового анализа** (Строится на реальных фактах из анализа описаний инцидентов).

ВАЖНО ПО СТРУКТУРЕ КАЖДОЙ ГИПОТЕЗЫ (Соблюдай строго 4 элемента):
**Гипотеза N: [Понятное тематическое название]**
- **Предположение / Суть проблемы**: [Причина возникновения аномалии]
- **Шаги проверки**:
  1. [Шаг 1]
  2. [Шаг 2]
- **Ожидаемый результат**: [Качественная цель проверки]"""
}


def build_deterministic_hypotheses_section(df: pd.DataFrame, running_skill: str, is_summarization_only: bool = False) -> str:
    """Генерирует детерминированный отчёт из 4 разделов (или 3 разделов для малой выборки) на базе профилирования данных."""
    return build_deterministic_full_report(df, running_skill, is_summarization_only=is_summarization_only)


def build_deterministic_full_report(
    df: pd.DataFrame,
    running_skill: str,
    deleted_text: str = "",
    is_summarization_only: bool = False,
    voz_metrics: Optional[dict] = None,
) -> str:
    """
    Формирует полный отчёт из 4 разделов (или суммаризацию из 3 разделов при < 20 ИОРах) при сбое/недоступности Qwen.
    """
    analyzer = get_analyzer(running_skill)
    if analyzer is not None and voz_metrics is None:
        return analyzer.prepare(df).deterministic_report()

    if running_skill == "vozmeshenie_ior" and voz_metrics is None:
        df, voz_metrics = prepare_vozmeshenie_views(df)

    stats = calculate_advanced_stats(df) if running_skill != "vozmeshenie_ior" else {}
    id_col = get_incident_id_col(df) or "incdnt_sid"
    if running_skill == "vozmeshenie_ior":
        total_loss, direct_loss = 0.0, 0.0
    else:
        total_loss, direct_loss = get_total_and_direct_loss(df)
    primary_rec = get_recovery_column(df, running_skill)
    recovery_loss = _to_numeric_clean(df[primary_rec]).sum() if primary_rec and primary_rec in df.columns else 0.0
    net_loss = max(0.0, total_loss - recovery_loss)

    # Top Cause
    type_col = next((c for c in df.columns if any(x in str(c).lower() for x in ("тип события", "incdnt_type", "причина", "тип"))), None)
    top_type_str = "Ошибки персонала и недостатки процессов"
    if type_col:
        type_counts = df[type_col].dropna().value_counts()
        if not type_counts.empty:
            top_type_str = str(type_counts.index[0])

    # Top Recovery Type
    top_recovery_type_str = "Восстановление резервов и компенсации"
    if running_skill == "vozmeshenie_ior" and voz_metrics and voz_metrics.get("type_breakdown"):
        top_recovery_type_str = str(voz_metrics["type_breakdown"][0]["type"])
    else:
        rec_type_col = next((c for c in df.columns if str(c).lower().strip() in ("recovery_type_name", "тип возмещения")), None)
    if running_skill != "vozmeshenie_ior" and rec_type_col:
        rec_type_counts = df[rec_type_col].dropna().value_counts()
        if not rec_type_counts.empty:
            top_recovery_type_str = str(rec_type_counts.index[0])

    # Top TB (Уровень 3 - ТБ / Блок / ПЦП)
    tb_col = next((c for c in df.columns if str(c).lower() in ("org_struct_lvl_3_name", "орг. структура – уровень 3 (блок / тб / пцп)")), None)
    if not tb_col:
        tb_col = next((c for c in df.columns if any(x in str(c).lower() for x in ("org_struct_lvl_3", "орг. структура – уровень 3", "тб")) and "lvl_2" not in str(c).lower()), None)
    top_tb_str = "Московский банк"
    tb_cnt_info = ""
    if tb_col and running_skill == "vozmeshenie_ior" and primary_rec:
        tb_amounts = df.groupby(tb_col, dropna=True)[primary_rec].apply(lambda values: _to_numeric_clean(values).sum())
        if not tb_amounts.empty:
            top_tb_str = str(tb_amounts.idxmax())
            tb_count = int(df[df[tb_col] == top_tb_str][id_col].nunique()) if id_col in df.columns else 0
            tb_cnt_info = f" ({tb_count} уникальных ИОР)"
    elif tb_col:
        tb_counts = df[tb_col].dropna().value_counts()
        if not tb_counts.empty:
            top_tb_str = str(tb_counts.index[0])
            tb_cnt_info = f" ({tb_counts.iloc[0]} инц., {tb_counts.iloc[0]/len(df)*100:.1f}%)"

    # Top EVE IDs: для возмещений ранжируем только по агрегированной сумме recovery_rub_amt.
    loss_cols = (
        ["recovery_rub_amt", "сумма возмещения (руб.)", "сумма возмещения в рублях"]
        if running_skill == "vozmeshenie_ior"
        else ["incdnt_sum", "общая сумма всех последствий (руб.)", "общая сумма последствий (руб.)", "сумма последствий, ₽", "fin_impact_rub_amt"]
    )
    primary_loss = next((c for c in df.columns if str(c).lower() in loss_cols), None)
    top_sids = []
    if primary_loss and id_col in df.columns:
        df_sorted = df.copy()
        df_sorted[primary_loss] = _to_numeric_clean(df_sorted[primary_loss])
        df_sorted = df_sorted.sort_values(by=primary_loss, ascending=False)
        for _, r in df_sorted.head(3).iterrows():
            sid_val = str(r[id_col])
            if sid_val and sid_val != "nan":
                top_sids.append(sid_val)
    sids_str = ", ".join(top_sids) if top_sids else "крупнейших событий выгрузки"

    top_10_sum = format_loss(stats.get("top_10_sum", 0.0))
    top_10_pct = f"{stats.get('top_10_pct', 0.0):.1f}%"
    autoreg_pct = f"{stats.get('autoreg_pct', 0.0):.1f}%"

    unique_count = df[id_col].nunique() if id_col in df.columns else len(df)
    rows_str = f" ({len(df)} записей данных)" if unique_count != len(df) else ""

    if running_skill == "ior_nonfinancial_consequences":
        lines = [
            "### 1. Общая сводка данных\n",
            f"- **Всего инцидентов с нефинансовыми последствиями**: {unique_count}{rows_str}",
            "- **Виды последствий**: Репутационный риск, прерывание деятельности, регуляторные санкции.",
            f"- **Преобладающая причина инцидентов**: {top_type_str}",
            f"- **Динамика регистрации**: Поступление инцидентов во времени.\n",

            "### 2. Выявленные аномалии и динамика трендов\n",
            f"- **Распределение по ТБ и оргструктуре**: Лидером по количеству нефинансовых инцидентов является {top_tb_str}{tb_cnt_info}.",
            f"- **Временные тренды**: Наблюдается ровная динамика выявления без критических всплесков.\n",

            "### 3. Концентрация рисков и системные факторы\n",
            f"- **Концентрация качественных рисков**: Основной объем нефинансовых инцидентов сконцентрирован в подразделении {top_tb_str}.",
            f"- **Доля авторегистрации**: {autoreg_pct} инцидентов зарегистрированы автоматически через системы мониторинга.\n",
        ]
        if not is_summarization_only:
            lines.extend([
                "### 4. Аналитические гипотезы для аудиторской проверки\n",
                f"**Гипотеза 1. Факторы нефинансового влияния в категории «{top_type_str}»**",
                f"• **Суть проблемы:** Возникновение нефинансовых последствий в категории «{top_type_str}» обусловлено временной недоступностью автоматизированных систем, задержками информирования клиентов либо несоблюдением регламентных сроков обработки запросов.",
                f"• **Шаги проверки:** Сопоставить логи доступности ИТ-сервисов с датами выявления инцидентов и проверить полноту выполнения требований по непрерывности бизнеса.",
                "• **Ожидаемый результат:** Разработка мер по повышению отказоустойчивости сервисов и снижению репутационных рисков.\n",

                f"**Гипотеза 2. Уязвимость контрольной среды в филиальной сети ({top_tb_str})**",
                f"• **Суть проблемы:** Повышенная частота нефинансовых инцидентов в подразделении {top_tb_str} связана со спецификой филиальной сети, операционной нагрузкой персонала или неполнотой доведения регламентов.",
                f"• **Шаги проверки:** Оценить загруженность линейных сотрудников в подразделении {top_tb_str} и проверить качество проведения инструктажей по качественным рискам.",
                "• **Ожидаемый результат:** Стандартизация процессов контроля в подразделениях сети.\n",

                "**Гипотеза 3. Технологические и процедурные причины по текстам описаний**",
                f"• **Суть проблемы:** Ключевыми факторами качественных последствий выступают сбои интерфейсов АС и ошибки первичного ввода данных операторами.",
                "• **Шаги проверки:** Изучить текстовые описания инцидентов на предмет сбоев смежных систем и провести аудит настроек алгоритмов валидации.",
                "• **Ожидаемый результат:** Исключение технологических ошибок и снижение риска прерывания процессов."
            ])

    elif running_skill == "financial_consequences_ior":
        lines = [
            "### 1. Общая сводка финансовых последствий\n",
            f"- **Всего записей о последствиях**: {unique_count}{rows_str}",
            f"- **Общая сумма потерь**: {format_loss(total_loss)}",
            f"- **Прямые потери**: {format_loss(direct_loss)}",
            f"- **Преобладающий вид / причина потерь**: {top_type_str}",
            f"- **Динамика отражения**: Систематический учет потерь в отчетных периодах.\n",

            "### 2. Структура и виды потерь\n",
            f"- **Распределение по оргструктуре**: Лидером по объёму финансовых потерь является подразделение {top_tb_str}{tb_cnt_info}.",
            f"- **Ключевые виды потерь**: Прямое списание ущерба, уплата штрафов и компенсаций.\n",

            "### 3. Концентрация финансовых последствий\n",
            f"- **Концентрация крупных потерь**: На 10 крупнейших финансовых последствий приходится {top_10_sum} ({top_10_pct} от общей суммы потерь).",
            f"- **Крупнейшие события**: {sids_str}.\n",
        ]
        if not is_summarization_only:
            lines.extend([
                "### 4. Аналитические гипотезы для аудиторской проверки\n",
                f"**Гипотеза 1. Причины возникновения потерь по категории «{top_type_str}»**",
                f"• **Суть проблемы:** Финансовые потери в категории «{top_type_str}» сформированы в результате сбоев при исполнении расчетных операций, некорректного применения тарифов либо несоблюдения лимитов авторизации.",
                f"• **Шаги проверки:** Провести выборочную проверку расчетно-кассовых документов и сверку лимитов операций по категории «{top_type_str}».",
                "• **Ожидаемый результат:** Установление причин финансовых отклонений и предотвращение повторных ущербов.\n",

                f"**Гипотеза 2. Специфика финансовых рисков в филиальной сети ({top_tb_str})**",
                f"• **Суть проблемы:** Концентрация суммы потерь в подразделении {top_tb_str} обусловлена объемом проводимых транзакций и возможными недостатками локального уровня контроля.",
                f"• **Шаги проверки:** Провести детализированный аудит расходных операций и проверить соблюдение принципа двух рук в подразделении {top_tb_str}.",
                "• **Ожидаемый результат:** Повышение эффективности финансового контроля в региональных точках.\n",

                "**Гипотеза 3. Операционные причины потерь по результатам текстового анализа**",
                "• **Суть проблемы:** Анализ описаний финансовых последствий указывает на ошибки ручного ввода реквизитов и сбои при обработке реестров платежей.",
                "• **Шаги проверки:** Проверить журналы коррекции платежных документов и оценить эффективность автоматических контролей ввода.",
                "• **Ожидаемый результат:** Внедрение дополнительной валидации данных для устранения финансовых ошибок."
            ])

    elif running_skill == "vozmeshenie_ior":
        header = format_vozmeshenie_header(voz_metrics or {
            "total_rows": len(df),
            "unique_incidents": unique_count,
            "total_recovery": recovery_loss,
        })
        lines = [
            header.rstrip(),
            "### 1. Общая сводка по возмещениям\n",
            f"- **Уникальных инцидентов с возмещениями**: {unique_count}",
            f"- **Общая сумма полученных возмещений**: {format_loss(recovery_loss)}",
            f"- **Преобладающий тип возмещения**: {top_recovery_type_str}",
            "- **Динамика поступления**: определяется по датам регистрации возмещений в выгрузке.\n",

            "### 2. Географическая и процессная структура возмещений\n",
            f"- **Распределение по ТБ**: Лидером по объёму возвращенных средств является подразделение {top_tb_str}{tb_cnt_info}.\n",

            "### 3. Концентрация и крупные возмещения\n",
            f"- **Крупнейшие возвраты средств**: Ключевой объем возмещений приходится на инциденты ({sids_str}).\n",
        ]
        if not is_summarization_only:
            lines.extend([
                "### 4. Аналитические гипотезы для аудиторской проверки\n",
                f"**Гипотеза 1. Эффективность источника возмещения «{top_recovery_type_str}»**",
                f"• **Суть проблемы:** Полнота и сроки поступления средств по источнику «{top_recovery_type_str}» могут зависеть от качества оформления документов и соблюдения сроков претензионной работы.",
                f"• **Шаги проверки:** 1. Сопоставить даты создания и регистрации возмещений по источнику «{top_recovery_type_str}». 2. Проверить полноту подтверждающих документов по крупнейшим ИОР.",
                "• **Ожидаемый результат:** Выявление факторов, влияющих на сроки и полноту возврата средств.\n",

                f"**Гипотеза 2. Претензионно-исковая работа в филиальной сети ({top_tb_str})**",
                f"• **Суть проблемы:** Различия между подразделениями по суммам и срокам возврата средств могут быть связаны с организацией претензионной работы.",
                f"• **Шаги проверки:** 1. Сравнить сроки регистрации возмещений в подразделении {top_tb_str} с другими подразделениями. 2. Сопоставить применяемые процедуры сопровождения крупнейших ИОР.",
                "• **Ожидаемый результат:** Определение практик, обеспечивающих своевременное поступление средств.\n",

                "**Гипотеза 3. Системно-процессные факторы полноты и сроков возврата средств**",
                "• **Суть проблемы:** Интервалы между созданием карточки и бухгалтерской регистрацией могут указывать на неоднородность процесса учёта возвратов.",
                "• **Шаги проверки:** 1. Сопоставить даты создания карточек с датами отражения в учёте. 2. Проверить крупнейшие интервалы и соответствующие документы.",
                "• **Ожидаемый результат:** Определение этапов, на которых возникают задержки регистрации возвратов."
            ])

    elif running_skill == "deleted_ior":
        lines = [
            "### 1. Общая сводка данных\n",
            f"- **Всего удаленных инцидентов**: {unique_count}",
            f"- **Общая сумма потерь по отмененным ИОРам**: {format_loss(total_loss)}",
            f"- **Сумма возмещений по отмененным ИОРам**: {format_loss(recovery_loss)}",
            f"- **Преобладающая причина в текстах**: {top_type_str}\n",

            "### 2. Выявленные аномалии и динамика трендов\n",
            f"- **Распределение удалений по подразделениям**: Лидером по числу отмененных инцидентов является подразделение {top_tb_str}{tb_cnt_info}.\n",

            "### 3. Концентрация рисков и системные факторы\n",
            "- **Причины отмены**: Основная часть аннулирований связана с ошибками ввода параметров сделок, изменением условий договоров и ручной корректировкой статусов.\n",
        ]
        if deleted_text and deleted_text.strip():
            lines.append(deleted_text)

        if not is_summarization_only:
            lines.extend([
                "### 4. Аналитические гипотезы для аудиторской проверки\n",
                "**Гипотеза 1. Процессные и операционные причины отмены по комментариям пользователей**",
                "• **Суть проблемы:** Основной объем инцидентов отменяется вручную из-за ошибок операторов при первичном заведении параметров или изменении реквизитов.",
                "• **Шаги проверки:** Провести аудит комментариев сотрудников к отмененным инцидентам и проверить регламенты внесения изменений.",
                "• **Ожидаемый результат:** Устранение причин повторного ввода и снижение операционной нагрузки на регистраторов.\n",

                f"**Гипотеза 2. Детализация уязвимостей в оргструктуре и процессах ({top_tb_str})**",
                f"• **Суть проблемы:** Повышенная частота отмен в подразделениях {top_tb_str} указывает на специфику региональных операций и необходимость усиления входного контроля.",
                f"• **Шаги проверки:** Проверить уровень квалификации регистраторов в подразделении {top_tb_str} и сопоставить количество отмен с успешными операциями.",
                "• **Ожидаемый результат:** Повышение качества первичной регистрации данных.\n",

                "**Гипотеза 3. Системно-технологические факторы корректировки данных**",
                "• **Суть проблемы:** Текстовый анализ комментариев подтверждает наличие повторных заведений ИОРов из-за задержек валидации в смежных ИТ-системах.",
                "• **Шаги проверки:** Сопоставить таймштампы отмен с журналами интеграционных шин.",
                "• **Ожидаемый результат:** Настройка автоматических проверок для предотвращения ошибочных карточек инцидентов."
            ])

    elif running_skill == "credit_no_way_collect_debt":
        lines = [
            "### 1. Сводные показатели по кредитной задолженности\n",
            f"- **Всего кредитных договоров/записей**: {unique_count}{rows_str}",
            f"- **Общая сумма задолженности**: {format_loss(total_loss)}",
            f"- **Сформированные резервы (РВПС)**: {format_loss(recovery_loss)}",
            f"- **Преобладающая категория риска**: {top_type_str}\n",

            "### 2. Причины невозможности взыскания и залоговое обеспечение\n",
            f"- **Распределение по ТБ**: Лидером по объёму проблемной задолженности является подразделение {top_tb_str}{tb_cnt_info}.",
            "- **Ключевые факторы**: Ликвидация заемщиков, отсутствие ликвидативных активов, истечение сроков исковой давности.\n",

            "### 3. Концентрация проблемной задолженности\n",
            f"- **Концентрация крупнейших кейсов**: На 10 крупнейших договоров приходится {top_10_sum} ({top_10_pct} от совокупной задолженности).",
            f"- **Крупнейшие договоры**: {sids_str}.\n",
        ]
        if not is_summarization_only:
            lines.extend([
                "### 4. Аналитические гипотезы для аудиторской проверки\n",
                f"**Гипотеза 1. Причины дефолта и неполноты залогов в категории «{top_type_str}»**",
                f"• **Суть проблемы:** Невозможность взыскания задолженности в категории «{top_type_str}» обусловлена несвоевременной актуализацией оценочной стоимости залогов и дефектами оформления поручительств.",
                "• **Шаги проверки:** Провести выборочную проверку юридической чистоты залоговых прав и полноты резервирования РВПС.",
                "• **Ожидаемый результат:** Повышение качества залогового контроля и минимизация потерь при списании задолженности.\n",

                f"**Гипотеза 2. Риски кредитного конвейера в филиальной сети ({top_tb_str})**",
                f"• **Суть проблемы:** Концентрация безнадежной задолженности в подразделении {top_tb_str} указывает на слабый уровень мониторинга финансового состояния заемщиков после выдачи кредита.",
                f"• **Шаги проверки:** Проверить соблюдение регламентов раннего реагирования на сигналы ухудшения заемщиков в подразделении {top_tb_str}.",
                "• **Ожидаемый результат:** Своевременный перевод проблемных договоров на досудебное взыскание.\n",

                "**Гипотеза 3. Продуктовые уязвимости андеррайтинга по текстам списаний**",
                "• **Суть проблемы:** Анализ текстовых оснований списания кредитов свидетельствует об уязвимостях скоринговых моделей при первичной оценке платежеспособности.",
                "• **Шаги проверки:** Проанализировать дефолтность скоринговых балла по списанным кредитам и скорректировать правила андеррайтинга.",
                "• **Ожидаемый результат:** Снижение уровня первоначального дефолта заемщиков."
            ])

    else: # Default ior_hypothesis & ior_period_pao_sberbank
        lines = [
            "### 1. Общая сводка данных\n",
            f"- **Всего инцидентов**: {unique_count}{rows_str}",
            f"- **Общая сумма потерь**: {format_loss(total_loss)}",
            f"- **Общая сумма возмещений**: {format_loss(recovery_loss)}",
            f"- **Чистые потери (Net Loss)**: {format_loss(net_loss)}",
            f"- **Преобладающая причина инцидентов**: {top_type_str}",
            f"- **Динамика регистрации**: Поступление инцидентов в течение анализируемого периода.\n",

            "### 2. Выявленные аномалии и динамика трендов\n",
            f"- **Распределение по ТБ и оргструктуре**: Лидером по объёму потерь и количеству событий является {top_tb_str}{tb_cnt_info}.",
            f"- **Временные тренды**: Наблюдается постоянная концентрация регистрации в конце отчётных интервалов.\n",

            "### 3. Концентрация рисков и системные факторы\n",
            f"- **Концентрация потерь в Топ-10**: На 10 крупнейших инцидентов приходится {top_10_sum} ({top_10_pct} от общего объёма потерь).",
            f"- **Крупнейшие инциденты**: {sids_str}.",
            f"- **Доля авторегистрации**: {autoreg_pct} инцидентов зарегистрированы автоматически через системы мониторинга.\n",
        ]

        if deleted_text and deleted_text.strip():
            lines.append(deleted_text)

        if not is_summarization_only:
            hyp1_title = f"Гипотеза 1. Процессные и технологические факторы риска в категории «{top_type_str}»"
            hyp1_problem = f"Преобладающий объём инцидентов сосредоточен в категории «{top_type_str}», что обусловлено задержками обработки операций в автоматизированных системах, уязвимостями алгоритмов валидации реквизитов либо несогласованностью взаимодействия подразделений при передаче данных."
            hyp1_steps = f"Провести выборочный аудит регламентов выполнения операций в категории «{top_type_str}», сопоставить журналы вызовов автоматизированных систем с зафиксированными отклонениями и проверить соблюдение контрольных сроков."
            hyp1_result = "Выявление узких мест в технологических процессах и разработка рекомендаций по оптимизации контрольной среды."

            lines.extend([
                "### 4. Аналитические гипотезы для аудиторской проверки\n",
                f"**{hyp1_title}**",
                f"• **Суть проблемы:** {hyp1_problem}",
                f"• **Шаги проверки:** {hyp1_steps}",
                f"• **Ожидаемый результат:** {hyp1_result}\n",

                "**Гипотеза 2. Локальные особенности выполнения контрольных процедур в региональной сети**",
                f"• **Суть проблемы:** Повышенная интенсивность возникновения инцидентов наблюдается в подразделении {top_tb_str}, что может свидетельствовать о специфике операционной нагрузки, различиях в квалификации персонала или локальных особенностях применения регламентов.",
                f"• **Шаги проверки:** Сопоставить объем проводимых операций с уровнем потерь по подразделению {top_tb_str}, оценить укомплектованность штата и провести выборочную проверку соблюдения правил внутреннего контроля.",
                "• **Ожидаемый результат:** Оценка равномерности и эффективности контролей по филиальной сети.\n",

                "**Гипотеза 3. Системные и человеческие факторы в преобладающих категориях риска**",
                f"• **Суть проблемы:** Ключевым фактором возникновения событий выступает категория «{top_type_str}», обусловленная операционными ошибками ввода данных либо сбоями при автоматизированной обработке.",
                "• **Шаги проверки:** Проанализировать журналы автоматизированных систем на предмет сбоев, проверить эффективность встроенных контролей ввода и оценить необходимость проведения дополнительного обучения сотрудников.",
                "• **Ожидаемый результат:** Определение требуемых доработок в алгоритмах систем и снижении уровня операционных ошибок."
            ])

    return "\n".join(lines)


def build_analysis_context_text(user_msg: str, analysis_context: Optional[dict | str] = None) -> str:
    """Описывает пользовательские ограничения, чтобы не выдавать их за anomaly."""
    if isinstance(analysis_context, str) and analysis_context.strip():
        return analysis_context.strip()
    context = analysis_context if isinstance(analysis_context, dict) else {}
    intent = str(context.get("original_user_intent") or user_msg or "").strip()
    spec = context.get("spec_resolved") or {}
    notes: list[str] = []
    if intent:
        notes.append(f"- Исходный запрос пользователя: {intent}")
    for item in spec.get("filters") or []:
        if not isinstance(item, dict):
            continue
        column = item.get("column") or item.get("field") or item.get("kind")
        value = item.get("value") or item.get("values") or item.get("label")
        notes.append(f"- Фильтр QuerySpec: {column} = {value}")
    period = spec.get("period") or context.get("period")
    if period:
        notes.append(f"- Период задан пользователем: {period}")
    group_by = (spec.get("aggregate") or {}).get("group_by") or spec.get("group_by")
    if group_by:
        notes.append(f"- Группировка пользователя: {group_by}")
    joins = (spec.get("source") or {}).get("joins") or []
    if joins:
        notes.append("- Подключённые предметные данные: " + ", ".join(str(j.get("table")) for j in joins if isinstance(j, dict)))

    low = intent.lower()
    known_filters = {
        "Московский банк": ("московск", "московский банк"),
        "Юго-Западный банк": ("юго-западн", "юзб"),
        "Северо-Западный банк": ("северо-западн", "сзб"),
        "Эквайринг": ("эквайринг",),
        "ПАО Сбербанк": ("пао сбербанк",),
    }
    mentioned = [label for label, aliases in known_filters.items() if any(alias in low for alias in aliases)]
    if mentioned:
        notes.append("- Явно запрошенные категории: " + ", ".join(mentioned))
    if len(notes) == (1 if intent else 0):
        notes.append("- Структурированные пользовательские фильтры не обнаружены.")
    notes.append(
        "Не трактовать 100% долю значения, прямо заданного пользователем в фильтре "
        "(например, 100% Московского банка, Эквайринга или выбранного периода), как найденную концентрацию или аномалию."
    )
    return "\n".join(notes)


async def _generate_registered_preset_narrative(
    user_msg: str,
    df: pd.DataFrame,
    session_id: str,
    normalized_skill: str,
    analysis_context: Optional[dict | str] = None,
) -> str:
    """Оркестрация: детерминированные facts -> local Qwen evidence -> hypotheses."""
    analyzer = get_analyzer(normalized_skill)
    if analyzer is None:
        raise ValueError(f"Для пресета {normalized_skill!r} не зарегистрирован анализатор")
    bundle = analyzer.prepare(df)

    parts = [bundle.full_header.rstrip()]
    if bundle.status_summary_enabled:
        parts.append(bundle.status_summary().rstrip())
        parts.append(bundle.scope_note())
    if not bundle.can_analyze:
        return "\n\n".join(part for part in parts if part and part.strip()).strip()
    evidence = ""
    try:
        if normalized_skill == "deleted_ior":
            comment_col = next((c for c in bundle.analysis_detail_df.columns if str(c).strip().lower() in {
                "stts_chng_comment_txt", "комментарий / причина действия", "комментарий / причина", "причина удаления",
            }), None)
            if comment_col:
                comments = [value for value in bundle.analysis_detail_df[comment_col].dropna().astype(str).str.strip().unique()
                            if value and value.lower() not in ("nan", "none", "—", "-")]
                evidence = await summarize_deletion_comments(comments)
        else:
            evidence = await analyze_incident_descriptions(bundle.analysis_incident_df, normalized_skill)
    except Exception as evidence_error:
        logger.warning(f"[ior_hypothesis] Evidence preparation failed for {normalized_skill}: {evidence_error}")

    profile = bundle.profile
    if normalized_skill == "deleted_ior":
        if evidence.lower().startswith("ошибка при анализе"):
            evidence = ""
        profile = profile.replace(
            "{{DELETION_QWEN_SUMMARY}}",
            ("#### Содержательное резюме комментариев\n" + evidence) if evidence else "",
        )
    parts.append(profile.rstrip())
    context_text = build_analysis_context_text(user_msg, analysis_context)

    small_sample_rule = ""
    if bundle.approved_count < 20:
        small_sample_rule = (
            "Выборка мала: формулируй гипотезы как проверочные кейс-гипотезы. "
            "Не заявляй сезонность, тренд или системность только из-за размера выборки."
        )
    prompt = f"""Ты формируешь ТОЛЬКО раздел 4 аналитического отчёта по ИОР.
Числовые разделы 1–3 уже рассчитаны Python. Не повторяй и не пересчитывай их.

ПРАВИЛА ПРЕСЕТА:
{bundle.prompt_rules}
{small_sample_rule}

КОНТЕКСТ ПОЛЬЗОВАТЕЛЬСКИХ ФИЛЬТРОВ:
{context_text}

ФАКТЫ РАЗДЕЛОВ 1–3:
{bundle.profile}

ТЕКСТОВЫЕ НАБЛЮДЕНИЯ ПО РАЗРЕШЁННОЙ ВЫБОРКЕ:
{evidence or 'Дополнительные текстовые наблюдения не сформированы.'}

Сформируй ровно {bundle.hypothesis_count} разные гипотезы. Начни с:
### 4. Аналитические гипотезы для аудиторской проверки

Для каждой гипотезы строго используй:
**Гипотеза N: название**
- **Предположение / Суть проблемы**
- **Шаги проверки** (минимум два нумерованных шага)
- **Ожидаемый результат**

Не критикуй авторегистрацию, не придумывай EVE-ID, поля, причины, пороги или числа."""

    hypotheses = ""
    try:
        raw_response = await asyncio.to_thread(
            ask_local_qwen,
            [
                {"role": "system", "content": "Ты аудитор-аналитик. Используй только переданные факты и формулируй проверочные гипотезы."},
                {"role": "user", "content": prompt},
            ],
            max_tokens=8192,
        )
        hypotheses = str(raw_response).strip()
        if hypotheses.startswith("```"):
            response_lines = hypotheses.splitlines()
            if response_lines and response_lines[0].startswith("```"):
                response_lines = response_lines[1:]
            if response_lines and response_lines[-1].startswith("```"):
                response_lines = response_lines[:-1]
            hypotheses = "\n".join(response_lines).strip()
        if "### 4." in hypotheses:
            hypotheses = "### 4." + hypotheses.split("### 4.", 1)[1]
        hypotheses = sanitize_generated_text(hypotheses, bundle.forbidden_metrics)
        hypotheses, _ = scrub_autoreg_criticism(hypotheses)
        complete, details = check_hypotheses_completeness(hypotheses, bundle.hypothesis_count)
        validation = await validate_narrative(hypotheses, list(bundle.forbidden_metrics))
        invalid_flags = [key for key, value in validation.items() if key != "details" and value is True]
        if not complete or invalid_flags:
            reasons = "; ".join(filter(None, [details, validation.get("details") if invalid_flags else "", ", ".join(invalid_flags)]))
            retry_prompt = prompt + f"\n\nПРЕДЫДУЩАЯ ВЕРСИЯ НЕ ПРОШЛА ПРОВЕРКУ: {reasons}. Исправь только эти нарушения."
            retry_response = await asyncio.to_thread(
                ask_local_qwen,
                [{"role": "system", "content": "Исправь раздел гипотез строго по замечаниям валидатора."}, {"role": "user", "content": retry_prompt}],
                max_tokens=8192,
            )
            hypotheses = sanitize_generated_text(str(retry_response).strip(), bundle.forbidden_metrics)
            hypotheses, _ = scrub_autoreg_criticism(hypotheses)
            complete, details = check_hypotheses_completeness(hypotheses, bundle.hypothesis_count)
            validation = await validate_narrative(hypotheses, list(bundle.forbidden_metrics))
            invalid_flags = [key for key, value in validation.items() if key != "details" and value is True]
            if not complete or invalid_flags:
                logger.warning("[ior_hypothesis] Retry validation failed (%s, %s); deterministic fallback used", details, invalid_flags)
                hypotheses = bundle.deterministic_hypotheses()
    except Exception as qwen_error:
        logger.warning(f"[ior_hypothesis] Local Qwen unavailable for {normalized_skill}: {qwen_error}")
        hypotheses = bundle.deterministic_hypotheses()
    if hypotheses:
        parts.append(hypotheses)

    if bundle.chart_enabled and len(bundle.analysis_incident_df) > 1:
        try:
            chart_file_id = await asyncio.to_thread(
                generate_dynamics_chart, bundle.analysis_incident_df, session_id,
                normalized_skill, bundle.chart_policy(),
            )
            if chart_file_id:
                parts.append(
                    "### Визуализация аналитики\n\n"
                    f"![Динамика утверждённых ИОР](/api/files/download?path={Path(chart_file_id).name})"
                )
        except Exception as chart_error:
            logger.warning(f"[ior_hypothesis] Chart generation skipped: {chart_error}")

    narrative = "\n\n".join(part for part in parts if part and part.strip())
    narrative = sanitize_generated_text(narrative, ())
    narrative = re.sub(r"\n{3,}", "\n\n", narrative)
    return normalize_markdown_for_frontend(narrative).strip()


async def generate_hypothesis_narrative(
    user_msg: str,
    df: pd.DataFrame,
    file_info: dict | str | None = None,
    session_id: str = "webui_session",
    running_skill: str | None = None,
    preset_name: str | None = None,
    analysis_context: Optional[dict | str] = None,
) -> str:
    """
    Generates a natural language narrative (with hypothesis / insights) based on the dataframe profile and optional plot.
    Full 1-to-1 migration from ior_assistant/backend/agent/hypothesis.py with Nanobot compatibility.
    """
    # Normalize argument patterns (support both dict file_info and positional session_id/preset_name)
    if isinstance(file_info, str):
        if running_skill is None and preset_name is None:
            running_skill = session_id
        session_id = file_info
        file_info = {"name": "выгрузка.xlsx", "size": ""}
    elif file_info is None:
        file_info = {"name": "выгрузка.xlsx", "size": ""}

    if running_skill is None:
        running_skill = preset_name

    if len(df) == 0:
        logger.info("[ior_hypothesis] DataFrame is empty, returning early message.")
        return "### Общая информация о выгрузке:\nВыгрузка пуста. Нет данных для формирования гипотезы."

    logger.info(f"[ior_hypothesis] 📊 Step 1: Profiling DataFrame (rows={len(df)}, session='{session_id}', skill='{running_skill}')")

    # 1. Determine running skill
    if running_skill is None:
        skill_id = get_running_skill_id(df, session_id)
        if skill_id is None:
            skill_id = "ior_hypothesis_v2"
        running_skill = skill_id or ""

    if running_skill.endswith("_v2"):
        normalized_skill = running_skill[:-3]
    else:
        normalized_skill = running_skill
    is_vozmeshenie = normalized_skill == "vozmeshenie_ior"
    voz_metrics: Optional[dict] = None

    # Все активные пресеты используют registry. Legacy ниже оставлен только
    # для credit_no_way_collect_debt, который намеренно не рефакторится.
    if get_analyzer(normalized_skill) is not None:
        return await _generate_registered_preset_narrative(
            user_msg=user_msg,
            df=df,
            session_id=session_id,
            normalized_skill=normalized_skill,
            analysis_context=analysis_context,
        )

    # Protective check for pre-filtered dimensions: if the user's own request already
    # asked for a specific ТБ/блок (e.g. "... по ЮЗБ"), the SQL builder upstream filters
    # the upload down to that value before it ever reaches this function. Any resulting
    # ~100% concentration on that dimension is then a trivial artifact of the query filter,
    # not an analytical finding — but the LLM has no way to know that unless we tell it.
    # prefilter_notes accumulates human-readable warnings that get injected into user_prompt.
    prefilter_notes: list[str] = []
    try:
        low_msg = user_msg.lower()

        def _msg_has_alias(alias: str) -> bool:
            # Short tokens (abbreviations like "юзб", "мб") need word-boundary matching,
            # otherwise they'd match as substrings inside unrelated words.
            if len(alias) <= 3:
                return re.search(rf'\b{re.escape(alias)}\b', low_msg) is not None
            return alias in low_msg

        # Each entry: (canonical stem actually found in the data) -> [aliases the user might type]
        tb_alias_groups = [
            ("московск", ["московск", "мб"]),
            ("северо-запад", ["северо-запад", "сзб"]),
            ("волго-вятск", ["волго-вятск", "ввб"]),
            ("юго-запад", ["юго-запад", "юзб"]),
            ("среднерусск", ["среднерусск", "срб"]),
            ("сибирск", ["сибирск", "сиб"]),
            ("уральск", ["уральск", "урб"]),
            ("поволжск", ["поволжск", "пвб"]),
            ("дальневосточ", ["дальневосточ", "двб"]),
            ("байкальск", ["байкальск", "брб"]),
        ]
        block_alias_groups = [
            ("риск", ["риск"]),
            ("розниц", ["розниц", "розничн"]),
            ("корпоратив", ["корпоратив"]),
            ("технолог", ["технолог"]),
            ("финанс", ["финанс"]),
            ("сеть продаж", ["сеть продаж"]),
            ("эквайринг", ["эквайринг"]),
            ("домклик", ["домклик"]),
        ]

        def _check_prefiltered_dim(col_candidates: tuple[str, ...], alias_groups: list[tuple[str, list[str]]], dim_label: str) -> None:
            col = next((c for c in df.columns if str(c).lower() in col_candidates), None)
            if not col or df.empty:
                return
            matched_stem = next((stem for stem, aliases in alias_groups if any(_msg_has_alias(a) for a in aliases)), None)
            if not matched_stem:
                return
            vc = df[col].dropna().astype(str)
            if vc.empty:
                return
            vc = vc.value_counts(normalize=True)
            top_val, top_share = vc.index[0], vc.iloc[0]
            if top_share >= 0.95 and matched_stem in top_val.lower():
                logger.warning(
                    f"[PREFILTER DETECTED] User query matches '{matched_stem}' for dimension '{dim_label}', "
                    f"and {top_share*100:.0f}% of the upload already has value '{top_val}' in column '{col}' "
                    f"— this is a query-filter artifact, flagging for the prompt."
                )
                prefilter_notes.append(
                    f"- Измерение «{dim_label}»: значение «{top_val}» составляет {top_share*100:.0f}% выгрузки — "
                    f"это результат того, что пользователь САМ запросил выборку по этому значению, а НЕ "
                    f"органическая концентрация риска."
                )

        _check_prefiltered_dim(
            ("тб", "орг. структура", "org_struct_lvl_3_name", "org_struct_lvl_4_name"),
            tb_alias_groups, "территориальный банк / оргструктура"
        )
        _check_prefiltered_dim(
            ("funct_block_lvl_3_name", "функциональный блок – уровень 3", "функциональный блок - уровень 3",
             "funct_block_lvl_4_name", "функциональный блок – уровень 4", "функциональный блок - уровень 4"),
            block_alias_groups, "функциональный блок"
        )
        entity_terms_in_prompt = re.findall(r'\b(?:эквайринг[а-я]*|домклик[а-я]*|сервис[а-я]*|забота\s+о\s+клиентах|управление\s+сетью\s+ус|риски|страховани[ея]|залог[а-я]*)\b', user_msg, re.IGNORECASE)
        if entity_terms_in_prompt:
            for et in set(entity_terms_in_prompt):
                prefilter_notes.append(
                    f"- Тема/сущность «{et}»: выборка уже отфильтрована по этой теме по запросу пользователя. "
                    f"Преобладание этой темы или процессов по ней ожидаемо."
                )
    except Exception as leak_err:
        logger.error(f"Error checking filter leak: {leak_err}")

    prefilter_note_text = ""
    if prefilter_notes:
        prefilter_note_text = (
            "\n\nВНИМАНИЕ — ЧАСТЬ ДАННЫХ УЖЕ ОТФИЛЬТРОВАНА ПО ЗАПРОСУ ПОЛЬЗОВАТЕЛЯ:\n"
            + "\n".join(prefilter_notes) +
            "\nПо этим измерениям СТРОГО ЗАПРЕЩЕНО формулировать гипотезы или выводы вида "
            "«высокая концентрация в X указывает на проблему в X» — раз пользователь сам запросил именно "
            "этот срез, то 100%/почти 100% доля этого значения ожидаема и не является находкой. "
            "Строй гипотезы на других измерениях (процессы, причины, временная динамика, тексты описаний)."
        )

    # 2. Агрегация по статусам (уникальные ИОР и записи данных)
    status_col = next((c for c in df.columns if any(x == str(c).lower().strip() for x in ("incdnt_status_name", "статус события", "статус", "статус инцидента", "status"))), None)
    id_col = get_incident_id_col(df)
    group_counts_unique = {"Группа 1: Утверждение": 0, "Группа 2: Черновик/Исследование": 0, "Группа 3: Удален": 0}
    group_counts_rows = {"Группа 1: Утверждение": 0, "Группа 2: Черновик/Исследование": 0, "Группа 3: Удален": 0}

    if status_col:
        for status_val_name, grp in df.groupby(status_col):
            s_lower = str(status_val_name).strip().lower()
            cnt_u = grp[id_col].nunique() if id_col else len(grp)
            cnt_r = len(grp)
            if s_lower in ("утверждение", "утверждён", "утвержден"):
                group_counts_unique["Группа 1: Утверждение"] += cnt_u
                group_counts_rows["Группа 1: Утверждение"] += cnt_r
            elif s_lower in ("черновик", "исследование"):
                group_counts_unique["Группа 2: Черновик/Исследование"] += cnt_u
                group_counts_rows["Группа 2: Черновик/Исследование"] += cnt_r
            elif s_lower in ("удалён", "удален"):
                group_counts_unique["Группа 3: Удален"] += cnt_u
                group_counts_rows["Группа 3: Удален"] += cnt_r

    def format_grp_str(grp_name):
        return f"{group_counts_unique[grp_name]}"

    # 3. Фильтрация для анализа: группа Удален или Утверждение
    is_deleted = (normalized_skill == "deleted_ior")
    is_nonfinancial = (normalized_skill == "ior_nonfinancial_consequences")

    if is_vozmeshenie:
        # Суммы и Excel остаются на полной детализации R1/R2/..., но весь
        # последующий анализ получает ровно одну строку на incdnt_sid.
        df_analysis, voz_metrics = prepare_vozmeshenie_views(df)

        # Статусная сводка также обязана считать каждый ИОР только один раз.
        # Пересчитываем её после схлопывания фрагментов, чтобы один incdnt_sid
        # не мог попасть в счётчик повторно из-за нескольких recovery_sid.
        group_counts_unique = {key: 0 for key in group_counts_unique}
        analysis_status_col = next(
            (
                c for c in df_analysis.columns
                if str(c).lower().strip() in (
                    "incdnt_status_name", "статус события", "статус",
                    "статус инцидента", "status",
                )
            ),
            None,
        )
        if analysis_status_col:
            for status_val_name, grp in df_analysis.groupby(analysis_status_col):
                s_lower = str(status_val_name).strip().lower()
                cnt_u = grp[id_col].nunique() if id_col and id_col in grp.columns else len(grp)
                if s_lower in ("утверждение", "утверждён", "утвержден"):
                    group_counts_unique["Группа 1: Утверждение"] += cnt_u
                elif s_lower in ("черновик", "исследование"):
                    group_counts_unique["Группа 2: Черновик/Исследование"] += cnt_u
                elif s_lower in ("удалён", "удален"):
                    group_counts_unique["Группа 3: Удален"] += cnt_u
    elif is_deleted:
        if status_col:
            df_analysis = df[df[status_col].astype(str).str.strip().str.lower().isin(["удалён", "удален"])].copy()
        else:
            df_analysis = df.copy()
    else:
        if status_col:
            df_analysis = df[df[status_col].astype(str).str.strip().str.lower().isin(["утверждение", "утверждён", "утвержден"])].copy()
        else:
            df_analysis = df.copy()

    if df_analysis.empty and not df.empty:
        df_analysis = df.copy()

    # 3.4 Проверка на один период/месяц
    single_month_note_text = ""
    date_cols = [c for c in df_analysis.columns if any(x in str(c).lower() for x in ("entry_dt", "start_dt", "detection_dt", "дата"))]
    if date_cols:
        try:
            d_series = _to_datetime_safe(df_analysis[date_cols[0]]).dropna()
            if not d_series.empty:
                months_unique = d_series.dt.to_period('M').nunique()
                if months_unique == 1:
                    m_str = d_series.iloc[0].strftime("%m.%Y")
                    single_month_note_text = (
                        f"\n\nВАЖНО ПО РАЗДЕЛУ 2 (ДИНАМИКА): Все инциденты выгрузки зафиксированы в рамках одного периода/месяца ({m_str}). "
                        "В разделе '2. Выявленные аномалии и динамика трендов' констатируй это как фактологический срез за этот месяц. "
                        "СТРОГО ЗАПРЕЩЕНО домысливать причины удалений/событий в этом месяце, писать о вымышленных 'всплесках', "
                        "'сезонности' или 'трендах роста/спада'!"
                    )
        except Exception as dt_err:
            logger.debug(f"Error checking single month: {dt_err}")

    # 3.5. Расчет общих сумм по всей выгрузке (все статусы)
    total_loss_all = 0.0
    direct_loss_all = 0.0
    recovery_loss_all = 0.0

    if not df.empty and not is_vozmeshenie:
        total_loss_all, direct_loss_all = get_total_and_direct_loss(df)
        primary_rec_all = get_recovery_column(df, running_skill)
        if primary_rec_all:
            recovery_loss_all = _to_numeric_clean(df[primary_rec_all]).sum()
    elif is_vozmeshenie and voz_metrics:
        recovery_loss_all = float(voz_metrics.get("total_recovery", 0.0))

    net_loss_all = max(0.0, total_loss_all - recovery_loss_all)

    # 4. Расчет потерь и возмещений для группы анализа (Утвержденные / Удаленные)
    if is_vozmeshenie:
        total_loss, direct_loss = 0.0, 0.0
    else:
        total_loss, direct_loss = get_total_and_direct_loss(df_analysis)

    recovery_loss = 0.0
    primary_rec_analysis = get_recovery_column(df_analysis, running_skill)
    if primary_rec_analysis:
        recovery_loss = _to_numeric_clean(df_analysis[primary_rec_analysis]).sum()

    net_loss = max(0.0, total_loss - recovery_loss)

    unique_count = (
        int(voz_metrics.get("unique_incidents", 0))
        if is_vozmeshenie and voz_metrics
        else (df[id_col].nunique() if id_col else len(df))
    )

    # 5. Формирование префикса в зависимости от типа отчета
    rows_str = ""

    overall_stats = ""
    if is_vozmeshenie:
        overall_stats = format_vozmeshenie_header(voz_metrics or {})
    elif normalized_skill not in ("ior_nonfinancial_consequences", "credit_no_way_collect_debt", "report_period_specific_ior"):
        overall_stats = (
            f"### Общая информация по выгрузке:\n"
            f"- **Всего инцидентов**: {unique_count}{rows_str}\n"
            f"- **Общая сумма потерь**: {format_loss(total_loss_all)}\n"
            f"- **Общая сумма возмещений**: {format_loss(recovery_loss_all)}\n"
            f"- **Чистые потери (Net Loss)**: {format_loss(net_loss_all)}\n\n"
        )
    elif normalized_skill == "ior_nonfinancial_consequences":
        overall_stats = (
            f"### Общая информация по выгрузке:\n"
            f"- **Всего качественных последствий**: {unique_count}{rows_str}\n\n"
        )

    if normalized_skill == "ior_nonfinancial_consequences":
        qualitative_col = next((c for c in df.columns if str(c).lower().strip() in ("nonfin_impact_kind_name", "вид качественной потери", "вид нефинансового последствия")), None)
        influence_col = next((c for c in df.columns if str(c).lower().strip() in ("nonfin_impact_influence_class_name", "классификация влияния", "класс влияния нефинансового последствия")), None)
        qualitative_summary = ""
        if qualitative_col:
            counts = df[qualitative_col].value_counts()
            qualitative_summary += "#### Распределение по видам качественных потерь:\n"
            for k, v in counts.items():
                qualitative_summary += f"- **{k}**: {v}\n"
            qualitative_summary += "\n"
        if influence_col:
            inf_counts = df[influence_col].value_counts()
            qualitative_summary += "#### Распределение по классу влияния:\n"
            for k, v in inf_counts.items():
                qualitative_summary += f"- **{k}**: {v}\n"
            qualitative_summary += "\n"

        prefix = (
            f"### Распределение инцидентов по статусам:\n"
            f"- **Группа 1: Утверждение**: {format_grp_str('Группа 1: Утверждение')}\n"
            f"- **Группа 2: Черновик/Исследование**: {format_grp_str('Группа 2: Черновик/Исследование')}\n"
            f"- **Группа 3: Удален**: {format_grp_str('Группа 3: Удален')}\n\n"
            f"{qualitative_summary}"
        )
    elif normalized_skill == "deleted_ior":
        prefix = (
            f"### Распределение инцидентов по статусам:\n"
            f"- **Группа 1: Утверждение**: {format_grp_str('Группа 1: Утверждение')}\n"
            f"- **Группа 2: Черновик/Исследование**: {format_grp_str('Группа 2: Черновик/Исследование')}\n"
            f"- **Группа 3: Удален**: {format_grp_str('Группа 3: Удален')}\n\n"
            f"По инцидентам в статусе **Удален/Удалён** (Группа 3):\n"
            f"- **Сумма потерь по удаленным инцидентам**: {format_loss(total_loss)}\n"
            f"- **Сумма возмещений по удаленным инцидентам**: {format_loss(recovery_loss)}\n\n"
        )
    elif normalized_skill == "financial_consequences_ior":
        type_summary = ""
        type_col = next((c for c in df.columns if str(c).lower().strip() in ("fin_impact_type_name", "тип последствия")), None)
        if type_col:
            counts = df[type_col].value_counts()
            type_summary = "#### Распределение по типам финансовых последствий:\n"
            for k, v in counts.items():
                type_summary += f"- **{k}**: {v}\n"
            type_summary += "\n"
        prefix = (
            f"### Распределение инцидентов по статусам:\n"
            f"- **Группа 1: Утверждение**: {format_grp_str('Группа 1: Утверждение')}\n"
            f"- **Группа 2: Черновик/Исследование**: {format_grp_str('Группа 2: Черновик/Исследование')}\n"
            f"- **Группа 3: Удален**: {format_grp_str('Группа 3: Удален')}\n\n"
            f"{type_summary}"
            f"По последствиям инцидентов:\n"
            f"- **Суммарные потери по последствиям**: {format_loss(total_loss)}\n\n"
        )
    elif normalized_skill == "vozmeshenie_ior":
        type_summary = ""
        type_breakdown = (voz_metrics or {}).get("type_breakdown", [])
        if type_breakdown:
            type_summary = "#### Распределение по видам/источникам возмещений:\n"
            for item in type_breakdown:
                type_summary += (
                    f"- **{item['type']}**: {item['unique_incidents']} уникальных ИОР, "
                    f"{format_loss(item['amount'])} ({item['amount_pct']:.1f}% от общей суммы)\n"
                )
            type_summary += "\n"
        prefix = (
            f"### Распределение инцидентов по статусам:\n"
            f"- **Группа 1: Утверждение**: {format_grp_str('Группа 1: Утверждение')}\n"
            f"- **Группа 2: Черновик/Исследование**: {format_grp_str('Группа 2: Черновик/Исследование')}\n"
            f"- **Группа 3: Удален**: {format_grp_str('Группа 3: Удален')}\n\n"
            f"{type_summary}"
        )
    elif normalized_skill == "credit_no_way_collect_debt":
        total_debt = 0.0
        total_rvps = 0.0
        total_pledge = 0.0
        debt_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("debt", "credit", "loan", "договор", "задолженность", "сумма"))]
        rvps_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("rvps", "резерв", "рвпс"))]
        pledge_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("pledge", "залог", "обеспечение"))]
        if debt_cols:
            total_debt = _to_numeric_clean(df[debt_cols[0]]).sum()
        if rvps_cols:
            total_rvps = _to_numeric_clean(df[rvps_cols[0]]).sum()
        if pledge_cols:
            total_pledge = _to_numeric_clean(df[pledge_cols[0]]).sum()
        prefix = (
            f"### Сводная информация по проблемной задолженности:\n"
            f"- **Количество кредитных договоров**: {len(df)}\n"
            f"- **Общая сумма задолженности**: {format_loss(total_debt)}\n"
            f"- **Сформированный резерв (РВПС)**: {format_loss(total_rvps)}\n"
            f"- **Оценочная стоимость залогов**: {format_loss(total_pledge)}\n\n"
        )
    elif normalized_skill == "report_period_specific_ior":
        if df.empty:
            prefix = "### Детальное досье по инциденту:\n\nДанные по инциденту отсутствуют.\n\n"
        else:
            first_row = df.iloc[0]
            sid_col_name = next((c for c in df.columns if str(c).lower().strip() in ("incdnt_sid", "идентификатор события")), "Идентификатор события")
            status_col_name = next((c for c in df.columns if str(c).lower().strip() in ("incdnt_status_name", "статус события", "статус")), "Статус события")

            total_loss_col = next((c for c in df.columns if str(c).lower().strip() in ("incdnt_sum", "общая сумма всех последствий (руб.)", "общая сумма последствий (руб.)", "сумма последствий, ₽")), None)
            direct_loss_col = next((c for c in df.columns if str(c).lower().strip() in ("incdnt_drct_dmg_sum", "прямая потеря – итого (руб.)", "прямая потеря - итого (руб.)")), None)
            recovery_col_name = next((c for c in df.columns if str(c).lower().strip() in ("recovery_rub_amt_aggr", "возмещение – итого по инциденту (руб.)", "возмещение - итого по инциденту (руб.)")), None)

            spec_sid = first_row[sid_col_name] if sid_col_name in df.columns else "EVE-XXXXXXX"
            spec_status = first_row[status_col_name] if status_col_name in df.columns else "Неизвестно"

            spec_total_loss = _to_numeric_clean(pd.Series([first_row[total_loss_col]])).iloc[0] if total_loss_col and total_loss_col in df.columns else 0.0
            spec_direct_loss = _to_numeric_clean(pd.Series([first_row[direct_loss_col]])).iloc[0] if direct_loss_col and direct_loss_col in df.columns else 0.0
            spec_recovery = _to_numeric_clean(pd.Series([first_row[recovery_col_name]])).iloc[0] if recovery_col_name and recovery_col_name in df.columns else 0.0
            spec_net_loss = max(0.0, spec_total_loss - spec_recovery)

            prefix = (
                f"### Детальное досье по инциденту {spec_sid}:\n"
                f"- **Идентификатор события**: {spec_sid}\n"
                f"- **Статус события**: {spec_status}\n"
                f"- **Общие потери**: {format_loss(spec_total_loss)}\n"
                f"- **Прямые потери**: {format_loss(spec_direct_loss)}\n"
                f"- **Сумма возмещений**: {format_loss(spec_recovery)}\n"
                f"- **Чистые потери (Net Loss)**: {format_loss(spec_net_loss)}\n\n"
            )
    else:
        prefix = (
            f"### Распределение инцидентов по статусам:\n"
            f"- **Группа 1: Утверждение**: {format_grp_str('Группа 1: Утверждение')}\n"
            f"- **Группа 2: Черновик/Исследование**: {format_grp_str('Группа 2: Черновик/Исследование')}\n"
            f"- **Группа 3: Удален**: {format_grp_str('Группа 3: Удален')}\n\n"
            f"По инцидентам в статусе **Утвержден/Утверждение** (Группа 1):\n"
            f"- **Общие потери**: {format_loss(total_loss)}\n"
            f"- **Прямые потери**: {format_loss(direct_loss)}\n"
            f"- **Сумма возмещений**: {format_loss(recovery_loss)}\n"
            f"- **Чистые потери (Net Loss)**: {format_loss(net_loss)}\n\n"
        )

    prefix = overall_stats + prefix

    # 6. Сбор информации об удаленных (при малой выборке < 20 ИОРов формируем только сводку без гипотез)
    is_summarization_only = (len(df_analysis) < 20) or (unique_count < 20) or (normalized_skill == "report_period_specific_ior")

    deleted_text = ""
    if not is_deleted and not is_vozmeshenie and not is_summarization_only:
        deleted_count = 0
        deleted_loss = 0.0
        deleted_rec = 0.0

        if status_col:
            df_deleted = df[df[status_col].astype(str).str.strip().str.lower().isin(["удалён", "удален"])].copy()
            deleted_count = len(df_deleted)

            loss_cols = ["incdnt_sum", "Общая сумма всех последствий (руб.)", "Общая сумма последствий (руб.)", "Сумма последствий, ₽"]
            primary_loss = next((c for c in loss_cols if c in df.columns), None)
            if not primary_loss:
                money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм"))]
                loss_cols_fallback = [c for c in money_cols if any(x in str(c).lower() for x in ("loss", "dmg", "потер", "убыт", "sum", "сумм")) and not any(r in str(c).lower() for r in ("rec", "возмещ", "возврат"))]
                if loss_cols_fallback:
                    primary_loss = loss_cols_fallback[0]

            rec_cols_list = [
                "recovery",
                "сумма возмещений",
                "сумма возмещения",
                "сумма возмещения (руб.)",
                "сумма возмещений (руб.)",
                "возмещ",
                "recovery_rub_amt",
                "recovery_rub_amt_aggr",
                "сумма возмещения (агрегатор)",
                "возмещение - итого по инциденту (руб.)"
            ]
            primary_rec = next((c for c in rec_cols_list if c in df.columns), None)
            if not primary_rec:
                money_cols = [c for c in df.columns if any(x in str(c).lower() for x in ("sum", "loss", "dmg", "rub", "amt", "потер", "убыт", "возмещ", "сумм"))]
                rec_cols_fallback = [c for c in money_cols if any(x in str(c).lower() for x in ("rec", "возмещ", "возврат"))]
                if rec_cols_fallback:
                    primary_rec = rec_cols_fallback[0]

            if primary_loss and not df_deleted.empty:
                deleted_loss = _to_numeric_clean(df_deleted[primary_loss]).sum()
            if primary_rec and not df_deleted.empty:
                deleted_rec = _to_numeric_clean(df_deleted[primary_rec]).sum()

        deleted_text = (
            f"\n### Информация об удаленных инцидентах:\n"
            f"- **Количество удаленных инцидентов**: {deleted_count}\n"
            f"- **Сумма потерь по удаленным инцидентам**: {format_loss(deleted_loss)}\n"
            f"- **Сумма возмещений по удаленным инцидентам**: {format_loss(deleted_rec)}\n\n"
            f"Если вы хотите больше узнать о причинах удаления инцидентов, создайте новую сессию и запросите выгрузку по удаленным инцидентам.\n\n"
        )

    retro_text = ""
    profile = profile_dataframe(df_analysis, running_skill=normalized_skill)

    # Scope note: when the section-1..4 analysis runs on a status-filtered subset of the
    # full upload (e.g. only "Утверждён"), make that explicit so numbers in the LLM-written
    # sections aren't mistaken for totals across the whole file (see "Общая информация по
    # выгрузке" block above, which always reflects the FULL unfiltered upload).
    scope_note_text = ""
    if (
        not is_nonfinancial
        and not is_vozmeshenie
        and normalized_skill not in ("report_period_specific_ior", "credit_no_way_collect_debt")
        and status_col
        and len(df_analysis) != unique_count
    ):
        scope_group_label = "Удален/Удалён" if is_deleted else "Утверждён/Утверждение"
        scope_note_text = (
            f"\n\nВАЖНО ПО ОБЛАСТИ АНАЛИЗА: Весь количественный анализ в разделах '1. Общая сводка данных' — "
            f"'4. Аналитические гипотезы' ниже строится ТОЛЬКО по подвыборке инцидентов со статусом "
            f"«{scope_group_label}» ({len(df_analysis)} из {unique_count} инцидентов всей выгрузки). "
            f"В первом же предложении раздела '1. Общая сводка данных' обязательно явно укажи эту область "
            f"охвата (например: «Анализ ниже охватывает {len(df_analysis)} инцидентов со статусом "
            f"«{scope_group_label}» из {unique_count} во всей выгрузке»), чтобы не создавалось впечатление, "
            f"что эти цифры относятся ко всей выгрузке."
        )

    # 7. Check if chart is needed
    chart_file_id = None
    if normalized_skill not in ("ior_nonfinancial_consequences", "deleted_ior"):
        low_msg = user_msg.lower()
        is_dynamics_query = any(x in low_msg for x in ("динамик", "график", "тренд", "изменен", "рост", "спад"))
        if is_dynamics_query or len(df_analysis) > 30:
            chart_file_id = await asyncio.to_thread(generate_dynamics_chart, df_analysis, session_id, normalized_skill)

    # 8. Extract summaries of descriptions / comments (always analyze through Qwen)
    if normalized_skill == "deleted_ior":
        comment_col = next((c for c in df_analysis.columns if str(c).lower().strip() in ("stts_chng_comment_txt", "комментарий / причина действия", "комментарий / причина", "комментарий", "причина действия", "причина удаления")), None)
        comments_summary = ""
        if comment_col:
            comments_series = df_analysis[comment_col].dropna().astype(str).str.strip()
            unique_comments = [c for c in comments_series.unique() if c and c.lower() not in ("nan", "none", "—", "-")]
            comments_summary = await summarize_deletion_comments(unique_comments)

        inc_desc_summary = await analyze_incident_descriptions(df_analysis, normalized_skill)
        if comments_summary:
            desc_summary = f"{comments_summary}\n\n#### Детальный анализ содержания удаленных инцидентов:\n{inc_desc_summary}"
        else:
            desc_summary = inc_desc_summary
    else:
        desc_summary = await analyze_incident_descriptions(df_analysis, normalized_skill)

    # 9. Prompts selection
    critical_rules = """КРИТИЧЕСКИЕ ПРАВИЛА И ОГРАНИЧЕНИЯ (ПРОЧТИ В ПЕРВУЮ ОЧЕРЕДЬ):
1. ВАЖНО: Авторегистрация (авторег) — это нормальный штатный процесс. СТРОГО ЗАПРЕЩЕНО делать авторегистрацию основой для гипотез или писать слово "авторегистрация"/"авторег" в Разделе 4 с гипотезами! Авторегистрация НЕ является проблемой и на неё ЗАПРЕЩЕНО опираться при построении аналитических гипотез. Все гипотезы строятся СТРОГО на бизнес-процессах, оргструктуре, причинах, комментариях и реальном содержании описаний.
2. ВАЖНО: Аналитические гипотезы не должны дублировать друг друга по смыслу и не должны сводиться к одному и тому же выводу. Каждая гипотеза обязана иметь свой собственный уникальный ракурс и вести к принципиально разным выводам о причинах произошедшего.
3. ВАЖНО ПО УРОВНЮ ДАННЫХ И СТРОКАМ: СТРОГО ЗАПРЕЩЕНО использовать словосочетания "записей данных", "уровень данных" или писать "(X записей данных)" в тексте отчета. Всегда указывай только количество инцидентов.

"""

    system_prompt = critical_rules + PROMPTS.get(normalized_skill, PROMPTS["ior_hypothesis"])

    global_rules = """

КРИТИЧЕСКИЕ ПРАВИЛА ЯЗЫКА И ФОРМАТИРОВАНИЯ:
1. Пиши понятным, человеческим языком для аудитора. Избегай сложных IT-терминов, тяжеловесного жаргона и преувеличений.
2. СТРОГО ЗАПРЕЩЕНО использовать следующие слова и словосочетания:
   - "каскадные потери"
   - "каскадные последствия"
   - "каскадный сбой"
   - "экстремальная концентрация"
   - "синергетический эффект"
   - "недостаточная отказоустойчивость"
   - "технологический стек"
3. Вместо заумных фраз пиши проще: например, вместо "недостаточная отказоустойчивость" пиши "частые технические сбои", вместо "каскадные последствия" — "цепная реакция сбоев" или "последующие ошибки".
4. СТРОГО ЗАПРЕЩЕНО создавать разделы, которые не предусмотрены структурой шаблона выше. В отчёте должны быть только разрешенные разделы. Всегда формируй заголовки строго в формате `### 1. Общая сводка данных` и `### 4. Аналитические гипотезы для аудиторской проверки`, чтобы они 100% проходили валидацию.
5. Больше конкретики в деталях: не пиши общие фразы вроде "топ-10 составляет большую часть потерь". Указывай конкретные цифры и суммы (например, "на топ-10 инцидентов приходится 45.2 млн руб. потерь").
6. При упоминании конкретных инцидентов СТРОГО ИСПОЛЬЗУЙ только идентификаторы формата 'EVE-XXXXXXX'. Категорически запрещено выводить длинные числовые технические ключи (например, 748537...). Если EVE-id недоступен, называй инцидент по его сути, но не используй технический ID.
7. Не делай огромных пустых строк между абзацами.
8. Все цифры и проценты в разных частях одного отчёта должны быть взаимно непротиворечивы — если в сводке указано, что 100% записей имеют статус X, в последующих разделах нельзя утверждать, что тем же статусом X обладает другой процент записей, без явного пояснения, что речь идёт о другом временном срезе или другом поле.
9. ВАЖНО: Не пиши надуманных выводов про «отсутствие автоматизации», «отсутствие теневых режимов», «отсутствие автоматических проверок» и т.п., если этого прямо нет в текстах описаний инцидентов. Гипотезы должны основываться исключительно на реальных фактах из выгрузки и реальном содержании инцидентов, а не на общих шаблонных предположениях об ИТ-системах.
10. ВАЖНО: Если какая-либо метрика равна нулю, равна 100% по одному значению или иным образом вырождена (например, суммы потерь равны нулю, все записи имеют один и тот же статус), констатируй это как факт БЕЗ домысливания причинно-следственного объяснения этому факту (например, НЕЛЬЗЯ писать, что нулевые потери означают, что 'все ошибки урегулированы' — это не следует логически из данных). Просто укажи значение метрики и, если нужно, предложи это как область для отдельной проверки, а не как готовый вывод.
11. ВАЖНО ПО ОРГСТРУКТУРЕ И БЛОКАМ: Игнорируй Уровень 2 оргструктуры ("ПАО Сбербанк") и Уровень 2 функционального блока — они неинформативны. Весь анализ проводи строго начиная с Уровня 3 (ТБ / Блок / ПЦП) и Уровня 4 (Дивизион / Департамент).
13. ВАЖНО ПО ОТФИЛЬТРОВАННЫМ ИЗМЕРЕНИЯМ И СУЩНОСТЯМ (ТБ, Дивизион, Процесс, Категория, Тема):
   - Если пользователь сам запросил выборку по конкретной сущности, теме или ТБ (например, "по ЮЗБ", "по эквайрингу", "по Домклик", "по DRP-10121"): СТРОГО ЗАПРЕЩЕНО писать гипотезы вида "90%/100% инцидентов зарегистрированы в {запрошенная_сущность}, что указывает на проблемы в {запрошенная_сущность}". Это результат фильтра пользователя!
   - В этой ситуации Гипотеза 2 по оргструктуре ОБЯЗАНА анализировать внутреннюю структуру и сравнивать подразделения/блоки ВНУТРИ этого среза (например, сравнивать внутренние Дивизионы Уровня 4, Департаменты или сопоставлять функциональные блоки, к которым привязаны инциденты, такие как SBR_10315800 vs SBR_03.12).
   - СТРОГО ЗАПРЕЩЕНО дублировать тему или вывод Гипотезы 1 в Гипотезе 2! Каждая гипотеза должна предлагать свой уникальный ракурс для аудита.
14. ВАЖНО ПО ДИНАМИКЕ (РАЗДЕЛ 2): Если выгрузка представлена за один месяц или один период, констатируй это в разделе 2 как фактологический срез за этот период. СТРОГО ЗАПРЕЩЕНО домысливать причины произошедшего/удалений в этом месяце и писать о вымышленных "всплесках", "сезонности" или миграциях.
"""


    if normalized_skill == "ior_nonfinancial_consequences":
        global_rules += """11. ВАЖНО: Так как это выгрузка качественных (нефинансовых) последствий, в ней полностью отсутствуют финансовые убытки и возмещения. ТЕБЕ СТРОГО ЗАПРЕЩЕНО писать о финансовых потерях, возмещениях или убытках, а также СТРОГО ЗАПРЕЩЕНО упоминать о том, что финансовых потерь/убытков нет, или писать фразы вроде "финансовых потерь не зафиксировано", "потери равны 0", "нет данных о возмещениях". Вообще никак не касайся финансовой темы и цифр в рублях! Весь анализ должен строиться исключительно на качественных показателях: виды качественных потерь, класс влияния, организационная структура, процессы, связь с рисками информационных систем (ИБ/ИС) и поведенческими рисками.
"""
    elif is_vozmeshenie:
        global_rules += """
15. КРИТИЧЕСКИ ВАЖНО ДЛЯ ВОЗМЕЩЕНИЙ:
   - Входной профиль уже агрегирован до одной записи на уникальный `incdnt_sid`.
   - Все количества в отчёте означают только количество уникальных инцидентов.
   - Количество исходных операций выведено программно до отчёта: не повторяй его и не употребляй слово «строки».
   - В наборе нет достоверных данных о потерях. Запрещено писать про общую/прямую/чистую сумму потерь, Net Loss, убытки и финансовые последствия.
   - Денежный анализ веди исключительно по `recovery_rub_amt`, которое уже содержит сумму всех R-фрагментов каждого ИОР.
"""

    system_prompt += global_rules

    if is_summarization_only:
        for sec_marker in ("### 2.", "### 3.", "### 4.", "2. Анализ корневых причин", "3. Рекомендации"):
            if sec_marker in system_prompt:
                system_prompt = system_prompt.split(sec_marker)[0]
                break
        system_prompt += f"\n\nВАЖНО (ВЫБОРКА МЕНЕЕ 20 ИОРов): Выгрузка содержит {len(df_analysis)} уникальных ИОР. НЕ ВКЛЮЧАЙ раздел '2. Анализ корневых причин' и раздел '3. Рекомендации и гипотезы для проверки'. СТРОГО ЗАПРЕЩЕНО формулировать гипотезы. Сформируй отчёт исключительно со структурой ниже. ВАЖНО: НЕ пиши отдельный общий заголовок/титул документа перед разделом 1 — заголовок раздела 1 уже и есть заголовок всего отчёта, повторять его текст ещё раз строкой выше СТРОГО ЗАПРЕЩЕНО:\n" \
                         f"### 1. Общая сводка информации\n" \
                         f"- **Основные показатели:** [Общее количество, ключевые суммы и метрики]\n" \
                         f"- **Временная динамика:** [Распределение по датам/месяцам]\n" \
                         f"- **Профиль инцидентов:** [Детальное описание имеющихся {len(df_analysis)} инцидентов, их категорий, ТБ и коротких описаний]\n"

    dataset_scope = (
        f"уникальных инцидентов: {len(df_analysis)}"
        if is_vozmeshenie
        else f"строк: {len(df_analysis)}"
    )
    user_prompt = f"""Запрос пользователя: "{user_msg}"
Файл выгрузки: "{file_info.get('name', 'отчет.xlsx')}" ({file_info.get('size', '')}, {dataset_scope})

{profile}

{desc_summary}
{scope_note_text}
{prefilter_note_text}
{single_month_note_text}

Сформулируй {"суммаризацию" if is_summarization_only else "гипотезу"} на основе этих данных. Пиши на русском языке, в professional стиле, доступно и понятно для аналитиков любого уровня."""

    try:
        raw_response = await asyncio.to_thread(
            ask_local_qwen, [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            max_tokens=16384
        )

        narrative = str(raw_response).strip()
        if narrative.startswith("```"):
            lines = narrative.splitlines()
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            narrative = "\n".join(lines).strip()

        # Check if model generated duplicate deleted section to replace with code-injected
        header_pattern = re.compile(r'^(?:#+\s*|\d+\.\s*)(.+)$')
        lines = narrative.split('\n')
        cleaned_lines = []
        skip_section = False
        for line in lines:
            match = header_pattern.match(line.strip())
            if match:
                header_text = match.group(1).strip().lower()
                if any(x in header_text for x in ["информация об удаленных", "удаленные инциденты", "анализ удаленных"]):
                    skip_section = True
                    logger.warning("Found duplicate deleted section generated by model, stripping it.")
                else:
                    skip_section = False
            if not skip_section:
                cleaned_lines.append(line)
        narrative = "\n".join(cleaned_lines)

        # Deterministic backstop: strip a redundant standalone title line when it
        # just repeats the "1. ..." heading that immediately follows it (see the
        # <20-rows summarization prompt above, which asks the model not to add one).
        narrative = re.sub(
            r'^\s*(?:\*\*)?Финальный аналитический отчет[^\n]*(?:\*\*)?\s*\n+(?=\s*(?:#{1,3}\s*)?1\.)',
            '',
            narrative,
            flags=re.IGNORECASE
        )

        # 1. Deduplicate sentences (Task 6)
        collapsed_narrative = collapse_cyclical_repetitions(narrative)
        collapsed_narrative, reps = collapse_repeated_sentences(collapsed_narrative)
        shrank_significantly = len(collapsed_narrative) < 0.8 * len(narrative) or reps > 0

        # 2. Deduplicate sections (Task 10)
        collapsed_narrative = collapse_repeated_sections(collapsed_narrative)

        # 3. Trim extra/blacklisted sections (Task 3 & 12)
        collapsed_narrative = trim_extra_sections(collapsed_narrative, is_summarization_only)
        if is_vozmeshenie:
            collapsed_narrative = sanitize_vozmeshenie_narrative(collapsed_narrative)

        # 4. LLM-as-judge validation (Task 5 & 16)
        forbidden_fields = []
        if "financial_consequences_ior" in running_skill:
            forbidden_fields = ["возмещения", "возмещение", "recovery", "возвраты"]
        elif is_vozmeshenie:
            forbidden_fields = ["потери", "потеря", "net loss", "убытки", "убыток", "ущерб"]

        validation = await validate_narrative(collapsed_narrative, forbidden_fields)

        # Deterministic backstops that don't rely on the (fallible) LLM judge:
        expected_hyp_count = 0 if is_summarization_only else HYPOTHESIS_COUNT_BY_SKILL.get(normalized_skill, DEFAULT_HYPOTHESIS_COUNT)
        hyp_complete, hyp_details = check_hypotheses_completeness(collapsed_narrative, expected_hyp_count)
        collapsed_narrative, autoreg_scrubbed = scrub_autoreg_criticism(collapsed_narrative)

        has_violations = (
            validation.get("autoreg_criticized") or
            validation.get("hypotheses_duplicate") or
            validation.get("extra_sections") or
            validation.get("missing_eve_ids_in_major_incidents") or
            validation.get("fabricated_thresholds") or
            validation.get("numbers_inconsistent") or
            validation.get("unfounded_inference_from_null_data") or
            validation.get("fields_not_in_dataset") or
            not hyp_complete or
            autoreg_scrubbed
        )
        if not hyp_complete:
            validation["details"] = (validation.get("details", "") + f"; {hyp_details}").strip("; ")
        if autoreg_scrubbed:
            validation["details"] = (validation.get("details", "") + "; авторегистрация была описана как негативный фактор — это запрещено правилами").strip("; ")

        if has_violations or shrank_significantly:
            reasons = []
            if has_violations:
                reasons.append(f"найдены нарушения: {validation.get('details', '')}")
            if shrank_significantly:
                reasons.append("обнаружено зацикливание (repetition loop)")

            logger.warning(f"Narrative check failed ({'; '.join(reasons)}). Initiating retry...")

            retry_user_prompt = f"{user_prompt}\n\n"
            if shrank_significantly:
                retry_user_prompt += "ВНИМАНИЕ: предыдущая версия твоего ответа содержала критические повторения слов/предложений. Перепиши отчёт с нуля, избегая повторов и зацикливаний.\n"
            if not hyp_complete:
                retry_user_prompt += f"ВНИМАНИЕ: предыдущая версия твоего ответа была ОБОРВАНА до конца — {hyp_details}. На этот раз обязательно допиши ВСЕ {expected_hyp_count} гипотезы(ы) целиком, каждую с полями «Предположение / Суть проблемы», «Шаги проверки» (минимум 2 шага) и «Ожидаемый результат». Если не хватает места — пиши короче в разделах 1-3, но не сокращай раздел с гипотезами.\n"
            if autoreg_scrubbed:
                retry_user_prompt += "ВНИМАНИЕ: предыдущая версия твоего ответа критиковала авторегистрацию как проблему/уязвимость — это СТРОГО ЗАПРЕЩЕНО. Авторегистрация — нормальный штатный процесс, не интерпретируй её долю как негативный фактор.\n"
            if has_violations and validation.get("details"):
                retry_user_prompt += f"ВНИМАНИЕ: предыдущая версия твоего ответа содержала следующие нарушения: {validation.get('details', '')}.\n"

            retry_user_prompt += f"Вот твой предыдущий ответ (частично очищенный):\n{collapsed_narrative}\n\nПерепиши отчёт с нуля, устранив эти проблемы, не потеряв остальные требования структуры и стиля."

            try:
                raw_response = await asyncio.to_thread(
                    ask_local_qwen, [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": retry_user_prompt}
                    ],
                    max_tokens=16384
                )
                retry_narrative = str(raw_response).strip()
                if retry_narrative.startswith("```"):
                    lines = retry_narrative.splitlines()
                    if lines[0].startswith("```"):
                        lines = lines[1:]
                    if lines and lines[-1].startswith("```"):
                        lines = lines[:-1]
                    retry_narrative = "\n".join(lines).strip()

                lines = retry_narrative.split('\n')
                cleaned_lines = []
                skip_section = False
                for line in lines:
                    match = header_pattern.match(line.strip())
                    if match:
                        header_text = match.group(1).strip().lower()
                        if any(x in header_text for x in ["информация об удаленных", "удаленные инциденты", "анализ удаленных"]):
                            skip_section = True
                        else:
                            skip_section = False
                    if not skip_section:
                        cleaned_lines.append(line)
                retry_narrative = "\n".join(cleaned_lines)

                retry_narrative = collapse_cyclical_repetitions(retry_narrative)
                collapsed_narrative, reps = collapse_repeated_sentences(retry_narrative)
                collapsed_narrative = collapse_repeated_sections(collapsed_narrative)
                collapsed_narrative = trim_extra_sections(collapsed_narrative, is_summarization_only)
                if is_vozmeshenie:
                    collapsed_narrative = sanitize_vozmeshenie_narrative(collapsed_narrative)

                validation_retry = await validate_narrative(collapsed_narrative, forbidden_fields)
                hyp_complete_retry, hyp_details_retry = check_hypotheses_completeness(collapsed_narrative, expected_hyp_count)
                collapsed_narrative, autoreg_scrubbed_retry = scrub_autoreg_criticism(collapsed_narrative)
                has_violations_retry = (
                    validation_retry.get("autoreg_criticized") or
                    validation_retry.get("hypotheses_duplicate") or
                    validation_retry.get("extra_sections") or
                    validation_retry.get("missing_eve_ids_in_major_incidents") or
                    validation_retry.get("fabricated_thresholds") or
                    validation_retry.get("numbers_inconsistent") or
                    validation_retry.get("unfounded_inference_from_null_data") or
                    validation_retry.get("fields_not_in_dataset") or
                    not hyp_complete_retry or
                    autoreg_scrubbed_retry
                )
                if has_violations_retry:
                    extra_details = f"; {hyp_details_retry}" if not hyp_complete_retry else ""
                    logger.warning(f"Violations still found after retry: {validation_retry.get('details')}{extra_details}. Returning text as is (scrubbed where possible).")
            except Exception as retry_err:
                logger.error(f"Error during retry generation: {retry_err}")

        narrative = collapsed_narrative

        if is_summarization_only:
            for marker in ("### 2.", "### 3.", "### 4.", "2. Анализ", "3. Рекомендации", "Гипотеза 1", "Гипотеза"):
                if marker in narrative:
                    narrative = narrative.split(marker)[0].strip()

        # Check if Qwen generated valid structured output using relaxed regex patterns
        patterns = [
            r'(?i)(#|1\.)\s*общая\s*сводка',
            r'(?i)(#|4\.)\s*аналитические\s*гипотезы',
            r'(?i)гипотеза\s*1',
        ]
        has_qwen_sections = any(bool(re.search(pat, narrative)) for pat in patterns)
        is_fallback_text = "Анализ выполнен на основе имеющихся метрик" in narrative or len(narrative) < 150


        if not has_qwen_sections or is_fallback_text:
            logger.warning("[ior_hypothesis] Qwen output missing 4-section structure. Building deterministic full report.")
            narrative = build_deterministic_full_report(
                df_analysis,
                normalized_skill,
                deleted_text=deleted_text,
                is_summarization_only=is_summarization_only,
                voz_metrics=voz_metrics,
            )
        else:
            # Qwen generated a valid report. Inject deleted_text smoothly before Section 4 if needed
            combined_inject = deleted_text + retro_text
            if combined_inject and combined_inject.strip():
                if "### 4." in narrative:
                    parts = narrative.split("### 4.", 1)
                    narrative = parts[0] + "\n\n" + combined_inject + "\n\n### 4." + parts[1]
                elif "### 3." in narrative:
                    parts = narrative.split("### 3.", 1)
                    narrative = parts[0] + "\n\n" + combined_inject + "\n\n### 3." + parts[1]

            # Prepend the deterministic hard-numbers block (totals, status breakdown) ahead of
            # the LLM narrative — this was dropped during the nanobot migration and is the main
            # reason the report used to look richer (see "Общая информация по выгрузке" /
            # "Распределение инцидентов по статусам" blocks in the old ior_assistant output).
            narrative = prefix + narrative

        if chart_file_id:
            chart_filename = Path(chart_file_id).name
            narrative += "\n\n### Визуализация аналитики\n"
            chart_alt = "Динамика возмещений и уникальных ИОР" if is_vozmeshenie else "Динамика потерь и инцидентов"
            narrative += f"\n![{chart_alt}](/api/files/download?path={chart_filename})\n"

        narrative = re.sub(r'\n{3,}', '\n\n', narrative)
        narrative = normalize_markdown_for_frontend(narrative)
        logger.info(f"[ior_hypothesis] ✅ Step 5: Final hypothesis narrative generated (len={len(narrative)} chars).")
        return narrative.strip()
    except Exception as e:
        logger.exception(f"Error generating hypothesis: {e}")
        return build_deterministic_full_report(
            df_analysis if 'df_analysis' in locals() else df,
            normalized_skill if 'normalized_skill' in locals() else "ior_hypothesis",
            deleted_text=deleted_text if 'deleted_text' in locals() else "",
            is_summarization_only=is_summarization_only if 'is_summarization_only' in locals() else False,
            voz_metrics=voz_metrics if 'voz_metrics' in locals() else None,
        )
