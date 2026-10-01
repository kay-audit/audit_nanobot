from __future__ import annotations
import argparse, json, os
from workspace.skills.sql_assistant.scripts._pg_admin import add_common, connect, run_updates
from workspace.skills.sql_assistant.scripts._offline import quote_table

def main():
    p=argparse.ArgumentParser(); add_common(p,table="sqlagent.kb_tables"); p.add_argument("--columns-table",default="sqlagent.kb_columns"); p.add_argument("--use-llm",action="store_true"); args=p.parse_args(); columns_table=quote_table(args.columns_table)
    conn=connect(args)
    def transform(row):
        if row.get("columns_summary") and not args.force: return None
        with conn.cursor() as cur:
            cur.execute(f"SELECT column_name,data_type,description FROM {columns_table} WHERE table_id=%s ORDER BY ordinal,column_name",(row["id"],)); columns=cur.fetchall()
        if not columns: return None
        factual="; ".join(f"{name} ({kind or '?'})"+(f": {desc}" if desc else "") for name,kind,desc in columns[:40])
        summary=factual
        if args.use_llm:
            from lib.services.llm_client import call_llm
            prompt=f"Table: {row['table_name']}\nDescription: {row.get('description') or ''}\nColumns: {factual}"
            summary=call_llm([{"role":"system","content":"Write a concise factual columns summary. Do not invent columns."},{"role":"user","content":prompt}],max_tokens=600,temperature=0.0)
        return {"columns_summary":summary} if summary!=row.get("columns_summary") else None
    stats=run_updates(args,select_columns="id, table_name, description, columns_summary",where="TRUE",transform=transform); conn.close(); print(json.dumps(stats)); return 0 if not stats["errors"] else 2
if __name__=="__main__": raise SystemExit(main())

