"""Decimal amounts and comparisons, independent of a particular data source."""
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re

from .request_outcome import ClarificationRequired

UNIT = r'(?:миллиард\w*|млрд|миллион\w*|милион\w*|млн|тысяч\w*|тыс\.?)'
WORDS = {
    **dict.fromkeys(('полтора', 'полторы', 'полутора'), '1.5'),
    **dict.fromkeys(('один', 'одна', 'одно', 'одного', 'одной'), '1'),
    **dict.fromkeys(('два', 'две', 'двух'), '2'),
    **dict.fromkeys(('три', 'трех', 'трёх'), '3'),
    **dict.fromkeys(('четыре', 'четырех', 'четырёх'), '4'),
    **{word: str(n) for n, forms in enumerate((('пять', 'пяти'), ('шесть', 'шести'),
       ('семь', 'семи'), ('восемь', 'восьми'), ('девять', 'девяти'), ('десять', 'десяти')), 5)
       for word in forms},
}
NUMBER = r'(?:\d+(?:[\s\u00a0]\d{3})*(?:[.,]\d+)?|' + '|'.join(sorted(WORDS, key=len, reverse=True)) + r')(?!\w)'
AMOUNT = rf'(?:{NUMBER}\s*(?:{UNIT})?|{UNIT})(?:\s*(?:руб\w*|₽))?'
COMPARE = r'не\s+менее|не\s+более|больше|более|свыше|менее|меньше|от|до|>=|<=|>|<'

@dataclass(frozen=True)
class MoneyCondition:
    op: str
    value: Decimal

    def sql(self, expression):
        if self.op not in {'>','>=','<','<='}:
            raise ValueError('Invalid monetary comparison')
        return f'{expression} {self.op} {self.value:f}'

def parse_amount(text, inherited_unit=''):
    t = text.lower().strip()
    unit = re.search(UNIT,t)
    unit_text = unit.group() if unit else inherited_unit
    number = re.search(NUMBER,t)
    raw = number.group() if number else '1'
    raw = WORDS.get(raw,re.sub(r'\s+','',raw).replace(',','.'))
    try:
        value = Decimal(raw)
    except InvalidOperation as exc:
        raise ClarificationRequired('Укажите, пожалуйста, сумму числом и единицу измерения.') from exc
    factor = Decimal(1)
    if unit_text.startswith(('миллиард','млрд')):
        factor = Decimal(1_000_000_000)
    elif unit_text.startswith(('миллион','милион','млн')):
        factor = Decimal(1_000_000)
    elif unit_text.startswith(('тысяч','тыс')):
        factor = Decimal(1000)
    return value*factor

def parse_money(text):
    t = text.lower()
    # A year range with an explicit year suffix is never a monetary range,
    # including the abbreviated form where NUMBER cannot consume "2025г.".
    t=re.sub(r'\b(?:от|с)\s+\d{4}\s+(?:до|по)\s+\d{4}\s*г(?:од\w*|\.)',
             lambda match:' '*len(match[0]),t)
    conditions=[]
    occupied=[]
    pattern=rf'\bот\s+({AMOUNT})\s+до\s+({AMOUNT})|({NUMBER})\s*[-–—]\s*({NUMBER})\s*({UNIT})'
    for m in re.finditer(pattern,t):
        if m.group(1) and re.fullmatch(r'20\d\d',m.group(1).strip()) and re.fullmatch(r'20\d\d',m.group(2).strip()) and re.match(r'\s*(?:год\w*|г\b)',t[m.end():]):
            occupied.append(m.span())
            continue
        if m.group(1):
            unit=re.search(UNIT,m.group(2))
            lo=parse_amount(m.group(1),unit.group() if unit else '')
            hi=parse_amount(m.group(2))
        else:
            lo=parse_amount(m.group(3),m.group(5))
            hi=parse_amount(m.group(4),m.group(5))
        if lo>hi:
            raise ClarificationRequired('Нижняя граница суммы больше верхней. Уточните диапазон.')
        conditions.extend([MoneyCondition('>=',lo),MoneyCondition('<=',hi)])
        occupied.append(m.span())
    ops={'не менее':'>=','не более':'<=','больше':'>','более':'>','свыше':'>','менее':'<','меньше':'<','от':'>=','до':'<='}
    for m in re.finditer(rf'(?<!\w)({COMPARE})\s*(?:чем\s+)?(?:на\s+)?({AMOUNT})',t):
        if any(a<=m.start()<b for a,b in occupied):
            continue
        # Date ranges are not monetary comparisons.
        if m.group(1) in {'от','до'} and re.fullmatch(r'\d{4}',m.group(2).strip()) and re.match(r'\s*(?:год\w*|г\b)',t[m.end():]):
            occupied.append(m.span())
            continue
        if m.group(1) in {'от','до'} and any(d.start()<=m.start(2)<d.end() for d in re.finditer(r'\b\d{1,2}[./]\d{1,2}[./]\d{4}\b|\b\d{4}-\d{1,2}-\d{1,2}\b',t)):
            occupied.append(m.span())
            continue
        if m.end()<len(t) and re.match(r'[\w.,]',t[m.end()]):
            raise ClarificationRequired('Уточните числовую сумму: число или единица измерения записаны некорректно.')
        operator=' '.join(m.group(1).split())
        conditions.append(MoneyCondition(ops.get(operator,operator),parse_amount(m.group(2))))
        occupied.append(m.span())
    monetary = bool(re.search(UNIT+r'|руб\w*|₽|сумм',t))
    if monetary:
        for m in re.finditer(r'(?<!\w)(?:'+COMPARE+r')(?!\w)',t):
            if not any(a<=m.start()<b for a,b in occupied):
                raise ClarificationRequired('Не удалось надёжно разобрать условие суммы. Укажите сравнение и сумму, например «более 1,5 млн рублей».')
    return conditions
