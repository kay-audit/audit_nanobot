from __future__ import annotations
import argparse, json
from lib.services.llm_client import call_llm_json
from workspace.skills.sql_assistant.scripts._pg_admin import add_common, run_updates

def main():
    p=argparse.ArgumentParser(); add_common(p,table="s_grnplm_ld_audit_da_project_34.kb_examples"); args=p.parse_args()
    def transform(row):
        if row.get("nl") and row.get("nl_variants") and not args.force: return None
        data=call_llm_json([{"role":"system","content":"Return JSON with factual nl and 2-3 nl_variants; infer only from description and SQL."},{"role":"user","content":json.dumps({"description":row.get("script_description"),"sql":row.get("sql")},ensure_ascii=False)}],max_tokens=800,temperature=0.0)
        if not data or not isinstance(data.get("nl_variants"),list): raise ValueError("invalid LLM enrichment JSON")
        return {"nl":str(data.get("nl") or ""),"nl_variants":json.dumps(data["nl_variants"][:3],ensure_ascii=False)}
    stats=run_updates(args,select_columns="id, nl, nl_variants, script_description, sql",where="TRUE",transform=transform); print(json.dumps(stats)); return 0 if not stats["errors"] else 2
if __name__=="__main__": raise SystemExit(main())

