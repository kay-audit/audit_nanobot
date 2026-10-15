"""Origin and responsibility are independent business constraints."""
import json
import logging
import re
from pathlib import Path
from .request_outcome import ClarificationRequired

logger=logging.getLogger(__name__)
ORIGIN='org_struct_lvl_3_name'
RESPONSIBILITY='funct_block_lvl_3_name'
CATEGORIES={ORIGIN:'origin',RESPONSIBILITY:'responsibility', 'risk_profile_id':'drp',
            'incdnt_sid':'eve','process_lvl_4_name':'process'}
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
        return {key.rsplit('.',1)[-1]:value['values'] for key,value in columns.items()}
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
    if len({name for _,_,name in found})>1 and current:
        start,end=current[-1]
        stop=min((pos for pos,_,name in found if pos>end),default=len(tail))
        local=tail[end:stop]
        if re.search(r'ответствен|происхожд|возникл|отвечает',local):
            tail=local
        elif not re.search(r'ответствен|происхожд|возникл|отвечает',tail[:found[0][0]]):
            return None
    if re.search(r'зон\w*\s+ответствен|за\s+которые|отвечает|ответственност',tail):
        return 'responsibility'
    if re.search(r'мест\w*\s+происхожд|возникл|возникшие|источник.*подраздел',tail):
        return 'origin'
    return None

def ground_query(user_query,max_hits=100):
    text=(user_query or '').lower().replace('ё','е')
    if not text.strip():
        return []
    if re.search(r'\bsbr\b|\bsbr[-_]\w+',text,re.I):
        raise ClarificationRequired('Фильтрация по SBR-кодам не поддерживается. Укажите название места происхождения или зоны ответственности за ИОР.')
    hits=[]
    def add(col,value,op='eq'):
        hit={'column':col,'value':value,'category':CATEGORIES[col],'phrase':value,
             'score':1.0,'count':1,'op':op}
        if not any(h['column']==col and h['value']==value for h in hits):
            hits.append(hit)
    for kind,col in [('DRP','risk_profile_id'),('EVE','incdnt_sid')]:
        for digits in re.findall(r'\b'+kind+r'[-_\s]?(\d+)\b',text,re.I):
            add(col,kind+'-'+digits)
    for code in re.findall(r'\b[пp]-?(\d{4,})\b',text,re.I):
        add('process_lvl_4_name','П'+code,'like')
    for short,value in TB.items():
        adjective=value.lower().rsplit(' ',1)[0]
        stem=re.sub(r'(?:ский|ный|ый|ий)$','',adjective)
        if re.search(r'\b'+short+r'\b',text) or re.search(r'(?<!\w)'+re.escape(stem)+r'\w*\s+банк\w*\b',text):
            add(ORIGIN,value)

    catalog=load_catalog()
    matched={}
    for col in (ORIGIN,RESPONSIBILITY,'process_lvl_4_name'):
        for raw in catalog.get(col,[]):
            value=str(raw).strip()
            normalized=value.lower().replace('ё','е')
            if not normalized or len(normalized)<3:
                continue
            # Exact names or quoted process names, never arbitrary shared words.
            if re.search(r'(?<!\w)'+re.escape(normalized)+r'(?!\w)',text):
                if normalized=='финансы' and re.search(r'финансов\w*\s+последств',text):
                    continue
                if normalized.startswith('риск') and re.search(r'цифров\w*\s+профил|\bdrp',text) and not re.search(r'блок|ответствен|происхожд',text):
                    continue
                matched.setdefault(value,set()).add(col)
    for value,columns in matched.items():
        semantic=columns & {ORIGIN,RESPONSIBILITY}
        choice=_choice(text,value,list(matched))
        if len(semantic)>1 and not choice:
            raise ClarificationRequired(f'По блоку «{value}» нужны ИОРы, которые возникли в подразделениях блока «{value}», или ИОРы, за которые блок «{value}» отвечает?')
        for col in columns:
            if col in semantic and choice and CATEGORIES[col]!=choice:
                continue
            add(col,value)
    # An explicit named organizational filter must not silently disappear.
    named=re.search(r'по\s+(?:блоку\s+|подразделению\s+|тб\s+)?[«"\']?([а-я][а-я\s-]+)',text)
    if named and not any(h['category'] in {'origin','responsibility','process'} for h in hits):
        if not re.match(r'(?:иор|инцидент|событ|финансов|нефинансов|возмещ|удален|период|цифров|профил|процесс)',named[1]):
            raise ClarificationRequired('Не удалось однозначно определить место происхождения или зону ответственности. Укажите точное название и поясните, где ИОР возникли или кто за них отвечает.')
    return hits
