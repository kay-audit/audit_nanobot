from __future__ import annotations

import asyncio
import json

from lib.services.skill_runtime_mode import load_testing_module


data_generator = load_testing_module("appeals-analyzer", "data_generator")
llm_search = load_testing_module("appeals-analyzer", "llm_search")
runner = load_testing_module("appeals-analyzer", "runner")


def test_generator_is_deterministic_short_and_unique():
    first = data_generator.generate_records(77)
    assert len(first) == 100
    assert first == data_generator.generate_records(77)
    assert len({row["appeal_id"] for row in first}) == 100
    assert all(150 <= len(row["text"]) <= 500 for row in first)


def test_filters_and_llm_id_validation():
    rows = data_generator.generate_records(77)
    filtered = runner.apply_filters(rows, {"prd": "Мошенничество", "chnl": "Мобильный банк"})
    assert filtered and all(row["prd"] == "Мошенничество" for row in filtered)
    bounded = runner.apply_filters(rows, {"date_from": "2025-01-01", "date_to": "2025-12-31"})
    assert bounded and all("2025-01-01" <= row["date"] <= "2025-12-31" for row in bounded)
    valid = llm_search.validate_results({"results": [
        {"id": filtered[0]["appeal_id"], "score": .9, "reason": "ok"},
        {"id": "APPEAL-TEST-999", "score": 1, "reason": "invented"},
    ]}, {row["appeal_id"] for row in filtered})
    assert [item["id"] for item in valid] == [filtered[0]["appeal_id"]]


def test_sessions_are_isolated(tmp_path, monkeypatch):
    rows = data_generator.generate_records(77)
    monkeypatch.setattr(runner, "SESSION_ROOT", tmp_path)
    monkeypatch.setattr(runner, "load_records", lambda: rows)
    fake = lambda messages, **kwargs: {"results": [{"id": json.loads(messages[1]["content"])["appeals"][0]["id"], "score": .8, "reason": "match"}]}
    asyncio.run(runner.run_testing_report(session_id="postgres:a", user_prompt="мошенничество", llm=fake))
    asyncio.run(runner.run_testing_report(session_id="postgres:b", user_prompt="образовательный кредит", llm=fake))
    assert (tmp_path / "postgres_a.json").read_text(encoding="utf-8") != (tmp_path / "postgres_b.json").read_text(encoding="utf-8")
