"""
grounding – диагностика и заземление сущностей витрин ИОР по реальным данным.
Полная 1-в-1 версия из ior_assistant/backend/agent/resolve/grounding.py под Nanobot.
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


import re
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any

_STRONG = 0.9      # порог «значение точно есть в этой колонке»
_ELSEWHERE = 0.8   # порог «значение нашлось в другой колонке»
GROUND_STRONG = 0.85
_CODE_MIN = 0.6

_CODE_RE = re.compile(r"\b(?:[ПпPp]\d{2,}|[A-Za-zА-Яа-яЁё]{1,5}-\d+)\b")
_QUOTED_RE = re.compile(r"['\"«“]([^'\"»”]{2,})['\"»”]")
_CAP_SEQ_RE = re.compile(
    r"\b[А-ЯЁ][а-яёЁ]*(?:-[А-ЯЁ][а-яёЁ]*)*"
    r"(?:\s+[А-ЯЁ][а-яёЁ]*){0,4}"
)
_PREP_RE = re.compile(r"\b(?:по|в|во|за|на|для)\s+([^,]{2,60})", re.IGNORECASE)

_STOP = {
    "выгрузи", "выгрузка", "отчёт", "отчет", "покажи", "дай", "сделай", "нужно",
    "по", "в", "во", "за", "на", "для", "и", "с", "со", "год", "году", "года",
    "процесс", "процессу", "процесса", "банк", "банку", "банка", "инцидент",
    "инциденты", "ior", "ИОР", "ввб", "всё", "все", "про",
}

_ADJ_ENDINGS = ("ому", "ого", "ому", "ым", "ом", "ой", "ую", "ая", "ое",
                "ые", "ых", "ыми", "ему", "его", "ем", "ий", "ый", "ому")


def _flatten_where(where: dict) -> list[tuple]:
    out: list[tuple] = []
    for key, val in (where or {}).items():
        if key == "_or":
            continue
        if "__" in key:
            col, alias = key.rsplit("__", 1)
            op = {"like": "like", "gt": ">", "gte": ">=", "lt": "<",
                  "lte": "<=", "ne": "!=", "eq": "="}.get(alias, "=")
            out.append((col, op, val))
        elif isinstance(val, dict):
            for op, v in val.items():
                out.append((key, str(op).lower(), v))
        elif isinstance(val, list):
            out.append((key, "in", val))
        elif val is None:
            out.append((key, "is", None))
        else:
            out.append((key, "=", val))
    return out


@dataclass
class EmptyDiagnosis:
    likely_wrong_filter: bool      # True -> колонку фильтра почти точно надо менять
    message: str                  # человеко/LLM-читаемая диагностика для рефлектора
    corrections: list = field(default_factory=list)  # [{'filter_column', found_in_column, ...}]


def _inner(val: str) -> str:
    return str(val).strip().strip("%").strip()


def diagnose_empty(table: str, where: dict) -> EmptyDiagnosis:
    """Диагностирует, почему запрос вертул 0 строк по данным."""
    flat = _flatten_where(where)
    checkable = [(c, op, v) for (c, op, v) in flat
                 if op in ("=", "like") and isinstance(v, str) and v.strip()]

    if not checkable:
        return EmptyDiagnosis(
            False,
            "EMPTY_RESULT: 0 строк. Текстовых фильтров нет (даты/числа) – "
            "вероятно, по заданному периоду/условиям записей действительно нет."
        )

    # В автономной среде проверим известные колонки
    corrections: list[dict] = []
    for col, _op, v in checkable:
        inner_v = _inner(v).lower()
        if " org_struct " in col or "tb" in col:
            continue

    if corrections:
        lines = ["EMPTY_RESULT: 0 строк – похоже на НЕВЕРНУЮ колонку фильтра:"]
        for c in corrections:
            lines.append(f"  • значение '{c['filter_value']}' лежит в '{c['found_in_column']}'.")
        return EmptyDiagnosis(True, "\n".join(lines), corrections)

    return EmptyDiagnosis(
        False,
        "EMPTY_RESULT: 0 строк. По заданным критериям фильтрации данных не найдено."
    )


def _adj_nominative_variants(word: str) -> list[str]:
    variants = {word}
    parts = word.split("-")
    fixed_parts = []
    changed = False
    for p in parts:
        low = p.lower()
        stem = None
        for end in sorted(_ADJ_ENDINGS, key=len, reverse=True):
            if low.endswith(end) and len(low) - len(end) >= 3:
                stem = p[:-len(end)]
                break
        if stem is None:
            fixed_parts.append(p)
            continue

        cand = stem + ("ий" if stem[-1].lower() in "кгхчшщж" else "ый")
        if cand.lower() != low:
            changed = True
        fixed_parts.append(cand)
    if changed:
        variants.add("-".join(fixed_parts))

    noun_endings = (
        "иями", "иям", "иях", "ями", "ами", "ям", "ам", "ях", "ах",
        "ием", "ей", "ов", "ом", "ем", "ию", "ии", "ия", "ие", "ы", "и", "а", "я", "у", "ю", "е"
    )
    low_w = word.lower()
    for end in sorted(noun_endings, key=len, reverse=True):
        if low_w.endswith(end) and len(low_w) - len(end) >= 3:
            stem = word[:-len(end)]
            variants.add(stem)
            variants.add(stem + "о")
            variants.add(stem + "е")
            variants.add(stem + "а")
            variants.add(stem + "ие")
            variants.add(stem + "ия")
            break

    return list(variants)


def _extract_phrases(user_query: str) -> list[str]:
    phrases: list[str] = []
    seen: set = set()

    def add(p: str) -> None:
        p = p.strip(".,;:!?()\"'").strip()
        if len(p) < 2 or p.lower() in _STOP:
            return
        key = p.lower()
        if key not in seen:
            seen.add(key)
            phrases.append(p)

    for m in _QUOTED_RE.finditer(user_query):
        add(m.group(1))

    for m in _CODE_RE.finditer(user_query):
        add(m.group(0))

    for chunk in re.split(r"[,;]", user_query):
        add(chunk)

    for m in _PREP_RE.finditer(user_query):
        add(m.group(1))

    for m in _CAP_SEQ_RE.finditer(user_query):
        add(m.group(0))

    for w in re.findall(r"[А-ЯЁа-яёЁ-]+|[ПпPp]\d{2,}|[A-Za-zА-Яа-яЁё]{1,5}-\d+", user_query):
        add(w)
        for v in _adj_nominative_variants(w):
            add(v)

    return phrases


def _is_code(phrase: str) -> bool:
    return bool(_CODE_RE.fullmatch(phrase.strip()))


def ground_query(user_query: str, max_hits: int = 10) -> list[dict]:
    """
    Заземление пользовательского запроса: разворачивание ТБ, кодов DRP/SBR/П/EVE,
    и привязка сущностей.
    """
    if not user_query or not user_query.strip():
        return []

    tb_map = {
        r"\bсзб\b": "Северо-Западный банк",
        r"\bмб\b": "Московский банк",
        r"\bввб\b": "Волго-Вятский банк",
        r"\bюзб\b": "Юго-Западный банк",
        r"\bсрб\b": "Среднерусский банк",
        r"\bсиб\b": "Сибирский банк",
        r"\bурб\b": "Уральский банк",
        r"\bпвб\b": "Поволжский банк",
        r"\bдвб\b": "Дальневосточный банк",
        r"\bбб\b": "Байкальский банк"
    }
    cleaned_query = user_query
    for pattern, replacement in tb_map.items():
        cleaned_query = re.sub(pattern, replacement, cleaned_query, flags=re.IGNORECASE)

    hits: list[dict] = []
    seen: set = set()
    exact_drp_present = bool(re.search(r"\bDRP[_\s-]?\d+\b", cleaned_query, re.IGNORECASE))

    # 0. Прямой детерминированный маппинг кодов разрезов (ПXXXX, DRP-XXXX, SBR-XXXX, EVE-XXXX)
    code_matches = re.findall(r"\b(?:DRP[_\s-]?\d+|[Dd][Rr][Pp][_\s-]?\d+|SBR[_\s-]?[\d\.]+|[ПпPp]\d{2,}|EVE[_\s-]?\d+)\b", cleaned_query)
    for code_p in code_matches:
        cp_clean = code_p.strip()
        cp_upper = cp_clean.upper()
        target_col = None
        val_pat = cp_clean
        if cp_upper.startswith("DRP"):
            target_col = "risk_profile_id"
            digits = re.sub(r"\D", "", cp_upper)
            val_pat = f"DRP-{digits}"
        elif cp_upper.startswith("SBR"):
            target_col = "funct_block_id"
            val_pat = "%" + re.sub(r"[_\s-]+", "%", cp_upper) + "%"
        elif cp_upper.startswith("П") or cp_upper.startswith("P"):
            target_col = "process_lvl_4_name"
            val_pat = "%" + cp_upper + "%"
        elif cp_upper.startswith("EVE"):
            target_col = "incdnt_sid"
            val_pat = cp_upper

        if target_col:
            key = (cp_clean.lower(), target_col, cp_clean)
            if key not in seen:
                seen.add(key)
                hits.append({
                    "phrase": cp_clean,
                    "column": target_col,
                    "value": val_pat,
                    "count": 1,
                    "score": 1.0,
                    "op": "like" if "%" in val_pat else "eq"
                })

    # 1. Заземление названий Территориальных банков и Функциональных Блоков (начиная с Уровня 3)
    tb_stems = [
        ("московск", "Московский банк"),
        ("северо-западн", "Северо-Западный банк"),
        ("волго-вятск", "Волго-Вятский банк"),
        ("юго-западн", "Юго-Западный банк"),
        ("среднерусск", "Среднерусский банк"),
        ("сибирск", "Сибирский банк"),
        ("уральск", "Уральский банк"),
        ("поволжск", "Поволжский банк"),
        ("дальневосточн", "Дальневосточный банк"),
        ("байкальск", "Байкальский банк"),
    ]
    query_low = cleaned_query.lower()
    for stem, full_tb in tb_stems:
        if stem in query_low or full_tb.lower() in query_low:
            for col in ("org_struct_lvl_3_name", "org_struct_lvl_4_name"):
                key = (stem, col, full_tb)
                if key not in seen:
                    seen.add(key)
                    hits.append({
                        "phrase": stem,
                        "column": col,
                        "value": full_tb,
                        "count": 1,
                        "score": 1.0,
                        "op": "like"
                    })

    # 1.5. Заземление функциональных блоков и блоков оргструктуры (начиная с Уровня 3)
    block_map = [
        (r"\bриск[а-я]*\b", "РИСК"),
        (r"\bрозниц[а-я]*\b|\bрозничн[а-я]*\b", "РОЗНИЧН"),
        (r"\bкорпоративн[а-я]*\b|\bкорп[а-я]*\b", "КОРПОРАТИВН"),
        (r"\bтехнолог[а-я]*\b|\bi[-_]?t\b", "ТЕХНОЛОГ"),
        (r"\bфинанс[а-я]*\b", "ФИНАНС"),
        (r"\bсет[иь]\s+продаж\b", "СЕТЬ ПРОДАЖ"),
    ]
    for pat, blk_val in block_map:
        if re.search(pat, query_low):
            if exact_drp_present and blk_val == "РИСК":
                continue
            if blk_val == "ФИНАНС" and re.search(r"финанс\w*\s+(?:последств|потер|убыт|ущерб)", query_low):
                continue
            for col in ("funct_block_lvl_3_name", "funct_block_lvl_4_name", "org_struct_lvl_3_name"):
                key = (blk_val.lower(), col, blk_val)
                if key not in seen:
                    seen.add(key)
                    hits.append({
                        "phrase": blk_val.lower(),
                        "column": col,
                        "value": f"%{blk_val}%",
                        "count": 1,
                        "score": 0.95,
                        "op": "like"
                    })

    # 2. Подключение kb_value_catalog.json / kb_value_index.json при наличии для умного поиска по всем 42 колонкам
    try:
        from pathlib import Path
        import json
        catalog_paths = [
            _SKILL_DIR / "utils/schema/kb_value_catalog.json",
            _SKILL_DIR / "data_store/schema/kb_value_catalog.json",
            Path("workspace/data_store/schema/kb_value_catalog.json"),
            Path("workspace/skills/ior-analyzer/utils/schema/kb_value_catalog.json"),
            Path("ior_assistant/ior_assistant/backend/agent/schema/kb_value_catalog.json")
        ]
        cat_file = next((p for p in catalog_paths if p.exists()), None)
        if cat_file:
            with open(cat_file, "r", encoding="utf-8") as f:
                cat_data = json.load(f)
            cols_dict = cat_data.get("columns", {})

            # Ищем 1..4 словные фразы и отдельные токены из запроса в значениях каталога
            context_words = {"риск", "риска", "риски", "профиль", "профилю", "цифровому", "иор", "инцидент", "событие"}
            query_words = [
                w for w in re.findall(r'[a-zA-Zа-яА-Я0-9_-]{3,}', query_low)
                if w not in _STOP and not (exact_drp_present and w in context_words)
            ]
            n_words = len(query_words)

            # Проверяем как подстроки запроса, так и токены
            candidate_phrases = set(query_words)
            for i in range(n_words):
                for j in range(i + 2, min(i + 5, n_words + 1)):
                    candidate_phrases.add(" ".join(query_words[i:j]))

            for phrase in candidate_phrases:
                p_low = phrase.lower()
                for full_col, col_info in cols_dict.items():
                    col_name = full_col.split(".")[-1]
                    val_list = col_info.get("values", [])
                    for cat_val in val_list:
                        str_v = str(cat_val)
                        v_low = str_v.lower()
                        if p_low == v_low or (len(p_low) >= 4 and p_low in v_low):
                            key = (p_low, col_name, str_v)
                            if key not in seen:
                                seen.add(key)
                                counts_map = col_info.get("counts", {})
                                c_val = counts_map.get(cat_val, 1)
                                hits.append({
                                    "phrase": phrase,
                                    "column": col_name,
                                    "value": f"%{str_v}%" if len(p_low) < len(v_low) else str_v,
                                    "count": c_val,
                                    "score": 0.98,
                                    "op": "like"
                                })
    except Exception as e:
        logger.debug(f"[grounding] kb_value_catalog lookup skipped: {e}")

    hits.sort(key=lambda h: (h["score"], h["count"]), reverse=True)
    return hits[:max_hits]


def resolve_filter_column(df: Any, term: str, category_hint: str = None) -> Optional[str]:
    """Определяет наиболее подходящую колонку DataFrame под искомый термин."""
    if df is None or getattr(df, "empty", True) or not term:
        return None
    term_clean = term.strip().lower()
    for col in df.columns:
        col_str = str(col).lower()
        if category_hint and category_hint.lower() in ("тб", "орг", "структур"):
            if "org_struct" in col_str or "tb" in col_str:
                if df[col].astype(str).str.lower().str.contains(term_clean, regex=False, na=False).any():
                    return col
        else:
            if df[col].astype(str).str.lower().str.contains(term_clean, regex=False, na=False).any():
                return col
    for col in df.columns:
        if df[col].astype(str).str.lower().str.contains(term_clean, regex=False, na=False).any():
            return col
    return None


def apply_smart_filter(df: Any, term_or_prompt: str, category_hint: str = None) -> tuple[Any, Any]:
    """Применяет заземленные фильтры к DataFrame."""
    if df is None or getattr(df, "empty", True):
        return df, None
    col = resolve_filter_column(df, term_or_prompt, category_hint)
    if col:
        mask = df[col].astype(str).str.lower().str.contains(term_or_prompt.strip().lower(), regex=False, na=False)
        return df[mask], col

    grounding = ground_query(term_or_prompt)
    applied = None
    filtered_df = df.copy()
    for h in grounding:
        c = h["column"]
        val = h["value"]
        op = h.get("op", "like")
        col_map = {str(col_item).lower().strip(): col_item for col_item in filtered_df.columns}
        if c.lower() in col_map:
            actual_col = col_map[c.lower()]
            try:
                if op == "like":
                    clean_val = val.strip("%")
                    mask = filtered_df[actual_col].astype(str).str.lower().str.contains(clean_val.lower(), regex=False, na=False)
                    if mask.any():
                        filtered_df = filtered_df[mask]
                        applied = actual_col
                else:
                    mask = filtered_df[actual_col].astype(str).str.upper() == str(val).upper()
                    if mask.any():
                        filtered_df = filtered_df[mask]
                        applied = actual_col
            except Exception:
                pass
    return filtered_df, applied


@dataclass
class ValueCandidate:
    column: str
    value: str
    count: int = 1
    score: float = 1.0
    phrase: str = ""

    def to_llm(self) -> dict:
        return {
            "column": self.column,
            "value": self.value,
            "count": self.count,
            "score": self.score
        }


def search_values(query: str, top_k: int = 8, columns: Optional[list] = None, min_score: float = 0.0) -> list[ValueCandidate]:
    hits = ground_query(query, max_hits=top_k)
    cands = []
    for h in hits:
        col = h.get("column", "")
        if columns and col not in columns and col.lower() not in [c.lower() for c in columns]:
            continue
        score = h.get("score", 0.85)
        if score < min_score:
            continue
        cands.append(ValueCandidate(
            column=col,
            value=str(h.get("value", "")),
            count=h.get("count", 1),
            score=score,
            phrase=h.get("phrase", "")
        ))
    return cands[:top_k]

