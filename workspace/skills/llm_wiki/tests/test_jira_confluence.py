from __future__ import annotations

import json

from wiki_agent.jira_confluence import (
    EvidenceSelection,
    _consolidate_merged_topics,
    export_json_directory,
    extract_evidence,
    ingest_jira_json,
    load_search_cards,
    prepare_jira_confluence,
    select_evidence_cards,
    select_jira_sources,
)
from wiki_agent.provider import FakeProvider
from wiki_agent.workspace import Workspace

from .helpers import create_workspace


def _new_export(contour="alpha", page_contour="alpha"):
    return {
        "contour": contour, "issue_key": "TEST-1", "issue_id": "10",
        "summary": "Ошибка добавления профиля", "issuetype_name": "Bug",
        "description": "Не добавляется профиль", "updated": "2026-09-11",
        "users": [{"role": "assignee", "display_name": "Тестовый пользователь"}],
        "customfields": [{"field_name": "Роль", "string_value": "Участник ПСИ"}],
        "links": [{"linked_issue_key": "TEST-2", "direction": "OUTWARD", "link_type_name": "Implement in"}],
        "remotelinks": [{"link_title": "Page", "url": "https://example.test/?pageId=100"}],
        "comments": [{"body": "Проверен чек-лист", "author": "user-1"}],
        "changelog": [{"field": "status", "oldstring": "Open", "newstring": "Done"}],
        "testcases": [{"test_case_key": "T-1", "objective": "Проверить профиль"}],
        "testresults": [{"test_result_key": "R-1", "test_result_status_id": "2"}],
        "attachments": [{"filename": "checklist.pdf", "filesize": 100}],
        "pages": [
            {"page_id": "100", "conf_contour": page_contour, "page_title": "Профиль",
             "page_version": "2", "body_text": "Передайте <CLIENT_ID>. x < 10 && y > 0.",
             "status": "ok", "macro_count": 0, "has_excerpt_include": False},
            {"page_id": "101", "conf_contour": page_contour, "page_title": "Нет текста",
             "page_version": "2", "body_text": None, "status": "text_unavailable"},
        ],
        "data_quality": {"pages_total": 2, "pages_unavailable": 1},
    }


def test_load_by_key_is_exact_and_preserves_previous_documents(tmp_path):
    from wiki_agent.errors import ValidationError
    create_workspace(tmp_path)
    for key, page_id in (("TRCORE-1", "201"), ("TRCORE-10", "202"), ("OTHER-1", "203")):
        data = _new_export()
        data.update(issue_key=key, project_key=key.split("-")[0])
        data["pages"] = [dict(data["pages"][0], page_id=page_id)]
        (tmp_path / f"raw/sources/{key}.json").write_text(json.dumps(data))
    workspace = Workspace(tmp_path)
    before = workspace.snapshot_sources()
    wiki_before = {str(p): p.read_bytes() for p in (tmp_path / "wiki").rglob("*.md")}
    assert select_jira_sources(workspace, " trcore-1 ") == ("raw/sources/TRCORE-1.json",)
    assert len(select_jira_sources(workspace, "trcore")) == 2
    provider = FakeProvider(['{"summary":"S"}'])
    first = prepare_jira_confluence(workspace, provider, key="trcore-1")
    assert (first.jira_count, first.confluence_count, first.llm_calls) == (1, 1, 1)
    assert prepare_jira_confluence(workspace, FakeProvider([]), key="TRCORE-1").llm_calls == 0
    second = prepare_jira_confluence(workspace, FakeProvider(['{"summary":"S2"}']), key="OTHER-1")
    assert (second.jira_count, second.confluence_count) == (2, 2)
    third = prepare_jira_confluence(workspace, FakeProvider(['{"summary":"S3"}']), key="trcore")
    assert (third.jira_count, third.confluence_count) == (3, 3)
    manifest_before = (tmp_path / third.manifest_path).read_bytes()
    for key in ("TRCORE-100", "../TRCORE", "TRCORE-1 OR 1=1"):
        try:
            prepare_jira_confluence(workspace, FakeProvider([]), key=key)
        except ValidationError:
            pass
        else:
            raise AssertionError(f"Expected rejection: {key}")
    assert (tmp_path / third.manifest_path).read_bytes() == manifest_before
    assert workspace.snapshot_sources() == before
    assert wiki_before == {str(p): p.read_bytes() for p in (tmp_path / "wiki").rglob("*.md")}


def test_load_old_format_and_multiple_contours(tmp_path):
    create_workspace(tmp_path)
    _write_json(tmp_path, "ABC-1", "Bug")
    for scope in ("alpha", "sigma"):
        (tmp_path / f"raw/sources/{scope}.json").write_text(json.dumps(_new_export(scope)))
    workspace = Workspace(tmp_path)
    assert len(select_jira_sources(workspace, "test-1")) == 2
    assert len(select_jira_sources(workspace, "abc")) == 1
    result = prepare_jira_confluence(workspace, FakeProvider(['{"summary":"S"}']), key="abc")
    assert result.jira_count == 1


def test_load_cli_and_python_api(tmp_path):
    from unittest.mock import patch
    from wiki_agent.api import WikiAgent
    from wiki_agent.cli import build_parser
    from .helpers import settings
    create_workspace(tmp_path)
    (tmp_path / "raw/sources/test.json").write_text(json.dumps(_new_export()))
    agent = WikiAgent(tmp_path, settings=settings(tmp_path), provider=FakeProvider(['{"summary":"S"}']))
    with patch.object(agent, "build_index") as build_index:
        result = agent.jira.load("test-1")
        assert result.jira_count == 1
        assert agent.jira.prepare("TEST").llm_calls == 0
        assert build_index.call_count == 2
    assert build_parser().parse_args(["jira", "load", "trcore"]).key == "trcore"
    assert build_parser().parse_args(["jira", "prepare"]).key is None


def test_new_format_preserves_fields_plaintext_and_missing_pages(tmp_path):
    create_workspace(tmp_path)
    path = tmp_path / "raw/sources/new.json"
    path.write_text(json.dumps(_new_export()), encoding="utf-8")
    workspace = Workspace(tmp_path)
    before = workspace.snapshot_sources()
    provider = FakeProvider(['{"summary":"Профиль и CLIENT_ID"}'])
    result = prepare_jira_confluence(workspace, provider)
    assert (result.jira_count, result.confluence_count, result.llm_calls) == (1, 2, 1)
    assert "<CLIENT_ID>" in provider.requests[0].user_prompt
    cards = {c.card_id: c for c in load_search_cards(workspace)}
    missing = cards["confluence:alpha:101"]
    assert not missing.content_available
    assert missing.related_ids == ("jira:alpha:TEST-1",)
    text = workspace.read_text(cards["jira:alpha:TEST-1"].text_path)
    for value in ("Участник ПСИ", "TEST-2", "чек-лист", "Done", "T-1", "R-1", "checklist.pdf", "pages_unavailable"):
        assert value in text
    evidence = extract_evidence(workspace, FakeProvider([]), "Что на странице?",
        EvidenceSelection((missing,), (missing.text_path,)),
        max_document_chars=100000, max_context_chars=100000)
    assert "содержание неизвестно" in evidence.text
    assert workspace.snapshot_sources() == before


def test_new_format_contours_do_not_collide_and_relations_are_bidirectional(tmp_path):
    create_workspace(tmp_path)
    for i, (jira_scope, page_scope) in enumerate((("alpha", "sigma"), ("sigma", "alpha"))):
        (tmp_path / f"raw/sources/{i}.json").write_text(
            json.dumps(_new_export(jira_scope, page_scope)), encoding="utf-8")
    result = prepare_jira_confluence(Workspace(tmp_path), FakeProvider(['{"summary":"A"}', '{"summary":"B"}']))
    cards = {c.card_id: c for c in load_search_cards(Workspace(tmp_path))}
    assert (result.jira_count, result.confluence_count) == (2, 4)
    assert cards["confluence:alpha:100"].related_ids == ("jira:sigma:TEST-1",)
    assert "confluence:sigma:100" in cards["jira:alpha:TEST-1"].related_ids


def test_new_format_outdated_and_macro_warnings_reach_proposal(tmp_path):
    from .helpers import valid_knowledge_json
    create_workspace(tmp_path)
    data = _new_export()
    data["pages"][0].update(status="text_outdated", body_validto="2025-01-01", macro_count=2, has_excerpt_include=True)
    (tmp_path / "raw/sources/new.json").write_text(json.dumps(data), encoding="utf-8")
    provider = FakeProvider(['{"summary":"Профиль"}', 'Анализ Jira', 'Анализ Confluence', valid_knowledge_json(tmp_path)])
    result = ingest_jira_json(Workspace(tmp_path), provider, "raw/sources/new.json",
        max_file_chars=100000, max_context_chars=100000, max_query_pages=6)
    proposal = (tmp_path / result.proposal_path).read_text()
    assert "text_outdated" in proposal and "excerpt-include" in proposal
    assert "Текст отсутствует" in proposal
    assert len(provider.requests) == 4  # Для пустой страницы LLM не вызывается.


def test_new_format_metadata_invalidates_summary_and_body_change_is_detected(tmp_path):
    create_workspace(tmp_path)
    path = tmp_path / "raw/sources/new.json"
    data = _new_export()
    path.write_text(json.dumps(data), encoding="utf-8")
    workspace = Workspace(tmp_path)
    prepare_jira_confluence(workspace, FakeProvider(['{"summary":"S1"}']))
    assert prepare_jira_confluence(workspace, FakeProvider([])).llm_calls == 0
    data["pages"][0]["page_title"] = "Новое название"
    path.write_text(json.dumps(data), encoding="utf-8")
    assert prepare_jira_confluence(workspace, FakeProvider(['{"summary":"S2"}'])).llm_calls == 1
    data["pages"][0]["body_text"] += " Изменение тела."
    path.write_text(json.dumps(data), encoding="utf-8")
    assert prepare_jira_confluence(workspace, FakeProvider(['{"summary":"S3"}'])).llm_calls == 1


def test_new_format_and_old_export_work_together_without_llm(tmp_path):
    create_workspace(tmp_path)
    _write_json(tmp_path, "ABC-1", "Bug")
    (tmp_path / "raw/sources/new.json").write_text(json.dumps({"root": _new_export()}), encoding="utf-8")
    report = export_json_directory(tmp_path / "raw/sources", tmp_path / "out")
    assert (report["jira_count"], report["confluence_count"]) == (2, 3)
    assert "<CLIENT_ID>" in (tmp_path / "out/confluence_alpha_100.md").read_text()
    assert "Текст не извлечён" in (tmp_path / "out/confluence_alpha_101.md").read_text()


def test_scoped_jira_is_found_by_unscoped_exact_key(tmp_path):
    create_workspace(tmp_path)
    (tmp_path / "raw/sources/new.json").write_text(json.dumps(_new_export()), encoding="utf-8")
    workspace = Workspace(tmp_path)
    prepare_jira_confluence(workspace, FakeProvider(['{"summary":"Профиль"}']))
    selected = select_evidence_cards(workspace, "Расскажи о TEST-1", [])
    assert "jira:alpha:TEST-1" in {card.card_id for card in selected.cards}
    assert not select_evidence_cards(workspace, "Расскажи о TEST-10", []).cards


def test_standalone_notebook_accepts_new_and_old_exports(tmp_path):
    from pathlib import Path
    notebook = json.loads((Path(__file__).parents[1] / "jira_to_markdown.ipynb").read_text())
    context = {}
    # Не выполняем настройку cwd/директорий пользователя; только определения.
    exec("from pathlib import Path\nfrom html import unescape\nfrom html.parser import HTMLParser\n"
         "import hashlib, json, re", context)
    context.update(INPUT_DIR=tmp_path / "input", OUTPUT_DIR=tmp_path / "output", INCLUDE_EMAILS=False,
                   EXPECTED_SECTIONS=("issue_key", "issues", "users", "customfield", "links",
                                      "remotelink", "confluence_body", "traceability"))
    context["INPUT_DIR"].mkdir(); context["OUTPUT_DIR"].mkdir()
    for index in (2, 3):
        exec(compile("".join(notebook["cells"][index]["source"]), f"cell-{index}", "exec"), context)
    (context["INPUT_DIR"] / "new.json").write_text(json.dumps(_new_export()), encoding="utf-8")
    (context["INPUT_DIR"] / "old.json").write_text(json.dumps({
        "issue_key": "OLD-1", "issues": {"summary": "Old issue"},
        "confluence_body": [{"contentid": "200", "title": "Old page", "body": "<p>Old text</p>"}],
    }), encoding="utf-8")
    report = context["convert_all"]()
    assert not report["errors"]
    assert report["relations"]["confluence_to_jira"]["alpha:100"] == ["alpha:TEST-1"]
    output = context["OUTPUT_DIR"]
    assert "<CLIENT_ID>" in (output / "confluence_alpha_100.md").read_text()
    assert "Текст не извлечён" in (output / "confluence_alpha_101.md").read_text()
    assert "Участник ПСИ" in (output / "jira_alpha_TEST-1.md").read_text()
    assert "Old text" in (output / "confluence_200.md").read_text()


def test_confluence_collects_jira_from_all_json(tmp_path):
    create_workspace(tmp_path)
    _write_json(tmp_path, "ABC-1", "Bug")
    _write_json(tmp_path, "ABC-2", "Bug")
    workspace = Workspace(tmp_path)

    result = prepare_jira_confluence(
        workspace,
        FakeProvider(['{"summary":"Профиль третьих лиц и код TP-01."}']),
    )

    confluence = next(
        card
        for card in load_search_cards(workspace)
        if card.record_type == "confluence"
    )
    assert result.llm_calls == 1
    assert set(confluence.related_ids) == {"jira:ABC-1", "jira:ABC-2"}


def test_prepare_ignores_nested_jupyter_checkpoint_json(tmp_path):
    create_workspace(tmp_path)
    _write_json(tmp_path, "ABC-1", "Bug")
    checkpoint = tmp_path / "raw/sources/.ipynb_checkpoints/ABC-checkpoint.json"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text("not a source", encoding="utf-8")

    result = prepare_jira_confluence(
        Workspace(tmp_path),
        FakeProvider(['{"summary":"Профиль третьих лиц."}']),
    )

    assert result.jira_count == 1
    assert result.confluence_count == 1


def test_confluence_summary_cache_depends_on_sha_and_model(tmp_path):
    create_workspace(tmp_path)
    _write_json(tmp_path, "ABC-1", "Bug")
    workspace = Workspace(tmp_path)

    first = prepare_jira_confluence(
        workspace,
        FakeProvider(['{"summary":"Модель A."}']),
        llm_model_name="model-a",
    )
    reused = prepare_jira_confluence(
        workspace,
        FakeProvider([]),
        llm_model_name="model-a",
    )
    changed_model = prepare_jira_confluence(
        workspace,
        FakeProvider(['{"summary":"Модель B."}']),
        llm_model_name="model-b",
    )

    assert first.llm_calls == 1
    assert reused.llm_calls == 0
    assert changed_model.llm_calls == 1


def test_bug_query_expands_confluence_to_related_bug_jira(tmp_path):
    create_workspace(tmp_path)
    _write_json(tmp_path, "ABC-1", "Bug")
    _write_json(tmp_path, "ABC-2", "Task")
    workspace = Workspace(tmp_path)
    prepare_jira_confluence(
        workspace,
        FakeProvider(['{"summary":"Добавление профиля третьих лиц."}']),
    )
    cards = load_search_cards(workspace)
    confluence = next(card for card in cards if card.record_type == "confluence")

    selected = select_evidence_cards(
        workspace,
        "Дай все баги про добавление профиля третьих лиц",
        [confluence.card_path],
    )

    assert {card.card_id for card in selected.cards} == {
        "confluence:100",
        "jira:ABC-1",
    }


def test_ingest_consolidates_more_than_eight_final_topics():
    too_many = _knowledge_json(9)
    consolidated = _knowledge_json(3)
    provider = FakeProvider([consolidated])

    result = _consolidate_merged_topics(
        provider,
        too_many,
        source_path="raw/sources/ABC-1.json",
        user_request="",
    )

    assert json.loads(result)["topics"] == json.loads(consolidated)["topics"]
    assert len(provider.requests) == 1
    assert (
        provider.requests[0].operation
        == "jira_confluence_ingest_consolidate_topics"
    )


def _write_json(root, key: str, issue_type: str) -> None:
    payload = {
        "issue_key": key,
        "issues": {
            "issue_key": key,
            "summary": "Ошибка добавления профиля",
            "issuetype_name": issue_type,
            "status_name": "Open",
            "description": "Не добавляется профиль третьего лица.",
        },
        "users": [],
        "customfield": [],
        "links": [],
        "remotelink": [],
        "confluence_body": [
            {
                "contentid": "100",
                "title": "Добавление профиля третьих лиц",
                "version": "1",
                "body": "<p>Добавление профиля. Код ошибки TP-01.</p>",
            }
        ],
        "traceability": [],
    }
    (root / "raw/sources" / f"{key}.json").write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


def _knowledge_json(topic_count: int) -> str:
    return json.dumps(
        {
            "protocol": "knowledge-v1",
            "summary": "Интегрировать знания.",
            "source_title": "Jira ABC-1",
            "source_summary": "Связанные Jira и Confluence.",
            "source_limitations": [],
            "conflicts": [],
            "topics": [
                {
                    "title": f"Тема {number}",
                    "summary": f"Описание темы {number}.",
                    "claims": [f"Утверждение {number}."],
                    "aliases": [],
                    "tags": ["jira"],
                    "category": "concepts",
                    "index_section": "Jira и Confluence",
                    "related_topics": [],
                    "limitations": [],
                }
                for number in range(1, topic_count + 1)
            ],
        },
        ensure_ascii=False,
    )
