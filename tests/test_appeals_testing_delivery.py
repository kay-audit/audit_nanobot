from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from openpyxl import load_workbook


@pytest.fixture
def testing_runner(monkeypatch, tmp_path):
    from workspace.utils.skill_runtime_mode import load_testing_module

    runner = load_testing_module("appeals-analyzer", "runner")
    monkeypatch.setattr(runner, "SESSION_ROOT", tmp_path / "state")
    monkeypatch.setattr(runner, "__file__", str(
        tmp_path / "workspace/skills/appeals-analyzer/testing/runner.py"))
    rows = [dict(appeal_id=key, date="2026-04-01", prd="Ипотека", s_prd="Платёж",
                 chnl="Офис", text="Исходный текст " + key) for key in ("selected", "other")]
    monkeypatch.setattr(runner, "load_records", Mock(return_value=rows))
    return runner


def test_export_selected_without_state_returns_none(testing_runner):
    assert testing_runner.export_selected("missing") is None
    testing_runner.load_records.assert_not_called()


@pytest.mark.parametrize("failure", ["json", "backend", "write"])
def test_export_selected_propagates_errors(testing_runner, monkeypatch, failure):
    runner = testing_runner
    runner._write_session("session", "test", ["selected"])
    if failure == "json":
        runner._session_path("session").write_text("{", encoding="utf-8")
        expected = json.JSONDecodeError
    elif failure == "backend":
        runner.load_records.side_effect = RuntimeError("backend unavailable")
        expected = RuntimeError
    else:
        import pandas as pd
        monkeypatch.setattr(pd.DataFrame, "to_excel", Mock(side_effect=OSError("write failed")))
        expected = OSError
    with pytest.raises(expected):
        runner.export_selected("session")


@pytest.mark.asyncio
@pytest.mark.parametrize("outcome", ["xlsx", "no_result", "export_error", "message_error"])
async def test_testing_delivery_never_loads_production(testing_runner, monkeypatch, tmp_path, outcome):
    from nanobot.agent.tools.context import RequestContext, current_request_context, request_context
    from nanobot.agent.tools.message import MessageTool
    from lib.utils.verbatim_report import verified_source
    from workspace.tools import appeals_analyzer as native

    monkeypatch.setenv("APPEALS_ANALYZER_RUNTIME", "testing")
    ctx = RequestContext(channel="postgres", chat_id="chat", session_key="postgres:testing",
                         message_id="m1", sender_id="alice", original_user_text="test",
                         metadata={"origin_message_id": "m1", "answer_id": "a1"})
    report = "Полный отчёт\n" + "\n".join(["Источник и доказательства."] * 600)
    if outcome != "no_result":
        testing_runner._write_session(ctx.session_key, "test", ["selected"])
    if outcome == "export_error":
        testing_runner._session_path(ctx.session_key).write_text("{", encoding="utf-8")
    runner = SimpleNamespace(run_testing_report=AsyncMock(return_value=report),
                             export_selected=Mock(wraps=testing_runner.export_selected))
    load = Mock(return_value=runner)
    production = Mock(side_effect=AssertionError("production loaded"))
    imports = Mock(side_effect=AssertionError("production module imported"))
    monkeypatch.setattr(native, "load_testing_module", load)
    monkeypatch.setattr(native.AppealsAnalyzerTool, "_load_runner", production)
    monkeypatch.setattr(native, "import_module", imports)
    outbound = []

    async def send(message):
        outbound.append(message)
        if outcome == "message_error":
            raise RuntimeError("delivery failed")

    tool = native.AppealsAnalyzerTool(config=native.AppealsAnalyzerToolConfig(),
        message_tool=MessageTool(send_callback=send, workspace=tmp_path))
    original_metadata = dict(ctx.metadata)
    with request_context(ctx):
        result = await tool.execute(prompt="rewritten", session_id="forged")
        assert current_request_context() is ctx
        assert ctx.metadata == original_metadata
    production.assert_not_called()
    imports.assert_not_called()
    load.assert_called_once_with("appeals-analyzer", "runner")
    runner.run_testing_report.assert_awaited_once_with(session_id=ctx.session_key, user_prompt="test")
    runner.export_selected.assert_called_once_with(ctx.session_key)
    if outcome in {"export_error", "message_error"}:
        assert result.is_error
        assert "Не удалось выполнить анализ обращений" in str(result)
    else:
        assert result == report
    assert len(outbound) == (1 if outcome in {"xlsx", "message_error"} else 0)
    if outbound:
        message = outbound[0]
        assert message.content == report
        assert verified_source(message, message.metadata) == "appeals_analyzer"
        assert len(message.media) == 1
        path = Path(message.media[0])
        assert path.suffix == ".xlsx"
        assert path.parent.name == "results"
        workbook = load_workbook(path, read_only=True)
        try:
            rows = list(workbook.active.values)
        finally:
            workbook.close()
        assert rows[0] == ("appeal_id", "date", "prd", "s_prd", "chnl", "text")
        assert len(rows) == 2 and rows[1][0] == "selected"
