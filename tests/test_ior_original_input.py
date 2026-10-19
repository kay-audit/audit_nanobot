"""Trusted user input and session-scoped clarification; no live SQL or LLM."""
from importlib import import_module
import asyncio
import json
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
                           plans=plans, queries=queries, reports=reports, root=tmp_path)


def ctx(text, *, session="postgres:A", sender="user-A", turn="turn-1"):
    return RequestContext(channel="postgres", chat_id="A", session_key=session,
                          original_user_text=text, sender_id=sender, turn_id=turn, message_id=turn)


@pytest.fixture
def followup_runtime(native_input, monkeypatch):
    """Real native tool/report/search/client; only OSIRIS transport and Qwen are fake."""
    import pandas as pd
    from utils import bge_search_engine as search
    from utils import session_extract_manager as sessions
    frame = pd.DataFrame({"incdnt_sid": ["EVE-1"], "incdnt_full_descr_txt": ["Мошенничество"]})
    extract = sessions.set_session_extract("postgres:A", frame)
    monkeypatch.setitem(search._SMALL_FAISS_SESSION_CACHE, "postgres:A",
                        {"version": extract["version"], "ids": ["EVE-1"]})
    state = SimpleNamespace(failure=None, failure_kind="search", empty=False,
                            remote_index_error=False, remote_version=None)
    def dispatch(session, kind, query, payload):
        assert session == "postgres:A" and query == "В каких из этих ИОР есть информация о мошенничестве?"
        if state.failure is not None and kind == state.failure_kind:
            raise state.failure
        if state.remote_index_error:
            return {"version": payload["version"], "error": "session_index_unavailable", "items": []}
        if kind == "search":
            assert payload["allowed_ids"] == ["EVE-1"]
            items = [] if state.empty else [{"id": "EVE-1", "text": "Мошенничество", "score": .9}]
            return {"version": state.remote_version or payload["version"], "items": items}
        assert kind == "rerank"
        return {"version": payload["version"], "items": [{"id": "EVE-1", "score": .9}]}
    request = Mock(side_effect=dispatch)
    monkeypatch.setattr(search.osiris_client, "request", request)
    monkeypatch.setattr(IORAnalyzerTool, "_load_runner", staticmethod(lambda: native_input.reports.run_ior_report))
    answer = Mock(return_value="Найдены ИОР: EVE-1")
    monkeypatch.setattr(native_input.reports, "answer_follow_up_with_qwen", answer)
    monkeypatch.setattr(native_input.reports, "get_data_store", Mock(side_effect=AssertionError("No global SQL")))
    yield SimpleNamespace(tool=native_input.tool, state=state, request=request, answer=answer,
                          search=search, sessions=sessions, extract=extract)
    sessions.clear_session_extract("postgres:A")


FOLLOWUP = "В каких из этих ИОР есть информация о мошенничестве?"


@pytest.mark.asyncio
@pytest.mark.parametrize("empty", [False, True])
async def test_followup_distinguishes_matches_from_successful_empty_search(followup_runtime, empty):
    runtime = followup_runtime
    runtime.state.empty = empty
    with request_context(ctx(FOLLOWUP)):
        result = await runtime.tool.execute(prompt="rewritten")
    assert not getattr(result, "is_error", False)
    if empty:
        assert result == "По вашему запросу в выбранных ИОР совпадений не найдено."
        runtime.answer.assert_not_called()
    else:
        assert result == "Найдены ИОР: EVE-1"
        runtime.answer.assert_called_once()
    assert runtime.request.call_count == (1 if empty else 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure_kind", ["search", "rerank"])
@pytest.mark.parametrize("failure", ["worker_lost", "timeout"])
async def test_followup_error_is_terminal_in_turn_and_retryable_in_next_turn(followup_runtime, failure, failure_kind):
    from workspace.utils.osiris_runtime.errors import OsirisRequestError, OsirisRequestTimeoutError
    runtime = followup_runtime
    runtime.state.failure = (OsirisRequestTimeoutError("timeout") if failure == "timeout"
                             else OsirisRequestError("Osiris worker lost during request"))
    runtime.state.failure_kind = failure_kind
    with request_context(ctx(FOLLOWUP)):
        first, second = await asyncio.gather(runtime.tool.execute(prompt="rewritten"),
                                            runtime.tool.execute(prompt="another formulation"))
        third = await runtime.tool.execute(prompt="try again")
    assert first is second is third and first.is_error
    assert "OSIRIS сейчас недоступен" in first and "Предыдущая выгрузка сохранена" in first
    assert "совпадений не найдено" not in first
    attempts = 1 if failure_kind == "search" else 2
    assert runtime.request.call_count == attempts
    assert runtime.sessions.get_session_extract("postgres:A") is runtime.extract
    assert runtime.search._SMALL_FAISS_SESSION_CACHE["postgres:A"]["version"] == runtime.extract["version"]
    runtime.answer.assert_not_called()
    runtime.state.failure = None
    with request_context(ctx(FOLLOWUP, turn="turn-2")):
        assert await runtime.tool.execute(prompt="rewritten") == "Найдены ИОР: EVE-1"
    assert runtime.request.call_count == attempts + 2


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["missing", "stale", "worker_lost_index", "worker_stale_index"])
async def test_followup_index_error_is_distinct_and_does_not_drop_extract(followup_runtime, kind):
    runtime = followup_runtime
    if kind == "missing":
        runtime.search.invalidate_small_index("postgres:A")
    elif kind == "stale":
        runtime.search._SMALL_FAISS_SESSION_CACHE["postgres:A"]["version"] = "old"
    elif kind == "worker_lost_index":
        runtime.state.remote_index_error = True
    else:
        runtime.state.remote_version = "old"
    with request_context(ctx(FOLLOWUP)):
        first = await runtime.tool.execute(prompt="rewritten")
        assert await runtime.tool.execute(prompt="retry") is first
    assert first.is_error and "Поисковый индекс предыдущей выгрузки недоступен" in first
    assert "совпадений не найдено" not in first
    assert runtime.request.call_count == (0 if kind in {"missing", "stale"} else 1)
    assert runtime.sessions.get_session_extract("postgres:A") is runtime.extract
    runtime.answer.assert_not_called()


@pytest.mark.asyncio
async def test_followup_error_identity_uses_turn_fallback_and_sender(followup_runtime):
    runtime = followup_runtime
    runtime.state.failure = TimeoutError("timeout")
    def turn_context(sender):
        return RequestContext(channel="postgres", chat_id="A", session_key="postgres:A",
            original_user_text=FOLLOWUP, sender_id=sender, turn_id="trusted-turn")
    with request_context(turn_context("user-A")):
        first = await runtime.tool.execute(prompt="rewritten")
        assert await runtime.tool.execute(prompt="retry", session_id="forged") is first
    assert first.is_error and runtime.request.call_count == 1
    with request_context(turn_context("user-B")):
        assert (await runtime.tool.execute(prompt="rewritten")).is_error
    assert runtime.request.call_count == 2


@pytest.mark.parametrize("reply,question,expected", [
    ("Выгрузи финансовые последствия за март 2026", "Уточните год", "new"),
    ("за Q3 2026", "Место происхождения или зона ответственности?", "ambiguous"),
    ("за Q3 2026", "Уточните период и год", "answer"),
    ("ИОР по месту происхождения", "Место происхождения или зона ответственности?", "answer"),
])
def test_pending_answer_requires_its_expected_context(reply, question, expected):
    from workspace.tools.ior_analyzer import _pending_input_kind
    assert _pending_input_kind(reply, question) == expected


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
        assert "Вы хотите получить ИОРы" in first
        assert await native_input.tool.execute(prompt="forged second attempt") == first
    assert native_input.calls[-1]["user_prompt"] == original + "\nУточнение: 2026"
    with request_context(ctx("ИОР по месту происхождения", turn="turn-3")):
        assert await native_input.tool.execute(prompt="зона ответственности Финансы") == "готовый отчёт"
    assert native_input.calls[-1]["user_prompt"] == original + "\nУточнение: 2026\nУточнение: ИОР по месту происхождения"
    assert native_input.plans[-1].period.intervals == [("2026-07-01", "2026-10-01")]
    assert {hit["category"] for hit in native_input.plans[-1].hits} == {"origin"}


@pytest.mark.asyncio
async def test_ambiguous_ior_reply_retains_pending_conditions(native_input):
    with request_context(ctx("ИОРы за Q3 2826")):
        await native_input.tool.execute(prompt="forged")
    path = next(native_input.root.rglob("ior_clarification_*.json"))
    before = path.read_bytes()
    with request_context(ctx("ИОР", turn="turn-2")):
        assert "новый запрос" in await native_input.tool.execute(prompt="Выгрузи новые ИОР за 2026")
    assert path.read_bytes() == before
    assert len(native_input.calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("broken", ["expired", "missing_timestamp", "malformed", "wrong_owner"])
async def test_stale_or_corrupt_state_never_executes_old_request(native_input, broken):
    with request_context(ctx("ИОРы за Q3 2826")):
        await native_input.tool.execute(prompt="forged")
    path = next(native_input.root.rglob("ior_clarification_*.json"))
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["created_at"] > 0
    if broken == "expired":
        data["created_at"] -= 3601
    elif broken == "missing_timestamp":
        data.pop("created_at")
    elif broken == "wrong_owner":
        data["owner"] = "another-user"
    path.write_text("{invalid" if broken == "malformed" else json.dumps(data), encoding="utf-8")
    with request_context(ctx("2026", turn="turn-2")):
        result = await IORAnalyzerTool(config=IORAnalyzerToolConfig()).execute(prompt="ИОР за 2026")
    assert result.is_error
    assert not native_input.queries and len(native_input.calls) == 1
    with request_context(ctx("Выгрузи ИОР за Q3 2026", turn="turn-3")):
        assert await native_input.tool.execute(prompt="forged") == "готовый отчёт"
    assert not path.exists()


@pytest.mark.asyncio
async def test_technical_error_preserves_pending_for_same_turn_retry(native_input, monkeypatch, caplog):
    with request_context(ctx("ИОРы за Q3 2826")):
        await native_input.tool.execute(prompt="forged")
    path = next(native_input.root.rglob("ior_clarification_*.json"))
    before = path.read_bytes()
    runner = IORAnalyzerTool._load_runner()
    failed = AsyncMock(side_effect=RuntimeError("PRIVATE ORIGINAL TEXT"))
    monkeypatch.setattr(IORAnalyzerTool, "_load_runner", staticmethod(lambda: failed))
    with request_context(ctx("2026", turn="turn-2")):
        assert (await native_input.tool.execute(prompt="forged")).is_error
        assert path.read_bytes() == before
        monkeypatch.setattr(IORAnalyzerTool, "_load_runner", staticmethod(lambda: runner))
        assert (await native_input.tool.execute(prompt="forged retry")).is_error
    with request_context(ctx("2026", turn="turn-3")):
        assert await native_input.tool.execute(prompt="forged retry") == "готовый отчёт"
    assert not path.exists() and len(native_input.queries) == 1
    assert "PRIVATE ORIGINAL TEXT" not in caplog.text


@pytest.mark.parametrize("text", ["Выгрузи ИОРы за 2826", "Найди ИОРы за третий квартал 2826", "ИОРы за сентябрь 2826", "ИОР за Q3 2826", "ИОР за 2826 год"])
def test_bad_year_in_date_context_requires_confirmation(native_input, text):
    from utils.resolve.period_parser import parse_period
    from utils.resolve.request_outcome import ClarificationRequired
    with pytest.raises(ClarificationRequired, match="год"):
        parse_period(text)


@pytest.mark.parametrize("text", ["ИОР по DRP-2826", "Досье EVE-2826", "Финансовые последствия за 2826 рублей"])
def test_numeric_ids_and_amounts_are_not_years(native_input, text):
    from utils.resolve.period_parser import parse_period
    assert parse_period(text) is None


@pytest.mark.asyncio
async def test_report_count_question_uses_existing_selection_without_semantic_search(native_input, monkeypatch):
    import pandas as pd
    monkeypatch.setattr(native_input.reports, "get_session_extract", lambda session: {"df": pd.DataFrame({"id": [1, 2]})})
    search = Mock(side_effect=AssertionError("No semantic search for report metadata"))
    monkeypatch.setattr(native_input.reports, "search_small_index", search)
    monkeypatch.setattr(native_input.reports, "get_data_store", Mock(side_effect=AssertionError("No SQL")))
    result = await native_input.reports.run_ior_report(None, "report", "Почему в прошлой выгрузке так мало ИОР?")
    assert "2 строк" in result and "нельзя достоверно" in result
    search.assert_not_called()


@pytest.mark.asyncio
async def test_no_context_preserves_direct_call_contract(native_input, monkeypatch):
    # No callback or context-specific options are injected into trusted direct calls.
    runner = AsyncMock(return_value="direct")
    monkeypatch.setattr(IORAnalyzerTool, "_load_runner", staticmethod(lambda: runner))
    assert await native_input.tool.execute(prompt="ИОР за Q3 2026", preset="ior_hypothesis", session_id="cli-A") == "direct"
    runner.assert_awaited_once_with(trusted_preset=False, preset_name="ior_hypothesis",
                                   session_id="cli-A", user_prompt="ИОР за Q3 2026")


@pytest.fixture
def catalog5(native_input, monkeypatch):
    from utils.resolve import semantic_grounding as grounding
    catalog={
        grounding.ORIGIN:['Дивизион Эквайринг','ООО Страхование','Среднерусский банк'],
        grounding.ORIGIN_DETAIL:['Управление Эквайринг','Центр Страхования'],
        grounding.RESPONSIBILITY:['Дивизион Эквайринг','Страхование клиентов'],
        grounding.PROCESS_DIRECTION:['Эквайринг торговых предприятий','Страхование по направлениям'],
        grounding.PROCESS:['П1234 Страхование путешественников','П2608 Страхование имущества',
                           'П7777 Страхование транспорта','П12345 Эквайринг карт','П0001 Эквайринг терминалов']
                          +[f'П{8000+i} Страхование объектов {i}' for i in range(15)],
    }
    loader=grounding.load_catalog
    monkeypatch.setattr(grounding,'load_catalog',lambda:catalog)
    return SimpleNamespace(catalog=catalog,grounding=grounding,native=native_input,loader=loader)


@pytest.mark.parametrize('codes', ['П1234, П2608, П7777','P1234, P2608, P7777'])
def test_explicit_process_codes_are_exact_catalog_values_without_scope_questions(catalog5, codes):
    from utils.resolve.request_plan import build_plan
    plan=build_plan(None,'Финансовые последствия по '+codes+' за Q1 2025 по СРБ и DRP-2026 свыше 2000 рублей')
    values={h['value'] for h in plan.hits if h['column']==catalog5.grounding.PROCESS}
    assert values==set(catalog5.catalog[catalog5.grounding.PROCESS][:3])
    assert not any(h['op']=='like' for h in plan.hits)
    assert plan.period.intervals==[('2025-01-01','2025-04-01')]
    assert plan.preset=='financial_consequences_ior' and plan.money
    predicates=' AND '.join(plan.predicates())
    assert 'П12345' not in predicates and 'DRP-2026' in predicates
    assert len(plan.predicates())==4  # period, process alternatives, DRP, bank


def test_process_code_boundaries_unknown_codes_and_leading_zero(catalog5):
    from utils.resolve.request_plan import build_plan
    from utils.resolve.request_outcome import ClarificationRequired
    for code in ['П0001','P0001','П12345']:
        plan=build_plan(None,'Выведи ИОРы по '+code)
        assert len(plan.hits)==1 and plan.hits[0]['value'].startswith('П'+code[1:]+' ')
    with pytest.raises(ClarificationRequired,match='П9999'):
        build_plan(None,'Выведи ИОРы по П1234, П9999')


@pytest.mark.parametrize('subject,preset',[
    ('ИОРы','ior_hypothesis'),('Утвержденные финансовые последствия','financial_consequences_ior'),
    ('Возмещения','vozmeshenie_ior'),('Удалённые ИОРы','deleted_ior'),
])
def test_existing_pao_status_and_preset_constraints_survive(catalog5, subject, preset):
    from utils.resolve.request_plan import build_plan
    plan=build_plan(None,subject+' по ПАО Сбербанк и П1234 за Q1 2025')
    assert plan.pao and plan.preset==preset and len(plan.hits)==1
    if 'Утвержденные' in subject:
        assert plan.status


@pytest.mark.parametrize('topic',['эквайрингу','Дивизион Эквайринг','страховке'])
def test_catalog_topic_asks_category_before_column(catalog5, topic):
    from utils.resolve.request_plan import build_plan
    from utils.resolve.request_outcome import ClarificationRequired
    with pytest.raises(ClarificationRequired) as error:
        build_plan(None,'Выведи ИОРы по '+topic+' за Q1 2025')
    assert error.value.stage=='category'
    assert str(error.value)=='Вы хотите получить ИОРы по месту, где они произошли, или по подразделению владельца процесса?'
    assert not any(col in str(error.value) for col in catalog5.grounding.FIELDS)


@pytest.mark.asyncio
async def test_multistage_clarification_persists_original_and_includes_all_processes(catalog5):
    native=catalog5.native
    original='Финансовые последствия по страховке за первый квартал 2025 года по СРБ и DRP-2026 свыше 2000 рублей'
    answers=[original,'По подразделению владельца процесса','По конкретным процессам']
    for number,text in enumerate(answers):
        with request_context(ctx(text,turn=f'catalog-{number}')):
            one,two=await asyncio.gather(native.tool.execute(prompt='FORGED'),native.tool.execute(prompt='ANOTHER FORGED'))
            assert one==two
        if number<2:
            assert not native.queries
            state=json.loads(next(native.root.rglob('ior_clarification_*.json')).read_text(encoding='utf-8'))
            assert state['original_prompt']==original
            assert state['stage']==('category' if number==0 else 'field')
            if number==1:
                assert state['category']=='responsibility'
                assert all(word in one for word in ['функциональному блоку','направлению деятельности','конкретным процессам'])
    assert one=='готовый отчёт' and len(native.calls)==3 and len(native.queries)==1
    plan=native.plans[-1]
    insurance=[v for v in catalog5.catalog[catalog5.grounding.PROCESS] if 'Страхование' in v]
    assert len(insurance)>10
    assert {h['value'] for h in plan.hits if h['column']==catalog5.grounding.PROCESS}==set(insurance)
    assert plan.period.intervals==[('2025-01-01','2025-04-01')]
    assert plan.preset=='financial_consequences_ior' and plan.money
    assert native.calls[-1]['user_prompt']==original+'\nУточнение: '+answers[1]+'\nУточнение: '+answers[2]
    assert all(v.upper() in native.queries[-1] for v in insurance)
    assert not list(native.root.rglob('ior_clarification_*.json'))


def test_single_field_new_catalog_values_and_unknown_topic(catalog5):
    from utils.resolve.request_plan import build_plan
    from utils.resolve.request_outcome import ClarificationRequired
    for field in catalog5.grounding.FIELDS:
        catalog5.catalog.clear(); catalog5.catalog[field]=['Архитектура мостов']
        plan=build_plan(None,'ИОРы по архитектуре мостов за 2025 год')
        assert [(h['column'],h['value']) for h in plan.hits]==[(field,'Архитектура мостов')]
    with pytest.raises(ClarificationRequired):
        build_plan(None,'Выведи ИОРы по неизвестному направлению за 2025 год')


def test_only_real_field_choices_and_ambiguous_departments(catalog5):
    from utils.resolve.request_plan import build_plan
    from utils.resolve.request_outcome import ClarificationRequired
    g=catalog5.grounding
    catalog5.catalog.pop(g.PROCESS_DIRECTION)
    with pytest.raises(ClarificationRequired) as error:
        build_plan(None,'ИОРы по страховке. Уточнение: По владельцу процесса')
    assert 'функциональному блоку' in str(error.value) and 'конкретным процессам' in str(error.value)
    assert 'направлению деятельности' not in str(error.value)
    catalog5.catalog.clear(); catalog5.catalog[g.ORIGIN]=['Дирекция архитектуры Север','Дирекция архитектуры Юг']
    with pytest.raises(ClarificationRequired) as error:
        build_plan(None,'ИОРы по архитектуре')
    assert error.value.stage=='object'
    plan=build_plan(None,'ИОРы по архитектуре. Уточнение: Дирекция архитектуры Север')
    assert [hit['value'] for hit in plan.hits]==['Дирекция архитектуры Север']


@pytest.mark.parametrize('category,answer,column',[
    ('По месту, где они произошли','По более крупному организационному подразделению','org_struct_lvl_3_name'),
    ('По месту происхождения','По конкретному подразделению','org_struct_lvl_4_name'),
    ('По владельцу процесса','По функциональному блоку','funct_block_lvl_3_name'),
    ('По подразделению владельца процесса','По дивизиону','process_lvl_3_name'),
    ('По владельцу процесса','По процессам','process_lvl_4_name'),
])
def test_business_answers_select_each_of_five_fields(catalog5, category, answer, column):
    from utils.resolve.request_plan import build_plan
    plan=build_plan(None,'ИОРы по эквайрингу за Q1 2025. Уточнение: '+category+'. Уточнение: '+answer)
    assert {h['column'] for h in plan.hits}=={column}


@pytest.mark.asyncio
async def test_unknown_code_can_be_corrected_without_losing_original_period(catalog5):
    native=catalog5.native
    with request_context(ctx('Выведи ИОРы по П1234, П9999 за Q1 2025',turn='unknown')):
        assert 'П9999' in await native.tool.execute(prompt='FORGED')
    assert not native.queries
    with request_context(ctx('П1234, П2608',turn='corrected')):
        assert await native.tool.execute(prompt='FORGED')=='готовый отчёт'
    assert len(native.queries)==1 and native.plans[-1].period.intervals==[('2025-01-01','2025-04-01')]
    assert len(native.plans[-1].hits)==2


@pytest.mark.asyncio
async def test_wrong_year_with_amount_is_corrected_without_changing_money(catalog5):
    native=catalog5.native
    with request_context(ctx('Финансовые последствия за 2826г. свыше 2000 рублей',turn='year')):
        assert 'Уточните год' in await native.tool.execute(prompt='FORGED')
    state=json.loads(next(native.root.rglob('ior_clarification_*.json')).read_text(encoding='utf-8'))
    assert state['stage']=='period' and state['preset']=='financial_consequences_ior'
    with request_context(ctx('2025',turn='confirmed-year')):
        assert await native.tool.execute(prompt='FORGED')=='готовый отчёт'
    assert native.plans[-1].period.intervals==[('2025-01-01','2026-01-01')]
    assert str(native.plans[-1].money[0].value)=='2000'


@pytest.mark.parametrize('text',['ИОРы от 2024 до 2025г.','ИОРы от 2024 до 2025 года'])
def test_explicit_year_range_does_not_switch_preset_to_money(catalog5, text):
    from utils.resolve.request_plan import build_plan
    plan=build_plan(None,text)
    assert plan.preset=='ior_hypothesis' and not plan.money
    assert plan.period.intervals==[('2024-01-01','2026-01-01')]


def test_unknown_second_filter_is_not_hidden_by_a_known_bank(catalog5):
    from utils.resolve.request_plan import build_plan
    from utils.resolve.request_outcome import ClarificationRequired
    for text in ['ИОРы по СРБ и по несуществующему подразделению',
                 'ИОРы по СРБ и по работе управления']:
        with pytest.raises(ClarificationRequired):
            build_plan(None,text)


def test_catalog_loader_merges_all_five_columns_without_writing_production(catalog5, monkeypatch, tmp_path):
    ground=catalog5.grounding
    skill=tmp_path/'skill'
    path=skill/'utils/schema/kb_value_catalog.json'
    path.parent.mkdir(parents=True)
    columns={f'main.{col}':{'values':[f'fixture-{col}']} for col in ground.FIELDS}
    columns[f'second.{ground.ORIGIN}']={'values':['additional']}
    path.write_text(json.dumps({'columns':columns}),encoding='utf-8')
    monkeypatch.setattr(ground,'__file__',str(skill/'utils/resolve/semantic_grounding.py'))
    loaded=catalog5.loader()
    assert set(loaded)==set(ground.FIELDS)
    assert loaded[ground.ORIGIN]==[f'fixture-{ground.ORIGIN}','additional']
    assert all(loaded[col] for col in ground.FIELDS)


def test_actual_sql_keeps_independent_columns_as_and_and_process_alternatives_as_or(catalog5):
    import pandas as pd
    import duckdb
    reports=catalog5.native.reports
    g=catalog5.grounding
    from utils.data_store import DUCKDB_TABLES
    rows=[]
    for index,code in enumerate(['П1234','П12345','П2608']):
        row={column:None for column in reports.MAIN_REPORT_COLUMNS}
        row.update(incdnt_id=index,incdnt_sid=f'EVE-{index}',incdnt_entry_dt=pd.Timestamp('2025-02-01'),
                   org_struct_lvl_3_name='Альфа',org_struct_lvl_4_name='Омега' if index==0 else 'Другое',
                   process_lvl_4_name=next(v for v in catalog5.catalog[g.PROCESS] if v.startswith(code+' ')))
        rows.append(row)
    with duckdb.connect(':memory:') as connection:
        connection.register(DUCKDB_TABLES['ior'],pd.DataFrame(rows))
        sql=reports.build_dynamic_sql_from_prompt('ИОРы по П1234, П2608 за Q1 2025',tables=DUCKDB_TABLES)
        assert set(connection.execute(sql).df()['incdnt_sid'])=={'EVE-0','EVE-2'}
        catalog5.catalog.clear(); catalog5.catalog.update({g.ORIGIN:['Альфа'],g.ORIGIN_DETAIL:['Омега']})
        sql=reports.build_dynamic_sql_from_prompt('ИОРы по Альфа и по Омега за 2025 год',tables=DUCKDB_TABLES)
        assert connection.execute(sql).df()['incdnt_sid'].tolist()==['EVE-0']


@pytest.mark.asyncio
async def test_new_turn_new_request_and_success_are_not_reexecuted(catalog5):
    native=catalog5.native
    with request_context(ctx('Выведи ИОРы по страховке за Q1 2025',turn='ask')):
        assert 'Вы хотите' in await native.tool.execute(prompt='FORGED')
    for turn in ('new-1','new-2'):
        with request_context(ctx('Выведи ИОРы по П1234 за Q1 2025',turn=turn)):
            first,second=await asyncio.gather(native.tool.execute(prompt='FORGED'),native.tool.execute(prompt='CHANGED'))
            assert first==second=='готовый отчёт'
    assert len(native.queries)==2


@pytest.mark.parametrize('text',[
    'Финансовые последствия свыше 2000 рублей','Финансовые последствия более 2000',
    'ИОРы по DRP-2026','Досье EVE-2025','ИОРы по П2025',
    'Финансовые последствия от 2000 до 3000 рублей','ИОРы с суммой от 2000 до 3000',
    'ИОРы с суммой от 2000',
])
def test_all_period_branches_ignore_amounts_and_ids(native_input, text):
    from utils.resolve.period_parser import parse_period
    assert parse_period(text) is None


@pytest.mark.parametrize('text',[
    'Выгрузи ИОРы за 2826г.','за март 2826','за Q1 2826','за 31.02.2026',
    'за прошлый год с 31.02.2026','за 2025-02-30','за 2826-01-01',
    'за 2026-2025 годы',
])
def test_invalid_years_and_dates_never_drop_period(native_input, text):
    from utils.resolve.period_parser import parse_period
    from utils.resolve.request_outcome import ClarificationRequired
    with pytest.raises(ClarificationRequired):
        parse_period(text)


@pytest.mark.parametrize('text,expected',[
    ('Финансовые последствия за 2025 год свыше 2000 рублей',[('2025-01-01','2026-01-01')]),
    ('ИОРы по DRP-2026 за март 2025 года',[('2025-03-01','2025-04-01')]),
    ('ИОРы за Q1 и Q3 2025 по DRP-2026',[('2025-01-01','2025-04-01'),('2025-07-01','2025-10-01')]),
    ('ИОРы за 2826г. свыше 2000 рублей. Уточнение: 2025',[('2025-01-01','2026-01-01')]),
    ('ИОРы за 2024-2025 годы по DRP-2026',[('2024-01-01','2026-01-01')]),
    ('Финансовые последствия за 2025 год от 2000 до 3000 рублей',[('2025-01-01','2026-01-01')]),
])
def test_period_clarification_changes_only_temporal_year(native_input, text, expected):
    from utils.resolve.period_parser import parse_period
    assert parse_period(text).intervals==expected


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
