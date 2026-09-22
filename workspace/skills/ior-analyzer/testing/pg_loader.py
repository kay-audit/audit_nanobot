"""Загрузка тестовых данных d6_nanobot в PostgreSQL.

Дополняет ``data_generator.py``: тот генерит JSON в
``workspace/data_store/cache/testing/ior/ior.json`` (детерминированный
кэш, используется skill runner'ом), этот скрипт заливает тот же JSON
в таблицу ``test_d6.ior_events`` (для SQL-flow: skill читает напрямую
из PostgreSQL через ``workspace.utils.db``).

Идемпотентность: ``TRUNCATE`` + bulk INSERT в одной транзакции. Можно
запускать много раз подряд — финальное состояние таблицы детерминировано
через seed data_generator'а.

DSN — через переменную окружения ``IOR_TEST_DSN`` (явная override)
или через ``workspace.utils.db.resolve_dsn()`` (fallback на
``channels.postgres.dsn`` из project.json).

Команда из feature.yaml::

    python workspace/skills/ior-analyzer/testing/pg_loader.py \
        --json workspace/data_store/cache/testing/ior/ior.json \
        --schema test_d6 \
        --table ior_events
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def _resolve_dsn(explicit: str | None) -> str:
    """DSN в порядке приоритета: --dsn, $IOR_TEST_DSN, channels.postgres.dsn."""
    if explicit:
        return explicit
    env_dsn = os.environ.get("IOR_TEST_DSN")
    if env_dsn:
        return env_dsn
    # Fallback на runtime-конфиг через workspace.utils.db
    try:
        from workspace.utils.db import resolve_dsn as _runtime_dsn
        return _runtime_dsn()
    except Exception as exc:  # noqa: BLE001 - startup error path
        raise SystemExit(
            f"Не удалось разрешить DSN: {exc}. "
            "Укажите --dsn или переменную IOR_TEST_DSN."
        ) from exc


def _normalize_record(raw: dict[str, Any]) -> dict[str, Any]:
    """Преобразовать JSON-запись data_generator'а в dict для psycopg2.

    data_generator пишет ``date`` как ISO-строку (``"2024-05-12"``);
    PostgreSQL ожидает ``date`` тип, psycopg2 принимает ``datetime.date``.
    Все остальные поля — passthrough с type-coerce для числовых.
    """
    from datetime import date

    out: dict[str, Any] = {}
    out["eve_id"] = raw.get("eve_id", "")
    out["drp"] = raw.get("drp", "")
    # ``date`` → ``datetime.date`` для psycopg2
    raw_date = raw.get("date")
    if isinstance(raw_date, str):
        try:
            out["event_date"] = date.fromisoformat(raw_date)
        except ValueError:
            logger.warning("Некорректная дата %r для eve_id=%s — пропуск",
                           raw_date, out["eve_id"])
            out["event_date"] = None
    elif isinstance(raw_date, date):
        out["event_date"] = raw_date
    else:
        out["event_date"] = None
    out["event_type"] = raw.get("event_type", "")
    out["category"] = raw.get("category", "")
    out["description"] = raw.get("description", "")
    out["status"] = raw.get("status", "")
    out["financial_loss"] = float(raw.get("financial_loss", 0) or 0)
    out["reimbursement"] = float(raw.get("reimbursement", 0) or 0)
    out["business_line"] = raw.get("business_line", "")
    out["product"] = raw.get("product", "")
    out["channel"] = raw.get("channel", "")
    out["cause"] = raw.get("cause", "")
    out["consequences"] = raw.get("consequences", "")
    return out


def load_to_postgres(
    json_path: Path,
    dsn: str,
    *,
    schema: str = "test_d6",
    table: str = "ior_events",
    truncate: bool = True,
) -> int:
    """Загрузить записи из JSON в PostgreSQL.

    Returns: количество загруженных строк.
    Raises: SystemExit с понятным сообщением при ошибках подключения/DDL.
    """
    if not json_path.is_file():
        raise SystemExit(f"Файл тестовых данных не найден: {json_path}")

    try:
        records_raw = json.loads(json_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"Некорректный JSON в {json_path}: {exc}") from exc

    if not isinstance(records_raw, list):
        raise SystemExit(
            f"Ожидался список записей в {json_path}, "
            f"получен {type(records_raw).__name__}"
        )

    records = [_normalize_record(r) for r in records_raw if isinstance(r, dict)]
    if not records:
        logger.warning("Нет валидных записей для загрузки (пустой JSON)")
        return 0

    full_table = f"{schema}.{table}"

    # Workspace utils: configure(dsn) → execute(...). Импортируем лениво —
    # чтобы --help работал без psycopg2 на хосте.
    from workspace.utils.db import configure, execute, transaction

    try:
        configure(dsn)
    except Exception as exc:  # noqa: BLE001 - startup error path
        raise SystemExit(f"Не удалось подключиться к PostgreSQL: {exc}") from exc

    insert_sql = f"""
        INSERT INTO {full_table} (
            eve_id, drp, event_date, event_type, category, description,
            status, financial_loss, reimbursement, business_line, product,
            channel, cause, consequences
        ) VALUES (
            %(eve_id)s, %(drp)s, %(event_date)s, %(event_type)s, %(category)s,
            %(description)s, %(status)s, %(financial_loss)s, %(reimbursement)s,
            %(business_line)s, %(product)s, %(channel)s, %(cause)s,
            %(consequences)s
        )
        ON CONFLICT (eve_id) DO UPDATE SET
            drp = EXCLUDED.drp,
            event_date = EXCLUDED.event_date,
            event_type = EXCLUDED.event_type,
            category = EXCLUDED.category,
            description = EXCLUDED.description,
            status = EXCLUDED.status,
            financial_loss = EXCLUDED.financial_loss,
            reimbursement = EXCLUDED.reimbursement,
            business_line = EXCLUDED.business_line,
            product = EXCLUDED.product,
            channel = EXCLUDED.channel,
            cause = EXCLUDED.cause,
            consequences = EXCLUDED.consequences;
    """.strip()

    try:
        with transaction() as conn:
            if truncate:
                conn.execute(f"TRUNCATE TABLE {full_table}")
            for record in records:
                conn.execute(insert_sql, record)
    except Exception as exc:  # noqa: BLE001 - DML error path
        raise SystemExit(f"Не удалось загрузить данные в {full_table}: {exc}") from exc

    logger.info("Загружено %d записей в %s", len(records), full_table)
    return len(records)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument(
        "--json",
        type=Path,
        default=Path("workspace/data_store/cache/testing/ior/ior.json"),
        help="Путь к JSON с тестовыми данными (по умолчанию ior.json)",
    )
    parser.add_argument(
        "--schema", default="test_d6",
        help="PostgreSQL schema (по умолчанию test_d6)",
    )
    parser.add_argument(
        "--table", default="ior_events",
        help="Имя таблицы (по умолчанию ior_events)",
    )
    parser.add_argument(
        "--dsn", default=None,
        help="PostgreSQL DSN (override; иначе $IOR_TEST_DSN или channels.postgres.dsn)",
    )
    parser.add_argument(
        "--no-truncate", action="store_true",
        help="Не очищать таблицу перед загрузкой (по умолчанию truncate=true)",
    )
    parser.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = parser.parse_args(argv)

    logging.basicConfig(level=getattr(logging, args.log_level))
    dsn = _resolve_dsn(args.dsn)
    count = load_to_postgres(
        args.json,
        dsn,
        schema=args.schema,
        table=args.table,
        truncate=not args.no_truncate,
    )
    print(f"OK: loaded {count} records into {args.schema}.{args.table}")
    return 0


__all__ = ["load_to_postgres", "main"]


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
