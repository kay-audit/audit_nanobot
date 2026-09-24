"""Загрузка тестовых данных d6_nanobot в production-таблицы IOR.

Дополняет ``pg_loader.py`` (который заливает в ``test_d6.ior_events``
минимальный набор колонок для smoke-тестов): этот скрипт берёт тот же
JSON из ``data_generator.py`` и заливает его в таблицы с production-
именами (``public.t_db_oarb_ior_d6_*``) под нашу DEV-схему.

Зачем нужен отдельный скрипт:
  * ``data_generator`` генерирует короткие поля (``eve_id``/``drp``/``date``),
    а таблица ожидает production-имена (``incdnt_sid``/``risk_profile_id``/
    ``incdnt_entry_dt``). Маппинг — здесь.
  * Production-таблица содержит 5 связанных таблиц (ior, stts_chng,
    recovery, fin_impact, nonfin_impact) — скрипт заполняет основную
    (``t_db_oarb_ior_d6_base_of_knowledge_ior``) и создаёт по одной
    записи в каждой связанной для демонстрации JOIN-возможностей.

Идемпотентность: ``TRUNCATE ... RESTART IDENTITY CASCADE`` + bulk INSERT
в одной транзакции. Можно запускать много раз подряд.

DSN — через ``$IOR_TEST_DSN``, ``--dsn`` или fallback в ``project.json``.

Команда::

    python workspace/skills/ior-analyzer/testing/pg_loader_real.py \
        --json workspace/data_store/cache/testing/ior/ior.json

Это **standalone-скрипт**: использует ``psycopg2`` напрямую, а не
``workspace.utils.db``, потому что последний содержит относительные
импорты (``from utils.clean_text``), которые не работают без
``pip install -e .``.
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


_DEFAULT_TABLES = (
    "t_db_oarb_ior_d6_base_of_knowledge_ior",
    "t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng",
    "t_db_oarb_ior_d6_base_of_knowledge_incident_recovery",
    "t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact",
    "t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact",
)


def _resolve_dsn(explicit: str | None) -> str:
    """DSN в порядке приоритета: --dsn, $IOR_TEST_DSN, channels.postgres.dsn."""
    if explicit:
        return explicit
    env_dsn = os.environ.get("IOR_TEST_DSN")
    if env_dsn:
        return env_dsn
    project_json = Path(__file__).resolve().parents[3] / "project.json"
    if project_json.is_file():
        text = project_json.read_text(encoding="utf-8")
        stripped = re.sub(r"//[^\n]*", "", text)
        stripped = re.sub(r"/\*.*?\*/", "", stripped, flags=re.DOTALL)
        try:
            data = json.loads(stripped)
            dsn = (((data or {}).get("channels") or {}).get("postgres") or {}).get("dsn")
            if dsn:
                return dsn
        except json.JSONDecodeError:
            pass
    raise SystemExit(
        "Не удалось разрешить DSN. "
        "Укажите --dsn или переменную IOR_TEST_DSN, "
        "или channels.postgres.dsn в project.json."
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


def _map_category_to_risk(raw: str) -> tuple[str, str]:
    """data_generator.category → (risk_profile_id, risk_profile_name)."""
    cat = (raw or "").strip()
    mapping = {
        "Операционный процесс": ("DRP-OP", "Операционный процесс"),
        "ИТ-системы": ("DRP-IT", "Сбой информационной системы"),
        "Человеческий фактор": ("DRP-HF", "Ошибка сотрудника"),
        "Внешнее мошенничество": ("DRP-EM", "Внешнее мошенничество"),
        "Недоступность сервиса": ("DRP-NS", "Недоступность сервиса"),
        "Регуляторные риски": ("DRP-RR", "Регуляторный риск"),
        "Процедурные нарушения": ("DRP-PN", "Процедурное нарушение"),
        "Конфликт интересов": ("DRP-CI", "Конфликт интересов"),
        "Утечка данных": ("DRP-DL", "Утечка данных"),
        "Неавторизованный доступ": ("DRP-UA", "Неавторизованный доступ"),
    }
    return mapping.get(cat, ("DRP-OP", "Операционный процесс"))


def _build_ior_record(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Маппинг JSON data_generator'а в production-колонки t_db_oarb_ior_d6_base_of_knowledge_ior."""
    eve_id = raw.get("eve_id") or f"EVE-TEST-{idx:04d}"
    drp = raw.get("drp") or f"DRP-TEST-{idx:03d}"
    entry_dt = _parse_date(raw.get("date"))
    risk_id, risk_name = _map_category_to_risk(raw.get("category"))
    status = _map_status(raw.get("status"))
    loss = float(raw.get("financial_loss") or 0)
    reimb = float(raw.get("reimbursement") or 0)
    return {
        "incdnt_id": idx,
        "incdnt_sid": eve_id,
        "incdnt_status_name": status,
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
        "incdnt_type_lvl_1_name": "Действия персонала",
        "incdnt_type_lvl_2_name": raw.get("event_type") or "Операционный процесс",
        "incdnt_source_name": "Реестровое уведомление",
        "src_type_lvl_1_name": "Действия персонала",
        "src_type_lvl_2_name": "Непреднамеренные ошибки сотрудников",
        "org_struct_id": drp.replace("DRP-", "SBR_"),
        "org_struct_lvl_2_name": "ПАО Сбербанк (ЦА)",
        "org_struct_lvl_3_name": raw.get("business_line") or "Блок «Розничный бизнес»",
        "funct_block_id": drp.replace("DRP-", "FB-"),
        "funct_block_lvl_2_name": "Блок «Розничный бизнес»",
        "funct_block_lvl_3_name": raw.get("business_line") or "Розничный бизнес",
        "process_lvl_1_name": "ПАО Сбербанк",
        "process_lvl_2_name": "Розничный бизнес",
        "process_lvl_3_name": raw.get("business_line") or "Розничный бизнес",
        "process_lvl_4_name": raw.get("product") or "Вклад",
        "incdnt_client_type_name": "ФЛ",
        "incdnt_sum": loss,
        "incdnt_drct_dmg_sum": loss * 0.7 if loss else 0,
        "incdnt_indrct_dmg_sum": loss * 0.3 if loss else 0,
        "incdnt_gain_sum": 0,
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
        "recovery_rub_amt": reimb,
        "recovery_creation_dttm": (entry_dt + timedelta(days=30)) if entry_dt else None,
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
        "fin_impact_sid": f"FI-TEST-{idx:06d}",
        "fin_impact_rub_amt": loss * 0.7 if loss else 0,
        "fin_impact_creation_dttm": entry_dt,
        "fin_impact_account_num": f"40817{idx:06d}",
        "fin_impact_docum_num": f"DOC-{idx:06d}",
    }


def _build_nonfin_record(idx: int, raw: dict[str, Any]) -> dict[str, Any]:
    """Запись нефинансового последствия."""
    return {
        "incdnt_id": idx,
        "nonfin_impact_sid": f"NFI-TEST-{idx:06d}",
        "nonfin_impact_name": "Репутационный риск",
        "nonfin_impact_comment": (raw.get("consequences") or "")[:200],
        "nonfin_impact_creation_dttm": _parse_date(raw.get("date")),
    }


def load_real_tables(
    json_path: Path,
    dsn: str,
    *,
    schema: str = "public",
    truncate: bool = True,
) -> int:
    """Загрузить записи из JSON в production-таблицы IOR.

    Returns: количество загруженных строк в основной таблице.
    """
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

    for idx, raw in enumerate(records_raw, start=1):
        if not isinstance(raw, dict):
            continue
        ior_records.append(_build_ior_record(idx, raw))
        status_records.append(_build_status_record(idx, raw))
        fin_records.append(_build_fin_impact_record(idx, raw))
        nonfin_records.append(_build_nonfin_record(idx, raw))
        if float(raw.get("reimbursement") or 0) > 0:
            recovery_records.append(_build_recovery_record(idx, raw))

    try:
        import psycopg2  # noqa: F401
        from psycopg2.extras import execute_values
    except ImportError as exc:
        raise SystemExit(
            f"psycopg2 не установлен: {exc}. "
            "Установите: pip install psycopg2-binary"
        ) from exc

    try:
        conn = psycopg2.connect(dsn)
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Не удалось подключиться к PostgreSQL: {exc}") from exc

    try:
        with conn:
            with conn.cursor() as cur:
                if truncate:
                    for t in _DEFAULT_TABLES:
                        cur.execute(f"TRUNCATE TABLE {schema}.{t} RESTART IDENTITY CASCADE")

                # Main ior table
                cols_ior = list(ior_records[0].keys())
                t_ior = f"{schema}.t_db_oarb_ior_d6_base_of_knowledge_ior"
                execute_values(
                    cur,
                    f"INSERT INTO {t_ior} ({', '.join(cols_ior)}) VALUES %s",
                    [tuple(r[c] for c in cols_ior) for r in ior_records],
                )

                # Status history
                cols_st = list(status_records[0].keys())
                t_st = f"{schema}.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng"
                execute_values(
                    cur,
                    f"INSERT INTO {t_st} ({', '.join(cols_st)}) VALUES %s",
                    [tuple(r[c] for c in cols_st) for r in status_records],
                )

                # Recovery
                if recovery_records:
                    cols_rec = list(recovery_records[0].keys())
                    t_rec = f"{schema}.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery"
                    execute_values(
                        cur,
                        f"INSERT INTO {t_rec} ({', '.join(cols_rec)}) VALUES %s",
                        [tuple(r[c] for c in cols_rec) for r in recovery_records],
                    )

                # Financial impact
                cols_fi = list(fin_records[0].keys())
                t_fi = f"{schema}.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact"
                execute_values(
                    cur,
                    f"INSERT INTO {t_fi} ({', '.join(cols_fi)}) VALUES %s",
                    [tuple(r[c] for c in cols_fi) for r in fin_records],
                )

                # Non-financial impact
                cols_nf = list(nonfin_records[0].keys())
                t_nf = f"{schema}.t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact"
                execute_values(
                    cur,
                    f"INSERT INTO {t_nf} ({', '.join(cols_nf)}) VALUES %s",
                    [tuple(r[c] for c in cols_nf) for r in nonfin_records],
                )
    except Exception as exc:  # noqa: BLE001
        raise SystemExit(f"Не удалось загрузить данные в {schema}.t_db_oarb_ior_d6_*: {exc}") from exc
    finally:
        conn.close()

    logger.info(
        "Загружено %d инцидентов + %d статусов + %d возмещений + "
        "%d fin + %d non-fin в %s.t_db_oarb_ior_d6_*",
        len(ior_records), len(status_records), len(recovery_records),
        len(fin_records), len(nonfin_records), schema,
    )
    return len(ior_records)


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
        "--no-truncate", action="store_true",
        help="Не очищать таблицы перед загрузкой (по умолчанию truncate=true)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level))
    dsn = _resolve_dsn(args.dsn)
    count = load_real_tables(
        args.json, dsn, schema=args.schema, truncate=not args.no_truncate,
    )
    print(f"OK: loaded {count} records into {args.schema}.t_db_oarb_ior_d6_*")
    return 0


__all__ = ["load_real_tables", "main"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
