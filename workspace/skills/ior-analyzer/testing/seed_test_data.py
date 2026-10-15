"""Идемпотентный seed тестовых данных d6_nanobot IOR.

Создаёт таблицы ``public.t_db_oarb_ior_d6_*`` (если их нет) и заполняет их
тестовыми инцидентами операционного риска. **Если таблицы уже существуют
и содержат данные, скрипт ничего не делает** — это безопасно для повторного
запуска поверх существующей production-БД.

Зачем нужен отдельный скрипт:
  * ``data_generator`` генерирует короткие поля (``eve_id``/``drp``/``date``),
    а таблица ожидает production-имена (``incdnt_sid``/``risk_profile_id``/
    ``incdnt_entry_dt``). Маппинг — здесь.
  * Production-таблица содержит 5 связанных таблиц (ior, stts_chng,
    recovery, fin_impact, nonfin_impact) — скрипт заполняет основную
    (``t_db_oarb_ior_d6_base_of_knowledge_ior``) и по одной записи в
    каждой связанной для демонстрации JOIN-возможностей.
  * SQL-таблицы создаются через ``CREATE TABLE IF NOT EXISTS`` —
    скрипт не дропает существующие данные, что критично для прода.

Идемпотентность:
  * Если таблица уже существует → ничего не делает.
  * Если таблица существует, но пуста → заполняет.
  * Если таблица не существует → ``CREATE TABLE IF NOT EXISTS`` +
    ``INSERT``.

DSN — через ``$IOR_TEST_DSN``, ``--dsn`` или fallback в ``project.json``.

Команда::

    python workspace/skills/ior-analyzer/testing/seed_test_data.py \\
        --json workspace/data_store/cache/testing/ior/ior.json

Автоматический запуск через audit_bridge ``feature.yaml``::

    scripts:
      - path: workspace/skills/ior-analyzer/testing/seed_test_data.py
        command: "python workspace/skills/ior-analyzer/testing/seed_test_data.py --json workspace/data_store/cache/testing/ior/ior.json"
        idempotent: true   # см. docstring: ничего не делает поверх существующих таблиц
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


# ----------------------------------------------------------------------------
# 5 production таблиц под схемой public. Имена совпадают с прод-GP
# (s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_*), но схема — public
# (наша DEV-БД). Подробнее см. feature.yaml::feature.id = d6_ior.
# ----------------------------------------------------------------------------
_DEFAULT_TABLES = (
    "t_db_oarb_ior_d6_base_of_knowledge_ior",
    "t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng",
    "t_db_oarb_ior_d6_base_of_knowledge_incident_recovery",
    "t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact",
    "t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact",
)


_ENV_RE = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_env(value: str) -> str:
    """Подставить ${VAR} из os.environ (как делает lib.services.config_service.resolve_value).

    Если переменная не задана — оставляем литерал (не падаем, чтобы тесты с
    DSN-литералами работали).
    """
    def replace(m: re.Match[str]) -> str:
        return os.environ.get(m.group(1), m.group(0))
    return _ENV_RE.sub(replace, value)


def _resolve_dsn(explicit: str | None) -> str:
    """DSN в порядке приоритета: --dsn, $IOR_TEST_DSN, channels.postgres.dsn.

    Глобальный параметр audit_nanobot: ``channels.postgres.dsn`` в ``project.json``
    ссылается на ``${DATABASE_URL}`` и резолвится ``lib.services.config_service``
    во время merge с ``.secrets.env``. Этот скрипт читает ``project.json``
    напрямую (минуя ``config.py``), поэтому резолвит ``${VAR}`` через
    ``os.environ`` самостоятельно — без дублирования DSN/host/port в коде ветки.
    """
    if explicit:
        return explicit
    env_dsn = os.environ.get("IOR_TEST_DSN")
    if env_dsn:
        return env_dsn
    project_json = Path(__file__).resolve().parents[4] / "project.json"
    if project_json.is_file():
        text = project_json.read_text(encoding="utf-8")
        stripped = re.sub(r"//[^\n]*", "", text)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
        try:
            data = json.loads(stripped)
            raw_dsn = (((data or {}).get("channels") or {}).get("postgres") or {}).get("dsn")
            if raw_dsn:
                resolved = _resolve_env(raw_dsn)
                if resolved and "${" not in resolved:
                    return resolved
        except json.JSONDecodeError:
            pass
    raise SystemExit(
        "Не удалось разрешить DSN. "
        "Укажите --dsn или переменную IOR_TEST_DSN, "
        "или channels.postgres.dsn в project.json с DATABASE_URL в .secrets.env."
    )


def _parse_date(raw: Any) -> date | None:
    if isinstance(raw, str):
        try:
            return date.fromisoformat(raw)
        except ValueError:
            return None
    if isinstance(raw, date):
        return raw
    return None


# ----------------------------------------------------------------------------
# Risk profile mapping. Используем ЧИСЛОВЫЕ DRP-коды (DRP-10121 и т.д.),
# потому что build_dynamic_sql_from_prompt в ior_reports.py фильтрует только
# по regex r"DRP[_\s-]?(\d+)" — буквенные коды (DRP-IT, DRP-OP) этот regex
# не матчит, и SQL-запрос возвращает 0 строк.
# ----------------------------------------------------------------------------
_RISK_MAP = {
    "Операционный процесс":      ("DRP-10120", "Операционный процесс"),
    "ИТ-системы":                 ("DRP-10121", "Сбой информационной системы"),
    "Человеческий фактор":       ("DRP-10122", "Ошибка сотрудника"),
    "Внешнее мошенничество":      ("DRP-10123", "Внешнее мошенничество"),
    "Недоступность сервиса":      ("DRP-10124", "Недоступность сервиса"),
    "Регуляторные риски":         ("DRP-10125", "Регуляторный риск"),
    "Процедурные нарушения":     ("DRP-10126", "Процедурное нарушение"),
    "Конфликт интересов":        ("DRP-10127", "Конфликт интересов"),
    "Утечка данных":             ("DRP-10128", "Утечка данных"),
    "Неавторизованный доступ":   ("DRP-10129", "Неавторизованный доступ"),
}


def _map_status(raw: str) -> str:
    """data_generator: закрыт/в работе/черновик/на рассмотрении
       → production: Утверждён/Исследование/Черновик/Согласование/Возмещение/Закрыт"""
    mapping = {
        "закрыт": "Закрыт",
        "в работе": "Исследование",
        "черновик": "Черновик",
        "на рассмотрении": "Согласование",
    }
    return mapping.get((raw or "").strip().lower(), "Исследование")


def _build_ior_record(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Маппинг JSON data_generator'а в production-колонки t_db_oarb_ior_d6_base_of_knowledge_ior.

    Заполняет ВСЕ колонки production-схемы (65+ колонок), чтобы DuckDB
    правильно определял VARCHAR-типы (если оставить NULL, DuckDB
    интерпретирует как INTEGER — UPPER() потом падает с Binder Error).
    """
    eve_id = raw.get("eve_id") or f"EVE-TEST-{idx:04d}"
    drp = raw.get("drp") or f"DRP-TEST-{idx:03d}"
    entry_dt = _parse_date(raw.get("date"))
    risk_id, risk_name = _RISK_MAP.get(
        (raw.get("category") or "").strip(),
        ("DRP-10120", "Операционный процесс"),
    )
    status = _map_status(raw.get("status"))
    loss = float(raw.get("financial_loss") or 0)
    reimb = float(raw.get("reimbursement") or 0)
    business_line = raw.get("business_line") or "Блок «Розничный бизнес»"
    product = raw.get("product") or "Вклад"
    org_struct_lvl_3 = raw.get("business_line") or "Розничный бизнес"
    return {
        "incdnt_id": idx,
        "incdnt_sid": eve_id,
        "incdnt_agr_num": drp.replace("DRP-TEST-", "AGR-"),
        "incdnt_agr_sid": f"AGR-SID-{idx:06d}",
        "incdnt_appl_num": f"APPL-{idx:06d}",
        "incdnt_status_name": status,
        "incdnt_status_code": "IN_RES" if status != "Удалён" else "DEL",
        "incdnt_autoreg_flag": "N",
        "incdnt_detection_person_name": "Вторая линия",
        "incdnt_mistake_cnt": 1,
        "incdnt_entry_dt": entry_dt,
        "incdnt_detection_dt": entry_dt,
        "incdnt_start_dt": entry_dt,
        "incdnt_first_validated_dttm": entry_dt + timedelta(days=7) if entry_dt else None,
        "incdnt_last_validate_dttm": entry_dt + timedelta(days=14) if entry_dt else None,
        "incdnt_summary_descr_txt": raw.get("description") or raw.get("event_type") or "",
        "incdnt_full_descr_txt": raw.get("consequences") or "",
        "risk_profile_id": risk_id,
        "risk_profile_name": risk_name,
        "incdnt_security_risk_flag": "N",
        "incdnt_infrmtn_sys_risk_flag": "N",
        "incdnt_behavior_risk_flag": "N",
        "incdnt_model_risk_flag": "N",
        "incdnt_type_lvl_1_name": "Действия персонала",
        "incdnt_type_lvl_2_name": raw.get("event_type") or "Операционный процесс",
        "incdnt_source_name": "Реестровое уведомление",
        "src_type_lvl_1_name": "Действия персонала",
        "src_type_lvl_2_name": "Непреднамеренные ошибки сотрудников",
        "org_struct_id": drp.replace("DRP-", "SBR_"),
        "org_struct_lvl_2_name": "ПАО Сбербанк (ЦА)",
        "org_struct_lvl_3_name": org_struct_lvl_3,
        "org_struct_lvl_4_name": f"Уровень 4 / {org_struct_lvl_3}",
        "org_struct_lvl_5_name": "Уровень 5 / Территориальный",
        "org_struct_lvl_6_name": "Уровень 6 / Региональный",
        "org_struct_lvl_7_name": "Уровень 7 / Городской",
        "org_struct_lvl_8_name": "Уровень 8 / Районный",
        "org_struct_lvl_9_name": "Уровень 9 / Дополнительный",
        "org_struct_lvl_10_name": "Уровень 10 / Вспомогательный",
        "funct_block_id": drp.replace("DRP-", "FB-"),
        "funct_block_lvl_2_name": "Блок «Розничный бизнес»",
        "funct_block_lvl_3_name": business_line,
        "funct_block_lvl_4_name": f"Подблок / {business_line}",
        "process_lvl_1_name": "ПАО Сбербанк",
        "process_lvl_2_name": "Розничный бизнес",
        "process_lvl_3_name": business_line,
        "process_lvl_4_name": product,
        "incdnt_client_type_name": "ФЛ",
        "incdnt_sum": loss,
        "incdnt_drct_dmg_sum": loss * 0.7 if loss else 0,
        "incdnt_drct_dmg_cred_rub_amt": 0,
        "incdnt_drct_dmg_noncred_rub_amt": loss * 0.7 if loss else 0,
        "incdnt_indrct_dmg_sum": loss * 0.3 if loss else 0,
        "incdnt_indrct_dmg_cred_rub_amt": 0,
        "incdnt_indrct_dmg_noncred_rub_amt": loss * 0.3 if loss else 0,
        "incdnt_gain_sum": 0,
        "incdnt_gain_cred_rub_amt": 0,
        "incdnt_gain_noncred_rub_amt": 0,
        "incdnt_thrd_prt_sum": 0,
        "incdnt_thrd_prt_cred_rub_amt": 0,
        "incdnt_thrd_prt_noncred_rub_amt": 0,
        "incdnt_unrlzd_dmg_sum": 0,
        "incdnt_unrlzd_dmg_cred_rub_amt": 0,
        "incdnt_unrlzd_dmg_noncred_rub_amt": 0,
        "recovery_rub_amt_aggr": reimb,
        "incdnt_security_risk_flag": "N",
        "incdnt_infrmtn_sys_risk_flag": "N",
    }


def _build_status_record(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Одна запись истории смены статуса для каждого инцидента."""
    entry_dt = _parse_date(raw.get("date"))
    return {
        "incdnt_id": idx,
        "incdnt_status_name": "Исследование",
        "incdnt_status_code": "IN_RES",
        "stts_chng_action_code": "ACT_IN_RES",
        "stts_chng_action_name": "Перевод в исследование",
        "stts_chng_comment_txt": "Первичная регистрация инцидента",
        "stts_chng_action_dttm": datetime.combine(entry_dt, datetime.min.time()) if entry_dt else None,
        "stts_chng_user_num": "01234567",
    }


def _build_recovery_record(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Запись возмещения (если есть reimbursement > 0)."""
    reimb = float(raw.get("reimbursement") or 0)
    entry_dt = _parse_date(raw.get("date"))
    return {
        "incdnt_id": idx,
        "recovery_id": idx,
        "recovery_sid": f"REC-TEST-{idx:06d}",
        "recovery_type_name": "Возмещение",
        "recovery_crncy_code": "RUB",
        "recovery_local_crncy_code": "RUB",
        "recovery_src_account_num": f"40817{idx:06d}",
        "recovery_doc_num": f"REC-DOC-{idx:06d}",
        "recovery_creation_dttm": (entry_dt + timedelta(days=30)) if entry_dt else None,
        "recovery_reg_dt": (entry_dt + timedelta(days=35)) if entry_dt else None,
        "recovery_ccy_amt": reimb,
        "recovery_local_ccy_amt": reimb,
        "recovery_rub_amt": reimb,
        "recovery_comment_txt": "Частичное возмещение по инциденту" if reimb else "",
        "recovery_user_num": "01234567",
    }


def _build_fin_impact_record(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Запись финансового последствия (прямая потеря)."""
    loss = float(raw.get("financial_loss") or 0)
    entry_dt = _parse_date(raw.get("date"))
    return {
        "incdnt_id": idx,
        "fin_impact_id": idx,
        "fin_impact_type_name": "Прямая потеря",
        "fin_impact_kind_name": "Финансовые последствия",
        "fin_impact_monitoring_flag": "N",
        "fin_impact_crncy_code": "RUB",
        "fin_impact_local_crncy_code": "RUB",
        "fin_impact_detection_dt": entry_dt,
        "fin_impact_creation_dttm": entry_dt,
        "fin_impact_reg_dt": (entry_dt + timedelta(days=1)) if entry_dt else None,
        "fin_impact_account_num": f"40817{idx:06d}",
        "fin_impact_docum_num": f"FI-DOC-{idx:06d}",
        "fi_busn_area_id": f"BA-{idx % 100:03d}",
        "fi_org_struct_id": f"OS-{idx % 200:03d}",
        "fin_impact_ccy_amt": loss * 0.7 if loss else 0,
        "fin_impact_local_ccy_amt": loss * 0.7 if loss else 0,
        "fin_impact_rub_amt": loss * 0.7 if loss else 0,
    }


def _build_nonfin_record(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Запись нефинансового последствия."""
    return {
        "incdnt_id": idx,
        "nonfin_impact_sid": f"NFI-TEST-{idx:06d}",
        "nonfin_impact_kind_name": "Репутационный риск",
        "nonfin_impact_influence_class_name": "Среднее влияние",
        "nonfin_impact_name": "Репутационный риск",
        "nonfin_impact_comment": (raw.get("consequences") or "")[:200],
        "nonfin_impact_creation_dttm": _parse_date(raw.get("date")),
    }


# ----------------------------------------------------------------------------
# CREATE TABLE IF NOT EXISTS для всех 5 таблиц.
# ----------------------------------------------------------------------------
_SCHEMA_DDL: dict[str, str] = {
    "t_db_oarb_ior_d6_base_of_knowledge_ior": """
        CREATE TABLE IF NOT EXISTS public.{tbl} (
            incdnt_id                    BIGINT PRIMARY KEY,
            incdnt_sid                   VARCHAR(32) NOT NULL UNIQUE,
            incdnt_agr_num               VARCHAR(64),
            incdnt_agr_sid               VARCHAR(64),
            incdnt_appl_num              VARCHAR(64),
            incdnt_status_name           VARCHAR(64),
            incdnt_status_code           VARCHAR(16),
            incdnt_autoreg_flag          VARCHAR(2),
            incdnt_detection_person_name VARCHAR(256),
            incdnt_mistake_cnt           INTEGER,
            incdnt_entry_dt              TIMESTAMP,
            incdnt_detection_dt          TIMESTAMP,
            incdnt_start_dt              TIMESTAMP,
            incdnt_first_validated_dttm  TIMESTAMP,
            incdnt_last_validate_dttm    TIMESTAMP,
            incdnt_summary_descr_txt     TEXT,
            incdnt_full_descr_txt        TEXT,
            risk_profile_id              VARCHAR(64),
            risk_profile_name            VARCHAR(256),
            incdnt_security_risk_flag    VARCHAR(2),
            incdnt_infrmtn_sys_risk_flag VARCHAR(2),
            incdnt_behavior_risk_flag    VARCHAR(2),
            incdnt_model_risk_flag       VARCHAR(2),
            incdnt_type_lvl_1_name       VARCHAR(256),
            incdnt_type_lvl_2_name       VARCHAR(256),
            incdnt_source_name           VARCHAR(256),
            src_type_lvl_1_name          VARCHAR(256),
            src_type_lvl_2_name          VARCHAR(256),
            org_struct_id                VARCHAR(64),
            org_struct_lvl_2_name        VARCHAR(256),
            org_struct_lvl_3_name        VARCHAR(256),
            org_struct_lvl_4_name        VARCHAR(256),
            org_struct_lvl_5_name        VARCHAR(256),
            org_struct_lvl_6_name        VARCHAR(256),
            org_struct_lvl_7_name        VARCHAR(256),
            org_struct_lvl_8_name        VARCHAR(256),
            org_struct_lvl_9_name        VARCHAR(256),
            org_struct_lvl_10_name       VARCHAR(256),
            funct_block_id               VARCHAR(64),
            funct_block_lvl_2_name       VARCHAR(256),
            funct_block_lvl_3_name       VARCHAR(256),
            funct_block_lvl_4_name       VARCHAR(256),
            process_lvl_1_name           VARCHAR(256),
            process_lvl_2_name           VARCHAR(256),
            process_lvl_3_name           VARCHAR(256),
            process_lvl_4_name           VARCHAR(256),
            incdnt_client_type_name      VARCHAR(32),
            incdnt_sum                   NUMERIC(28,4),
            incdnt_drct_dmg_sum          NUMERIC(28,4),
            incdnt_drct_dmg_cred_rub_amt NUMERIC(28,4),
            incdnt_drct_dmg_noncred_rub_amt NUMERIC(28,4),
            incdnt_indrct_dmg_sum        NUMERIC(28,4),
            incdnt_indrct_dmg_cred_rub_amt NUMERIC(28,4),
            incdnt_indrct_dmg_noncred_rub_amt NUMERIC(28,4),
            incdnt_gain_sum              NUMERIC(28,4),
            incdnt_gain_cred_rub_amt     NUMERIC(28,4),
            incdnt_gain_noncred_rub_amt  NUMERIC(28,4),
            incdnt_thrd_prt_sum          NUMERIC(28,4),
            incdnt_thrd_prt_cred_rub_amt NUMERIC(28,4),
            incdnt_thrd_prt_noncred_rub_amt NUMERIC(28,4),
            incdnt_unrlzd_dmg_sum        NUMERIC(28,4),
            incdnt_unrlzd_dmg_cred_rub_amt NUMERIC(28,4),
            incdnt_unrlzd_dmg_noncred_rub_amt NUMERIC(28,4),
            recovery_rub_amt_aggr        NUMERIC(28,4),
            incdnt_security_risk_flag    VARCHAR(2),
            incdnt_infrmtn_sys_risk_flag VARCHAR(2)
        );
    """,

    "t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng": """
        CREATE TABLE IF NOT EXISTS public.{tbl} (
            incdnt_id              BIGINT NOT NULL,
            incdnt_status_name     VARCHAR(64),
            incdnt_status_code     VARCHAR(16),
            stts_chng_action_code  VARCHAR(32),
            stts_chng_action_name  VARCHAR(256),
            stts_chng_comment_txt  TEXT,
            stts_chng_action_dttm  TIMESTAMP,
            stts_chng_user_num     VARCHAR(32)
        );
        CREATE INDEX IF NOT EXISTS idx_{tbl}_id ON public.{tbl} (incdnt_id);
    """,

    "t_db_oarb_ior_d6_base_of_knowledge_incident_recovery": """
        CREATE TABLE IF NOT EXISTS public.{tbl} (
            incdnt_id              BIGINT NOT NULL,
            recovery_id            BIGINT,
            recovery_sid           VARCHAR(64),
            recovery_type_name     VARCHAR(64),
            recovery_crncy_code    VARCHAR(16),
            recovery_local_crncy_code VARCHAR(16),
            recovery_src_account_num VARCHAR(64),
            recovery_doc_num       VARCHAR(64),
            recovery_creation_dttm TIMESTAMP,
            recovery_reg_dt        TIMESTAMP,
            recovery_ccy_amt       NUMERIC(28,4),
            recovery_local_ccy_amt NUMERIC(28,4),
            recovery_rub_amt       NUMERIC(28,4),
            recovery_comment_txt   TEXT,
            recovery_user_num      VARCHAR(32)
        );
        CREATE INDEX IF NOT EXISTS idx_{tbl}_id ON public.{tbl} (incdnt_id);
    """,

    "t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact": """
        CREATE TABLE IF NOT EXISTS public.{tbl} (
            incdnt_id                  BIGINT NOT NULL,
            fin_impact_id              BIGINT,
            fin_impact_sid             VARCHAR(64),
            fin_impact_type_name       VARCHAR(64),
            fin_impact_kind_name       VARCHAR(256),
            fin_impact_monitoring_flag VARCHAR(2),
            fin_impact_crncy_code      VARCHAR(16),
            fin_impact_local_crncy_code VARCHAR(16),
            fin_impact_detection_dt    TIMESTAMP,
            fin_impact_creation_dttm   TIMESTAMP,
            fin_impact_reg_dt          TIMESTAMP,
            fin_impact_account_num     VARCHAR(64),
            fin_impact_docum_num       VARCHAR(64),
            fi_busn_area_id            VARCHAR(64),
            fi_org_struct_id           VARCHAR(64),
            fin_impact_ccy_amt         NUMERIC(28,4),
            fin_impact_local_ccy_amt   NUMERIC(28,4),
            fin_impact_rub_amt         NUMERIC(28,4)
        );
        CREATE INDEX IF NOT EXISTS idx_{tbl}_id ON public.{tbl} (incdnt_id);
    """,

    "t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact": """
        CREATE TABLE IF NOT EXISTS public.{tbl} (
            incdnt_id                       BIGINT NOT NULL,
            nonfin_impact_sid               VARCHAR(64),
            nonfin_impact_kind_name         VARCHAR(256),
            nonfin_impact_influence_class_name VARCHAR(256),
            nonfin_impact_name              VARCHAR(256),
            nonfin_impact_comment           TEXT,
            nonfin_impact_creation_dttm     TIMESTAMP
        );
        CREATE INDEX IF NOT EXISTS idx_{tbl}_id ON public.{tbl} (incdnt_id);
    """,
}


def ensure_schema(dsn: str, schema: str = "public") -> dict[str, bool]:
    """Создать таблицы, если их нет. Возвращает dict {tbl: created_now}."""
    import psycopg2
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    cur = conn.cursor()
    created: dict[str, bool] = {}
    for tbl in _DEFAULT_TABLES:
        cur.execute(
            """
            SELECT EXISTS (
                SELECT 1 FROM information_schema.tables
                WHERE table_schema = %s AND table_name = %s
            )
            """,
            (schema, tbl),
        )
        exists = cur.fetchone()[0]
        if exists:
            created[tbl] = False
            continue
        ddl = _SCHEMA_DDL[tbl].format(tbl=tbl)
        cur.execute(ddl)
        cur.execute(
            f"CREATE INDEX IF NOT EXISTS {tbl}_status_idx ON public.{tbl} (incdnt_status_name)"
            if tbl == _DEFAULT_TABLES[0]
            else f"SELECT 1"
        )
        # Main ior table also needs entry_dt index for time-range queries.
        if tbl == _DEFAULT_TABLES[0]:
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {tbl}_entry_dt_idx ON public.{tbl} (incdnt_entry_dt)"
            )
            cur.execute(
                f"CREATE INDEX IF NOT EXISTS {tbl}_risk_idx ON public.{tbl} (risk_profile_id)"
            )
        created[tbl] = True
        logger.info("Created table %s.%s", schema, tbl)
    cur.close()
    conn.close()
    return created


def _is_table_populated(dsn: str, schema: str, tbl: str) -> bool:
    """True, если в таблице schema.tbl есть хотя бы одна строка."""
    import psycopg2
    conn = psycopg2.connect(dsn)
    cur = conn.cursor()
    cur.execute(f'SELECT COUNT(*) FROM {schema}."{tbl}"')
    n = cur.fetchone()[0]
    cur.close()
    conn.close()
    return n > 0


def seed_test_data(
    json_path: Path,
    dsn: str,
    *,
    schema: str = "public",
    record_count: int = 1000,
) -> dict[str, int]:
    """Заполнить таблицы тестовыми инцидентами, если они пусты.

    Возвращает {tbl: inserted_count}.
    """
    import psycopg2
    from psycopg2.extras import execute_values

    if not json_path.is_file():
        raise SystemExit(f"Файл тестовых данных не найден: {json_path}")

    records_raw = json.loads(json_path.read_text(encoding="utf-8"))
    if not isinstance(records_raw, list):
        raise SystemExit(
            f"Ожидался список записей в {json_path}, "
            f"получен {type(records_raw).__name__}"
        )

    ior_records: list[dict[str, Any]] = []
    status_records: list[dict[str, Any]] = []
    recovery_records: list[dict[str, Any]] = []
    fin_records: list[dict[str, Any]] = []
    nonfin_records: list[dict[str, Any]] = []
    for idx, raw in enumerate(records_raw[:record_count], start=1):
        if not isinstance(raw, dict):
            continue
        ior_records.append(_build_ior_record(idx, raw))
        status_records.append(_build_status_record(idx, raw))
        fin_records.append(_build_fin_impact_record(idx, raw))
        nonfin_records.append(_build_nonfin_record(idx, raw))
        if float(raw.get("reimbursement") or 0) > 0:
            recovery_records.append(_build_recovery_record(idx, raw))

    conn = psycopg2.connect(dsn)
    try:
        with conn, conn.cursor() as cur:
            inserted: dict[str, int] = {}
            for tbl, recs, fk_field in [
                (_DEFAULT_TABLES[0], ior_records, "incdnt_id"),
                (_DEFAULT_TABLES[1], status_records, "incdnt_id"),
                (_DEFAULT_TABLES[2], recovery_records, "incdnt_id"),
                (_DEFAULT_TABLES[3], fin_records, "incdnt_id"),
                (_DEFAULT_TABLES[4], nonfin_records, "incdnt_id"),
            ]:
                if _is_table_populated(dsn, schema, tbl):
                    logger.info(
                        "Table %s.%s already populated, skip seeding", schema, tbl
                    )
                    continue
                if not recs:
                    continue
                cols = list(recs[0].keys())
                execute_values(
                    cur,
                    f"INSERT INTO {schema}.\"{tbl}\" ({', '.join(cols)}) VALUES %s",
                    [tuple(r[c] for c in cols) for r in recs],
                )
                inserted[tbl] = len(recs)
        conn.commit()
    finally:
        conn.close()
    return inserted


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("workspace/data_store/cache/testing/ior/ior.json"),
        help="Путь к JSON с тестовыми данными (по умолчанию ior.json)",
    )
    parser.add_argument(
        "--schema", default="public",
        help="PostgreSQL schema (по умолчанию public)",
    )
    parser.add_argument(
        "--dsn", default=None,
        help="PostgreSQL DSN (override; иначе $IOR_TEST_DSN или channels.postgres.dsn)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    parser.add_argument(
        "--record-count", type=int, default=1000,
        help="Максимум записей для загрузки (default 1000)",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level), format="%(message)s")

    dsn = _resolve_dsn(args.dsn)

    # Шаг 1: убедиться, что таблицы существуют
    created = ensure_schema(dsn, schema=args.schema)
    for tbl, was_created in created.items():
        if was_created:
            print(f"  Created {args.schema}.{tbl}")

    # Шаг 2: заполнить, если таблица пуста
    inserted = seed_test_data(
        args.json, dsn, schema=args.schema, record_count=args.record_count,
    )
    if inserted:
        for tbl, n in inserted.items():
            print(f"  Inserted {n} rows into {args.schema}.{tbl}")
        total = sum(inserted.values())
        print(f"OK: loaded {total} rows into {args.schema}.t_db_oarb_ior_d6_*")
    else:
        print(
            "OK: все 5 таблиц уже заполнены, ничего не сделано "
            "(идемпотентный seed)."
        )
    return 0


__all__ = [
    "_resolve_dsn",
    "ensure_schema",
    "seed_test_data",
    "main",
]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
