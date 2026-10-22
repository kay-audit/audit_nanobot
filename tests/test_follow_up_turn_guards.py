"""Ограждения хода навыка ``follow_up`` (D5), из-за которых агент уходил в круг вызовов.

* несуществующая КМ — отказ до поиска, а не рассказ о чужих проверках;
* уточнение в беседе остаётся в пределах её проверки (``pin``), в том числе
  в заходах критика; критичность находится в любой форме слова;
* отказ навыка и дедлайн хода говорят агенту не повторять вызов;
* уточнение проверки по письму доходит до агента текстом, а карточка
  продолжается по ``check_id``.

Сеть и модель не используются: конвейер навыка подменяется.
"""
from __future__ import annotations

import asyncio
import json
import sys
from types import SimpleNamespace

import pytest

from tests.conftest import REPO_ROOT

pytest.importorskip("sqlalchemy")          # код навыка — со своими зависимостями

# Код навыка тянет torch (эмбеддер). Если в процесс прогона уже загружен faiss,
# первый импорт torch на macOS и Windows роняет интерпретатор целиком — два
# OpenMP в одном процессе (OMP Error #15). Пропуск честнее, чем оборванный
# прогон всех остальных тестов; отдельный запуск этого файла идёт полностью.
if (sys.platform in ("darwin", "win32") and "faiss" in sys.modules
        and "torch" not in sys.modules):
    pytest.skip("torch после faiss в одном процессе на этой ОС роняет прогон; "
                "запустите файл отдельно", allow_module_level=True)

SKILL_ROOT = REPO_ROOT / "workspace" / "skills" / "follow_up"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from backend.core import identity, orchestrator  # noqa: E402
from backend.core.critic import CheckScope  # noqa: E402
from backend.core.evidence import Evidence, Ledger, ToolResult  # noqa: E402
from backend.skill import render  # noqa: E402

KM_A, KM_B, KM_MISSING = "КМ-99-12345", "КМ-99-12346", "КМ-99-99001"


# ── несуществующая КМ ──────────────────────────────────────────────

@pytest.fixture
def reg(monkeypatch):
    def _install(hydrated=(KM_A, KM_B), vitrina=(), degraded=()):
        r = identity.Registry(frozenset(hydrated), frozenset(vitrina), tuple(degraded))
        monkeypatch.setattr(identity, "registry", lambda force=False: r)
        return r
    return _install


def _refusal(kms):
    from backend.api.routes.chat import unknown_km_refusal
    return unknown_km_refusal(kms)


def test_unknown_km_is_refused_without_naming_others(reg):
    reg()
    text = _refusal([KM_MISSING])
    assert KM_MISSING in text and "не подставляю" in text
    assert KM_A not in text and KM_B not in text


def test_known_km_goes_the_usual_way(reg):
    reg()
    assert _refusal([KM_A]) is None
    assert _refusal([]) is None


def test_one_known_km_among_unknown_is_not_refused(reg):
    """«Сравни 12345 и 99001» — ответ про известную, о второй скажет конвейер."""
    reg()
    assert _refusal([KM_A, KM_MISSING]) is None


def test_vitrina_only_km_is_not_refused(reg):
    reg(vitrina=(KM_MISSING,))
    assert _refusal([KM_MISSING]) is None


def test_incomplete_registry_never_refuses(reg):
    """Greenplum лёг — справочник неполон, «такой проверки нет» было бы ложью."""
    reg(degraded=("gp",))
    assert _refusal([KM_MISSING]) is None


def test_empty_corpus_says_so(reg):
    reg(hydrated=())
    assert "корпус актов пока пуст" in _refusal([KM_MISSING])


# ── уточнение в пределах проверки беседы ───────────────────────────

class _Tools:
    """Подмена реестра инструментов ядра: запоминает аргументы и отдаёт
    улики по двум проверкам, как охватный поиск без номера."""

    def __init__(self):
        self.calls = []

    def install(self, monkeypatch):
        from backend.core.tools import registry
        spec = SimpleNamespace(role="enrich")
        schemas = {"search_passages": {"query": {}, "km": {}},
                   "read_document": {"check_id": {"required": True}},
                   "compare_checks": {"ids": {}}}

        def get(name):
            if name not in schemas:
                raise KeyError(name)

            async def fn(cancel=None, **args):
                self.calls.append((name, args))
                return ToolResult(evidence=[
                    Evidence(kind="passage", check_id=KM_A, quote="своя"),
                    Evidence(kind="passage", check_id=KM_B, quote="чужая")])
            return spec, fn

        def validate(name, args):
            out = {}
            for k, rule in schemas[name].items():
                if args.get(k) is not None:
                    out[k] = args[k]
                elif rule.get("required"):
                    raise ValueError(k)
            return out

        monkeypatch.setattr(registry, "get", get)
        monkeypatch.setattr(registry, "validate", validate)
        return self


def _run(plan, pin, scope=None):
    ledger = Ledger()
    scope = scope or CheckScope(mode="corpus_wide")

    async def say(*a, **k):
        return None
    asyncio.run(orchestrator._execute(plan, ledger, scope, None, say, pin=pin))
    return ledger


def test_pin_goes_into_steps_that_take_a_km(monkeypatch):
    tools = _Tools().install(monkeypatch)
    _run([{"tool": "search_passages", "args": {"query": "критичные"}},
          {"tool": "read_document", "args": {}}], pin=KM_A)
    assert tools.calls == [("search_passages", {"query": "критичные", "km": KM_A}),
                           ("read_document", {"check_id": KM_A})]


def test_pin_drops_other_checks_from_results(monkeypatch):
    """Охватная область пропускает всё (`allows` → True) — поэтому сверка
    по самому номеру, а не по области."""
    _Tools().install(monkeypatch)
    ledger = _run([{"tool": "search_passages", "args": {"query": "q"}}], pin=KM_A)
    assert {e.check_id for e in ledger.evidence()} == {KM_A}


def test_family_of_the_pinned_check_stays(monkeypatch):
    _Tools().install(monkeypatch)
    scope = CheckScope(mode="corpus_wide", family_of={KM_A: frozenset({KM_A, KM_B})})
    ledger = _run([{"tool": "search_passages", "args": {"query": "q"}}], pin=KM_A, scope=scope)
    assert {e.check_id for e in ledger.evidence()} == {KM_A, KM_B}


def test_comparison_is_not_narrowed(monkeypatch):
    _Tools().install(monkeypatch)
    ledger = _run([{"tool": "compare_checks", "args": {}}], pin=KM_A)
    assert {e.check_id for e in ledger.evidence()} == {KM_A, KM_B}


def test_without_pin_plan_runs_as_written(monkeypatch):
    tools = _Tools().install(monkeypatch)
    ledger = _run([{"tool": "search_passages", "args": {"query": "q"}},
                   {"tool": "read_document", "args": {}}], pin=None)
    assert tools.calls == [("search_passages", {"query": "q"})]   # read_document без номера пропущен
    assert {e.check_id for e in ledger.evidence()} == {KM_A, KM_B}


# ── отказ навыка не зовёт повторить ────────────────────────────────

def test_failed_envelope_tells_the_agent_not_to_retry():
    out = render.envelope({"ok": False, "reason": "что-то не так"})
    assert out["next"] == render.NO_RETRY_NOTE


def test_successful_envelope_has_no_retry_note():
    out = render.envelope({"ok": True, "answer_md": "ответ"})
    assert "next" not in out


def test_deadline_reason_is_not_an_invitation_to_retry(monkeypatch):
    import backend.api.routes.chat as chat
    from backend.skill import api

    async def fake(*a, **k):
        yield "event: error\ndata: " + json.dumps(
            {"message": "Ответ не собрался за 150 с. Попробуйте повторить позже.",
             "reason": "deadline"}, ensure_ascii=False) + "\n\n"
    monkeypatch.setattr(chat, "_stream_agent_response", fake)
    r = asyncio.run(api._run_turn_collect("вопрос", 1))
    assert r["ok"] is False
    assert "не поможет" in r["reason"] and "повторить позже" not in r["reason"]


def test_widening_stays_within_the_pinned_check(monkeypatch):
    """Пустой ответ по одной КМ критик просит расширить — расширение
    охватное, и без сужения в ответ приезжали все акты корпуса."""
    from backend.core.tools import registry

    async def coverage(cancel=None, **args):
        return ToolResult(evidence=[
            Evidence(kind="passage", check_id=KM_A, quote="своя"),
            Evidence(kind="passage", check_id=KM_B, quote="чужая")])
    monkeypatch.setattr(registry, "get", lambda name: (None, coverage))

    async def say(*a, **k):
        return None
    ledger = Ledger()
    v = SimpleNamespace(action="widen")
    u = SimpleNamespace(plan=[{"tool": "query_deviations", "args": {"severity": "критичные"}}])
    c = SimpleNamespace(kind="rows")
    changed = asyncio.run(orchestrator._escalate(
        v, u, c, ledger, CheckScope(mode="corpus_wide"), None, say, None,
        "А какие из них критичные?", pin=KM_A))
    assert changed and {e.check_id for e in ledger.evidence()} == {KM_A}


@pytest.mark.parametrize("asked,stored", [("критичные", "критичное"),
                                          ("Критичное", "критичное"),
                                          ("высокая", "высокий"),
                                          ("низкие", "низкая")])
def test_severity_matches_any_word_form(asked, stored):
    from backend.core.tools.facts import _stem
    assert _stem(asked) in stored


# ── уточнение проверки по письму ──────────────────────────────────

def _fake_stream(events):
    async def gen(*a, **k):
        for name, data in events:
            yield f"event: {name}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"
    return gen


_RESOLVE = {"resolve_id": "r1", "mode": "choice", "letter_saved": True, "hint": None,
            "candidates": [{"ref": 1, "kms": ["99-12345"], "title": "Поручение про лимиты"},
                           {"ref": 2, "kms": ["99-12346"], "title": "Поручение про залоги"}]}


def test_resolve_card_reaches_the_agent_as_a_question(monkeypatch):
    """Раньше событие пропускалось — «ход завершился без текста ответа», и
    агент решал, что навык не подходит."""
    import backend.api.routes.chat as chat
    from backend.skill import api
    monkeypatch.setattr(chat, "_stream_agent_response", _fake_stream([
        ("resolve_card", _RESOLVE), ("done", {"needs_clarification": True})]))
    r = asyncio.run(api._run_turn_collect("вопрос", 1))
    assert r["ok"] is True and r["status"] == "clarify"
    assert "КМ-99-12345" in r["answer_md"] and "КМ-99-12346" in r["answer_md"]
    assert [c["check_id"] for c in r["clarify"]["candidates"]] == ["КМ-99-12345", "КМ-99-12346"]
    assert "check_id" in r["clarify"]["next"]


def _capture_card(monkeypatch):
    from backend.skill import api
    seen = {}

    async def fake_collect(message, session_id, model=None, attachment_ids=None,
                           progress=None):
        seen.update(message=message, attachments=attachment_ids)
        return {"ok": True, "answer_md": "карточка"}
    monkeypatch.setattr(api, "_run_turn_collect", fake_collect)
    monkeypatch.setattr(api, "_session", lambda key, user: 1)
    return api, seen


def test_card_resumes_by_check_id_with_the_letter_of_the_dialog(monkeypatch):
    from backend.skill import jobs
    api, seen = _capture_card(monkeypatch)

    async def go():
        st = await api.card_start("postgres:1", check_id="КМ-99-12345")
        return await jobs.wait(st["job_id"], 5)
    r = asyncio.run(go())
    assert r["done"] and r["ok"]
    assert seen == {"message": "Контроль исполнения по КМ-99-12345", "attachments": None}


def test_card_start_without_letter_or_check_is_refused(monkeypatch):
    api, _ = _capture_card(monkeypatch)
    r = asyncio.run(api.card_start("postgres:1"))
    assert r["ok"] is False and "check_id" in r["reason"]


def test_long_markdown_is_not_cut_before_delivery(monkeypatch):
    from backend.api.routes import chat
    from backend.skill import api
    body = "# Карточка\n\n| Номер | Решение |\n|---|---|\n" + "| 1 | Сохранить полностью [источник](https://example.invalid) |\n" * 500
    monkeypatch.setattr(chat, "_stream_agent_response", _fake_stream([("answer", {"text": body}), ("done", {})]))
    result = asyncio.run(api._run_turn_collect("вопрос", 1))
    assert result["answer_md"] == body.strip()
    assert render.envelope(result)["answer_md"] == body.strip()


def test_invalid_card_check_does_not_start_work_or_replace_letter(monkeypatch):
    from backend.agents import execution_control as ec
    from backend.skill import api, jobs
    monkeypatch.setattr(ec, "_pending_letters", {})
    ec._store_pending_letter(1, "своё письмо")
    monkeypatch.setattr(jobs, "start", lambda *a, **k: pytest.fail("invalid check started work"))
    result = asyncio.run(api.card_start("s", check_id="случайный текст вместо номера"))
    assert not result["ok"] and "check_id" in result["reason"]
    assert ec._recall_pending_letter(1) == "своё письмо"


def test_user_and_session_pair_has_stable_separate_history(monkeypatch):
    from contextlib import contextmanager

    from backend.skill import api
    from backend.storage import database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine("sqlite://")
    database.Base.metadata.create_all(engine)

    @contextmanager
    def db():
        with Session(engine) as session:
            yield session
            session.commit()

    monkeypatch.setattr(database, "get_db", db)
    try:
        alice = api._session("same-chat", "alice")
        bob = api._session("same-chat", "bob")
        other = api._session("other-chat", "alice")
        assert len({alice, bob, other}) == 3
        assert api._session("same-chat", "alice") == alice
        assert api.forget("same-chat", "bob")["forgotten"]
        assert api._session("same-chat", "alice") == alice
    finally:
        engine.dispose()


def test_card_job_result_is_scoped_to_user_and_session(monkeypatch):
    from backend.skill import api, jobs
    monkeypatch.setattr(jobs, "_JOBS", {})
    monkeypatch.setattr(api, "_session", lambda key, user: 1)

    async def collect(*args, **kwargs):
        return {"ok": True, "answer_md": "личная карточка Alice"}

    monkeypatch.setattr(api, "_run_turn_collect", collect)

    async def go():
        started = await api.card_start("s", user_id="alice", check_id=KM_A)
        for session, user in [("s", "bob"), ("other", "alice")]:
            denied = await api.card_status(started["job_id"], 0, session_key=session, user_id=user)
            assert not denied["ok"] and "answer_md" not in denied and "label" not in denied
        own = await api.card_status(started["job_id"], 2, session_key="s", user_id="alice")
        assert own["done"] and own["answer_md"] == "личная карточка Alice"

    asyncio.run(go())


def test_repeated_letter_clarification_keeps_only_own_session_document(monkeypatch):
    from backend.agents import execution_control as ec
    from backend.agents import poruch_resolver
    from backend.rag.query_understanding import QueryContext
    monkeypatch.setattr(ec, "_pending_letters", {})
    monkeypatch.setattr(ec, "_mark_card_activity", lambda: None)
    monkeypatch.setattr(ec, "_save_history", lambda *a, **k: None)
    monkeypatch.setattr(poruch_resolver, "log_resolve", lambda *a, **k: None)
    seen = []

    async def resolve(ctx, model):
        seen.append((tuple(ctx.km_numbers), ctx.attachment_text))
        return {"status": "choice", "candidates": [{"km_id": "99-12345", "title": "первый", "rows": [], "score": 1}], "resolve_id": "synthetic"}

    monkeypatch.setattr(poruch_resolver, "resolve_smart", resolve)

    async def turn(sid, text=None, km=None):
        ctx = QueryContext(raw_query="Контроль исполнения", attachment_text=text, has_attachment=bool(text), km_numbers=[km] if km else [])
        return [chunk async for chunk in ec.stream_execution_control(ctx, sid)]

    async def go():
        await turn(1, "письмо Alice: ЦА-001")
        await turn(2, "письмо Bob: ЦА-002")
        again = await turn(1, km=KM_A)
        assert any("resolve_card" in part for part in again)
        await turn(1, km=KM_A)
        await turn(3, km=KM_A)

    asyncio.run(go())
    assert [letter for _, letter in seen] == ["письмо Alice: ЦА-001", "письмо Bob: ЦА-002", "письмо Alice: ЦА-001", "письмо Alice: ЦА-001", None]


def test_card_clarify_then_selected_check_delivers_full_text_with_saved_letter(monkeypatch):
    from backend.agents import execution_control as ec
    from backend.agents import poruch_resolver
    from backend.api.routes import chat, files
    from backend.rag.query_understanding import QueryContext
    from backend.skill import api, jobs
    monkeypatch.setattr(ec, "_pending_letters", {})
    monkeypatch.setattr(jobs, "_JOBS", {})
    monkeypatch.setattr(ec, "_mark_card_activity", lambda: None)
    monkeypatch.setattr(ec, "_save_history", lambda *a, **k: None)
    monkeypatch.setattr(ec, "_use_gp", lambda: False)
    monkeypatch.setattr(poruch_resolver, "log_resolve", lambda *a, **k: None)
    monkeypatch.setattr(api, "_session", lambda key, user: {"a": 1, "b": 2}[key])
    captured = []

    async def resolve(ctx, model):
        if ctx.km_numbers:
            return {"status": "resolved", "rows": [{"km_id": "99-12345", "poruch_key": "synthetic", "assignment_": "поручение"}]}
        return {"status": "choice", "candidates": [{"km_id": "99-12345", "title": "проверка", "rows": []}]}

    async def empty(*a, **k):
        return {}

    async def poruch(rows, letter, model, previous):
        captured.append(letter)
        return {"rows": rows, "analysis": {"items": [{"claim": letter, "evidence_quality": "synthetic"}]}}

    async def stream(message, sid, model, attachment_ids, surface):
        letter = files.get_attachment(attachment_ids[0])["text"] if attachment_ids else None
        kms = identity.parse(message)
        ctx = QueryContext(raw_query=message, km_numbers=kms, has_attachment=bool(letter), attachment_text=letter)
        async for part in ec.stream_execution_control(ctx, sid, model):
            yield part

    monkeypatch.setattr(poruch_resolver, "resolve_smart", resolve)
    monkeypatch.setattr(ec, "block_related", lambda *a: {})
    monkeypatch.setattr(ec, "block_stats", lambda *a: {})
    for name in ("block_method", "block_repo", "block_plan"):
        monkeypatch.setattr(ec, name, empty)
    monkeypatch.setattr(ec, "block_poruch", poruch)
    monkeypatch.setattr(chat, "_stream_agent_response", stream)

    async def go():
        first = await api.card_start("a", letter_text="своё письмо ЦА-001", user_id="alice")
        result = await api.card_status(first["job_id"], 2, session_key="a", user_id="alice")
        assert result["status"] == "clarify" and result["answer_md"]
        second = await api.card_start("a", check_id=KM_A, user_id="alice")
        result = await api.card_status(second["job_id"], 2, session_key="a", user_id="alice")
        assert result["ok"] and result["done"] and KM_A in result["answer_md"]
        assert "своё письмо ЦА-001" in result["answer_md"]
        assert captured == ["своё письмо ЦА-001"]
        assert ec._recall_pending_letter(2) is None

    asyncio.run(go())


@pytest.mark.parametrize("old_owner", ["alice", None])
def test_legacy_history_is_adopted_only_by_confirmed_owner(monkeypatch, old_owner):
    from contextlib import contextmanager

    from backend.skill import api
    from backend.storage import database
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session
    engine = create_engine("sqlite://")
    database.Base.metadata.create_all(engine)

    @contextmanager
    def db():
        with Session(engine) as session:
            yield session
            session.commit()

    monkeypatch.setattr(database, "get_db", db)
    try:
        with db() as session:
            old = database.ExternalSessionRepo.resolve(session, "old-chat", old_owner)
        assert api._session("old-chat", "bob") != old
        alice = api._session("old-chat", "alice")
        assert (alice == old) == (old_owner == "alice")
        assert api._session("old-chat", "alice") == alice
    finally:
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("kms,expected_query,expected_id", [
    ([KM_A], "", KM_A),
    ([], "контроль сверки", None),
])
async def test_hypothesis_explicit_km_does_not_text_search_its_number(monkeypatch,kms,expected_query,expected_id):
    from contextlib import nullcontext
    from backend.agents import hypothesis_agent as mod
    class MaterialCollected(Exception):
        pass
    seen = []
    def search(db, *, query, check_id):
        seen.append((query, check_id))
        raise MaterialCollected
    monkeypatch.setattr(mod, "get_db", lambda: nullcontext(object()))
    monkeypatch.setattr(mod, "build_context", lambda chunks: "")
    monkeypatch.setattr(mod.DeviationRepo, "search", search)
    agent = object.__new__(mod.HypothesisAgent)
    ctx = SimpleNamespace(topic="контроль сверки",raw_query="вопрос",km_numbers=kms)
    with pytest.raises(MaterialCollected):
        await agent._generate(ctx, [])
    assert seen == [(expected_query, expected_id)]
