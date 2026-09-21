from __future__ import annotations

import asyncio
import json

from lib.services.skill_runtime_mode import load_testing_module


data_generator = load_testing_module("ior-analyzer", "data_generator")
runner = load_testing_module("ior-analyzer", "runner")


def test_generator_is_deterministic_and_unique():
    first = data_generator.generate_records(42)
    second = data_generator.generate_records(42)
    assert len(first) == 1000
    assert first == second
    assert len({row["eve_id"] for row in first}) == 1000


def test_filters_and_aggregations(tmp_path, monkeypatch):
    rows = data_generator.generate_records(42)
    drp = rows[0]["drp"]
    assert runner._filter(rows, rows[0]["eve_id"]) == [rows[0]]
    assert all(row["drp"] == drp for row in runner._filter(rows, drp))
    exact_date = rows[0]["date"]
    assert all(row["date"] == exact_date for row in runner._filter(rows, exact_date))
    monkeypatch.setattr(runner, "SESSION_ROOT", tmp_path)
    monkeypatch.setattr(runner, "load_records", lambda: rows)
    report = asyncio.run(runner.run_testing_report(
        session_id="postgres:one", user_prompt=f"Сколько ИОР по {drp} и какая сумма потерь?"
    ))
    expected = [row for row in rows if row["drp"] == drp]
    assert f"**{len(expected)}**" in report
    assert f"{sum(row['financial_loss'] for row in expected):,.2f}" in report


def test_sessions_are_isolated(tmp_path, monkeypatch):
    rows = data_generator.generate_records(42)
    monkeypatch.setattr(runner, "SESSION_ROOT", tmp_path)
    monkeypatch.setattr(runner, "load_records", lambda: rows)
    asyncio.run(runner.run_testing_report(session_id="postgres:a", user_prompt=rows[0]["eve_id"]))
    asyncio.run(runner.run_testing_report(session_id="postgres:b", user_prompt=rows[1]["eve_id"]))
    a = json.loads((tmp_path / "postgres_a.json").read_text(encoding="utf-8"))
    b = json.loads((tmp_path / "postgres_b.json").read_text(encoding="utf-8"))
    assert a["selected_ids"] == [rows[0]["eve_id"]]
    assert b["selected_ids"] == [rows[1]["eve_id"]]
