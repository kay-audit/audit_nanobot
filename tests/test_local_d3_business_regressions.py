from __future__ import annotations
import importlib.util,json,sys
from pathlib import Path
import pytest
ROOT=Path(__file__).resolve().parents[1]
def runner():
 path=ROOT/"workspace/skills/appeals-analyzer/testing/runner.py"
 spec=importlib.util.spec_from_file_location("local_d3_business_runner",path)
 mod=importlib.util.module_from_spec(spec);sys.modules[spec.name]=mod;spec.loader.exec_module(mod);return mod

def test_mortgage_period_channel_filters_before_relevance():
 m=runner();filters,query=m._parse_request("Выведи обращения по ипотеке из контакт-центра за второй квартал 2026 года")
 assert filters=={"date_from":"2026-04-01","date_to":"2026-06-30","chnl":["Контакт-центр"],"prd":["Ипотека"]}
 rows=[dict(appeal_id=str(i),date=dt,prd="Ипотека",s_prd="Расчёт платежа",chnl=ch,text="DEMO_SYNTHETIC") for i,dt,ch in [(1,"2026-04-01","Контакт-центр"),(2,"2026-06-30","Контакт-центр"),(3,"2026-07-01","Контакт-центр"),(4,"2026-05-01","Офис")]]
 assert [r["appeal_id"] for r in m.apply_filters(rows,filters)]==["1","2"]

@pytest.mark.asyncio
async def test_followup_does_not_expand_saved_ids(monkeypatch,tmp_path):
 m=runner();monkeypatch.setattr(m,"SESSION_ROOT",tmp_path)
 rows=[dict(appeal_id=str(i),date="2026-04-01",prd="Ипотека",s_prd="Расчёт платежа",chnl="Контакт-центр",text="Ошибка признана") for i in [1,2]]
 monkeypatch.setattr(m,"load_records",lambda:rows);m._write_session("demo","original",["1"])
 seen=[]
 def rank(query,candidates,llm):
  seen.extend(r["appeal_id"] for r in candidates);return [dict(id=r["appeal_id"],score=1,reason="source") for r in candidates]
 monkeypatch.setattr(m,"search",rank);monkeypatch.setattr(m,"render_report",lambda q,r:str([x["appeal_id"] for x in r]))
 result=await m.run_testing_report(session_id="demo",user_prompt="Среди найденных оставь те, где оператор признал ошибку")
 assert result=="['1']" and seen==["1"]

@pytest.mark.asyncio
async def test_rich_exact_id_without_previous_search(monkeypatch,tmp_path):
 m=runner();monkeypatch.setattr(m,"SESSION_ROOT",tmp_path)
 row=dict(appeal_id="DEMO-SYNTHETIC-RICH-000009",date="2026-01-09",prd="Безопасность",s_prd="Навязанный кредит",chnl="Контакт-центр",text="DEMO_SYNTHETIC exact source")
 monkeypatch.setattr(m,"load_records",lambda:[row]);result=await m.run_testing_report(session_id="new",user_prompt="Подробнее DEMO-SYNTHETIC-RICH-000009")
 assert row["text"] in result


def test_shortlist_keeps_semantic_topic_candidate_among_lexical_clones():
    m=runner()
    rows=[dict(appeal_id=f"LEGACY-{i:04}",date="2026-01-01",prd="Кредиты",s_prd="Условия",chnl="Офис",text="кредит звонка мошенников") for i in range(100)]
    rows.append(dict(appeal_id="DEMO-SYNTHETIC-RICH-000009",date="2026-01-01",prd="Безопасность",s_prd="Навязанный кредит",chnl="Контакт-центр",text="Звонивший убедил оформить заём и отправить деньги на безопасный счёт"))
    selected=m.shortlist("Кредит после звонка мошенников",rows,limit=60)
    assert len(selected)==60
    assert "DEMO-SYNTHETIC-RICH-000009" in {row["appeal_id"] for row in selected}
