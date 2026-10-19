"""Origin and responsibility are independent business constraints."""
import json
import logging
import re
from difflib import SequenceMatcher
from pathlib import Path
from .request_outcome import ClarificationRequired

logger=logging.getLogger(__name__)
ORIGIN='org_struct_lvl_3_name'
RESPONSIBILITY='funct_block_lvl_3_name'
ORIGIN_DETAIL='org_struct_lvl_4_name'
PROCESS_DIRECTION='process_lvl_3_name'
PROCESS='process_lvl_4_name'
FIELDS=(ORIGIN,ORIGIN_DETAIL,RESPONSIBILITY,PROCESS_DIRECTION,PROCESS)
CATEGORIES={ORIGIN:'origin',ORIGIN_DETAIL:'origin',RESPONSIBILITY:'responsibility',
            PROCESS_DIRECTION:'responsibility',PROCESS:'responsibility',
            'risk_profile_id':'drp','incdnt_sid':'eve'}
LABELS={ORIGIN:'более крупному организационному подразделению',
        ORIGIN_DETAIL:'конкретному подразделению',RESPONSIBILITY:'функциональному блоку',
        PROCESS_DIRECTION:'направлению деятельности (дивизиону)',PROCESS:'конкретным процессам'}
TB={ 'срб':'Среднерусский банк','мб':'Московский банк','сзб':'Северо-Западный банк',
     'юзб':'Юго-Западный банк','ввб':'Волго-Вятский банк','сиб':'Сибирский банк',
     'урб':'Уральский банк','пвб':'Поволжский банк','двб':'Дальневосточный банк','бб':'Байкальский банк'}

def load_catalog():
    root=Path(__file__).resolve().parents[2]
    path=root/'utils/schema/kb_value_catalog.json'
    try:
        data=json.loads(path.read_text(encoding='utf-8'))
        columns=data['columns']
        if not isinstance(columns,dict):
            raise ValueError('Invalid catalog columns')
        result={field:[] for field in FIELDS}
        for key, entry in columns.items():
            column=key.rsplit('.',1)[-1]
            if column not in result:
                continue
            values=entry['values']
            if not isinstance(values,list) or any(not isinstance(v,str) for v in values):
                raise ValueError('Invalid catalog values')
            result[column].extend(values)
        return {key:list(dict.fromkeys(values)) for key,values in result.items()}
    except (OSError,ValueError,KeyError,TypeError) as exc:
        logger.exception('Cannot read IOR grounding catalog')
        raise ClarificationRequired('Не удалось безопасно применить фильтр: справочник значений недоступен. Повторите запрос после восстановления справочника.') from exc

def _choice(text,value,names=()):
    # The repeated tool prompt carries the original request plus the answer.
    tail=text.rsplit('уточнение:',1)[-1] if 'уточнение:' in text else text
    found=sorted((m.start(),m.end(),name) for name in names for m in re.finditer(re.escape(name.lower().replace('ё','е')),tail))
    current=[(start,end) for start,end,name in found if name==value]
    if found and not current:
        return None
    if current:
        start,end=current[-1]
        stop=min((pos for pos,_,name in found if pos>end),default=len(tail))
        previous=max((finish for pos,finish,name in found if finish<start),default=0)
        after=tail[end:stop]
        before=tail[previous:start].rsplit(';',1)[-1]
        if re.match(r'\s*[-–—:]',after):
            tail=after
        elif re.search(r'ответствен|происхожд|возникл|отвечает|владел|произошл',before):
            tail=before
        elif len({name for _,_,name in found})>1:
            return None
    if re.search(r'зон\w*\s+ответствен|за\s+которые|отвечает|ответственност|владел\w*\s+процесс',tail):
        return 'responsibility'
    if re.search(r'мест\w*\s+(?:происхожд|где)|возникл|возникшие|произошл|источник.*подраздел',tail):
        return 'origin'
    return None

def _stem(word):
    # Conservative inflection/derivation rules, independent of business names.
    word=re.sub(r'(?:иями|ями|ами|ого|ему|ому|ыми|ими|иях|ах|ях|ия|ие|ий|ый|ой|ая|яя|ое|ее|ов|ев|ам|ям|ом|ем|а|я|ы|и|у|ю|е|ь|й)$','',word)
    reduced=re.sub(r'(?:ирован|ован|овк|евк|ени|ани|аци|ств)$','',word)
    return reduced if len(reduced)>=4 else word


_NOISE=('и или по в на за с из к о об от до для где они их этих нужно нужны получить '
        'выведи выгрузи найди покажи сделай сформируй проанализируй анализ отчет иор иоры '
        'инцидент инциденты операционный финансовый нефинансовый последствия возмещения '
        'блок подразделение дивизион управление дирекция процесс направление деятельность владелец '
        'функциональный структура клиентский сервис '
        'место происхождение ответственность конкретный крупный организационный '
        'владельца произошли возникли которые которым '
        'год квартал месяц первый второй третий четвертый текущий прошлый предыдущий '
        'рубль рублей сумма свыше более меньше утвержден черновик закрыт исследование '
        'банк клиент клиенты работа операция услуга обслуживание это').split()
_STOP={_stem(word) for word in _NOISE}


def _tokens(text):
    return {_stem(word) for word in re.findall(r'[а-яa-z]+',text.lower().replace('ё','е'))
            if len(word)>=2 and _stem(word) not in _STOP}


def _code_values(catalog, digits):
    pattern=re.compile(r'(?<!\w)[пp]-?'+re.escape(digits)+r'(?!\w)',re.I)
    return [str(value).strip() for value in catalog.get(PROCESS,[]) if pattern.search(str(value))]


def _field_choice(parts, category):
    patterns={PROCESS:r'конкретн\w*\s+процесс|по\s+процесс',
              PROCESS_DIRECTION:r'направлен\w*\s+деятельност|по\s+дивизион',
              RESPONSIBILITY:r'функциональн\w*\s+блок',
              ORIGIN:r'(?:крупн\w*|верхн\w*)\s+организационн|крупн\w*\s+подраздел',
              ORIGIN_DETAIL:r'конкретн\w*\s+подраздел'}
    for answer in reversed(parts):
        choices=[col for col,pattern in patterns.items()
                 if CATEGORIES[col]==category and re.search(pattern,answer)]
        if len(choices)==1:
            return choices[0]
    return None


def ground_query(user_query,max_hits=100, *, pao_scope=False):
    """Resolve catalog values without a candidate cap; clarify before emitting SQL."""
    text=(user_query or '').lower().replace('ё','е')
    if not text.strip():
        return []
    if re.search(r'\bsbr\b|\bsbr[-_]\w+',text,re.I):
        raise ClarificationRequired('Фильтрация по SBR-кодам не поддерживается. Укажите название места происхождения или зоны ответственности за ИОР.')
    parts=re.split(r'уточнение:',text)
    original=parts[0]
    catalog=load_catalog()
    hits=[]
    def add(col,value,op='eq',match='exact'):
        hit={'column':col,'value':value,'category':CATEGORIES[col],'phrase':value,
             'score':{'exact':1.,'normalized':.95,'thematic':.85}[match],
             'match':match,'count':1,'op':op}
        if not any(h['column']==col and h['value']==value for h in hits):
            hits.append(hit)
    for kind,col in [('DRP','risk_profile_id'),('EVE','incdnt_sid')]:
        for digits in re.findall(r'\b'+kind+r'[-_\s]?(\d+)\b',text,re.I):
            add(col,kind+'-'+digits)
    codes=list(dict.fromkeys(re.findall(r'(?<!\w)[пp]-?(\d{4,})(?!\w)',original,re.I)))
    if codes and any(not _code_values(catalog,code) for code in codes):
        for answer in reversed(parts[1:]):
            if re.fullmatch(r'\s*[пp]-?\d{4,}(?:\s*(?:,|и)\s*[пp]-?\d{4,})*\s*[.!]?\s*',answer):
                codes=list(dict.fromkeys(re.findall(r'[пp]-?(\d{4,})',answer)))
                break
    unknown=[]
    for code in codes:
        values=_code_values(catalog,code)
        if not values:
            unknown.append('П'+code)
        for value in values:
            add(PROCESS,value)
    if unknown:
        raise ClarificationRequired('В справочнике не найдены процессы: '+', '.join(unknown)+'. Укажите полный исправленный список номеров процессов.',stage='process_codes')
    fixed=[]
    for short,value in TB.items():
        adjective=value.lower().rsplit(' ',1)[0]
        stem=re.sub(r'(?:ский|ный|ый|ий)$','',adjective)
        if re.search(r'\b'+short+r'\b',original) or re.search(r'(?<!\w)'+re.escape(stem)+r'\w*\s+банк\w*\b',original):
            actual=[v for v in catalog.get(ORIGIN,[]) if str(v).strip().casefold()==value.casefold()]
            if not actual:
                raise ClarificationRequired('Не найдено название территориального банка в справочнике. Уточните название.',stage='object')
            for item in actual:
                add(ORIGIN,item)
                fixed.append(item)
    query=re.sub(r'\b(?:drp|eve)[-_\s]?\d+|(?<!\w)[пp]-?\d+(?!\w)',' ',original,flags=re.I)
    if pao_scope:
        # The preset already resolved this legacy business flag; it is not a
        # catalog topic and must not turn into a second organization selector.
        query=re.sub(r'\bпао\s+\S+',' ',query)
    query=re.sub(r'операционн\w*\s+риск\w*|цифров\w*\s+профил\w*(?:\s+риск\w*)?',' ',query)
    query=re.sub(r'\d+(?:[.,]\d+)?\s*(?:руб\w*|млн\w*|тыс\w*|млрд\w*)',' ',query)
    for value in fixed:
        query=query.replace(str(value).casefold(),' ')
    for short in TB:
        if any(str(value).casefold()==TB[short].casefold() for value in fixed):
            query=re.sub(r'\b'+short+r'\b',' ',query)
    roots=_tokens(query)
    matched=[]
    for col in FIELDS:
        if codes and col==PROCESS:
            continue
        for raw in catalog.get(col,[]):
            value=str(raw).strip()
            normalized=value.lower().replace('ё','е')
            if not normalized or value in fixed:
                continue
            value_roots=_tokens(re.sub(r'(?<!\w)[пp]-?\d+(?!\w)',' ',normalized))
            anchors=roots & value_roots
            exact=bool(re.search(r'(?<!\w)'+re.escape(normalized)+r'(?!\w)',query))
            if not anchors and not exact:
                # One small spelling variation is allowed only for long words.
                anchors={q for q in roots for v in value_roots if min(len(q),len(v))>=6
                         and abs(len(q)-len(v))<=1 and SequenceMatcher(None,q,v).ratio()>=.9}
            if not anchors and not (exact and value_roots):
                continue
            matched.append({'column':col,'value':value,'anchors':anchors or value_roots,
                            'match':'exact' if exact else 'normalized' if value_roots<=roots else 'thematic'})
    # Connected topic groups keep independent organizational conditions separate.
    groups=[]
    for item in matched:
        overlaps=[g for g in groups if any(h['anchors'] & item['anchors'] for h in g)]
        if not overlaps:
            groups.append([item])
        else:
            group=overlaps[0]
            group.append(item)
            for old in overlaps[1:]:
                group.extend(old); groups.remove(old)
    names=list(dict.fromkeys(h['value'] for h in matched))
    # Each explicit topic clause must be accounted for. A recognized bank or
    # process code must not hide a second, unknown organizational condition.
    represented=set().union(*(h['anchors'] for h in matched),*(_tokens(value) for value in fixed))
    for clause in re.split(r'\b(?:по|про)\s+',query)[1:]:
        topic=re.split(r'\b(?:за|свыше|более|меньше|сумм\w*|утвержд\w*|черновик\w*|закрыт\w*|исследован\w*)\b',clause)[0]
        unknown=_tokens(topic)-represented
        # General report subjects are interpreted by the preset, not the catalog.
        if unknown and not re.match(r'\s*(?:иор|инцидент|событ|(?:не)?финансов|возмещ|удален|период|цифров|профил)',topic):
            raise ClarificationRequired('Не удалось найти надёжные совпадения для всех условий. Уточните название подразделения или процесса.',stage='object')
        if re.search(r'работ\w*|клиентск\w*|обслуживан\w*',topic) and not _tokens(topic):
            raise ClarificationRequired('Уточните название подразделения или процесса: общих слов недостаточно для надёжного фильтра.',stage='object')
    for group in groups:
        required=set().union(*(h['anchors'] for h in group))
        group=[h for h in group if required<=h['anchors'] or h['match']=='exact']
        if not group:
            raise ClarificationRequired('Уточните название бизнес-объекта: надёжных совпадений в справочнике нет.',stage='object')
        value=next((h['value'] for h in group if h['match']=='exact'),group[0]['value'])
        choice=None
        for part in reversed(parts):
            choice=_choice(part,value,names)
            if choice:
                break
        categories={CATEGORIES[h['column']] for h in group}
        if len(categories)>1 and not choice:
            raise ClarificationRequired('Вы хотите получить ИОРы по месту, где они произошли, или по подразделению владельца процесса?',stage='category')
        category=choice or next(iter(categories))
        chosen=[h for h in group if CATEGORIES[h['column']]==category]
        if not chosen:
            raise ClarificationRequired('Для выбранной категории совпадений нет. Уточните название бизнес-объекта.',stage='object',category=category)
        columns={h['column'] for h in chosen}
        column=_field_choice(parts[1:] or ([original] if choice else []),category)
        if column and column not in columns:
            raise ClarificationRequired('В выбранном варианте совпадений нет. Уточните бизнес-объект.',stage='field',category=category)
        if len(columns)>1 and not column:
            options=' или по '.join(LABELS[col] for col in FIELDS if col in columns)
            raise ClarificationRequired('Уточните, пожалуйста, искать по '+options+'?',stage='field',category=category)
        column=column or next(iter(columns))
        selected=[h for h in chosen if h['column']==column]
        if column!=PROCESS:
            for answer in reversed(parts[1:]):
                explicit=[h for h in selected if re.search(r'(?<!\w)'+re.escape(h['value'].lower().replace('ё','е'))+r'(?!\w)',answer)]
                if explicit:
                    selected=[{**h,'match':'exact'} for h in explicit]
                    break
            exact=[h for h in selected if h['match']=='exact']
            selected=exact or selected
            if len(selected)>1 and not exact:
                labels='; '.join('«'+h['value']+'»' for h in selected[:10])
                raise ClarificationRequired('Уточните конкретное подразделение: '+labels+'. Укажите полное название.',stage='object',category=category,column=column)
        for hit in selected:
            add(column,hit['value'],match=hit['match'])
    # An explicit named organizational filter must not silently disappear.
    named=re.search(r'по\s+(?:блоку\s+|подразделению\s+|тб\s+)?[«"\']?([а-я][а-я\s-]+)',original)
    if named and not pao_scope and not any(h['column'] in FIELDS for h in hits):
        if not re.match(r'(?:иор|инцидент|событ|финансов|нефинансов|возмещ|удален|период|цифров|профил)',named[1]):
            raise ClarificationRequired('Не удалось найти надёжные совпадения в справочнике. Уточните название подразделения или процесса.',stage='object')
    return hits
