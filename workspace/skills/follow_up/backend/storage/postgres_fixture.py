"""PostgreSQL-адаптер витрины поручений для dev-режима follow_up.

DB_ENV__MODE=postgres: чтение из public.t_fu_poruch_data (см. миграцию
sql/migrations/V008__fu_poruch_data.sql). Источник один и тот же, что
fixture.json, но в таблице — можно менять SQL'ем без перезапуска навыка.

Контракт fetch_view_rows() повторяет backend.storage.gp.PoruchRepo.fetch_view_rows():
    [{km_id, doc_reg_num, problem, assignment_, poruch_status,
      close_fact, actions, block_unit, poruch_key, row_hash}, ...]

DSN берётся из того же канала, что и bot postgres-channel —
config.CHANNELS_POSTGRES_DSN. Соединение — короткое, на каждый запрос,
без пула: витрина маленькая, миграция в stub-режиме.
"""
from __future__ import annotations

import hashlib
import logging
import os
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)


def poruch_key(km_id: str, doc_reg_num: Optional[str], assignment: Optional[str]) -> str:
    """Совпадает с backend.storage.gp.poruch_key() — общая формула для GP и PG."""
    a_hash = hashlib.md5((assignment or "").encode("utf-8")).hexdigest()
    raw = f"{km_id or ''}|{doc_reg_num or ''}|{a_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def poruch_row_hash(row: Dict) -> str:
    """Совпадает с backend.storage.gp.poruch_row_hash() — дельта-детекция."""
    parts = [
        row.get("problem") or "",
        row.get("assignment_") or "",
        row.get("actions") or "",
        row.get("poruch_status") or "",
        str(row.get("close_fact") or ""),
        row.get("block_unit") or "",
    ]
    return hashlib.md5("|".join(parts).encode("utf-8")).hexdigest()


_VIEW_COLS = ("km_id, doc_reg_num, problem, assignment_, "
              "poruch_status, close_fact, actions, block_unit")


def _dsn() -> Optional[str]:
    """DSN из env (CHANNELS_POSTGRES_DSN, DATABASE_URL) или None."""
    for k in ("CHANNELS_POSTGRES_DSN", "DATABASE_URL"):
        v = os.environ.get(k)
        if v:
            return v
    return None


def pg_enabled() -> bool:
    """Доступен ли PG для fixture-режима. Без DSN — False."""
    return _dsn() is not None


def fetch_view_rows() -> List[Dict]:
    """Все строки витрины из public.t_fu_poruch_data.

    При отсутствии таблицы — возвращает [] (резолвер даст status='empty'
    и пользователь увидит «реестр недоступен»).
    """
    dsn = _dsn()
    if not dsn:
        logger.warning("[PG-Fix] DSN не задан — fetch_view_rows() = []")
        return []
    try:
        import psycopg2
        import psycopg2.extras
    except ImportError:
        logger.warning("[PG-Fix] psycopg2 не установлен — fetch_view_rows() = []")
        return []

    try:
        with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
            cur.execute(
                f"SELECT {_VIEW_COLS} FROM public.t_fu_poruch_data "
                f"ORDER BY km_id, doc_reg_num")
            cols = [d[0] for d in cur.description]
            rows = [dict(zip(cols, r)) for r in cur.fetchall()]
    except Exception as e:
        logger.warning(f"[PG-Fix] Не удалось прочитать public.t_fu_poruch_data: {e}")
        return []

    for r in rows:
        r["poruch_key"] = poruch_key(r["km_id"], r.get("doc_reg_num"), r.get("assignment_"))
        r["row_hash"] = poruch_row_hash(r)
    return rows