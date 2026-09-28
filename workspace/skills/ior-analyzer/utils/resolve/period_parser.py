"""
period_parser – детерминированный разбор русских периодов в диапазон дат.
Полная версия из ior_assistant/backend/agent/resolve/period_parser.py под Nanobot.
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
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Optional

# Колонка по умолчанию для фильтра по периоду
DEFAULT_DATE_COLUMN = "incdnt_entry_dt"

# Месяц: стем (для именительного и родительного: январь/января) -> номер + им. название.
_MONTHS = [
    (r"январ\w*", 1, "январь"),
    (r"феврал\w*", 2, "февраль"),
    (r"март\w*", 3, "март"),
    (r"апрел\w*", 4, "апрель"),
    (r"ма[йяе]\b", 5, "май"),
    (r"июн\w*", 6, "июнь"),
    (r"июл\w*", 7, "июль"),
    (r"август\w*", 8, "август"),
    (r"сентябр\w*", 9, "сентябрь"),
    (r"октябр\w*", 10, "октябрь"),
    (r"ноябр\w*", 11, "ноябрь"),
    (r"декабр\w*", 12, "декабрь"),
]

_QUARTER_WORD = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "четвёрт": 4}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}

# Явные даты: DD.MM.YYYY (рус. формат, день первый) и ISO YYYY-MM-DD.
_DMY = re.compile(r"\b(\d{1,2})[\.\-/](\d{1,2})[\.\-/](20\d\d|\d\d)\b")
_YMD = re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b")


def _find_explicit_dates(text: str) -> list:
    """Все явные даты в тексте (DD.MM.YYYY и YYYY-MM-DD), валидные, без сортировки."""
    out: list = []
    for m in _DMY.finditer(text):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            out.append(date(y, mo, d))
        except ValueError:
            pass
    for m in _YMD.finditer(text):
        y, mo, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
        try:
            out.append(date(y, mo, d))
        except ValueError:
            pass
    return out


@dataclass
class Period:
    column: str
    start: str              # ISO 'YYYY-MM-DD', включительно
    end: str                # ISO 'YYYY-MM-DD', ИСКЛЮЧИТЕЛЬНО
    label: str              # человекочитаемо, для нарратора (совпадает с фильтром)
    kind: str               # 'month' | 'quarter' | 'year' | 'half' | 'range'

    def as_filter(self) -> dict:
        """Удобный вид для где-условий: {col__gte: start, col__lt: end}."""
        return {f"{self.column}__gte": self.start, f"{self.column}__lt": self.end}


def _first_day_next_month(y: int, m: int) -> date:
    return date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)


def _find_months(text: str) -> list:
    """Все месяцы в порядке появления: [(pos, num, name), ...]."""
    found = []
    for pat, num, name in _MONTHS:
        for mt in re.finditer(pat, text):
            found.append((mt.start(), num, name))
    found.sort()
    return found


def _find_quarters(text: str) -> list[int]:
    qs = []
    # Q1 / q2
    for m in re.finditer(r"\bq\s*([1-4])\b", text):
        qs.append(int(m.group(1)))
    # «1 квартал», «1-й квартал», «4 кв.», «1 и 2 квартал»
    for m in re.finditer(r"\b([1-4])\s*[-]?(?:й|го|ый)?\s*кв", text):
        qs.append(int(m.group(1)))
    # римские: «I кв», «IV квартал»
    for m in re.finditer(r"\b(iv|iii|ii|i)\s*кв", text):
        qs.append(_ROMAN[m.group(1)])
    # словом: «первый квартал»
    for stem, q in _QUARTER_WORD.items():
        if re.search(stem + r"\w*\s+квартал", text):
            qs.append(q)
    return sorted(list(set(qs)))


def _find_half(text: str) -> Optional[int]:
    m = re.search(r"(перв|втор)\w*\s+полугод", text)
    if not m:
        return None
    return 1 if m.group(1).startswith("перв") else 2


def parse_period(text: str, column: str = DEFAULT_DATE_COLUMN) -> Optional[Period]:
    """Главная функция разбора периода. None – если периода в тексте нет."""
    if not text:
        return None
    t = text.lower().replace("  ", " ")

    # Обработка периодов типа "первые N дней/суток месяца года"
    _GENITIVE_MONTHS = {
        "январь": "января", "февраль": "февраля", "март": "марта", "апрель": "апреля",
        "май": "мая", "июнь": "июня", "июль": "июля", "август": "августа",
        "сентябрь": "сентября", "октябрь": "октября", "ноябрь": "ноября", "декабрь": "декабря"
    }
    _WORD_NUMBERS = {
        "один": 1, "два": 2, "три": 3, "четыре": 4, "пять": 5,
        "шесть": 6, "семь": 7, "восемь": 8, "девять": 9, "десять": 10
    }

    days_m = re.search(
        r"\b(?:первые\s+(\d+|один|два|три|четыре|пять|шесть|семь|восемь|девять|десять)\s+(?:дней|дня|день|суток|сутки|сут)"
        r"|(\d+|один|два|три|четыре|пять|шесть|семь|восемь|девять|десять)\s+первых\s+(?:дней|дня|день|суток|сутки|сут))\b",
        t
    )
    if days_m:
        raw_val = days_m.group(1) or days_m.group(2)
        days_count = int(raw_val) if raw_val.isdigit() else _WORD_NUMBERS.get(raw_val, 1)
        years = [int(y) for y in re.findall(r"\b(20\d\d)\b", t)]
        months = _find_months(t)
        if years and months:
            year = years[-1]
            m_num = months[0][1]
            m_name = months[0][2]
            m_lbl = _GENITIVE_MONTHS.get(m_name, m_name)
            start = date(year, m_num, 1)
            end = start + timedelta(days=days_count)
            lbl = f"первые {days_count} дня {m_lbl} {year}"
            return Period(column, start.isoformat(), end.isoformat(), lbl, "range")

    # Явные даты DD.MM.YYYY / YYYY-MM-DD
    expl = _find_explicit_dates(t)
    if expl:
        start = min(expl)
        end_incl = max(expl)
        end = end_incl + timedelta(days=1)
        if start == end_incl:
            lbl = start.strftime("%d.%m.%Y")
        else:
            lbl = f"{start.strftime('%d.%m.%Y')}-{end_incl.strftime('%d.%m.%Y')}"
        return Period(column, start.isoformat(), end.isoformat(), lbl, "range")

    years = [int(y) for y in re.findall(r"\b(20\d\d)\b", t)]
    if not years:
        return None
    year = years[-1]

    months = _find_months(t)
    quarters = _find_quarters(t)
    half = _find_half(t)

    # 1) Квартал (или несколько кварталов, например Q1 и Q2)
    if quarters:
        min_q = min(quarters)
        max_q = max(quarters)
        start_m = (min_q - 1) * 3 + 1
        start = date(year, start_m, 1)
        end = _first_day_next_month(year, max_q * 3)
        lbl = f"Q{min_q}-Q{max_q} {year}" if min_q != max_q else f"Q{min_q} {year}"
        return Period(column, start.isoformat(), end.isoformat(), lbl, "quarter")

    # 2) Полугодие
    if half is not None:
        start = date(year, 1 if half == 1 else 7, 1)
        end = date(year, 7, 1) if half == 1 else date(year + 1, 1, 1)
        return Period(column, start.isoformat(), end.isoformat(),
                      f"{'первое' if half == 1 else 'второе'} полугодие {year}", "half")

    # 3) Диапазон месяцев
    if len(months) >= 2 and months[0][1] != months[-1][1]:
        m1, m2 = months[0][1], months[-1][1]
        if m1 > m2:
            m1, m2 = m2, m1
        start = date(year, m1, 1)
        end = _first_day_next_month(year, m2)
        lbl = f"{months[0][2]}-{months[-1][2]} {year}"
        return Period(column, start.isoformat(), end.isoformat(), lbl, "range")

    # 4) Один месяц
    if months:
        m = months[0][1]
        start = date(year, m, 1)
        end = _first_day_next_month(year, m)
        return Period(column, start.isoformat(), end.isoformat(),
                      f"{months[0][2]} {year}", "month")

    # 5) Только год
    return Period(column, date(year, 1, 1).isoformat(), date(year + 1, 1, 1).isoformat(),
                  f"{year} год", "year")
