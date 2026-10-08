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


from utils.resolve.semantic_grounding import ground_query


def resolve_filter_column(df: Any, term: str, category_hint: str = None) -> Optional[str]:
    """Определяет наиболее подходящую колонку DataFrame под искомый термин."""
    if df is None or getattr(df, "empty", True) or not term:
        return None
    term_clean = term.strip().lower()
    hint=(category_hint or "").lower()
    if hint in ("тб", "орг", "структур", "origin"):
        permitted=[c for c in df.columns if str(c).lower()=="org_struct_lvl_3_name"]
    elif hint in ("блок", "ответственность", "responsibility"):
        permitted=[c for c in df.columns if str(c).lower()=="funct_block_lvl_3_name"]
    else:
        permitted=[c for c in df.columns if not str(c).lower().startswith(("org_struct_lvl_", "funct_block_lvl_")) or str(c).lower() in ("org_struct_lvl_3_name", "funct_block_lvl_3_name")]
    matches=[c for c in permitted if df[c].astype(str).str.lower().str.contains(term_clean,regex=False,na=False).any()]
    if len(matches)>1 and not hint:
        from utils.resolve.request_outcome import ClarificationRequired
        raise ClarificationRequired("Уточните, нужны ИОР по месту происхождения или по зоне ответственности?")
    return matches[0] if matches else None


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
    if columns:
        from utils.resolve.semantic_grounding import load_catalog
        catalogue=load_catalog()
        needle=query.strip().strip("%").casefold()
        candidates=[]
        for col in columns:
            for raw in catalogue.get(col,[]):
                value=str(raw)
                if needle and needle in value.casefold():
                    score=1.0 if needle==value.casefold() else .8
                    if score>=min_score:
                        candidates.append(ValueCandidate(col,value,score=score,phrase=query))
        return sorted(candidates,key=lambda c:c.score,reverse=True)[:top_k]
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


import logging
logger = logging.getLogger(__name__)
