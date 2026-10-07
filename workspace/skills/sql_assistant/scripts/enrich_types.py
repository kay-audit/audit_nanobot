from __future__ import annotations
import argparse, json
from lib.services.spark_backend import SparkBackend
from workspace.skills.sql_assistant.scripts._pg_admin import add_common, connect, run_updates
from workspace.skills.sql_assistant.scripts._offline import quote_table

def main():
    p=argparse.ArgumentParser(); add_common(p,table="s_grnplm_ld_audit_da_project_34.kb_columns"); p.add_argument("--tables-table",default="s_grnplm_ld_audit_da_project_34.kb_tables"); args=p.parse_args()
    if not SparkBackend.available(): print(json.dumps({"status":"unavailable","error":"PySpark is not installed"})); return 3
    conn=connect(args); tables=quote_table(args.tables_table); cache={}
    def transform(row):
        table_id=row["table_id"]
        if table_id not in cache:
            with conn.cursor() as cur: cur.execute(f"SELECT table_name FROM {tables} WHERE id=%s",(table_id,)); found=cur.fetchone()
            if not found: return None
            cache[table_id]={c["name"].lower():c["data_type"] for c in SparkBackend._columns_sync(found[0])}
        kind=cache[table_id].get(str(row["column_name"]).lower())
        return {"data_type":kind} if kind and (args.force or kind!=row.get("data_type")) else None
    stats=run_updates(args,select_columns="id, table_id, column_name, data_type",where="TRUE",transform=transform); conn.close(); print(json.dumps(stats)); return 0 if not stats["errors"] else 2
if __name__=="__main__": raise SystemExit(main())

