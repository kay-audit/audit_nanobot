"""Trusted user input and session-scoped clarification; no live SQL or LLM."""
from importlib import import_module
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from workspace.tools.ior_analyzer import IORAnalyzerTool, IORAnalyzerToolConfig


@pytest.fixture
def native_input(monkeypatch, tmp_path):
    # Load the actual deterministic planner/SQL builder via the native entrypoint.
    actual = IORAnalyzerTool._load_runner()
    reports = import_module(actual.__module__)
    from utils.resolve.request_plan import build_plan
    from utils.resolve.request_outcome import ClarificationRequired
    import workspace.utils.session_key as session_keys
    real_safe = session_keys.safe_session_key
    monkeypatch.setattr(session_keys, "safe_session_key", lambda key: str(tmp_path / real_safe(key)))
    calls, plans, queries = [], [], []

    async def runner(**kwargs):
        calls.append(kwargs)
        try:
            plan = build_plan(kwargs["preset_name"], kwargs["user_prompt"], trusted_preset=kwargs["trusted_preset"])
        except ClarificationRequired as outcome:
            kwargs["on_clarification"](outcome)
            return str(outcome)
        plans.append(plan)
        queries.append(reports.build_dynamic_sql_from_prompt(kwargs["user_prompt"], preset_name=plan.preset))
        return "готовый отчёт"

    monkeypatch.setattr(IORAnalyzerTool, "_load_runner", staticmethod(lambda: runner))
    return SimpleNamespace(tool=IORAnalyzerTool(config=IORAnalyzerToolConfig()), calls=calls,
                           plans=plans, queries=queries, reports=reports)


def ctx(text, *, session="postgres:A", sender="user-A", turn="turn-1"):
    return RequestContext(channel="postgres", chat_id="A", session_key=session,
                          original_user_text=text, sender_id=sender, turn_id=turn, message_id=turn)


@pytest.mark.asyncio
@pytest.mark.parametrize("original,rewritten,preset", [
    ("ИОРы СРБ за Q3 2026", "Финансовые последствия за Q3 2026", "ior_hypothesis"),
    ("Финансовые последствия за март 2026", "Общие ИОР за март", "financial_consequences_ior"),
    ("Возмещения по ИОР", "Общий анализ ИОР", "vozmeshenie_ior"),
    ("ИОР по DRP-10121", "ИОР по DRP-10122", "ior_hypothesis"),
    ("Досье EVE-12345 за Q3 2026", "Досье EVE-54321 за Q4 2025", "report_period_specific_ior"),
])
async def test_original_wins_over_llm_text_and_preset(native_input, original, rewritten, preset):
    with request_context(ctx(original)):
        assert await native_input.tool.execute(prompt=rewritten, preset="financial_consequences_ior") == "готовый отчёт"
    assert native_input.calls[-1]["user_prompt"] == original
    assert native_input.calls[-1]["preset_name"] is None
    assert native_input.plans[-1].preset == preset
    if "DRP-10121" in original:
        assert "DRP-10121" in native_input.queries[-1] and "DRP-10122" not in native_input.queries[-1]
    if "EVE-12345" in original:
        assert "EVE-12345" in native_input.queries[-1] and "EVE-54321" not in native_input.queries[-1]


@pytest.mark.asyncio
async def test_wrong_year_clarification_then_confirmed_sql_survives_tool_recreation(native_input):
    original = "Выгрузи ИОР за 3 квартал 2826 года по СРБ"
    with request_context(ctx(original)):
        result = await native_input.tool.execute(prompt="Финансовые последствия за Q3 2026")
    assert "Уточните год" in result
    assert not native_input.queries
    # A new tool instance (and restored conversation) still has its scoped pending file.
    tool = IORAnalyzerTool(config=IORAnalyzerToolConfig())
    with request_context(ctx("2026", turn="turn-2")):
        assert await tool.execute(prompt="Произвольный новый запрос от LLM") == "готовый отчёт"
    assert native_input.calls[-1]["user_prompt"] == original + "\nУточнение: 2026"
    plan = native_input.plans[-1]
    assert plan.preset == "ior_hypothesis"
    assert plan.period.intervals == [("2026-07-01", "2026-10-01")]
    sql = native_input.queries[-1]
    assert "2026-07-01" in sql and "2026-10-01" in sql and "2826" not in sql
    with request_context(ctx("2026", turn="turn-3")):
        assert (await tool.execute(prompt="ИОР за 2026 год")).is_error
    assert len(native_input.queries) == 1


@pytest.mark.asyncio
async def test_pending_clarifications_do_not_cross_sessions_or_senders(native_input):
    with request_context(ctx("ИОРы за Q3 2826")):
        await native_input.tool.execute(prompt="ИОРы за Q3 2026")
    for context in (ctx("2026", session="postgres:B"), ctx("2026", sender="user-B")):
        with request_context(context):
            assert (await native_input.tool.execute(prompt="ИОРы за Q3 2026", session_id="postgres:A")).is_error
    assert len(native_input.calls) == 1 and not native_input.queries


@pytest.mark.asyncio
async def test_full_new_request_never_reuses_old_pending_intent(native_input):
    with request_context(ctx("Финансовые последствия за Q3 2826")):
        await native_input.tool.execute(prompt="bad")
    original = "Возмещения по ИОР за март 2026"
    with request_context(ctx(original, turn="turn-2")):
        assert await native_input.tool.execute(prompt="Финансовые последствия за Q3 2026") == "готовый отчёт"
    assert native_input.calls[-1]["user_prompt"] == original
    assert native_input.plans[-1].preset == "vozmeshenie_ior"


@pytest.mark.asyncio
async def test_year_then_organization_clarification_and_same_turn_retry(native_input, monkeypatch):
    from utils.resolve import semantic_grounding as grounding
    monkeypatch.setattr(grounding, "load_catalog", lambda: {
        grounding.ORIGIN: ["Риски"], grounding.RESPONSIBILITY: ["Риски"]})
    original = "ИОРы за Q3 2826 по блоку Риски"
    with request_context(ctx(original)):
        assert "Уточните год" in await native_input.tool.execute(prompt="forged")
    with request_context(ctx("2026", turn="turn-2")):
        first = await native_input.tool.execute(prompt="forged period and department")
        assert "По блоку" in first
        assert await native_input.tool.execute(prompt="forged second attempt") == first
    assert native_input.calls[-1]["user_prompt"] == original + "\nУточнение: 2026"
    with request_context(ctx("место происхождения", turn="turn-3")):
        assert await native_input.tool.execute(prompt="зона ответственности Финансы") == "готовый отчёт"
    assert native_input.calls[-1]["user_prompt"] == original + "\nУточнение: 2026\nУточнение: место происхождения"
    assert native_input.plans[-1].period.intervals == [("2026-07-01", "2026-10-01")]
    assert {hit["category"] for hit in native_input.plans[-1].hits} == {"origin"}


@pytest.mark.asyncio
async def test_no_context_preserves_direct_call_contract(native_input, monkeypatch):
    # No callback or context-specific options are injected into trusted direct calls.
    runner = AsyncMock(return_value="direct")
    monkeypatch.setattr(IORAnalyzerTool, "_load_runner", staticmethod(lambda: runner))
    assert await native_input.tool.execute(prompt="ИОР за Q3 2026", preset="ior_hypothesis", session_id="cli-A") == "direct"
    runner.assert_awaited_once_with(trusted_preset=False, preset_name="ior_hypothesis",
                                   session_id="cli-A", user_prompt="ИОР за Q3 2026")


@pytest.mark.asyncio
async def test_missing_original_in_context_fails_closed(native_input):
    with request_context(ctx(None)):
        assert (await native_input.tool.execute(prompt="ИОР за 2026 год")).is_error
    assert not native_input.calls


@pytest.mark.asyncio
async def test_structured_analysis_is_forwarded_without_agent_preset(native_input, monkeypatch):
    runner = AsyncMock(return_value="structured")
    monkeypatch.setattr(IORAnalyzerTool, "_load_runner", staticmethod(lambda: runner))
    original = "Анализ ИОР\n\nПериод:\n3 квартал 2026\n\nЗапрос:\nИсходный текст"
    with request_context(ctx(original)):
        assert await native_input.tool.execute(prompt="Потери за другой период", preset="financial_consequences_ior") == "structured"
    assert runner.call_args.kwargs["user_prompt"] == original
    assert runner.call_args.kwargs["preset_name"] is None


@pytest.mark.asyncio
async def test_actual_report_notifies_clarification_before_touching_store(native_input, monkeypatch):
    from utils.resolve.request_outcome import ClarificationRequired
    callback = Mock()
    store = Mock(side_effect=AssertionError("No SQL before clarification"))
    monkeypatch.setattr(native_input.reports, "get_data_store", store)
    result = await native_input.reports.run_ior_report(None, "callback-test", "ИОРы за Q3 2826", on_clarification=callback)
    assert "Уточните год" in result
    assert isinstance(callback.call_args.args[0], ClarificationRequired)
    store.assert_not_called()


@pytest.mark.asyncio
async def test_question_about_previous_report_does_not_execute_new_sql(native_input, monkeypatch):
    monkeypatch.setattr(native_input.reports, "get_session_extract", lambda session: None)
    store = Mock(side_effect=AssertionError("Must not query SQL for a report question"))
    monkeypatch.setattr(native_input.reports, "get_data_store", store)
    result = await native_input.reports.run_ior_report(None, "past-report", "Почему в прошлой выгрузке так мало ИОР?")
    assert "ещё нет выгрузки" in result
    store.assert_not_called()


@pytest.mark.parametrize("text,expected", [
    ("ИОР за Q3 2826\nУточнение: 2026", [("2026-07-01", "2026-10-01")]),
    ("ИОР за Q3\nУточнение: 2026", [("2026-07-01", "2026-10-01")]),
    ("ИОР за Q3 2826\nУточнение: 2026\nУточнение: место происхождения", [("2026-07-01", "2026-10-01")]),
    ("ИОР за 01.03.2026 — 31.03.2026", [("2026-03-01", "2026-04-01")]),
])
def test_confirmed_period_does_not_reparse_conflicting_years(native_input, text, expected):
    from utils.resolve.period_parser import parse_period
    assert parse_period(text).intervals == expected


@pytest.mark.asyncio
async def test_real_postgres_turn_context_uses_current_message_after_restore(tmp_path):
    from nanobot.agent.loop import AgentLoop
    from nanobot.bus.events import InboundMessage
    from nanobot.session.manager import SessionManager
    loop = object.__new__(AgentLoop)
    loop.sessions = SessionManager(tmp_path)
    session = loop.sessions.get_or_create("postgres:A")
    session.add_message("user", "ИОРы за прошлый год")
    loop.sessions.save(session)
    loop.sessions = SessionManager(tmp_path)  # Restore from disk, without cached context.
    loop.tools = SimpleNamespace(tool_names=[])
    loop.workspace_scopes = SimpleNamespace(
        persist_message_scope=lambda *args: None,
        for_turn=lambda **kwargs: SimpleNamespace(project_path=tmp_path))
    loop._remember_session_route = Mock()
    captured = []
    async def stage(turn, name, handler):
        if name == "restore":
            await AgentLoop._restore_turn(loop, turn)
            request = AgentLoop._request_context_for_turn(loop, turn)
            captured.append(request)
            raise StopAsyncIteration
    loop._run_turn_stage = stage
    original = "Выгрузи информацию по ИОР СРБ за 3 квартал 2826 года"
    delivery = SimpleNamespace(session_key="postgres:A", streaming=False, events=None,
        started=AsyncMock(), route=SimpleNamespace(channel="postgres", chat_id="A"))
    with pytest.raises(StopAsyncIteration):
        await AgentLoop._process_message(loop, InboundMessage(channel="postgres", sender_id="user-A",
            chat_id="A", content=original, metadata={"message_id": "new-message"}), delivery=delivery)
    assert captured[0].original_user_text == original
    assert captured[0].session_key == "postgres:A"
    assert captured[0].message_id == "new-message"
