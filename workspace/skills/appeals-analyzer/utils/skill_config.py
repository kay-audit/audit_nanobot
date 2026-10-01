"""Appeals access to the gateway's shared snapshot configuration."""
from __future__ import annotations

from pathlib import Path

SKILL_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME = "appeals_analyzer"
SCHEMA = "s_grnplm_ld_audit_da_project_27"
PREFIX = "40_kaluginvs_anofl_"
SOURCE_TABLES = tuple(f"{SCHEMA}.{PREFIX}{kind}_2026" for kind in (
    "appeal", "appeal_dialogs", "appeal_task",
))


def build_cache_provider():
    from lib.core import skill_config as shared

    return shared.build_cache_provider(SKILL_NAME, SKILL_ROOT)


def source_years() -> list[int]:
    from lib.core import skill_config as shared

    tables = shared.get_db_tables(SKILL_NAME)
    names = {str(name) for name in tables}
    years = sorted({int(name.rsplit("_", 1)[1]) for name in names
                    if name.startswith(f"{SCHEMA}.{PREFIX}appeal_")
                    and name.rsplit("_", 1)[1].isdigit()})
    return [year for year in years if all(
        f"{SCHEMA}.{PREFIX}{kind}_{year}" in names
        for kind in ("appeal", "appeal_dialogs", "appeal_task")
    )] or [2026]
