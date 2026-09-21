"""Opt-in, read-only Greenplum smoke test for the IOR physical migration.

Run only after the target tables are loaded::

    IOR_GP_SMOKE=1 IOR_DATA_BACKEND=greenplum python scripts/smoke_greenplum.py

The script never runs as part of pytest and executes SELECT statements only.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = SKILL_DIR / "scripts"
WORKSPACE_DIR = SKILL_DIR.parents[1]
for path in (WORKSPACE_DIR, SKILL_DIR, SCRIPTS_DIR):
    path_str = str(path)
    if path_str not in sys.path:
        sys.path.insert(0, path_str)

# Use gateway's shared utils.db while exposing IOR-only utils.data_store.
workspace_path = str(WORKSPACE_DIR)
sys.path.remove(workspace_path)
sys.path.insert(0, workspace_path)
import utils

skill_utils_path = str(SKILL_DIR / "utils")
if skill_utils_path not in utils.__path__:
    utils.__path__.append(skill_utils_path)

from utils.data_store import GREENPLUM_TABLES, GreenplumStore
from ior_reports import build_preset_sql_queries


ACTIVE_PRESETS = (
    "financial_consequences_ior",
    "deleted_ior",
    "vozmeshenie_ior",
    "ior_nonfinancial_consequences",
    "ior_period_pao_sberbank",
    "report_period_specific_ior",
    "ior_hypothesis",
)


def main() -> int:
    if os.environ.get("IOR_GP_SMOKE") != "1":
        print("SKIP: set IOR_GP_SMOKE=1 to run the read-only Greenplum smoke test")
        return 0

    store = GreenplumStore()
    main_table = GREENPLUM_TABLES["ior"]
    count_df = store.query_sql(f"SELECT COUNT(*) AS row_count FROM {main_table}")
    sample_df = store.query_sql(f"SELECT * FROM {main_table} LIMIT 5")
    print(f"main row_count={count_df.iloc[0]['row_count']}; sample_rows={len(sample_df)}")

    queries = build_preset_sql_queries(GREENPLUM_TABLES)
    for preset in ACTIVE_PRESETS:
        limited_sql = f"SELECT * FROM ({queries[preset]}) AS smoke_result LIMIT 5"
        result = store.query_sql(limited_sql)
        print(f"{preset}: ok, rows={len(result)}, columns={len(result.columns)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
