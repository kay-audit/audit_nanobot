"""Separate deterministic slices in one report, without extra exports or LLMs."""
import pandas as pd
from .registry import get_analyzer

def render_slices(df,plan):
    analyzer=get_analyzer(plan.preset)
    if analyzer is None:
        return ''
    slices=[]
    for category,column in [('drp','risk_profile_id'),('origin','org_struct_lvl_3_name'),('responsibility','funct_block_lvl_3_name')]:
        values=list(dict.fromkeys(h['value'] for h in plan.hits if h['category']==category))
        if len(values)>1 and column in df:
            for value in values:
                frame=df[df[column].astype(str).str.casefold().eq(value.casefold())]
                slices.append((value,frame))
    if plan.period and len(plan.period.intervals)>1 and plan.period.column in df:
        dates=pd.to_datetime(df[plan.period.column],errors='coerce')
        for (a,b),label in zip(plan.period.intervals,plan.period.slice_labels):
            slices.append((label,df[(dates>=pd.Timestamp(a)) & (dates<pd.Timestamp(b))]))
    if not slices:
        return ''
    lines=['### Отдельные срезы выбранной популяции',
           'Срезы представлены отдельно; пересекающиеся разрезы нельзя складывать между собой.']
    for label,frame in slices:
        lines.extend(['#### '+label,analyzer.prepare(frame).full_header])
    return '\n\n'.join(lines)
