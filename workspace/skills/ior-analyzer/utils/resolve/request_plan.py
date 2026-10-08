"""One deterministic interpretation shared by SQL, reports and LLM context."""
from dataclasses import dataclass, field
import re
from .money_parser import parse_money
from .period_parser import parse_period
from .semantic_grounding import ground_query
from .request_outcome import ClarificationRequired

LOSS_TAB='Анализ конкретных видов потерь необходимо запускать из соответствующей вкладки Единого рабочего места. Откройте вкладку специального анализа ИОР и задайте параметры там.'

def subjects(prompt):
    t=(prompt or '').lower().replace('ё','е')
    result=[]
    if re.search(r'возмещ|возврат|компенсац|взыскан',t): result.append('vozmeshenie_ior')
    if re.search(r'удал|причин\w*\s+удален',t): result.append('deleted_ior')
    if re.search(r'нефинанс|качествен|репутац|прерыван',t): result.append('ior_nonfinancial_consequences')
    if re.search(r'(?<!не)финансов\w*\s+последств|потер|убыт|ущерб',t) and not re.search(r'нефинанс',t): result.append('financial_consequences_ior')
    return list(dict.fromkeys(result))

def resolve_preset(preset,prompt):
    preset=preset.removesuffix('_v2') if preset else None
    t=(prompt or '').lower().replace('ё','е')
    if re.search(r'потер\w*\s+третьих\s+лиц|(?:прям\w*|косвен\w*|нереализ\w*|кредитн\w*|третьих\s+лиц)\s+(?:финансов\w*\s+)?(?:потер|ущерб|последств)',t):
        raise ClarificationRequired(LOSS_TAB)
    found=subjects(prompt)
    money = parse_money(prompt)
    if money:
        # Recovery wording switches the monetary source; otherwise it is financial.
        if 'vozmeshenie_ior' in found:
            found = [s for s in found if s != 'financial_consequences_ior']
        elif not found:
            found = ['financial_consequences_ior']
    tail=t.rsplit('уточнение:',1)[-1] if 'уточнение:' in t else ''
    selected=subjects(tail) if tail else []
    if len(found)>1 and len(selected)!=1:
        raise ClarificationRequired('Запрос одновременно относится к нескольким видам анализа. Какой анализ нужно выполнить: '+', '.join({'vozmeshenie_ior':'возмещения','deleted_ior':'удалённые ИОР','financial_consequences_ior':'финансовые последствия','ior_nonfinancial_consequences':'нефинансовые последствия'}[s] for s in found)+'?')
    resolved=(selected[0] if len(selected)==1 else found[0] if found else preset)
    eves=list(dict.fromkeys(re.findall(r'\bEVE[-_\s]?(\d+)\b',prompt or '',re.I)))
    if not resolved or (resolved=='report_period_specific_ior' and found):
        resolved='report_period_specific_ior' if eves else ('ior_period_pao_sberbank' if 'сбербанк' in t else 'ior_hypothesis')
    if resolved=='report_period_specific_ior':
        if not eves:
            return 'ior_hypothesis'
        if len(eves)>1:
            chosen=re.findall(r'\beve[-_\s]?(\d+)\b',tail,re.I)
            if len(set(chosen))!=1:
                raise ClarificationRequired('Досье формируется для одного ИОР. Укажите, пожалуйста, какой EVE нужно разобрать: '+', '.join('EVE-'+s for s in eves)+'?')
    allowed={'financial_consequences_ior','deleted_ior','vozmeshenie_ior','ior_nonfinancial_consequences','ior_period_pao_sberbank','report_period_specific_ior','ior_hypothesis','credit_no_way_collect_debt'}
    if resolved not in allowed:
        raise ClarificationRequired('Указан неизвестный вид анализа. Уточните, нужен общий отчёт, досье, последствия, возмещения или удалённые ИОР.')
    return resolved

@dataclass
class RequestPlan:
    preset: str
    period: object = None
    hits: list = field(default_factory=list)
    money: list = field(default_factory=list)
    status: str = ''
    pao: bool = False

    def context(self,prompt):
        categories={}
        for h in self.hits:
            categories.setdefault(h['category'],[]).append(h['value'])
        return {'original_user_intent':prompt,'preset':self.preset,
                'period_intervals':self.period.intervals if self.period else [],
                'constraints':categories,'money':[(m.op,str(m.value)) for m in self.money],
                'status':self.status,'pao':self.pao}

    def predicates(self):
        clauses=[]
        if self.period:
            clauses.append('('+' OR '.join(f"{self.period.column} >= TIMESTAMP '{a}' AND {self.period.column} < TIMESTAMP '{b}'" for a,b in self.period.intervals)+')')
        grouped={}
        for h in self.hits:
            col=h['column']
            value=h['value'].replace("'","''")
            predicate=f"UPPER(TRIM({col})) = '{value.upper()}'" if h['op']=='eq' else f"UPPER({col}) LIKE '%{value.upper()}%'"
            grouped.setdefault(h['category'],[]).append(predicate)
        clauses.extend('('+' OR '.join(v)+')' for v in grouped.values())
        if self.pao:
            clauses.append("SUBSTR(UPPER(org_struct_id), 1, 4) IN ('SBR_', 'EXT_', 'GRC_', 'MON_', 'BPS_')")
        if self.status=='deleted': clauses.append("UPPER(incdnt_status_name) IN ('УДАЛЁН', 'УДАЛЕН')")
        if self.status=='approved': clauses.append("UPPER(incdnt_status_name) IN ('УТВЕРЖДЁН', 'УТВЕРЖДЕН', 'УТВЕРЖДЕНИЕ')")
        if isinstance(self.status,list) and self.status:
            values=', '.join("'"+s.replace("'","''").upper()+"'" for s in self.status)
            clauses.append('UPPER(incdnt_status_name) IN ('+values+')')
        return clauses

def build_plan(preset,prompt):
    resolved=resolve_preset(preset,prompt)
    period=parse_period(prompt)
    hits=ground_query(prompt)
    if resolved=='report_period_specific_ior' and 'уточнение:' in prompt.lower():
        chosen=re.findall(r'\beve[-_\s]?(\d+)\b',prompt.lower().rsplit('уточнение:',1)[1],re.I)
        if len(set(chosen))==1:
            hits=[h for h in hits if h['category']!='eve' or h['value']=='EVE-'+chosen[0]]
    money=parse_money(prompt)
    if money and resolved in {'ior_nonfinancial_consequences','deleted_ior','credit_no_way_collect_debt','report_period_specific_ior'}:
        raise ClarificationRequired('В этом виде отчёта денежный фильтр не поддерживается. Уточните вид анализа: финансовые последствия или возмещения.')
    low=prompt.lower()
    statuses=[]
    for stem,values in [('утвержд',['Утверждён','Утвержден','Утверждение']),('черновик',['Черновик']),('исследован',['Исследование']),('закрыт',['Закрыт'])]:
        if stem in low: statuses.extend(values)
    status='deleted' if resolved=='deleted_ior' else statuses
    if re.search(r'не\s*закрыт|незакрыт',low):
        raise ClarificationRequired('Укажите нужные статусы ИОР явно: например «Черновик» или «Исследование».')
    return RequestPlan(resolved,period,hits,money,status,'пао сбербанк' in low or resolved=='ior_period_pao_sberbank')

def explicit_followup(prompt):
    return bool(re.search(r'\b(?:в|из)\s+(?:этой|полученной|прошлой|последней)\s+(?:выборк[еи]|выгрузк[еи])\b|\bсреди\s+выгруженных\s+выше\b|\bв\s+предыдущем\s+отч[её]те\b|\b(?:среди|из)\s+этих\s+иор\b',prompt.lower()))
