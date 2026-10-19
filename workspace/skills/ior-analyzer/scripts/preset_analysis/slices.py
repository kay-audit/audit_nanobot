"""Separate deterministic slices in one report, without extra exports or LLMs."""
import pandas as pd

from .registry import get_analyzer


SLICE_COLUMNS = {
    'org_struct_lvl_3_name', 'org_struct_lvl_4_name',
    'funct_block_lvl_3_name', 'process_lvl_3_name', 'process_lvl_4_name',
    'risk_profile_id',
}
MAX_TEXT_SLICES = 20
MAX_TEXT_CHARS = 24000


def _hit_mask(df, hit):
    values = df[hit['column']].astype('string').str.upper()
    target = str(hit['value']).upper()
    if hit.get('op', 'eq') == 'eq':
        return values.str.strip().eq(target).fillna(False)
    return values.str.contains(target, regex=False, na=False)


def render_slices(df, plan):
    analyzer = get_analyzer(plan.preset)
    if analyzer is None:
        return ''

    # OR within a field, AND across independent fields. The incoming frame is
    # already selected by SQL; reapplying catalog filters also guards callers
    # that provide a broader frame. Never mutate the source or export.
    groups = {}
    for hit in plan.hits:
        if hit['column'] in df:
            groups.setdefault(hit['column'], []).append(hit)
    selected = pd.Series(True, index=df.index)
    for hits in groups.values():
        alternatives = pd.Series(False, index=df.index)
        for hit in hits:
            alternatives |= _hit_mask(df, hit)
        selected &= alternatives

    dates = None
    if plan.period and plan.period.column in df:
        dates = pd.to_datetime(df[plan.period.column], errors='coerce')
        intervals = pd.Series(False, index=df.index)
        for start, end in plan.period.intervals:
            intervals |= (dates >= pd.Timestamp(start)) & (dates < pd.Timestamp(end))
        selected &= intervals

    # Keep descriptors instead of materializing every frame for large plans.
    slices = []
    for column, hits in groups.items():
        unique = {(h['value'], h.get('op', 'eq')): h for h in hits}
        if column in SLICE_COLUMNS and len(unique) > 1:
            slices.extend((str(h['value']), h) for h in unique.values())
    if dates is not None and len(plan.period.intervals) > 1:
        slices.extend(zip(plan.period.slice_labels, plan.period.intervals))
    if not slices:
        return ''

    lines = ['### Отдельные срезы выбранной популяции',
             'Срезы представлены отдельно; пересекающиеся разрезы нельзя складывать между собой.']
    shown = 0
    for label, condition in slices[:MAX_TEXT_SLICES]:
        if isinstance(condition, dict):
            mask = _hit_mask(df, condition)
        else:
            start, end = condition
            mask = (dates >= pd.Timestamp(start)) & (dates < pd.Timestamp(end))
        bundle = analyzer.prepare(df.loc[selected & mask])
        section = ('#### ' + label + '\n\n'
                   + f'Уникальных ИОР в срезе (все статусы): {len(bundle.incident_all_df)}. '
                   + f'Из них утверждённых: {bundle.approved_count}.\n\n'
                   + bundle.full_header)
        if len('\n\n'.join(lines)) + len(section) + 300 > MAX_TEXT_CHARS:
            break
        lines.append(section)
        shown += 1
    if shown < len(slices):
        lines.append(f'Показано срезов: {shown} из {len(slices)}. Остальные срезы '
                     'не включены из-за ограничения размера текстового отчёта. '
                     'Полная выборка и XLSX этим ограничением не сокращаются.')
    return '\n\n'.join(lines)
