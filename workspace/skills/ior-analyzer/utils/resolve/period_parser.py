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
    (r"январ(?:ь|я|е|ю|ем)\b", 1, "январь"),
    (r"феврал(?:ь|я|е|ю|ем)\b", 2, "февраль"),
    (r"март(?:а|е|у|ом)?\b", 3, "март"),
    (r"апрел(?:ь|я|е|ю|ем)\b", 4, "апрель"),
    (r"ма[йяе]\b", 5, "май"),
    (r"июн(?:ь|я|е|ю|ем)\b", 6, "июнь"),
    (r"июл(?:ь|я|е|ю|ем)\b", 7, "июль"),
    (r"август(?:а|е|у|ом)?\b", 8, "август"),
    (r"сентябр(?:ь|я|е|ю|ем)\b", 9, "сентябрь"),
    (r"октябр(?:ь|я|е|ю|ем)\b", 10, "октябрь"),
    (r"ноябр(?:ь|я|е|ю|ем)\b", 11, "ноябрь"),
    (r"декабр(?:ь|я|е|ю|ем)\b", 12, "декабрь"),
]

_QUARTER_WORD = {"перв": 1, "втор": 2, "трет": 3, "четверт": 4, "четвёрт": 4}
_ROMAN = {"i": 1, "ii": 2, "iii": 3, "iv": 4}
_CARDINALS = {'один': 1, 'одна': 1, 'одно': 1, 'два': 2, 'две': 2,
              'три': 3, 'четыре': 4, 'пять': 5, 'шесть': 6, 'семь': 7,
              'восемь': 8, 'девять': 9, 'десять': 10, 'двенадцать': 12}
_QUARTER_ORDINAL = r'(?:перв\w*|втор\w*|трет\w*|четв[её]рт\w*)'

# Явные даты: DD.MM.YYYY (рус. формат, день первый) и ISO YYYY-MM-DD.
_DMY = re.compile(r"\b(\d{1,2})[\.\-/](\d{1,2})[\.\-/](\d{4}|\d\d)\b")
_YMD = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")


def _temporal_text(text):
    """Mask IDs and amounts before every date branch, preserving positions."""
    t=(text or '').lower().replace('ё','е')
    patterns=(r'\b(?:от\s+)?\d+(?:[.,]\d+)?\s*(?:до|[-–—])\s*\d+(?:[.,]\d+)?\s*(?:руб\w*|тыс\w*|млн\w*|млрд\w*|доллар\w*|евро)',
              r'\b(?:сумм\w*|потер\w*|последств\w*)\s+(?:от|до|не\s+менее|не\s+более)\s+\d+(?:[.,]\d+)?(?:\s*до\s*\d+(?:[.,]\d+)?)?(?!\d|\s*г(?:од|\.))',
              r'(?<!\w)(?:drp|eve)[-_\s]?\d+(?:[-_]\d+)*\b',
              r'(?<!\w)[пp]-?\d+\b',
              r'(?<!\w)\d[\d\s.,]*\s*(?:руб\w*|тыс\w*|млн\w*|млрд\w*|доллар\w*|евро|%)',
              r'\b(?:свыше|более|больше|менее|меньше)\s+\d+(?:[.,]\d+)?(?!\d|\s*г(?:од|\.))')
    for pattern in patterns:
        t=re.sub(pattern,lambda match:' '*len(match[0]),t)
    return t


def _year_matches(text):
    """Four digits are a year only inside a temporal expression."""
    candidates=list(re.finditer(r'(?<!\w)\d{4}(?!\d)',text))
    dates=list(_DMY.finditer(text))+list(_YMD.finditer(text))
    months='|'.join(r'\b'+pattern for pattern,_,_ in _MONTHS)
    confirmed=[]
    pure=bool(re.fullmatch(r'\s*\d{4}(?:\s*(?:,|и|[-–—]|по|до)\s*\d{4})*\s*[.!]?\s*',text))
    for match in candidates:
        before,after=text[:match.start()],text[match.end():]
        context=(pure or any(d.start()<=match.start()<d.end() for d in dates)
                 or re.search(r'\b(?:за|с|от|до|по)\s*$',before)
                 or re.match(r'\s*г(?:од\w*\b|\.|\b)',after)
                 or re.search(r'(?:квартал\w*|\bq\s*[1-4]|полугод\w*|'+months+r')\s*$',before)
                 or re.match(r'\s+(?:г\.?\s*)?(?:квартал|q\s*[1-4]|полугод)',after))
        if context:
            confirmed.append(match)
    # A shared temporal noun/year applies to the complete list or year range.
    changed=True
    while changed:
        changed=False
        for match in candidates:
            if match in confirmed:
                continue
            for other in confirmed:
                a,b=sorted((match,other),key=lambda value:value.start())
                if re.fullmatch(r'\s*(?:г(?:од\w*)?\.?)?\s*(?:,|и|[-–—]|по|до)\s*',text[a.end():b.start()]):
                    confirmed.append(match); changed=True; break
    return sorted(confirmed,key=lambda match:match.start())


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
        for mt in re.finditer(r'\b'+pat, text):
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
    t = _temporal_text(text)

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
        years = [int(match[0]) for match in _year_matches(t)]
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

    years = [int(match[0]) for match in _year_matches(t)]
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
    t=_temporal_text(text)
    # Keep the request verbatim outside this parser. Only a confirmed answer
    # may replace a previously supplied year in the effective period text.
    parts = re.split(r'уточнение:', t)
    t = parts[0]
    for answer in parts[1:]:
        year_answer = re.fullmatch(r'\s*(?:год\s*[:—-]?\s*)?(\d{4})(?:\s*(?:год\w*|г\.?))?[.!\s]*', answer)
        if year_answer:
            temporal_years=_year_matches(t)
            years = {match[0] for match in temporal_years}
            if len(years) == 1:
                for match in reversed(temporal_years):
                    t=t[:match.start()]+year_answer[1]+t[match.end():]
            elif not years:
                t += ' ' + year_answer[1]+' год'
            else:
                raise ClarificationRequired('Уточните полный период: даты начала и окончания либо квартал и год.')
        elif re.search(r'\b20\d{2}\b', answer) and re.search(r'квартал|\bq\s*[1-4]|месяц|\d[./-]\d|' + '|'.join(pattern for pattern, _, _ in _MONTHS), answer):
            t = answer
    # Do not silently ignore a mistyped year while parsing a valid quarter/month.
    for match in _year_matches(t):
        if not 2000 <= int(match[0]) <= 2099:
            raise ClarificationRequired(f'Уточните год периода: указан {match[0]}. Укажите год от 2000 до 2099.')
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

    # Validate endpoints before relative periods can return early.
    for match in _YMD.finditer(t):
        try:
            date(int(match[1]),int(match[2]),int(match[3]))
        except ValueError as exc:
            raise ClarificationRequired('Уточните даты периода: одна из дат некорректна.') from exc
    for match in _DMY.finditer(_YMD.sub('',t)):
        try:
            year=int(match[3]); year=year+2000 if year<100 else year
            date(year,int(match[2]),int(match[1]))
        except ValueError as exc:
            raise ClarificationRequired('Уточните даты периода: одна из дат некорректна.') from exc
    if re.search(r'(?:прошл|предыдущ|текущ|этот|последн)\w*\s+(?:\S+\s+)?(?:год|месяц|дн|день)',t) and (
        _year_matches(t) or _find_explicit_dates(t) or re.search(r'\bq\s*\d|квартал',t)
    ):
        raise ClarificationRequired('Уточните единый период: относительный период или явные даты и год.')

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
    years=[int(match[0]) for match in _year_matches(t)]
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
            local=_year_matches(t[pos:stop])
            y=int(local[-1][0]) if local else years[-1]
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
            if years[0]>years[-1]:
                raise ClarificationRequired('Дата начала периода позже окончания. Уточните годы.')
            return make([(date(min(years),1,1),date(max(years)+1,1,1))],[f'{min(years)}–{max(years)}'],'year')
        return make([(date(y,1,1),date(y+1,1,1)) for y in years],[f'{y} год' for y in years],'year')
    if years:
        return _parse_single_period(t,column)
    if months or quarters or re.search(r'\b(?:год\w*|месяц\w*|квартал\w*|полугод\w*|период\w*|последн\w*\s+\S+\s+(?:дн|месяц))\b',t):
        raise ClarificationRequired('Уточните период: укажите год и месяц/квартал либо даты начала и окончания.')
    return None
