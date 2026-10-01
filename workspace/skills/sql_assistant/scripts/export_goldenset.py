from __future__ import annotations
import argparse, json, random
from pathlib import Path
from lib.services.kb_store import KbStore
from lib.services.sql_static import sql_facts
from workspace.skills.sql_assistant.scripts._offline import DuckDbProvider

def main():
    p=argparse.ArgumentParser(); p.add_argument("--duckdb",required=True); p.add_argument("--output",required=True); p.add_argument("--count",type=int,default=60); p.add_argument("--seed",type=int,default=20260903); args=p.parse_args()
    rows=[r for r in KbStore(DuckDbProvider(args.duckdb)).corpus_frame("examples") if r.get("nl") and r.get("sql")]
    random.Random(args.seed).shuffle(rows); selected=rows[:max(0,args.count)]; out=Path(args.output); out.parent.mkdir(parents=True,exist_ok=True)
    with out.open("w",encoding="utf-8") as fh:
        for row in selected:
            try: facts=sql_facts(row["sql"],dialect=row.get("dialect") or "spark")
            except Exception: facts={"tables":[],"columns_by_table":{}}
            item={"id":row.get("id"),"nl":row["nl"],"sql":row["sql"],"dialect":row.get("dialect") or "spark","reference_tables":facts.get("tables",[]),"reference_columns":facts.get("columns_by_table",{}),"labels_source":"derived_from_existing_sql"}; fh.write(json.dumps(item,ensure_ascii=False,default=str)+"\n")
    print(json.dumps({"available":len(rows),"exported":len(selected),"output":str(out)})); return 0
if __name__=="__main__": raise SystemExit(main())

