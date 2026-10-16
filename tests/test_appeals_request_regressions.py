"""Offline chat-message contracts and per-inbound-request pipeline idempotency."""
import asyncio
import ast
import importlib
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pandas as pd
import pytest

from nanobot.agent.tools.context import RequestContext, request_context
from workspace.tools.appeals_analyzer import AppealsAnalyzerTool, AppealsAnalyzerToolConfig


@pytest.fixture
def bundle(monkeypatch, tmp_path):
    runner = AppealsAnalyzerTool._load_runner()
    reports = importlib.import_module(runner.__module__)
    hypothesis = importlib.import_module("appeals_analyzer_runtime.scripts.appeals_hypothesis")
    local = importlib.import_module("appeals_analyzer_runtime.utils.local_qwen")
    artifacts = importlib.import_module("appeals_analyzer_runtime.utils.appeals_artifacts")
    sessions = importlib.import_module("appeals_analyzer_runtime.utils.session_extract_manager")
    monkeypatch.setenv("APPEALS_ANALYZER_RUNTIME", "production")
    monkeypatch.setenv("NANOBOT_SKILLS_RUNTIME", "production")
    monkeypatch.setattr(artifacts, "session_results", lambda key: tmp_path / key.replace(":", "_"))
    frame = pd.DataFrame({"id": ["1", "2"], "req_desc": ["description", "description"]})
    calls = dict(search=Mock(return_value=["1", "2"]), hydrate=Mock(return_value=frame),
                 rerank=Mock(return_value=frame.assign(score=.9)), narrative=AsyncMock(return_value="FULL REPORT"))
    for name, key in (("retrieve_via_srb_d3", "search"), ("fetch_appeals_by_ids", "hydrate"),
                      ("rerank_via_srb_d3", "rerank"), ("generate_complaint_hypothesis_narrative", "narrative")):
        monkeypatch.setattr(reports, name, calls[key])
    monkeypatch.setattr(reports, "extract_search_params", lambda text: {"date_range": None})
    def export(frame, query, session):
        path = artifacts.output_directory(session) / "report.xlsx"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"XLSX fixture")
        artifacts.register_artifact(path)
        return {"name": path.name}
    calls["export"] = Mock(side_effect=export)
    monkeypatch.setattr(reports, "export_complaints_excel", calls["export"])
    delivery = SimpleNamespace(execute=AsyncMock(return_value=SimpleNamespace(is_error=False)))
    tool = AppealsAnalyzerTool(config=AppealsAnalyzerToolConfig(), message_tool=delivery)
    for key in ("postgres:A", "postgres:B"):
        sessions.clear_session_extract(key)
    yield SimpleNamespace(tool=tool, reports=reports, hypothesis=hypothesis, local=local,
                          calls=calls, delivery=delivery, sessions=sessions, frame=frame)
    for key in ("postgres:A", "postgres:B"):
        sessions.clear_session_extract(key)


def context(text="Найди обращения о мошенничестве", *, session="postgres:A", message="m-1"):
    return RequestContext(channel="postgres", chat_id=session, sender_id=session,
                          session_key=session, message_id=message, turn_id="turn-" + message,
                          original_user_text=text, attributes={"tool_call_id": "tc-1"})


def assert_pipeline_counts(bundle, count):
    for key in ("search", "hydrate", "rerank", "export"):
        assert bundle.calls[key].call_count == count, key
    assert bundle.calls["narrative"].await_count == count


@pytest.mark.asyncio
@pytest.mark.parametrize("text", [
    "Сколько было обращений, в которых клиент жалуется на бонусные категории?",
    "Почему клиенты жалуются на комиссии?",
    "Есть ли обращения по мошенничеству?",
    "Что чаще всего не устраивает клиентов?",
    "Найди жалобы на карты, переводы и кредиты",
    "Жалобы: карты, переводы, кредиты, комиссии",
])
async def test_arbitrary_natural_question_starts_new_search_with_original_text(bundle, text):
    # A previous result must not silently restrict an independent question.
    bundle.sessions.set_session_extract("postgres:A", bundle.frame,
        extra={"final_ids": ["old-id"], "hypothesis": "old report"})
    with request_context(context(text)):
        result = await bundle.tool.execute(prompt="rewritten by agent")
    assert "FULL REPORT" in result and "CSV" not in result
    assert "точное число" in result
    bundle.calls["search"].assert_called_once_with("postgres:A", text, None)
    assert_pipeline_counts(bundle, 1)


@pytest.mark.asyncio
async def test_question_with_reference_searches_only_saved_selection(bundle, monkeypatch):
    bundle.sessions.set_session_extract("postgres:A", bundle.frame,
        extra={"final_ids": ["1", "2"], "hypothesis": "old report"})
    monkeypatch.setattr(bundle.reports, "answer_complaint_follow_up", Mock(return_value="bounded answer"))
    text = "А почему клиенты в этих обращениях жалуются?"
    with request_context(context(text)):
        assert await bundle.tool.execute(prompt="rewritten") == "bounded answer"
    bundle.calls["search"].assert_called_once_with("postgres:A", text, ["1", "2"])


@pytest.mark.asyncio
@pytest.mark.parametrize("fallback", [False, True])
async def test_same_turn_repeated_and_parallel_calls_execute_pipeline_once(bundle, monkeypatch, fallback, caplog):
    if fallback:
        monkeypatch.setattr(bundle.hypothesis, "_get_ask_llm_fn", lambda: lambda messages: "")
        bundle.calls["narrative"].side_effect = bundle.hypothesis.generate_complaint_hypothesis_narrative
    caplog.set_level("INFO")
    ctx = context("Найди обращения SECRET_USER_QUERY о мошенничестве")
    with request_context(ctx):
        first, second = await asyncio.gather(bundle.tool.execute(prompt="forged"), bundle.tool.execute(prompt="Сузить анализ"))
        third = await bundle.tool.execute(prompt="Найди другие обращения")
    assert first == second == third
    assert_pipeline_counts(bundle, 1)
    assert bundle.calls["search"].call_args.args[1] == ctx.original_user_text
    assert bundle.delivery.execute.await_count == 1
    assert "SECRET_USER_QUERY" not in caplog.text
    assert '"duplicate_call": true' in caplog.text
    assert '"report_delivered": true' in caplog.text
    if fallback:
        assert "Качественный анализ недоступен" in first
        assert "Гипотеза 1" not in first


@pytest.mark.asyncio
async def test_same_text_in_new_user_message_executes_again(bundle):
    for message in ("m-1", "m-2"):
        with request_context(context(message=message)):
            await bundle.tool.execute(prompt="forged")
    assert_pipeline_counts(bundle, 2)
    assert bundle.delivery.execute.await_count == 2


@pytest.mark.asyncio
async def test_failed_delivery_retries_publication_without_pipeline_or_new_xlsx(bundle):
    bundle.delivery.execute.side_effect = [SimpleNamespace(is_error=True), SimpleNamespace(is_error=False)]
    with request_context(context()):
        first = await bundle.tool.execute(prompt="forged")
        assert first.is_error
        second = await bundle.tool.execute(prompt="Сузить анализ без пользователя")
        assert not getattr(second, "is_error", False)
    assert_pipeline_counts(bundle, 1)
    assert bundle.delivery.execute.await_count == 2
    assert bundle.delivery.execute.await_args_list[0].kwargs == bundle.delivery.execute.await_args_list[1].kwargs


@pytest.mark.asyncio
async def test_next_turn_followup_is_bounded_and_not_blocked(bundle, monkeypatch):
    with request_context(context()):
        await bundle.tool.execute(prompt="forged")
    monkeypatch.setattr(bundle.reports, "answer_complaint_follow_up", Mock(return_value="followup"))
    with request_context(context("Найди среди этих обращений жалобы на комиссии", message="m-2")):
        assert await bundle.tool.execute(prompt="forged") == "followup"
        assert await bundle.tool.execute(prompt="forged duplicate") == "followup"
    assert bundle.calls["search"].call_count == 2
    assert bundle.calls["search"].call_args.args[2] == ["1", "2"]
    assert bundle.calls["hydrate"].call_count == bundle.calls["rerank"].call_count == 2
    assert bundle.calls["export"].call_count == bundle.calls["narrative"].await_count == 1


@pytest.mark.asyncio
async def test_parallel_users_are_independent(bundle):
    entered, both = [], asyncio.Event()
    async def narrative(*args, **kwargs):
        entered.append(True)
        if len(entered) == 2:
            both.set()
        await both.wait()
        return "report"
    bundle.calls["narrative"].side_effect = narrative
    async def run(session):
        with request_context(context(session=session)):
            return await bundle.tool.execute(prompt="forged")
    await asyncio.wait_for(asyncio.gather(run("postgres:A"), run("postgres:B")), timeout=5)
    assert_pipeline_counts(bundle, 2)
    assert bundle.delivery.execute.await_count == 2


@pytest.mark.asyncio
async def test_unavailable_osiris_is_not_cached_as_completed_success(bundle):
    from workspace.utils.osiris_runtime import OsirisUnavailableError
    bundle.calls["search"].side_effect = [OsirisUnavailableError("offline"), ["1", "2"]]
    with request_context(context()):
        first = await bundle.tool.execute(prompt="forged")
        assert "сервис обработки недоступен" in first
        second = await bundle.tool.execute(prompt="forged retry")
    assert "FULL REPORT" in second
    assert bundle.calls["search"].call_count == 2
    for key in ("hydrate", "rerank", "export"):
        assert bundle.calls[key].call_count == 1
    assert bundle.calls["narrative"].await_count == bundle.delivery.execute.await_count == 1


def payload(identity="1"):
    return {"observed_patterns": [], "root_cause_signals": [],
            "evidence": [{"statement": "confirmed", "appeal_ids": [identity]}],
            "representative_ids": [identity], "counterexamples": [], "possible_actions": []}


@pytest.mark.asyncio
async def test_evidence_reduction_and_synthesis_always_include_user_data(bundle, monkeypatch):
    seen = []
    detail = "Что проверить: сверить факты из обращения ID: 1 с порядком обслуживания. " * 8
    def ask(messages):
        assert [item["role"] for item in messages] == ["system", "user"]
        assert all(item["content"].strip() for item in messages)
        seen.append(messages)
        if "JSON" in messages[0]["content"]:
            return json.dumps(payload())
        return "\n\n".join(f"Гипотеза {i}. Причина\n{detail}" for i in range(1, 5))
    monkeypatch.setattr(bundle.hypothesis, "_get_ask_llm_fn", lambda: ask)
    result = await bundle.hypothesis.generate_complaint_hypothesis_narrative("USER TASK", bundle.frame.iloc[:1])
    assert "Гипотеза 4" in result
    assert "Текст обращения" in seen[0][1]["content"]
    assert "USER TASK" in seen[-1][1]["content"] and '"statement": "confirmed"' in seen[-1][1]["content"]
    monkeypatch.setattr(bundle.hypothesis, "CONFIG", SimpleNamespace(hypothesis_reduce_char_budget=1))
    evidence = [bundle.hypothesis.validate_evidence_payload(payload(), ["1"], number) for number in (1, 2)]
    assert (await bundle.hypothesis.reduce_structured_evidence(evidence, ask))[0]["status"] == "ok"
    assert '"statement": "confirmed"' in seen[-1][1]["content"]


@pytest.mark.asyncio
async def test_http_400_is_not_retried_as_identical_synthesis(bundle, monkeypatch):
    calls = []
    def ask(messages):
        calls.append(messages)
        if "JSON" in messages[0]["content"]:
            return json.dumps(payload())
        error = RuntimeError("private response body")
        error.response = SimpleNamespace(status_code=400)
        raise error
    monkeypatch.setattr(bundle.hypothesis, "_get_ask_llm_fn", lambda: ask)
    result = await bundle.hypothesis.generate_complaint_hypothesis_narrative("query", bundle.frame.iloc[:1])
    assert len(calls) == 2  # one evidence call, one synthesis; no repeat of the rejected request
    assert "Качественный анализ недоступен" in result and "Гипотеза 1" not in result


def test_agent_status_diagnostics_do_not_touch_gigachat_contract(bundle, monkeypatch):
    monkeypatch.setattr(bundle.local, "_agent_endpoint", lambda: {"provider": "vllm", "model": "qwen", "chat_url": "http://fixture/chat/completions", "api_key": "", "extra_body": {}})
    monkeypatch.delenv("APPEALS_DISABLE_VLLM", raising=False)
    seen = []
    def post(url, headers, body, timeout):
        seen.append(body["messages"])
        return 400, {}, "private body"
    monkeypatch.setattr(bundle.local, "_post_json", post)
    assert bundle.local.call_agent_llm([{"role": "system", "content": "instruction"}, {"role": "user", "content": "task"}]) == ""
    assert bundle.local.last_llm_status() == ("vllm", 400)
    source = subprocess.check_output(["git", "show", "3fcbd3e:workspace/skills/appeals-analyzer/utils/local_qwen.py"], encoding="utf-8")
    baseline = next(item for item in ast.parse(source).body if getattr(item, "name", None) == "call_gigachat_sber_api")
    current = next(item for item in ast.parse(open(bundle.local.__file__, encoding="utf-8").read()).body if getattr(item, "name", None) == "call_gigachat_sber_api")
    assert ast.dump(current) == ast.dump(baseline)
