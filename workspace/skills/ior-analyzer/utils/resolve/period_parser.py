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
from dataclasses import dataclass, field
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
_CARDINALS = {'один': 1, 'одна': 1, 'одно': 1, 'два': 2, 'две': 2,
              'три': 3, 'четыре': 4, 'пять': 5, 'шесть': 6, 'семь': 7,
              'восемь': 8, 'девять': 9, 'десять': 10, 'двенадцать': 12}
_QUARTER_ORDINAL = r'(?:перв\w*|втор\w*|трет\w*|четв[её]рт\w*)'

# Явные даты: DD.MM.YYYY (рус. формат, день первый) и ISO YYYY-MM-DD.
_DMY = re.compile(r"\b(\d{1,2})[\.\-/](\d{1,2})[\.\-/](20\d\d|\d\d)\b")
_YMD = re.compile(r"\b(20\d\d)-(\d{1,2})-(\d{1,2})\b")


def _find_explicit_dates(text: str) -> list:
    """Все явные даты в тексте (DD.MM.YYYY и YYYY-MM-DD), валидные, без сортировки."""
    out: list = []
    for m in _DMY.finditer(_YMD.sub("", text)):
        d, mo, y = int(m.group(1)), int(m.group(2)), int(m.group(3))
        y = y + 2000 if y < 100 else y
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
    intervals: list[tuple[str, str]] = field(default_factory=list)
    slice_labels: list[str] = field(default_factory=list)

    def __post_init__(self):
        if not self.intervals:
            self.intervals = [(self.start, self.end)]
        if not self.slice_labels:
            self.slice_labels = [self.label]

    def as_filter(self) -> dict:
        """Удобный вид для где-условий: {col__gte: start, col__lt: end}."""
        items = [{f"{self.column}__gte": a, f"{self.column}__lt": b} for a, b in self.intervals]
        return items[0] if len(items) == 1 else {"_or": items}


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
    # A shared noun applies to every ordinal in a list: первый и третий кварталы.
    for group in re.finditer(rf'\b{_QUARTER_ORDINAL}(?:\s*(?:,|и|[-–—]|по|до)\s*{_QUARTER_ORDINAL})*\s+квартал\w*', text):
        for stem, q in _QUARTER_WORD.items():
            if re.search(r'\b' + stem + r'\w*', group.group()):
                qs.append(q)
    for match in re.finditer(r'\b([1-4])\s*(?:[-–—]|и)\s*([1-4])\s*кв',text):
        qs.extend((int(match[1]),int(match[2])))
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


def _parse_single_period(text: str, column: str = DEFAULT_DATE_COLUMN) -> Optional[Period]:
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


def parse_period(text: str, column: str = DEFAULT_DATE_COLUMN, *, today=None) -> Optional[Period]:
    """Preserve disjoint slices; reject an explicit but unparseable period."""
    from utils.resolve.request_outcome import ClarificationRequired
    import calendar
    from datetime import datetime
    from zoneinfo import ZoneInfo
    t=(text or '').lower().replace('ё','е')
    # Convert only explicit day + named month + year; month-only ranges stay intact.
    for month_pattern, month, _ in _MONTHS:
        def named_date(match):
            return f'{int(match[1]):02d}.{month:02d}.{match[2]}'
        t = re.sub(rf'\b(\d{{1,2}})(?:-?(?:го|е|ое))?\s+{month_pattern}\s+(20\d\d)\b', named_date, t)
    if today is None:
        today=datetime.now(ZoneInfo('Europe/Moscow')).date()

    def make(intervals,labels,kind):
        pairs=sorted(set(intervals))
        # Deduplicate without merging independent user-selected slices.
        label_map=dict(zip(intervals,labels))
        return Period(column,min(a for a,b in pairs).isoformat(),max(b for a,b in pairs).isoformat(),
                      '; '.join(label_map[p] for p in pairs),kind,
                      [(a.isoformat(),b.isoformat()) for a,b in pairs], [label_map[p] for p in pairs])

    def shift_month(d,n):
        year,month=divmod(d.year*12+d.month-1+n,12)
        return date(year,month+1,min(d.day,calendar.monthrange(year,month+1)[1]))

    if re.search(r'(?:прошл|предыдущ)\w*\s+год',t):
        return make([(date(today.year-1,1,1),date(today.year,1,1))],['прошлый год'],'year')
    if re.search(r'(?:текущ\w*|этот)\s+год',t):
        return make([(date(today.year,1,1),date(today.year+1,1,1))],['текущий год'],'year')
    if re.search(r'(?:прошл|предыдущ)\w*\s+месяц',t):
        end=today.replace(day=1)
        return make([(shift_month(end,-1),end)],['прошлый месяц'],'month')
    if re.search(r'(?:текущ\w*|этот)\s+месяц',t):
        start=today.replace(day=1)
        return make([(start,shift_month(start,1))],['текущий месяц'],'month')
    relative=re.search(r'последн\w*\s+(\d+|'+'|'.join(sorted(_CARDINALS,key=len,reverse=True))+r')\s+(месяц\w*|дн\w*|день)',t)
    if relative:
        n=int(relative[1]) if relative[1].isdigit() else _CARDINALS[relative[1]]
        if n<=0 or n>1200:
            raise ClarificationRequired('Укажите положительное разумное количество дней или месяцев.')
        end=today+timedelta(days=1)
        start=shift_month(today,-n) if relative[2].startswith('месяц') else end-timedelta(days=n)
        return make([(start,end)],[relative.group()],'range')
    # Validate every explicit date; a malformed endpoint must not widen a query.
    for match in _YMD.finditer(t):
        try:
            date(int(match[1]), int(match[2]), int(match[3]))
        except ValueError as exc:
            raise ClarificationRequired("Уточните даты периода: одна из дат некорректна.") from exc
    without_iso = _YMD.sub("", t)
    for match in _DMY.finditer(without_iso):
        try:
            year=int(match[3]); year=year+2000 if year<100 else year
            date(year, int(match[2]), int(match[1]))
        except ValueError as exc:
            raise ClarificationRequired("Уточните даты периода: одна из дат некорректна.") from exc
    try:
        explicit=_find_explicit_dates(t)
    except ValueError as exc:
        raise ClarificationRequired('Уточните даты периода: одна из дат некорректна.') from exc
    if explicit:
        tokens = re.findall(r'\b20\d\d-\d{1,2}-\d{1,2}\b|\b\d{1,2}[./]\d{1,2}[./](?:20\d\d|\d\d)\b',t)
        if len(tokens)==2:
            first=_find_explicit_dates(tokens[0])[0]
            last=_find_explicit_dates(tokens[1])[0]
            if first>last:
                raise ClarificationRequired('Дата начала периода позже окончания. Уточните даты.')
        return _parse_single_period(t,column)
    years=[int(y) for y in re.findall(r'\b(20\d\d)\b',t)]
    months=_find_months(t)
    quarters=_find_quarters(t)
    if any(int(q)>4 or int(q)<1 for q in re.findall(r'\bq\s*(\d+)\b',t)):
        raise ClarificationRequired('Укажите квартал от Q1 до Q4 и год.')
    if re.search(r'квартал|\b\d+\s*кв\b',t) and not quarters:
        raise ClarificationRequired('Уточните номер квартала (1–4) и год.')
    if years and quarters:
        if len(set(years)) > 1:
            raise ClarificationRequired("Для кварталов разных лет укажите отдельные даты начала и окончания периода.")
        y=years[-1]
        continuous=bool(re.search(r'(?:q\s*[1-4]|[1-4]\s*кв\w*)\s*[-–—]\s*(?:q\s*[1-4]|[1-4]\s*кв)|\b[1-4]\s*[-–—]\s*[1-4]\s*кв',t))
        continuous = continuous or bool(re.search(rf'{_QUARTER_ORDINAL}(?:\s+квартал\w*)?\s*(?:[-–—]|по|до)\s*{_QUARTER_ORDINAL}\s+квартал', t))
        if continuous:
            return make([(date(y,(min(quarters)-1)*3+1,1),_first_day_next_month(y,max(quarters)*3))],
                        [f'Q{min(quarters)}–Q{max(quarters)} {y}'],'quarter')
        return make([(date(y,(q-1)*3+1,1),_first_day_next_month(y,q*3)) for q in quarters],
                    [f'Q{q} {y}' for q in quarters],'quarter')
    if years and months:
        dated=[]
        for i,(pos,m,name) in enumerate(months):
            stop=months[i+1][0] if i+1<len(months) else len(t)
            local=re.search(r'\b20\d\d\b',t[pos:stop])
            y=int(local.group()) if local else years[-1]
            dated.append((date(y,m,1),_first_day_next_month(y,m),f'{name} {y}'))
        if len(dated)>1 and re.search(r'[-–—]|\b(?:с|по|до)\b',t[months[0][0]:months[-1][0]]):
            start,end=dated[0][0],dated[-1][1]
            if start>=end:
                raise ClarificationRequired('Уточните годы начала и окончания периода.')
            return make([(start,end)],[dated[0][2]+' — '+dated[-1][2]],'range')
        # Keep the established first-N-days and half-year contracts.
        if len(dated)==1:
            return _parse_single_period(t,column)
        return make([(a,b) for a,b,label in dated],[label for a,b,label in dated],'month')
    if len(years)>1:
        continuous=re.search(r'(?:с\s+)?20\d\d\s*(?:[-–—]|по|до)\s*20\d\d',t)
        if continuous:
            return make([(date(min(years),1,1),date(max(years)+1,1,1))],[f'{min(years)}–{max(years)}'],'year')
        return make([(date(y,1,1),date(y+1,1,1)) for y in years],[f'{y} год' for y in years],'year')
    if years:
        return _parse_single_period(t,column)
    if months or quarters or re.search(r'\b(?:год\w*|месяц\w*|квартал\w*|полугод\w*|период\w*|последн\w*\s+\S+\s+(?:дн|месяц))\b',t):
        raise ClarificationRequired('Уточните период: укажите год и месяц/квартал либо даты начала и окончания.')
    return None
