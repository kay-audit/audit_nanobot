"""Тесты postgres_fixture (dev-режим follow_up, V008__fu_poruch_data.sql).

Покрывает:
  * ключ `poruch_key` совпадает с backend.storage.gp.poruch_key();
  * `fetch_view_rows()` возвращает [{km_id, doc_reg_num, ..., poruch_key, row_hash}, ...];
  * выбор `_data_source()` по db_env_mode (gp / postgres / auto / fixture);
  * `_use_gp()` остаётся неизменным — GP-путь не сломан.

Не ходит в сеть / БД: мокает psycopg2.connect или _dsn().
"""
from __future__ import annotations

import sys
from pathlib import Path

# follow_up/backend/* живёт в skills/follow_up/backend — добавляем в sys.path
SKILL_ROOT = Path(__file__).resolve().parent.parent / "workspace/skills/follow_up"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))
if str(SKILL_ROOT / "backend") not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT / "backend"))

from unittest.mock import patch


def _fixture_rows():
    return [
        {"km_id": "KM-10121", "doc_reg_num": "DOC-001-2026",
         "problem": "p1", "assignment_": "a1",
         "poruch_status": "в работе", "close_fact": None,
         "actions": "x", "block_unit": "u1"},
        {"km_id": "KM-10121", "doc_reg_num": "DOC-002-2026",
         "problem": "p2", "assignment_": "a2",
         "poruch_status": "исполнено", "close_fact": "2026-06-30",
         "actions": "y", "block_unit": "u2"},
    ]


def test_poruch_key_matches_gp_formula():
    """Ключ строки должен совпадать с GP-формулой для одинаковых входов."""
    from backend.storage import gp as gp_mod
    from backend.storage import postgres_fixture as pg_mod

    for r in _fixture_rows():
        expected = gp_mod.poruch_key(r["km_id"], r.get("doc_reg_num"),
                                     r.get("assignment_"))
        actual = pg_mod.poruch_key(r["km_id"], r.get("doc_reg_num"),
                                   r.get("assignment_"))
        assert expected == actual, f"key mismatch for {r}"


def test_poruch_row_hash_matches_gp_formula():
    """row_hash должен совпадать с GP-формулой (используется sync'ом)."""
    from backend.storage import gp as gp_mod
    from backend.storage import postgres_fixture as pg_mod

    for r in _fixture_rows():
        expected = gp_mod.poruch_row_hash(r)
        actual = pg_mod.poruch_row_hash(r)
        assert expected == actual, f"row_hash mismatch for {r}"


def test_fetch_view_rows_shapes_rows():
    """fetch_view_rows() декорирует строки ключами (как GP PoruchRepo)."""
    from backend.storage import postgres_fixture as pg_mod

    fake_psycopg2_rows = [
        ("KM-10121", "DOC-001-2026", "p1", "a1", "в работе", None, "x", "u1"),
    ]
    fake_description = [("km_id",), ("doc_reg_num",), ("problem",),
                        ("assignment_",), ("poruch_status",), ("close_fact",),
                        ("actions",), ("block_unit",)]

    class _FakeCursor:
        description = fake_description
        def execute(self, sql, params=None): pass
        def fetchall(self): return fake_psycopg2_rows
        def __enter__(self): return self
        def __exit__(self, *a): return False

    class _FakeConn:
        def __enter__(self): return self
        def __exit__(self, *a): return False
        def cursor(self): return _FakeCursor()

    class _FakePsycopg2:
        def connect(self, dsn):
            return _FakeConn()

    # функция делает `import psycopg2` внутри — патчим через sys.modules
    with patch.object(pg_mod, "_dsn", return_value="postgresql://stub"), \
         patch.dict(sys.modules, {"psycopg2": _FakePsycopg2()}):
        rows = pg_mod.fetch_view_rows()

    assert len(rows) == 1
    r = rows[0]
    assert set(r.keys()) >= {"km_id", "doc_reg_num", "problem",
                              "assignment_", "poruch_status", "close_fact",
                              "actions", "block_unit", "poruch_key",
                              "row_hash"}
    assert r["km_id"] == "KM-10121"
    assert r["doc_reg_num"] == "DOC-001-2026"
    # Ключи посчитаны из текста колонок
    assert r["poruch_key"]
    assert r["row_hash"]


def test_fetch_view_rows_returns_empty_without_dsn():
    from backend.storage import postgres_fixture as pg_mod
    with patch.object(pg_mod, "_dsn", return_value=None):
        assert pg_mod.fetch_view_rows() == []
    with patch.object(pg_mod, "_dsn", return_value="postgresql://stub"):
        # если DSN есть но psycopg2 недоступен — пусто (warning в логе)
        with patch.dict(sys.modules, {"psycopg2": None}):
            assert pg_mod.fetch_view_rows() == []


def test_data_source_resolution():
    """_data_source() выбирает по db_env_mode + доступности."""
    from backend.config import get_settings
    from backend.agents.execution_control import _data_source
    from backend.storage import postgres_fixture as pg_mod

    def _reset(mode: str, gp: bool, pg: bool):
        s = get_settings()
        s.db_env_mode = mode
        with patch("backend.agents.execution_control._use_gp",
                   return_value=gp), \
             patch.object(pg_mod, "pg_enabled", return_value=pg):
            return _data_source()

    # gp: режим и GP доступен → gp
    assert _reset("gp", gp=True, pg=True) == "gp"
    # gp: режим GP выключен → fallback на fixture
    assert _reset("gp", gp=False, pg=True) == "fixture"
    # postgres: режим и PG доступен → postgres
    assert _reset("postgres", gp=False, pg=True) == "postgres"
    # postgres: PG недоступен → fallback на fixture
    assert _reset("postgres", gp=False, pg=False) == "fixture"
    # auto: GP предпочитаем
    assert _reset("auto", gp=True, pg=True) == "gp"
    # auto: только PG → postgres
    assert _reset("auto", gp=False, pg=True) == "postgres"
    # auto: ничего нет → fixture
    assert _reset("auto", gp=False, pg=False) == "fixture"


def test_use_gp_signature_unchanged():
    """_use_gp() возвращает bool (контракт для остального кода не сломан)."""
    from backend.agents.execution_control import _use_gp
    assert isinstance(_use_gp(), bool)


def test_settings_db_env_mode_is_optional():
    """Поле db_env_mode есть в Settings и принимает одно из 3 значений."""
    from backend.config import Settings
    s = Settings()
    assert s.db_env_mode in ("gp", "postgres", "auto")
    # Дефолт 'auto' зависит от того, переопределил ли .secrets.env.
    # Сам факт наличия поля и литеральный тип — инвариант.