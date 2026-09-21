"""Data backends and physical-table registry for ``ior-analyzer``.

The normal backend reads the gateway-published DuckDB snapshot. Direct
Greenplum, local DuckDB and Spark access are explicit diagnostic/dev modes.
"""
from __future__ import annotations
import sys
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)


import logging
import os
import threading
import time
from pathlib import Path
from collections.abc import Mapping, Sequence
from typing import Any, Optional

import pandas as pd

from skill_config import build_cache_provider

logger = logging.getLogger(__name__)

_DEFAULT_CACHE_WAIT_SECONDS = 60.0
_DEFAULT_CACHE_POLL_INTERVAL = 1.0

GP_SCHEMA = "s_grnplm_ld_audit_da_project_34"

GREENPLUM_TABLES: dict[str, str] = {
    "ior": f"{GP_SCHEMA}.t_db_oarb_ior_d6_base_of_knowledge_ior",
    "status": f"{GP_SCHEMA}.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng",
    "recovery": f"{GP_SCHEMA}.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery",
    "financial_impact": f"{GP_SCHEMA}.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact",
    "nonfinancial_impact": f"{GP_SCHEMA}.t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact",
}

DUCKDB_TABLES: dict[str, str] = {
    "ior": "d6_base_of_knowledge_ior",
    "status": "d6_base_of_knowledge_incident_stts_chng",
    "recovery": "d6_base_of_knowledge_incident_recovery",
    "financial_impact": "d6_base_of_knowledge_incident_fin_impact",
    "nonfinancial_impact": "d6_base_of_knowledge_incident_nonfin_impact",
    "credits": "d6_base_of_knowledge_incident_credits",
    "appeals": "d6_appeals",
}

HIVE_SCHEMA = "arnsdpsbx_t_team_sva_oarb_4"
HIVE_TABLES: dict[str, str] = {
    "ior": f"{HIVE_SCHEMA}.d6_base_of_knowledge_ior",
    "status": f"{HIVE_SCHEMA}.d6_base_of_knowledge_incident_stts_chng",
    "recovery": f"{HIVE_SCHEMA}.d6_base_of_knowledge_incident_recovery",
    "financial_impact": f"{HIVE_SCHEMA}.d6_base_of_knowledge_incident_fin_impact",
    "nonfinancial_impact": f"{HIVE_SCHEMA}.d6_base_of_knowledge_incident_nonfin_impact",
    "credits": f"{HIVE_SCHEMA}.d6_base_of_knowledge_incident_credits",
    "appeals": "arnsdpsbx_t_team_sva_oarb.40_kay_d3_crm_dataset_test_faiss_10kk",
}


def translate_physical_tables(
    sql_query: str,
    source: Mapping[str, str],
    target: Mapping[str, str],
) -> str:
    """Translate only exact registered identifiers between backends."""
    translated = sql_query
    for logical_name, source_name in source.items():
        target_name = target.get(logical_name)
        if target_name:
            translated = translated.replace(source_name, target_name)
    return translated

# ----- SQL Where-clause helpers -------------------------------------------

def normalize_where(where: Optional[dict]) -> list[tuple[str, str, Any]]:
    out: list[tuple[str, str, Any]] = []
    if not where:
        return out
    for key, val in where.items():
        if key == "_or":
            continue
        if "__" in key:
            col, op_alias = key.rsplit("__", 1)
            mapping = {
                "like": "like",
                "gt": ">",
                "gte": ">=",
                "lt": "<",
                "lte": "<=",
                "ne": "!=",
                "eq": "=",
            }
            op = mapping.get(op_alias, "=")
            out.append((col, op, val))
        elif isinstance(val, dict):
            for op_key, sub_val in val.items():
                op_map = {
                    "like": "like",
                    ">=": ">=",
                    "<=": "<=",
                    ">": ">",
                    "<": "<",
                    "!=": "!=",
                    "=": "=",
                }
                op = op_map.get(op_key, "=")
                out.append((key, op, sub_val))
        elif isinstance(val, (list, tuple, set)):
            out.append((key, "in", list(val)))
        else:
            out.append((key, "=", val))
    return out


def build_where_clauses(where: Optional[dict]) -> tuple[str, list[Any]]:
    preds = normalize_where(where)
    if not preds:
        return "", []
    parts = []
    params = []
    for col, op, val in preds:
        safe_col = f'"{col}"'
        if op == "like":
            parts.append(f"{safe_col} ILIKE %s")
            params.append(f"%{val}%" if not str(val).startswith("%") else val)
        elif op == "in":
            if not val:
                parts.append("1=0")
            else:
                placeholders = ", ".join(["%s"] * len(val))
                parts.append(f"{safe_col} IN ({placeholders})")
                params.extend(val)
        else:
            parts.append(f"{safe_col} {op} %s")
            params.append(val)
    return " WHERE " + " AND ".join(parts), params


# ----- GreenplumStore ---------------------------------------------------

class GreenplumStore:
    """Greenplum backend using the application's shared ``utils.db`` pool."""

    backend_name = "greenplum"
    tables = GREENPLUM_TABLES

    def __init__(self, db_module: Any = None, dsn: Optional[str] = None) -> None:
        if db_module is None:
            # ``utils.db`` is the gateway-wide connector configured by
            # SessionStorageService.  Do not import the legacy skill-local
            # ``db.py`` here: it has separate connection state.
            from utils import db as db_module
        self._db = db_module
        self._dsn_fallback = dsn or os.environ.get("DATABASE_URL", "")

    def _ensure_configured(self) -> None:
        resolver = getattr(self._db, "resolve_dsn", None)
        configured_dsn = resolver() if callable(resolver) else ""
        if not configured_dsn and self._dsn_fallback:
            self._db.configure(self._dsn_fallback)
            configured_dsn = self._dsn_fallback
        if not configured_dsn:
            raise RuntimeError(
                "Greenplum backend is selected, but shared utils.db has no DSN. "
                "Configure channels.postgres.dsn or DATABASE_URL."
            )

    @staticmethod
    def _validate_read_query(sql_query: str) -> None:
        first_token = sql_query.lstrip().split(None, 1)[0].upper() if sql_query.strip() else ""
        if first_token not in {"SELECT", "WITH"}:
            raise ValueError("GreenplumStore.query_sql accepts read-only SELECT/WITH queries")

    def query_sql(
        self,
        sql_query: str,
        params: Optional[Sequence[Any]] = None,
    ) -> pd.DataFrame:
        """Execute the filtered query in GP and return only its result rows."""
        self._validate_read_query(sql_query)
        self._ensure_configured()
        bound_params = tuple(params or ())

        def _work(conn: Any) -> tuple[list[str], list[tuple[Any, ...]]]:
            with conn.cursor() as cur:
                cur.execute(sql_query, bound_params or None)
                columns = [item[0] for item in (cur.description or ())]
                rows = list(cur.fetchall())
                return columns, rows

        columns, rows = self._db.run(_work)
        return pd.DataFrame.from_records(rows, columns=columns)


# ----- NanobotCacheStore -----------------------------------------------

class NanobotCacheStore:
    """Read-only adapter over the DuckDB snapshot published by the gateway."""

    backend_name = "cache"
    tables = GREENPLUM_TABLES

    _MISSING_CACHE_MESSAGE = (
        "Nanobot gateway DuckDB cache is not available. "
        "Start gateway and wait for initial synchronization."
    )

    def __init__(
        self,
        provider: Any = None,
        *,
        wait_seconds: float | None = None,
        poll_interval: float | None = None,
    ) -> None:
        self._provider = provider if provider is not None else build_cache_provider()
        wait_seconds = self._resolve_positive_setting(
            "IOR_CACHE_WAIT_SECONDS", wait_seconds, _DEFAULT_CACHE_WAIT_SECONDS
        )
        poll_interval = self._resolve_positive_setting(
            "IOR_CACHE_POLL_INTERVAL", poll_interval, _DEFAULT_CACHE_POLL_INTERVAL
        )
        self._wait_for_cache(wait_seconds, poll_interval)

    @staticmethod
    def _resolve_positive_setting(
        env_name: str,
        explicit_value: float | None,
        default: float,
    ) -> float:
        raw_value: Any = explicit_value
        if raw_value is None:
            raw_value = os.environ.get(env_name, default)
        try:
            value = float(raw_value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{env_name} must be a positive number") from exc
        if value <= 0:
            raise ValueError(f"{env_name} must be a positive number")
        return value

    def _wait_for_cache(self, wait_seconds: float, poll_interval: float) -> None:
        started = time.monotonic()
        deadline = started + wait_seconds

        # ``False`` is the provider's expected not-ready signal. Exceptions are
        # unexpected open failures and must reach the caller without retrying.
        if self._provider.open_cache():
            return

        logger.warning(
            "IOR gateway DuckDB cache is not ready; waiting for initial sync..."
        )
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                logger.error(
                    "IOR gateway DuckDB cache did not become available within %.1f sec",
                    wait_seconds,
                )
                raise RuntimeError(self._MISSING_CACHE_MESSAGE)

            time.sleep(min(poll_interval, remaining))
            if self._provider.open_cache():
                elapsed = time.monotonic() - started
                logger.info(
                    "IOR gateway DuckDB cache became available after %.1f sec",
                    elapsed,
                )
                return

    def query_sql(
        self,
        sql_query: str,
        params: Optional[Sequence[Any]] = None,
    ) -> pd.DataFrame:
        result = self._provider.query_sql(
            sql_query,
            list(params) if params is not None else None,
        )
        if result.get("status") != "success":
            error = result.get("error") or "unknown cache query error"
            raise RuntimeError(f"Nanobot gateway DuckDB cache query failed: {error}")
        return pd.DataFrame.from_records(
            result.get("rows") or [],
            columns=result.get("columns") or [],
        )


# ----- SparkHiveStore (explicit legacy backend) -------------------------

class SparkHiveStore:
    """Legacy backend: PySpark with Hive Metastore support."""

    backend_name = "spark"
    tables = HIVE_TABLES

    def __init__(self) -> None:
        self._spark = None

    def _get_spark(self):
        if self._spark is not None and getattr(self._spark.sparkContext, "_jsc", None) is not None:
            return self._spark

        _SPARK_TMP = os.path.expanduser("~/.spark-local-dir")
        os.makedirs(_SPARK_TMP, exist_ok=True)
        os.environ["SPARK_LOCAL_DIRS"] = _SPARK_TMP

        from pyspark import SparkConf
        from pyspark.sql import SparkSession

        conf = SparkConf().setAppName("nanobot_ior_store")
        conf.setAll([
            ("spark.ui.enabled", "true"),
            ("spark.master", os.environ.get("SPARK_MASTER", "local[*]")),
            ("spark.executor.cores", "2"),
            ("spark.executor.memory", "8g"),
            ("spark.executor.memoryOverhead", "1g"),
            ("spark.driver.memory", "8g"),
            ("spark.driver.maxResultSize", "8g"),
            ("spark.port.maxRetries", "100"),
            ("spark.local.dir", _SPARK_TMP),
            ("spark.kubernetes.executor.deleteOnTerminate", "false"),
        ])
        self._spark = (
            SparkSession.builder.config(conf=conf)
            .enableHiveSupport()
            .getOrCreate()
        )
        try:
            self._spark.conf.set("spark.sql.execution.arrow.pyspark.enabled", "true")
        except Exception:
            pass
        logger.info("[SparkHiveStore] PySpark session ready (Hive support enabled)")
        return self._spark

    def query_sql(self, sql_query: str) -> pd.DataFrame:
        spark = self._get_spark()
        sdf = spark.sql(sql_query)
        return sdf.toPandas()


# ----- LocalDuckDBStore --------------------------------------------------

_PROJECT_ROOT = Path(__file__).resolve().parents[4]

class LocalDuckDBStore:
    """Local backend: DuckDB для отладки на локальном ПК."""

    backend_name = "local_duckdb"
    tables = DUCKDB_TABLES

    def __init__(self, db_path: Optional[str] = None) -> None:
        if not db_path:
            candidate = _PROJECT_ROOT / "ior_assistant/ior_assistant/data/local_kb.duckdb"
            if not candidate.exists():
                candidate = _PROJECT_ROOT / "workspace/data_store/local_kb.duckdb"
            self.db_path = str(candidate.resolve())
        else:
            self.db_path = db_path
        self._conn = None

    def _get_conn(self):
        if self._conn is not None:
            return self._conn
        import duckdb

        p = Path(self.db_path)
        if not p.exists():
            logger.warning(f"[LocalDuckDBStore] File not found: {p}; creating dev fixture.")
            p.parent.mkdir(parents=True, exist_ok=True)
            self._conn = duckdb.connect(str(p))
        else:
            self._conn = duckdb.connect(str(p))

        self._ensure_views(self._conn)
        logger.info(f"[LocalDuckDBStore] Connected: {p}")
        return self._conn

    def _ensure_views(self, conn) -> None:
        try:
            tables_res = [t[0].lower() for t in conn.execute("SHOW TABLES").fetchall()]
            if "d6_base_of_knowledge_ior" not in tables_res:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS d6_base_of_knowledge_ior (
                        incdnt_id VARCHAR,
                        incdnt_sid VARCHAR,
                        incdnt_entry_dt TIMESTAMP,
                        incdnt_status_name VARCHAR,
                        incdnt_sum DOUBLE,
                        org_struct_lvl_2_name VARCHAR,
                        org_struct_lvl_3_name VARCHAR,
                        risk_profile_id VARCHAR,
                        funct_block_id VARCHAR,
                        process_lvl_4_name VARCHAR,
                        incdnt_summary_descr_txt VARCHAR,
                        incdnt_full_descr_txt VARCHAR
                    )
                """)
                conn.execute("""
                    INSERT INTO d6_base_of_knowledge_ior VALUES
                    ('1', 'EVE-1001', '2025-03-15 10:00:00', 'УТВЕРЖДЁН', 1500000.0, 'Московский банк', 'Московский банк', 'DRP-10121', 'SBR-01', 'П-1001', 'Сбой при проведении операции DRP-10121', 'Подробное описание события DRP-10121 в Московском банке за март 2025'),
                    ('2', 'EVE-1002', '2025-03-20 12:30:00', 'УТВЕРЖДЁН', 2500000.0, 'Московский банк', 'Московский банк', 'DRP-10121', 'SBR-01', 'П-1002', 'Крупный инцидент DRP-10121', 'Еще один инцидент DRP-10121 в Московском банке')
                """)
                tables_res.append("d6_base_of_knowledge_ior")

            # Keep the compact fixture compatible with every column used by
            # the dynamic SQL builder.  ALTER also upgrades an already-created
            # local_kb.duckdb without recreating user data.
            for column_name in (
                "org_struct_id",
                "org_struct_lvl_4_name",
                "funct_block_lvl_3_name",
                "funct_block_lvl_4_name",
                "process_lvl_1_name",
                "process_lvl_2_name",
                "process_lvl_3_name",
            ):
                conn.execute(
                    f"ALTER TABLE d6_base_of_knowledge_ior "
                    f"ADD COLUMN IF NOT EXISTS {column_name} VARCHAR"
                )
            conn.execute("""
                UPDATE d6_base_of_knowledge_ior
                SET org_struct_id = COALESCE(org_struct_id, 'SBR_LOCAL'),
                    org_struct_lvl_4_name = COALESCE(org_struct_lvl_4_name, org_struct_lvl_3_name),
                    funct_block_lvl_3_name = COALESCE(funct_block_lvl_3_name, funct_block_id),
                    funct_block_lvl_4_name = COALESCE(funct_block_lvl_4_name, funct_block_id),
                    process_lvl_1_name = COALESCE(process_lvl_1_name, process_lvl_4_name),
                    process_lvl_2_name = COALESCE(process_lvl_2_name, process_lvl_4_name),
                    process_lvl_3_name = COALESCE(process_lvl_3_name, process_lvl_4_name)
            """)

            if "d6_base_of_knowledge_incident_stts_chng" not in tables_res:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS d6_base_of_knowledge_incident_stts_chng (
                        incdnt_id VARCHAR,
                        incdnt_status_name VARCHAR,
                        incdnt_status_code VARCHAR,
                        stts_chng_action_code VARCHAR,
                        stts_chng_action_name VARCHAR,
                        stts_chng_comment_txt VARCHAR,
                        stts_chng_action_dttm TIMESTAMP,
                        stts_chng_user_num VARCHAR
                    )
                """)
                conn.execute("""
                    INSERT INTO d6_base_of_knowledge_incident_stts_chng VALUES
                    ('1', 'УДАЛЁН', 'DEL', 'ACT_DEL', 'УДАЛИТЬ', 'Дублирование записи при авторегистрации', '2025-03-16 11:00:00', '01234567'),
                    ('2', 'УДАЛЁН', 'DEL', 'ACT_DEL', 'УДАЛИТЬ', 'Ошибка ввода параметров сделки оператором', '2025-03-21 14:00:00', '07654321')
                """)
                tables_res.append("d6_base_of_knowledge_incident_stts_chng")

            if "d6_base_of_knowledge_incident_recovery" not in tables_res:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS d6_base_of_knowledge_incident_recovery (
                        incdnt_id VARCHAR,
                        recovery_sid VARCHAR,
                        recovery_type_name VARCHAR,
                        recovery_crncy_code VARCHAR,
                        recovery_local_crncy_code VARCHAR,
                        recovery_src_account_num VARCHAR,
                        recovery_doc_num VARCHAR,
                        recovery_creation_dttm TIMESTAMP,
                        recovery_reg_dt TIMESTAMP,
                        recovery_ccy_amt DOUBLE,
                        recovery_local_ccy_amt DOUBLE,
                        recovery_rub_amt DOUBLE
                    )
                """)
                conn.execute("""
                    INSERT INTO d6_base_of_knowledge_incident_recovery VALUES
                    ('1', 'EVE-7143663-R1', 'Восстановление резерва на возможные потери по ссудам', 'RUB', 'RUB', '40702810000000000001', 'DOC-001', '2025-03-24 10:00:00', '2025-03-24 10:00:00', 50000.0, 50000.0, 50000.0),
                    ('1', 'EVE-7143663-R2', 'Компенсации от клиента', 'RUB', 'RUB', '40702810000000000002', 'DOC-002', '2025-03-25 12:00:00', '2025-03-25 12:00:00', 25000.0, 25000.0, 25000.0),
                    ('2', 'EVE-7143301-R1', 'получение страховой выплаты от одной или нескольких страховых компаний группы', 'RUB', 'RUB', '40702810000000000003', 'DOC-003', '2025-03-26 15:00:00', '2025-03-26 15:00:00', 100000.0, 100000.0, 100000.0)
                """)
                tables_res.append("d6_base_of_knowledge_incident_recovery")

            if "d6_base_of_knowledge_incident_fin_impact" not in tables_res:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS d6_base_of_knowledge_incident_fin_impact (
                        incdnt_id VARCHAR,
                        fin_impact_id VARCHAR,
                        fin_impact_sid VARCHAR,
                        fin_impact_type_name VARCHAR,
                        fin_impact_kind_name VARCHAR,
                        fin_impact_monitoring_flag VARCHAR,
                        fin_impact_crncy_code VARCHAR,
                        fin_impact_local_crncy_code VARCHAR,
                        fin_impact_detection_dt TIMESTAMP,
                        fin_impact_creation_dttm TIMESTAMP,
                        fin_impact_reg_dt TIMESTAMP,
                        fin_impact_account_num VARCHAR,
                        fin_impact_docum_num VARCHAR,
                        fi_busn_area_id VARCHAR,
                        fi_org_struct_id VARCHAR,
                        fin_impact_ccy_amt DOUBLE,
                        fin_impact_local_ccy_amt DOUBLE,
                        fin_impact_rub_amt DOUBLE
                    )
                """)
                conn.execute("""
                    INSERT INTO d6_base_of_knowledge_incident_fin_impact VALUES
                    ('1', 'FI-1', 'EVE-1001-F1', 'Прямая потеря', 'Операционная потеря', 'Y', 'RUB', 'RUB', '2025-03-15 10:00:00', '2025-03-15 10:00:00', '2025-03-15 10:00:00', '40817810000000000001', 'FI-DOC-1', 'BA-1', 'ORG-1', 1500000.0, 1500000.0, 1500000.0),
                    ('2', 'FI-2', 'EVE-1002-F1', 'Прямая потеря', 'Операционная потеря', 'Y', 'RUB', 'RUB', '2025-03-20 12:30:00', '2025-03-20 12:30:00', '2025-03-20 12:30:00', '40817810000000000002', 'FI-DOC-2', 'BA-2', 'ORG-2', 2500000.0, 2500000.0, 2500000.0)
                """)
                tables_res.append("d6_base_of_knowledge_incident_fin_impact")

            if "d6_base_of_knowledge_incident_nonfin_impact" not in tables_res:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS d6_base_of_knowledge_incident_nonfin_impact (
                        incdnt_id VARCHAR,
                        nonfin_impact_sid VARCHAR,
                        nonfin_impact_kind_name VARCHAR,
                        nonfin_impact_influence_class_name VARCHAR
                    )
                """)
                conn.execute("""
                    INSERT INTO d6_base_of_knowledge_incident_nonfin_impact VALUES
                    ('1', 'NFI-001', 'Ущерб репутации', 'Высокий'),
                    ('2', 'NFI-002', 'Жалобы и обращения клиентов', 'Средний')
                """)
                tables_res.append("d6_base_of_knowledge_incident_nonfin_impact")

            if "d6_base_of_knowledge_ior" in tables_res:
                conn.execute("""
                    CREATE VIEW IF NOT EXISTS d6_deleted_ior AS 
                    SELECT ior.*, 
                           st.incdnt_status_name AS incdnt_status_name_at_action, st.incdnt_status_code, st.stts_chng_action_code, 
                           st.stts_chng_action_name, st.stts_chng_comment_txt, st.stts_chng_action_dttm, st.stts_chng_user_num
                    FROM d6_base_of_knowledge_ior AS ior
                    LEFT JOIN d6_base_of_knowledge_incident_stts_chng AS st ON ior.incdnt_id = st.incdnt_id
                """)
                conn.execute("""
                    CREATE VIEW IF NOT EXISTS d6_vozmeshenie_ior AS 
                    SELECT ior.*, 
                           r.recovery_sid, r.recovery_type_name, r.recovery_crncy_code, r.recovery_local_crncy_code, 
                           r.recovery_src_account_num, r.recovery_doc_num, r.recovery_creation_dttm, r.recovery_reg_dt, 
                           r.recovery_ccy_amt, r.recovery_local_ccy_amt, r.recovery_rub_amt
                    FROM d6_base_of_knowledge_ior AS ior
                    INNER JOIN d6_base_of_knowledge_incident_recovery AS r ON ior.incdnt_id = r.incdnt_id
                """)
                conn.execute("""
                    CREATE VIEW IF NOT EXISTS d6_ior_nonfinancial_consequences AS 
                    SELECT ior.*, 
                           nfi.nonfin_impact_sid, nfi.nonfin_impact_kind_name, nfi.nonfin_impact_influence_class_name
                    FROM d6_base_of_knowledge_ior AS ior
                    INNER JOIN d6_base_of_knowledge_incident_nonfin_impact AS nfi ON ior.incdnt_id = nfi.incdnt_id
                """)
                conn.execute("""
                    CREATE VIEW IF NOT EXISTS d6_financial_consequences_ior AS 
                    SELECT ior.*, 
                           fi.fin_impact_id, fi.fin_impact_sid, fi.fin_impact_type_name, fi.fin_impact_kind_name, fi.fin_impact_monitoring_flag, 
                           fi.fin_impact_crncy_code, fi.fin_impact_local_crncy_code, fi.fin_impact_detection_dt, 
                           fi.fin_impact_creation_dttm, fi.fin_impact_reg_dt, fi.fin_impact_account_num, 
                           fi.fin_impact_docum_num, fi.fi_busn_area_id, fi.fi_org_struct_id,
                           fi.fin_impact_ccy_amt, fi.fin_impact_local_ccy_amt, fi.fin_impact_rub_amt
                    FROM d6_base_of_knowledge_ior AS ior
                    INNER JOIN d6_base_of_knowledge_incident_fin_impact AS fi ON ior.incdnt_id = fi.incdnt_id
                """)
                presets = [
                    "d6_credit_no_way_collect_debt",
                    "d6_ior_period_pao_sberbank",
                    "d6_report_period_specific_ior",
                    "d6_ior_hypothesis"
                ]
                for p in presets:
                    if p not in tables_res:
                        conn.execute(f"CREATE VIEW IF NOT EXISTS {p} AS SELECT * FROM d6_base_of_knowledge_ior")

            if "d6_appeals" not in tables_res:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS d6_appeals (
                        id VARCHAR,
                        short_description VARCHAR,
                        description VARCHAR,
                        date VARCHAR,
                        "Метрика СВА" VARCHAR
                    )
                """)
                conn.execute("""
                    INSERT INTO d6_appeals VALUES
                    ('1002345891', 'Не проходит платёж в мобильном приложении', 'Клиент обратился с жалобой: при попытке перевода возникает ошибка 500. Просьба разобраться со сбоем.', '2025-02-10', 'М01'),
                    ('1002345892', 'Задержка выписки по счёту', 'Задержка формирования выписки по бизнес-счёту на протяжении 3 дней.', '2025-02-12', 'М02'),
                    ('1002345893', 'Списание неизвестной комиссии', 'Клиент оспаривает списание комиссии за обслуживание в размере 450 руб.', '2025-02-15', 'М03'),
                    ('1002345894', 'Не проходит платёж по карте', 'Недоступна оплата картой в терминалах. Ошибка авторизации.', '2025-02-18', 'М01'),
                    ('1002345895', 'Ошибка в отображении баланса', 'Баланс счета отображается неверно после проведения транзакции.', '2025-02-20', 'М04')
                """)

            if "d6_base_of_knowledge_incident_credits" not in tables_res:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS d6_base_of_knowledge_incident_credits (
                        incdnt_id VARCHAR,
                        credit_agr_num VARCHAR,
                        credit_debt_rub_amt DOUBLE
                    )
                """)
                conn.execute("""
                    INSERT INTO d6_base_of_knowledge_incident_credits VALUES
                    ('1', 'AGR-LOCAL-1', 125000.0),
                    ('2', 'AGR-LOCAL-2', 250000.0)
                """)
        except Exception as err:
            logger.warning(f"[LocalDuckDBStore] Failed to ensure views: {err}")

    def query_sql(self, sql_query: str) -> pd.DataFrame:
        conn = self._get_conn()
        clean_query = translate_physical_tables(sql_query, GREENPLUM_TABLES, DUCKDB_TABLES)
        clean_query = translate_physical_tables(clean_query, HIVE_TABLES, DUCKDB_TABLES)
        return conn.execute(clean_query).df()


# Compatibility for tests/importers that instantiate the old class explicitly.
DuckDBStore = LocalDuckDBStore


# ----- Factory -----------------------------------------------------------

_store_instance = None
_store_backend = ""
_store_lock = threading.Lock()

def _configured_backend() -> str:
    return os.environ.get("IOR_DATA_BACKEND", "cache").strip().lower() or "cache"


def reset_data_store() -> None:
    """Clear the singleton; intended for tests and explicit reconfiguration."""
    global _store_instance, _store_backend
    with _store_lock:
        _store_instance = None
        _store_backend = ""


def get_data_store():
    global _store_instance, _store_backend
    with _store_lock:
        backend = _configured_backend()
        if _store_instance is not None and _store_backend == backend:
            return _store_instance

        if backend == "cache":
            _store_instance = NanobotCacheStore()
        elif backend == "local_duckdb":
            _store_instance = LocalDuckDBStore()
        elif backend == "greenplum":
            _store_instance = GreenplumStore()
        elif backend == "spark":
            _store_instance = SparkHiveStore()
        else:
            raise ValueError(
                f"Unsupported IOR_DATA_BACKEND={backend!r}; expected "
                "cache, greenplum, local_duckdb or spark"
            )
        _store_backend = backend
        logger.info("[get_data_store] Selected %s backend", backend)
        return _store_instance
