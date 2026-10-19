"""Offline chat-message contracts and per-inbound-request pipeline idempotency."""
import asyncio
import ast
import importlib
import json
import subprocess
import time
import copy
from dataclasses import replace
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
@pytest.mark.parametrize("prefix", ["", "</think>\n", "<think>internal reasoning</think>\n"])
async def test_qwen_final_json_survives_reasoning_cleanup(bundle, monkeypatch, prefix):
    monkeypatch.setattr(bundle.local, "_agent_endpoint", lambda: {
        "provider": "vllm", "model": "configured-qwen", "chat_url": "http://fixture/v1/chat/completions",
        "api_key": "", "extra_body": {}})
    monkeypatch.delenv("APPEALS_DISABLE_VLLM", raising=False)
    response = {"choices": [{"message": {"content": prefix + json.dumps(payload()),
                 "reasoning_content": "PRIVATE REASONING"}, "finish_reason": "stop"}],
                "usage": {"prompt_tokens": 500, "completion_tokens": 180}}
    monkeypatch.setattr(bundle.local, "_post_json", lambda *args: (200, response, "PRIVATE BODY"))
    result = await bundle.hypothesis.analyze_evidence_batch(
        {"ids": ["1"], "items": ["ID: 1\nТекст обращения: fixture"]}, 1, bundle.local.def_ask_gigachat)
    assert result["status"] == "ok"
    assert result["evidence"] == [{"statement": "confirmed", "appeal_ids": ["1"]}]


@pytest.fixture
def qwen_chat(bundle, monkeypatch):
    for name in ('APPEALS_DISABLE_VLLM', 'APPEALS_VLLM_ENABLE_THINKING', 'APPEALS_VLLM_JSON_MODE',
                 'APPEALS_VLLM_MAX_TOKENS', 'APPEALS_VLLM_JSON_MAX_TOKENS',
                 'APPEALS_VLLM_API_URL', 'VLLM_API_URL', 'APPEALS_VLLM_MODEL', 'VLLM_MODEL_NAME'):
        monkeypatch.delenv(name, raising=False)
    resolver = bundle.local._agent_endpoint
    endpoint = {'provider': 'vllm', 'model': 'agent-selected-qwen', 'api_key': 'SECRET_KEY',
                'chat_url': 'http://fixture/v1/chat/completions',
                'extra_body': {'chat_template_kwargs': {'custom_template_option': 'fixture', 'enable_thinking': True}}}
    monkeypatch.setattr(bundle.local, '_agent_endpoint', lambda: endpoint)
    monkeypatch.setenv('APPEALS_USE_GIGACHAT', '1')  # Even explicit legacy opt-in must have no effect.
    gigachat = Mock(side_effect=AssertionError('GigaChat is forbidden'))
    monkeypatch.setattr(bundle.local, 'call_gigachat_sber_api', gigachat)
    state = SimpleNamespace(status=200, content=json.dumps(payload()), reasoning='', finish='stop')
    def post(url, headers, body, timeout):
        return state.status, {'choices': [{'message': {'content': state.content,
            'reasoning_content': state.reasoning}, 'finish_reason': state.finish}],
            'usage': {'prompt_tokens': 500, 'completion_tokens': 200}}, 'SECRET_RESPONSE_BODY'
    http = Mock(side_effect=post)
    monkeypatch.setattr(bundle.local, '_post_json', http)
    return SimpleNamespace(state=state, http=http, endpoint=endpoint, gigachat=gigachat, resolver=resolver)


def test_local_vllm_uses_agent_config_and_preserves_template_options(bundle, qwen_chat, monkeypatch):
    import lib.services.llm_config as llm_config
    monkeypatch.setattr(bundle.local, '_AGENT_ENDPOINT', None)
    monkeypatch.setattr(bundle.local, '_agent_endpoint', qwen_chat.resolver)
    configured = {**qwen_chat.endpoint, 'api_base': 'http://fixture/v1'}
    resolve = Mock(return_value=configured)
    monkeypatch.setattr(llm_config, 'agent_endpoint', resolve)
    messages = [{'role': 'system', 'content': 'Return strict JSON'}, {'role': 'user', 'content': 'Fixture input'}]
    assert bundle.local.def_ask_gigachat(messages)
    body = qwen_chat.http.call_args.args[2]
    assert body['model'] == 'agent-selected-qwen' and body['messages'] == messages
    assert qwen_chat.http.call_args.args[0] == 'http://fixture/v1/chat/completions'
    resolve.assert_called_once()
    assert body['chat_template_kwargs'] == {'custom_template_option': 'fixture', 'enable_thinking': False}
    assert 'response_format' not in body
    qwen_chat.gigachat.assert_not_called()


def test_guided_json_capability_retry_changes_payload_once(bundle, qwen_chat, monkeypatch):
    monkeypatch.setenv('APPEALS_VLLM_JSON_MODE', '1')
    seen = []
    original = qwen_chat.http.side_effect
    def post(url, headers, body, timeout):
        seen.append(copy.deepcopy(body))
        if len(seen) == 1:
            return 400, {'error': {'message': 'response_format is unsupported'}}, 'SECRET_ERROR_BODY'
        return original(url, headers, body, timeout)
    qwen_chat.http.side_effect = post
    result = bundle.local.def_ask_gigachat([{'role': 'system', 'content': 'Верни СТРОГО JSON'},
                                           {'role': 'user', 'content': 'Fixture'}])
    assert json.loads(result) == payload()
    assert len(seen) == 2 and seen[0] != seen[1]
    assert seen[0]['response_format'] == {'type': 'json_object'} and 'response_format' not in seen[1]
    assert seen[0]['max_tokens'] == 4096
    qwen_chat.gigachat.assert_not_called()


def test_configured_cloud_provider_is_not_used_or_replaced_by_guess(bundle, qwen_chat, monkeypatch):
    import lib.services.llm_config as llm_config
    monkeypatch.setattr(bundle.local, '_AGENT_ENDPOINT', None)
    monkeypatch.setattr(bundle.local, '_agent_endpoint', qwen_chat.resolver)
    monkeypatch.setattr(llm_config, 'agent_endpoint', lambda: {
        'provider': 'minimax', 'api_base': 'https://cloud.invalid/v1', 'model': 'cloud-model'})
    assert bundle.local.def_ask_gigachat([{'role': 'user', 'content': 'Fixture'}]) == ''
    qwen_chat.http.assert_not_called()
    qwen_chat.gigachat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind,expected', [
    ('markdown', 'ok'), ('reasoning_and_final', 'ok'),
    ('reasoning_only', 'reasoning_only'), ('empty', 'empty_response'),
    ('length', 'token_limit'), ('invalid_json', 'invalid_json'),
    ('invalid_schema', 'invalid_schema'), ('invalid_ids', 'invalid_ids'),
    ('parser', 'parser_error'), ('400', 'http_400'), ('500', 'http_error'),
    ('network', 'network_error'),
])
async def test_qwen_evidence_diagnostics_and_no_gigachat(bundle, qwen_chat, monkeypatch, caplog, kind, expected):
    caplog.set_level('INFO')
    qwen_chat.state.reasoning = 'SECRET_REASONING'
    if kind == 'markdown':
        qwen_chat.state.content = '```json\n' + json.dumps(payload()) + '\n```'
    elif kind == 'reasoning_only':
        qwen_chat.state.content = None
    elif kind == 'empty':
        qwen_chat.state.content = ''
        qwen_chat.state.reasoning = ''
    elif kind == 'length':
        qwen_chat.state.content = '{"evidence": ['
        qwen_chat.state.finish = 'length'
    elif kind == 'invalid_json':
        qwen_chat.state.content = 'SECRET_INVALID_JSON'
    elif kind == 'invalid_schema':
        qwen_chat.state.content = json.dumps({**payload(), 'representative_ids': None})
    elif kind == 'invalid_ids':
        qwen_chat.state.content = json.dumps(payload('SECRET_INVENTED_ID'))
    elif kind == 'parser':
        monkeypatch.setattr(bundle.hypothesis, '_extract_json_object', Mock(side_effect=ValueError('SECRET_PARSER_DATA')))
    elif kind in {'400', '500'}:
        qwen_chat.state.status = int(kind)
    elif kind == 'network':
        qwen_chat.http.side_effect = OSError('SECRET_CONNECTION_DETAILS')
    result = await bundle.hypothesis.analyze_evidence_batch(
        {'ids': ['1'], 'items': ['ID: 1\nSECRET_APPEAL_TEXT']}, 1, bundle.local.def_ask_gigachat)
    assert expected in caplog.text
    assert qwen_chat.http.call_count == 1
    if expected == 'ok':
        assert result['status'] == 'ok'
    elif expected == 'invalid_schema':
        assert result['status'] == 'invalid_schema'
        assert 'representative_ids must be an array' in caplog.text
    elif expected == 'invalid_ids':
        assert not result['evidence'][0]['appeal_ids']
    else:
        assert result['status'] == 'failed' and result['error'] == expected
    assert 'stage' in caplog.text and 'evidence:1' in caplog.text
    assert 'http_status' in caplog.text and 'finish_reason' in caplog.text
    assert 'SECRET_' not in caplog.text
    qwen_chat.gigachat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('kind', ['400', '500', 'invalid_json', 'invalid_schema', 'invalid_ids', 'length'])
async def test_systemic_evidence_failure_stops_after_first_batch(bundle, qwen_chat, monkeypatch, kind):
    frame = pd.DataFrame({'id': [str(i) for i in range(1, 201)], 'req_desc': ['Fixture complaint'] * 200})
    monkeypatch.setattr(bundle.hypothesis, '_get_ask_llm_fn', lambda: bundle.local.def_ask_gigachat)
    if kind in {'400', '500'}:
        qwen_chat.state.status = int(kind)
    elif kind == 'invalid_json':
        qwen_chat.state.content = 'invalid JSON'
    elif kind == 'invalid_schema':
        qwen_chat.state.content = '{}'
    elif kind == 'invalid_ids':
        qwen_chat.state.content = json.dumps(payload('unavailable-id'))
    else:
        qwen_chat.state.finish = 'length'
    result = await bundle.hypothesis.generate_complaint_hypothesis_narrative('Fixture request', frame)
    assert 'Качественный анализ недоступен' in result and 'Математический' in result
    assert qwen_chat.http.call_count == 1
    qwen_chat.gigachat.assert_not_called()


@pytest.mark.asyncio
async def test_failed_qwen_preserves_native_export_and_delivery(bundle, qwen_chat, monkeypatch):
    qwen_chat.state.status = 500
    monkeypatch.setattr(bundle.hypothesis, '_get_ask_llm_fn', lambda: bundle.local.def_ask_gigachat)
    bundle.calls['narrative'].side_effect = bundle.hypothesis.generate_complaint_hypothesis_narrative
    with request_context(context()):
        result = await bundle.tool.execute(prompt='rewritten')
    assert 'Качественный анализ недоступен' in result and 'Выгрузка: report.xlsx' in result
    assert_pipeline_counts(bundle, 1)
    assert bundle.delivery.execute.await_count == qwen_chat.http.call_count == 1
    assert bundle.delivery.execute.call_args.kwargs['media'][0].endswith('report.xlsx')
    qwen_chat.gigachat.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize('content', ['{}', 'invalid JSON', json.dumps({**payload(), 'evidence': []})])
async def test_failed_reducer_stops_without_dropping_source_groups(bundle, qwen_chat, monkeypatch, content):
    monkeypatch.setattr(bundle.hypothesis, 'CONFIG', replace(bundle.hypothesis.CONFIG, hypothesis_reduce_char_budget=1))
    qwen_chat.state.content = content
    evidence = [bundle.hypothesis.validate_evidence_payload(payload(), ['1'], i) for i in range(16)]
    result = await bundle.hypothesis.reduce_structured_evidence(evidence, bundle.local.def_ask_gigachat)
    assert qwen_chat.http.call_count == 1
    if content == 'invalid JSON':
        assert result[0]['source_batches'] == evidence[:8]
    assert result[1:] == evidence[8:]


@pytest.mark.asyncio
async def test_qualitative_budget_bounds_await_and_http_timeout(bundle, qwen_chat, monkeypatch):
    monkeypatch.setattr(bundle.hypothesis, 'CONFIG', replace(bundle.hypothesis.CONFIG, hypothesis_timeout_sec=.03))
    monkeypatch.setattr(bundle.hypothesis, '_get_ask_llm_fn', lambda: bundle.local.def_ask_gigachat)
    original_post = qwen_chat.http.side_effect
    def slow(*args):
        time.sleep(.15)  # Deliberately ignores the transport timeout to exercise the outer bound.
        return original_post(*args)
    qwen_chat.http.side_effect = slow
    started = time.monotonic()
    result = await bundle.hypothesis.generate_complaint_hypothesis_narrative('Fixture request', bundle.frame)
    elapsed = time.monotonic() - started
    assert elapsed < .14 and 'Качественный анализ недоступен' in result
    assert qwen_chat.http.call_count == 1 and 0 < qwen_chat.http.call_args.args[3] <= .030001
    await asyncio.sleep(.16)  # Let the test-only blocking thread finish before fixture teardown.
    assert qwen_chat.http.call_count == 1


@pytest.mark.asyncio
async def test_qwen_four_grounded_hypotheses_through_native_tool_with_xlsx(bundle, qwen_chat, monkeypatch):
    facts = (
        'Клиент ID: 1 сообщает, что после выбора бонусной категории приложение показывает прежнюю категорию.',
        'Клиент ID: 1 повторно обращается за подтверждением выбора категории, поскольку результат не отображается.',
        'Клиент ID: 2 не находит описание срока начисления бонусов после покупки.',
        'Клиент ID: 2 получает ответ без следующего шага и просит объяснить порядок проверки начисления.',
    )
    narrative = '\n\n'.join(f'Гипотеза {i}. {title}\n{fact}\n{explanation}' for i, (title, fact, explanation) in enumerate(zip(
        ('Не отображается результат выбора', 'Не хватает подтверждения операции',
         'Неясен срок начисления', 'Ответ не объясняет дальнейшие действия'), facts,
        ('Возможная причина — задержка обновления отображения. Это предположение требует проверки: '
         'сопоставить сохранённый выбор и показанную категорию по журналу операций. '
         'Пока проверка не выполнена, технический сбой не установлен. Клиент не понимает, сохранён ли выбор.',
         'Отсутствие видимого подтверждения может вести к повторному обращению. '
         'Проверить, какое подтверждение получает клиент, и сопоставить его с фактом сохранения операции. '
         'Повторный контакт сам по себе не доказывает отказ системы или финансовый ущерб.',
         'Возможна недостаточная ясность объяснения правил. Проверить текст условий и уведомление '
         'после покупки: содержит ли оно применимый срок начисления и условия проверки. '
         'Из обращения нельзя заключить, что бонусы начислены неверно или что возникла потеря денег.',
         'Возможная причина — неполный ответ поддержки. Проверить содержание ответа и наличие '
         'понятного следующего действия для клиента. Утверждать нарушение регламента до проверки '
         'нельзя; обращение подтверждает только потребность клиента в дополнительном объяснении.')), 1))
    grounded = {**payload(), 'evidence': [{'statement': fact, 'appeal_ids': ['1' if i < 2 else '2']}
                                        for i, fact in enumerate(facts)]}
    def post(url, headers, body, timeout):
        content = json.dumps(grounded) if 'JSON' in body['messages'][0]['content'] else narrative
        return 200, {'choices': [{'message': {'content': content, 'reasoning_content': 'SECRET_REASONING'},
                                  'finish_reason': 'stop'}]}, 'SECRET_BODY'
    qwen_chat.http.side_effect = post
    monkeypatch.setattr(bundle.hypothesis, '_get_ask_llm_fn', lambda: bundle.local.def_ask_gigachat)
    bundle.calls['narrative'].side_effect = bundle.hypothesis.generate_complaint_hypothesis_narrative
    with request_context(context()):
        result = await bundle.tool.execute(prompt='rewritten')
    assert all(f'Гипотеза {i}' in result for i in range(1, 5))
    assert len(narrative) >= 1200 and 'Качественный анализ недоступен' not in result
    assert_pipeline_counts(bundle, 1)
    assert qwen_chat.http.call_count == 2
    assert bundle.delivery.execute.await_count == 1
    assert bundle.delivery.execute.call_args.kwargs['media'][0].endswith('report.xlsx')
    qwen_chat.gigachat.assert_not_called()


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
