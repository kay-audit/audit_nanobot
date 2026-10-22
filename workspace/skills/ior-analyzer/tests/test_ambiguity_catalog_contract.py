from __future__ import annotations
import pytest
from utils.resolve import semantic_grounding as ground
from utils.resolve.request_plan import build_plan
from utils.resolve.request_outcome import ClarificationRequired


@pytest.fixture
def catalog(monkeypatch):
    monkeypatch.setattr(ground,'load_catalog',lambda:{field:['Риски','Сеть продаж'] for field in (ground.ORIGIN,ground.RESPONSIBILITY,ground.PROCESS_DIRECTION,ground.PROCESS)})


@pytest.mark.parametrize('value,inflected',[('Риски','Рискам'),('Сеть продаж','Сети продаж')])
def test_ambiguous_names_require_category_then_responsibility_field(catalog,value,inflected):
    prompt=f'Покажи ИОРы по {inflected} за 2026 год'
    with pytest.raises(ClarificationRequired) as first:
        build_plan(None,prompt)
    assert first.value.stage=='category'
    with pytest.raises(ClarificationRequired) as second:
        build_plan(None,prompt+'\nУточнение: по зоне ответственности')
    assert second.value.stage=='field'
    for answer,column in [('по функциональному блоку',ground.RESPONSIBILITY),('по направлению деятельности',ground.PROCESS_DIRECTION),('по конкретному процессу',ground.PROCESS)]:
        plan=build_plan(None,prompt+'\nУточнение: '+answer)
        assert [(hit['column'],hit['value']) for hit in plan.hits]==[(column,value)]
        assert plan.period.intervals==[('2026-01-01','2027-01-01')]


def test_explicit_origin_and_independent_responsibility_keep_and(catalog):
    plan=build_plan(None,'Выведи ИОРы по месту происхождения Риски за 2026 год')
    assert [(h['column'],h['value']) for h in plan.hits]==[(ground.ORIGIN,'Риски')]
    plan=build_plan(None,'Покажи ИОРы по месту происхождения Риски и по функциональному блоку Сеть продаж за 2026 год')
    assert {(h['column'],h['value']) for h in plan.hits}=={(ground.ORIGIN,'Риски'),(ground.RESPONSIBILITY,'Сеть продаж')}
    assert len(plan.predicates())==3
