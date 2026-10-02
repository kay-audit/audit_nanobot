"""Загрузка workspace/skills/follow_up/data/fixtures/poruch_fixture.json в public.t_fu_poruch_data.

Идемпотентный: при повторном запуске обновляет существующие строки
(по poruch_key) и вставляет отсутствующие. Удаления — нет, только
явный --truncate.

Зачем: dev-режим DB_ENV__MODE=postgres читает из этой таблицы.
Изменения — прямым SQL/CLI; этот скрипт — bootstrap + восстановление
согласованной fixture.json ↔ таблица.

Применяется вручную:
    python tools/seed_follow_up_data.py                # грузит/обновляет fixture
    python tools/seed_follow_up_data.py --truncate    # очистить, потом загрузить
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import psycopg2


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "workspace/skills/follow_up/data/fixtures/poruch_fixture.json"


def _poruch_key(row: dict) -> str:
    """Совпадает с backend.storage.gp.poruch_key().

    MD5(MD5(assignment_)) в составе — чтобы совпадать с прод-формулой.
    Это означает: одна и та же формула ключа для GP и Postgres-режима.
    """
    a_hash = hashlib.md5((row.get("assignment_") or "").encode("utf-8")).hexdigest()
    raw = f"{row.get('km_id') or ''}|{row.get('doc_reg_num') or ''}|{a_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _conn_dsn() -> str:
    """Резолвит DSN из .secrets.env (как бот)."""
    sys.path.insert(0, str(ROOT))
    import config as botcfg
    botcfg._initialize_settings("test")
    flat = botcfg._flatten_env(botcfg._load_secrets_override())
    dsn = (flat.get("CHANNELS_POSTGRES_DSN")
           or flat.get("channels_postgres_dsn")
           or flat.get("DATABASE_URL")
           or flat.get("database_url"))
    if not dsn:
        raise SystemExit("PG-источник не задан: CHANNELS__POSTGRES__DSN / DATABASE__URL в .secrets.env")
    return dsn


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--truncate", action="store_true",
                    help="TRUNCATE public.t_fu_poruch_data перед загрузкой")
    ap.add_argument("--fixture", default=str(FIXTURE),
                    help="Путь к fixture.json (по умолчанию — рабочая фикстура)")
    args = ap.parse_args()

    if not Path(args.fixture).exists():
        raise SystemExit(f"fixture не найден: {args.fixture}")
    rows = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    print(f"fixture: {args.fixture} ({len(rows)} строк)")

    dsn = _conn_dsn()
    print(f"DSN: {dsn}")

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        if args.truncate:
            cur.execute("TRUNCATE public.t_fu_poruch_data")
            print("TRUNCATE: OK")

        upserted = 0
        for r in rows:
            key = _poruch_key(r)
            cur.execute(
                "INSERT INTO public.t_fu_poruch_data "
                "(km_id, doc_reg_num, problem, assignment_, poruch_status, "
                " close_fact, actions, block_unit, poruch_key, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (poruch_key) DO UPDATE SET "
                "km_id=EXCLUDED.km_id, doc_reg_num=EXCLUDED.doc_reg_num, "
                "problem=EXCLUDED.problem, assignment_=EXCLUDED.assignment_, "
                "poruch_status=EXCLUDED.poruch_status, close_fact=EXCLUDED.close_fact, "
                "actions=EXCLUDED.actions, block_unit=EXCLUDED.block_unit, "
                "updated_at=now()",
                (r["km_id"], r.get("doc_reg_num"), r["problem"],
                 r["assignment_"], r["poruch_status"], r.get("close_fact"),
                 r.get("actions"), r.get("block_unit"), key))
            upserted += 1
        conn.commit()
        cur.execute("SELECT count(*) FROM public.t_fu_poruch_data")
        total = cur.fetchone()[0]
        print(f"upserted: {upserted}, всего в таблице: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())