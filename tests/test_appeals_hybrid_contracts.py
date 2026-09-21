"""Cache-free regression contracts for appeals hybrid retrieval."""
from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib
import importlib.util
import json
import sys
import threading
import time
import types
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "workspace/skills/appeals-analyzer"
PACKAGE = "appeals_analyzer_contract_runtime"
if PACKAGE not in sys.modules:
    spec = importlib.util.spec_from_file_location(
        PACKAGE, SKILL / "__init__.py", submodule_search_locations=[str(SKILL)]
    )
    assert spec is not None and spec.loader is not None
    package = importlib.util.module_from_spec(spec)
    sys.modules[PACKAGE] = package
    spec.loader.exec_module(package)

bge = importlib.import_module(f"{PACKAGE}.utils.bge_search_engine")
sva = importlib.import_module(f"{PACKAGE}.utils.sva_metrics")
hypothesis = importlib.import_module(f"{PACKAGE}.scripts.appeals_hypothesis")
reports = importlib.import_module(f"{PACKAGE}.scripts.appeals_reports")
gp = importlib.import_module(f"{PACKAGE}.utils.greenplum_engine")
session_manager = importlib.import_module(f"{PACKAGE}.utils.session_extract_manager")
clear_session_extract = session_manager.clear_session_extract
set_session_extract = session_manager.set_session_extract


def test_structured_parser_and_exact_canonicalization():
    parsed = gp.parse_structured_analytical_request('" кредиты ", "", " IVR ", "жалобы за 2026 год"')
    assert parsed == {
        "products": ["Кредиты"], "subproducts": [], "channels": ["IVR"],
        "query": "жалобы за 2026 год",
    }
    assert gp.parse_structured_analytical_request(
        '"Кредиты, Кредитные карты", "Потребительский кредит", "СБОЛ", "текст"'
    )["products"] == ["Кредиты", "Кредитные карты"]
    assert gp.parse_structured_analytical_request(
        '"Валюта, валютные операции", "", "", "текст"'
    )["products"] == ["Валюта, валютные операции"]
    assert gp.parse_structured_analytical_request(
        '"Валюта, валютные операции, Кредиты", "", "", "текст"'
    )["products"] == ["Валюта, валютные операции", "Кредиты"]
    assert gp.parse_structured_analytical_request(
        '"", "", "IVR, Рассылки (SMS, email, почта)", "текст"'
    )["channels"] == ["IVR", "Рассылки (SMS, email, почта)"]
    assert gp.parse_structured_analytical_request(
        '"", "", "Сайт ""ДомКлик""", "текст"'
    )["channels"] == ['Сайт "ДомКлик"']
    with pytest.raises(ValueError):
        gp.parse_structured_analytical_request('"кредит", "", "", "текст"')
    with pytest.raises(ValueError, match="канала"):
        gp.parse_structured_analytical_request('"", "", "Несуществующий канал", "текст"')
    with pytest.raises(ValueError):
        gp.parse_structured_analytical_request('"Кредиты", "", "текст"')


def test_canonical_parser_rejects_unparseable_and_ambiguous_segmentation():
    with pytest.raises(ValueError, match="Невозможно разобрать"):
        gp._canonicalize_field("Кредиты, несуществующее", gp.PRD_CANONICAL, "продукта")
    with pytest.raises(ValueError, match="Неоднозначный"):
        gp._canonicalize_field("A, B, C", ["A", "A, B", "B, C", "C"], "значения")


def test_product_prefilter_is_structural_and_and_combined():
    sql, params = gp.build_product_prefilter_sql(
        ["Кредиты", "Кредитные карты"], ["Потребительский кредит"], ["IVR", "СБОЛ"],
    )
    assert all(predicate in sql for predicate in ("a.prd IN", "a.s_prd IN", "a.chnl IN"))
    assert " AND " in sql
    assert not any(word in sql.upper() for word in ("ILIKE", "LIMIT", "ЖАЛОБ"))
    assert params.count("Кредиты") == len(gp.DEFAULT_YEARS)
    assert params.count("IVR") == len(gp.DEFAULT_YEARS)


def test_channel_only_structural_prefilter_is_supported():
    sql, params = gp.build_product_prefilter_sql([], [], ["Сайт \"ДомКлик\""])
    assert "a.chnl IN (%s)" in sql
    assert "a.prd IN" not in sql and "a.s_prd IN" not in sql
    assert params == ["Сайт \"ДомКлик\""] * len(gp.DEFAULT_YEARS)


def test_gp_date_prefilter_is_parameterized_on_req_reg_date_only():
    sql, params = gp.build_product_prefilter_sql(
        ["Кредиты"], [], date_range=("2026-01-01", "2026-12-31"),
    )
    assert "a.req_reg_date >= %s" in sql
    assert "a.req_reg_date < %s" in sql
    assert "2026-01-01" not in sql and "2027-01-01" not in sql
    assert params == ["Кредиты", date(2026, 1, 1), date(2027, 1, 1)]
    assert not any(value in sql for value in (
        "a.created", "a.req_created", "a.app_created", "CAST(a.req_reg_date", "COALESCE(a.req_reg_date",
    ))
    assert sql.count("SELECT ") == 1
    assert "req_reg_date" not in sql.split(" FROM ", 1)[0]


def test_gp_year_pruning_uses_only_supported_intersecting_tables():
    one_year_sql, _ = gp.build_product_prefilter_sql(
        ["Кредиты"], [], date_range=("2026-01-01", "2026-12-31"),
    )
    cross_year_sql, _ = gp.build_product_prefilter_sql(
        ["Кредиты"], [], date_range=("2025-11-01", "2026-02-01"),
    )
    all_years_sql, _ = gp.build_product_prefilter_sql(["Кредиты"], [], date_range=None)
    assert "appeal_2026" in one_year_sql and "appeal_2025" not in one_year_sql
    assert "appeal_2025" in cross_year_sql and "appeal_2026" in cross_year_sql
    assert "appeal_2024" not in cross_year_sql
    assert all(f"appeal_{year}" in all_years_sql for year in gp.DEFAULT_YEARS)


def test_product_date_masks_cover_all_combinations(monkeypatch):
    monkeypatch.setattr(bge, "doc_ids", [1, "2", " 3 "])
    monkeypatch.setattr(bge, "req_reg_dates", ["2026-01-01", "2025-05-01", "2026-12-31"])
    monkeypatch.setattr(bge, "id_to_positions", {"1": [0], "2": [1], "3": [2]})
    assert bge.build_allowed_mask(["1", "2", "3"]).tolist() == [True, True, True]
    assert bge.build_allowed_mask(None, ("2026-01-01", "2026-12-31")).tolist() == [True, False, True]
    assert bge.build_allowed_mask(["1", "3"], ("2026-01-01", "2026-12-31")).tolist() == [True, False, True]
    assert not bge.build_allowed_mask([], None).any()
    assert not bge.build_allowed_mask(["missing"], None).any()
    assert bge.build_allowed_mask() is None


def test_normalized_duplicate_ids_use_best_rank(monkeypatch):
    monkeypatch.setattr(bge, "doc_ids", [123, "123", " 123 ", "456"])
    ranks = {}
    bge._record_best_rank(ranks, 2, 3)
    bge._record_best_rank(ranks, 0, 1)
    bge._record_best_rank(ranks, 1, 2)
    bge._record_best_rank(ranks, 3, 4)
    assert ranks == {"123": 1, "456": 4}
    assert bge.fuse_rrf_rank_maps(ranks, {"123": 2}).count("123") == 1


def test_cache_metadata_mismatch_fails_fast():
    with pytest.raises(RuntimeError, match="FAISS ntotal"):
        bge.validate_cache_metadata(["1", "2"], [None, None], types.SimpleNamespace(ntotal=1))
    with pytest.raises(RuntimeError, match="req_reg_dates"):
        bge.validate_cache_metadata(["1", "2"], [None], types.SimpleNamespace(ntotal=2))


def test_hydration_collapses_task_dialog_cartesian_product():
    rows = []
    for dialogue in ("fragment one", "fragment two"):
        for task in ("a", "b", "c"):
            rows.append({"id": "123", "short_description": "x", "msg_pprb_chat": dialogue, "task_id": task})
    result = gp.normalize_hydrated_appeals(pd.DataFrame(rows))
    assert len(result) == 1
    semantic_dialogue = result.loc[0, "msg_pprb_chat"]
    assert semantic_dialogue.count("fragment one") == 1
    assert semantic_dialogue.count("fragment two") == 1
    assert result.loc[0, "task_id"] == ["a", "b", "c"]


def test_hydration_design_aggregates_relations_before_merge_and_preserves_tasks():
    sql = gp.build_hydration_sql(["123"], years=[2026])
    assert "appeal_dialogs" not in sql and "appeal_task" not in sql and " JOIN " not in sql
    assert "CAST(a.cust_epk_id AS VARCHAR) AS cust_epk_id" in sql
    assert "CAST(a.req_reg_date AS VARCHAR) AS req_reg_date" in sql
    assert "CAST(a.req_reg_date AS VARCHAR) AS date" in sql
    assert "COALESCE(CAST(a.created AS VARCHAR)" not in sql
    base = pd.DataFrame([{
        "source_year": 2026, "app_row_id": "row-1", "id": "123",
        "cust_epk_id": "epk-456", "req_reg_date": "2026-06-15",
        "date": "2026-06-15", "created": "2025-12-31", "short_description": "x",
    }])
    dialogs = pd.DataFrame([
        {"source_year": 2026, "app_row_id": "row-1", "msg_pprb_chat": "fragment one"},
        {"source_year": 2026, "app_row_id": "row-1", "msg_pprb_chat": "fragment two"},
    ])
    tasks = pd.DataFrame([
        {"source_year": 2026, "app_row_id": "row-1", "task_id": "1", "task_status": "A", "task_result": "R1"},
        {"source_year": 2026, "app_row_id": "row-1", "task_id": "2", "task_status": "B", "task_result": "R2"},
        {"source_year": 2026, "app_row_id": "row-1", "task_id": "2", "task_status": "B", "task_result": "R2"},
    ])
    result = gp.merge_hydration_frames(base, dialogs, tasks)
    assert len(result) == 1
    assert result.loc[0, "cust_epk_id"] == "epk-456"
    assert result.loc[0, "req_reg_date"] == "2026-06-15"
    assert result.loc[0, "date"] == result.loc[0, "req_reg_date"]
    assert result.loc[0, "msg_pprb_chat"].count("fragment one") == 1
    assert result.loc[0, "msg_pprb_chat"].count("fragment two") == 1
    assert [(task["task_id"], task["task_status"], task["task_result"]) for task in result.loc[0, "tasks"]] == [
        ("1", "A", "R1"), ("2", "B", "R2")
    ]


def test_threshold_keeps_all_passes_and_fallback_is_scored_only():
    scores = pd.DataFrame({"id": range(3000), "score": np.full(3000, .51)})
    final, fallback = bge.select_threshold_or_fallback(scores)
    assert len(final) == 3000 and not fallback
    final, fallback = bge.select_threshold_or_fallback(
        pd.DataFrame({"id": range(3000), "score": np.full(3000, .49)})
    )
    assert len(final) == 2048 and fallback
    with pytest.raises(RuntimeError):
        bge.select_threshold_or_fallback(pd.DataFrame({"id": [1]}))


def test_sva_classifier_is_isolated_and_remains_functional(monkeypatch):
    captured = []
    def ask(messages):
        captured.append(messages[0]["content"])
        return '{"1":"101","2":"102"}'
    local_qwen = importlib.import_module(f"{PACKAGE}.utils.local_qwen")
    monkeypatch.setattr(local_qwen, "def_ask_gigachat", ask)
    result = sva.batch_classify_sva_metrics(["one", "two"], batch_size=2)
    assert result == ["101", "102"]
    assert not hasattr(bge, "batch_classify_sva_metrics")
    assert not hasattr(bge, "prepare_texts_for_metrics")
    prompt = captured[0]
    for phrase in (
        "Финансовые потери клиентов", "Необоснованный отказ", "Нарушение срока",
        "Нарушение стандартов коммуникации", "Недобросовестные практики продаж",
    ):
        assert phrase in prompt


class _FakeEmbed:
    def encode(self, texts, **kwargs):
        vectors = []
        for text in texts:
            vectors.append([0.0, 1.0] if "semantic_query" in text or "second" in text else [1.0, 0.0])
        return np.asarray(vectors, dtype="float32")


class _FakeIndex:
    def __init__(self, dimension):
        self.vectors = None
    def add(self, vectors):
        self.vectors = vectors
    def search(self, query, k):
        scores = query @ self.vectors.T
        order = np.argsort(-scores, axis=1)[:, :k]
        return np.take_along_axis(scores, order, axis=1), order


def test_compute_devices_use_all_visible_gpus_and_allow_explicit_selection(monkeypatch):
    torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True, device_count=lambda: 2))
    monkeypatch.delenv("APPEALS_CUDA_DEVICES", raising=False)
    assert bge.resolve_compute_devices(torch) == ["cuda:0", "cuda:1"]
    monkeypatch.setenv("APPEALS_CUDA_DEVICES", "1,0")
    assert bge.resolve_compute_devices(torch) == ["cuda:1", "cuda:0"]
    monkeypatch.setenv("APPEALS_CUDA_DEVICES", "0,2")
    with pytest.raises(ValueError, match="2 visible GPU"):
        bge.resolve_compute_devices(torch)


def test_safe_default_model_placement_uses_one_replica_per_gpu(monkeypatch):
    torch = types.SimpleNamespace(cuda=types.SimpleNamespace(is_available=lambda: True, device_count=lambda: 2))
    for name in ("APPEALS_CUDA_DEVICES", "APPEALS_EMBED_DEVICES", "APPEALS_RERANK_DEVICES"):
        monkeypatch.delenv(name, raising=False)
    assert bge.resolve_model_devices("embed", torch) == ["cuda:0"]
    assert bge.resolve_model_devices("reranker", torch) == ["cuda:1"]
    monkeypatch.setenv("APPEALS_CUDA_DEVICES", "1,0")
    assert bge.resolve_model_devices("embed", torch) == ["cuda:1"]
    assert bge.resolve_model_devices("reranker", torch) == ["cuda:0"]
    monkeypatch.setenv("APPEALS_RERANK_DEVICES", "0,1")
    assert bge.resolve_model_devices("reranker", torch) == ["cuda:0", "cuda:1"]


def test_multi_gpu_embedding_and_reranker_preserve_order_and_use_both_devices():
    embed_calls, rerank_calls = [], []

    class EmbedReplica:
        def __init__(self, device): self.device = device
        def encode(self, values, **kwargs):
            embed_calls.append((self.device, list(values)))
            return np.asarray([[int(value)] for value in values], dtype="float32")

    class RerankerReplica:
        def __init__(self, device): self.device = device
        def predict(self, pairs, **kwargs):
            rerank_calls.append((self.device, list(pairs)))
            return np.asarray([int(pair[1]) for pair in pairs], dtype="float32")

    devices = ["cuda:0", "cuda:1"]
    embed = bge.MultiDeviceSentenceTransformer([EmbedReplica(d) for d in devices], devices)
    reranker = bge.MultiDeviceCrossEncoder([RerankerReplica(d) for d in devices], devices)
    assert embed.encode(["0", "1", "2", "3", "4"]).reshape(-1).tolist() == [0, 1, 2, 3, 4]
    assert reranker.predict([("q", str(i)) for i in range(5)]).tolist() == [0, 1, 2, 3, 4]
    assert sorted(embed_calls) == [("cuda:0", ["0", "1", "2"]), ("cuda:1", ["3", "4"])]
    assert sorted(rerank_calls) == [
        ("cuda:0", [("q", "0"), ("q", "1"), ("q", "2")]),
        ("cuda:1", [("q", "3"), ("q", "4")]),
    ]


def test_small_session_faiss_uses_vector_similarity_not_lexical(monkeypatch):
    fake_faiss = types.ModuleType("faiss")
    fake_faiss.IndexFlatIP = _FakeIndex
    fake_faiss.normalize_L2 = lambda matrix: None
    monkeypatch.setitem(sys.modules, "faiss", fake_faiss)
    monkeypatch.setattr(bge, "get_bge_models", lambda: (_FakeEmbed(), None))
    rows = {
        "1": {"id": "1", "desc": "alpha lexical match", "dialogue": "first"},
        "2": {"id": "2", "desc": "no lexical overlap", "dialogue": "second"},
    }
    assert bge.build_and_cache_small_index("session", rows)
    found = bge.search_small_index("session", "alpha semantic_query", max_candidates=2)
    assert bge._SMALL_FAISS_SESSION_CACHE["session"]["mode"] == "faiss"
    assert found[0]["id"] == "2"
    assert found[0]["_candidate_mode"] == "semantic"
    assert isinstance(found[0]["_similarity_score"], float)


def test_session_index_clear_and_replacement(monkeypatch):
    fake_faiss = types.ModuleType("faiss")
    fake_faiss.IndexFlatIP = _FakeIndex
    fake_faiss.normalize_L2 = lambda matrix: None
    monkeypatch.setitem(sys.modules, "faiss", fake_faiss)
    monkeypatch.setattr(bge, "get_bge_models", lambda: (_FakeEmbed(), None))
    assert bge.build_and_cache_small_index("lifecycle", {"old": {"id": "old", "dialogue": "first"}})
    bge.clear_small_index("lifecycle")
    assert bge.search_small_index("lifecycle", "semantic_query") == []
    assert bge.build_and_cache_small_index("lifecycle", {"new": {"id": "new", "dialogue": "second"}})
    assert [row["id"] for row in bge.search_small_index("lifecycle", "semantic_query")] == ["new"]


def test_failed_new_search_clears_old_session_index(monkeypatch):
    session_id = "failed-new-search"
    clear_session_extract(session_id)
    bge._SMALL_FAISS_SESSION_CACHE[session_id] = {"rows": [{"id": "old"}], "index": None}
    monkeypatch.setattr(reports, "extract_search_params", lambda query: {"date_range": None})
    monkeypatch.setattr(reports, "retrieve_hybrid_adaptive", lambda *args: [])
    result = asyncio.run(reports.run_appeals_report(session_id, '"", "", "", "new query"'))
    assert "не найдено" in result
    assert session_id not in bge._SMALL_FAISS_SESSION_CACHE
    assert bge.search_small_index(session_id, "old") == []


def test_missing_local_models_never_calls_remote_identifier(monkeypatch):
    calls = []
    module = types.ModuleType("sentence_transformers")
    module.SentenceTransformer = lambda path, **kwargs: calls.append(path)
    module.CrossEncoder = lambda path, **kwargs: calls.append(path)
    torch = types.ModuleType("torch")
    torch.cuda = types.SimpleNamespace(is_available=lambda: False)
    monkeypatch.setitem(sys.modules, "sentence_transformers", module)
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.delenv("APPEALS_BGE_MODEL_PATH", raising=False)
    monkeypatch.delenv("APPEALS_RERANKER_MODEL_PATH", raising=False)
    monkeypatch.setattr(bge, "_first_existing_model_path", lambda *args, **kwargs: None)
    bge._BGE_CACHE.clear()
    assert bge.get_bge_models() == (None, None)
    assert calls == []


def test_evidence_batching_parses_json_and_removes_invented_ids():
    df = pd.DataFrame({"id": [str(i) for i in range(35)], "short_description": ["x"] * 35, "description": ["y"] * 35})
    batches = hypothesis.build_evidence_batches(df)
    assert len(batches) > 1 and sum(len(batch["ids"]) for batch in batches) == 35
    async def run():
        return await hypothesis.analyze_evidence_batch(
            batches[0], 1,
            lambda messages: '{"observed_patterns":[],"root_cause_signals":[],"evidence":[{"statement":"x","appeal_ids":["invented"]}],"representative_ids":["0","invented"],"counterexamples":[],"possible_actions":[]}',
        )
    payload = asyncio.run(run())
    assert payload["representative_ids"] == ["0"]
    assert payload["evidence"][0]["appeal_ids"] == []
    assert payload["invalid_ids_removed"] == ["invented"]


def test_hypothesis_sample_is_exact_deterministic_and_stratified():
    frame = pd.DataFrame({
        "id": [str(i) for i in range(40)],
        "subj": ["rare"] * 2 + ["common"] * 38,
        "prd": ["cards"] * 20 + ["loans"] * 20,
        "date": pd.date_range("2026-01-01", periods=40, freq="D"),
    })
    first = hypothesis.select_hypothesis_sample(frame, fraction=.25)
    second = hypothesis.select_hypothesis_sample(frame.sample(frac=1, random_state=7), fraction=.25)
    assert len(first) == 10
    assert set(first.id) == set(second.id)
    assert set(first.subj) == {"rare", "common"}
    assert set(first.prd) == {"cards", "loans"}


def test_hypothesis_evidence_configuration_and_sample_cap():
    assert hypothesis.CONFIG.hypothesis_sample_size == 200
    assert hypothesis.CONFIG.hypothesis_batch_size == 20
    assert hypothesis.CONFIG.hypothesis_chars_per_appeal == 400
    assert hypothesis.CONFIG.hypothesis_batch_char_budget == 12_000
    frame = pd.DataFrame({
        "id": [str(i) for i in range(1000)],
        "prd": ["cards"] * 500 + ["loans"] * 500,
    })
    sample = hypothesis.select_hypothesis_sample(frame)
    assert len(sample) == 200
    assert set(sample.prd) == {"cards", "loans"}


def test_hypothesis_batches_are_capped_at_20_with_full_sample_coverage():
    frame = pd.DataFrame({
        "id": [str(i) for i in range(200)],
        "short_description": ["short"] * 200,
        "description": ["dialogue"] * 200,
    })
    batches = hypothesis.build_evidence_batches(frame)
    assert len(batches) == 10
    assert all(len(batch["ids"]) <= 20 for batch in batches)
    assert sum(len(batch["ids"]) for batch in batches) == 200


def test_loaded_columns_profile_uses_full_population_and_skips_text():
    profiler = importlib.import_module(f"{PACKAGE}.scripts.appeals_profiler")
    frame = pd.DataFrame({
        "id": [str(i) for i in range(8)],
        "subj": ["A"] * 6 + ["B"] * 2,
        "Метрика СВА": ["101"] * 5 + ["102"] * 3,
        "description": ["long dialogue " * 30] * 8,
    })
    profile = profiler.build_loaded_columns_profile(frame)
    assert profile["total_appeals"] == 8
    assert profile["columns"]["subj"]["top_values"][0] == {
        "value": "A", "count": 6, "percent_of_all": 75.0,
    }
    assert "Метрика СВА" not in profile["columns"]
    assert "description" not in profile["columns"]
    assert "СВА" not in profiler.profile_complaints_dataframe(frame)


def test_user_facing_profile_has_clean_heading_dates_grp_top5_and_no_raw_texts():
    profiler = importlib.import_module(f"{PACKAGE}.scripts.appeals_profiler")
    frame = pd.DataFrame({
        "id": [str(i) for i in range(7)],
        "prd": ["Страхование"] * 7,
        "subj": ["Консультация"] * 7,
        "grp": ["G1", "G1", "G2", "G3", "G4", "G5", "G6"],
        "date": ["2026-01-03", "2026-01-20", "2026-02-10", "2026-02-11", "2026-02-12", "2026-02-13", "2026-02-14"],
        "task_status": ["CLOSED"] * 7,
        "short_description": ["raw appeal text"] * 7,
        "description": ["raw dialogue"] * 7,
    })
    result = profiler.profile_complaints_dataframe(frame)
    assert "### Математический и частотный профиль массива обращений\n" in result
    assert "Greenplum" not in result
    assert "2026-01-31" in result and "2026-02-28" in result
    assert "Месяц 2026-" not in result
    assert "Распределение по группам обращений (Top-5)" in result
    assert all(group in result for group in ("G1", "G2", "G3", "G4", "G5"))
    assert "G6:" not in result
    assert "Статусы исполнения связанных задач" not in result
    assert "Частотная статистика обращений по всей выгрузке" not in result
    assert "raw appeal text" not in result and "raw dialogue" not in result


def _valid_evidence_payload(appeal_ids=None, representative_ids=None):
    return {
        "observed_patterns": [], "root_cause_signals": [],
        "evidence": [{"statement": "signal", "appeal_ids": appeal_ids or ["2"]}],
        "representative_ids": representative_ids or ["1"],
        "counterexamples": [], "possible_actions": [],
    }


def test_evidence_schema_is_strict_and_valid_payload_is_grounded():
    missing = hypothesis.validate_evidence_payload({"observed_patterns": []}, ["1"], 1)
    wrong = hypothesis.validate_evidence_payload({**_valid_evidence_payload(), "evidence": "bad"}, ["1", "2"], 1)
    valid = hypothesis.validate_evidence_payload(_valid_evidence_payload(["2", "999"]), ["1", "2"], 1)
    assert missing["status"] == "invalid_schema"
    assert wrong["status"] == "invalid_schema"
    assert valid["status"] == "ok"
    assert valid["evidence"] == [{"statement": "signal", "appeal_ids": ["2"]}]
    assert valid["invalid_ids_removed"] == ["999"]
    assert hypothesis.validate_narrative_grounding("Факт, ID: 2", ["1", "2"])[0]
    assert hypothesis.validate_narrative_grounding("Факт, ID: 999", ["1", "2"]) == (False, ["999"])


def test_evidence_provenance_survives_multiple_reduction_levels(monkeypatch):
    payloads = [hypothesis.validate_evidence_payload(_valid_evidence_payload(), ["1", "2", "3"], i) for i in range(9)]
    monkeypatch.setattr(hypothesis, "CONFIG", types.SimpleNamespace(hypothesis_reduce_char_budget=1))
    response = json.dumps(_valid_evidence_payload(["2", "999"], ["1"]), ensure_ascii=False)
    reduced = asyncio.run(hypothesis.reduce_structured_evidence(payloads, lambda messages: response))
    assert len(reduced) == 1
    assert reduced[0]["_source_ids"] == ["1", "2", "3"]
    assert reduced[0]["evidence"][0]["appeal_ids"] == ["2"]
    assert reduced[0]["invalid_ids_removed"] == ["999"]


def test_hypothesis_context_truncates_only_payload_and_respects_budget():
    dialogue = "HEAD-" + ("x" * 5000) + "-TAIL"
    frame = pd.DataFrame([{"id": str(i), "short_description": "desc", "description": dialogue} for i in range(12)])
    original = frame.copy(deep=True)
    batches = hypothesis.build_evidence_batches(frame)
    first = batches[0]["items"][0]
    appeal_text = first.split("Текст обращения:\n", 1)[1]
    assert len(appeal_text) <= hypothesis.CONFIG.hypothesis_chars_per_appeal
    assert "HEAD-" in first and "-TAIL" in first
    for batch in batches:
        assert len("\n---\n".join(batch["items"])) <= hypothesis.CONFIG.hypothesis_batch_char_budget
        assert len(batch["ids"]) <= hypothesis.CONFIG.hypothesis_batch_size
    pd.testing.assert_frame_equal(frame, original)


def test_final_report_omits_sampling_and_uncertainty_disclosures(monkeypatch):
    frame = pd.DataFrame({
        "id": [str(i) for i in range(400)],
        "prd": ["cards"] * 200 + ["loans"] * 200,
        "short_description": ["description"] * 400,
        "description": ["dialogue"] * 400,
    })
    prompts = []
    evidence_json = json.dumps(_valid_evidence_payload(["0"], ["0"]), ensure_ascii=False)
    def ask(messages):
        prompt = messages[0]["content"]
        prompts.append(prompt)
        if "СТРОГО один JSON-объект" in prompt:
            return evidence_json
        detail = (
            "Что происходит: клиенты подробно описывают одну и ту же проблему в обслуживании. "
            "Что на это указывает: в обращениях повторяются одинаковые обстоятельства и последствия для клиента. "
            "Возможная причина: порядок действий не объясняется достаточно ясно или выполняется не полностью. "
            "Влияние на клиента: вопрос остаётся нерешённым и требует дополнительного обращения. "
            "Что проверить: сопоставить обращения с фактическими этапами обслуживания и ответами исполнителей."
        )
        return "\n\n".join(f"**Гипотеза {number}. Проверяемая причина {number}.**\n{detail}" for number in range(1, 5))
    monkeypatch.setattr(hypothesis, "_get_ask_llm_fn", lambda: ask)
    result = asyncio.run(hypothesis.generate_complaint_hypothesis_narrative("query", frame))
    assert "### Аналитический отчёт" in result
    assert "### Гипотезы" in result
    assert all(f"Гипотеза {number}" in result for number in range(1, 5))
    assert "300 обращений" not in result
    assert "Uncertainties" not in result
    final_prompts = [prompt for prompt in prompts if "Сформируй четыре подробные" in prompt]
    assert final_prompts and "300 из 400" not in final_prompts[-1]


def test_fallback_report_still_contains_four_detailed_hypotheses():
    frame = pd.DataFrame([{"id": "1"}])
    result = hypothesis._build_fallback_narrative_report("query", frame, "profile")
    assert all(f"Гипотеза {number}" in result for number in range(1, 5))
    assert len(result) > 1200


def test_reranker_reduces_batch_after_cuda_oom(monkeypatch):
    calls = []
    class Reranker:
        devices = ["cuda:1"]
        def predict(self, pairs, batch_size):
            calls.append(batch_size)
            if batch_size > 2:
                raise RuntimeError("CUDA out of memory")
            return np.asarray([1.0] * len(pairs))
    monkeypatch.setattr(bge, "get_bge_models", lambda: (None, Reranker()))
    monkeypatch.setattr(bge, "_cuda_has_headroom", lambda *args, **kwargs: True)
    monkeypatch.setattr(bge, "_release_cuda_cache", lambda: None)
    frame = pd.DataFrame([{"id": "1", "description": "a"}, {"id": "2", "description": "b"}])
    result = bge.rerank_dataframe("query", frame)
    assert calls == [8, 4, 2]
    assert len(result) == 2


def test_metadata_precedence_and_explicit_selection(tmp_path, monkeypatch):
    meta = tmp_path / "meta.pkl"; meta.write_bytes(b"primary")
    final = tmp_path / "meta_final.pkl"; final.write_bytes(b"fallback")
    monkeypatch.delenv("APPEALS_RAG_META_FILE", raising=False)
    assert bge.resolve_rag_metadata_path(tmp_path) == meta
    meta.unlink()
    assert bge.resolve_rag_metadata_path(tmp_path) == final
    explicit = tmp_path / "chosen.pkl"; explicit.write_bytes(b"chosen")
    monkeypatch.setenv("APPEALS_RAG_META_FILE", "chosen.pkl")
    assert bge.resolve_rag_metadata_path(tmp_path) == explicit


@pytest.mark.parametrize("value, expected", [
    (["2026-01-01", "2026-12-31"], ("2026-01-01", "2026-12-31")),
    (["2026-99-01", "2026-12-31"], None),
    (["2026-12-31", "2026-01-01"], None),
    (["foo", "bar"], None),
])
def test_date_range_validation(value, expected):
    assert reports.validate_date_range(value) == expected


def test_date_extraction_uses_deterministic_year_fallback(monkeypatch):
    monkeypatch.setattr(reports, "def_ask_gigachat", lambda messages: "malformed")
    assert reports.extract_search_params("жалобы за 2026 год")["date_range"] == ("2026-01-01", "2026-12-31")


def test_retrieve_hybrid_masks_before_faiss_and_bm25(monkeypatch):
    class Selector:
        def __init__(self, selected): self.selected = {int(value) for value in selected}
    class Params:
        def __init__(self): self.sel = None; self.nprobe = None
    class Index:
        ntotal = 4
        nprobe = 1
        def search(self, vector, k, params=None):
            order = [0, 1, 2, 3]
            if params is not None:
                order = [position for position in order if position in params.sel.selected]
            order = order[:k]
            return np.asarray([[1.0] * len(order)]), np.asarray([order])
    captured = {}
    class BM25:
        scores = {"num_docs": 4}
        def retrieve(self, tokens, k, **kwargs):
            captured["weight_mask"] = kwargs.get("weight_mask").copy()
            order = [i for i in range(4) if kwargs["weight_mask"][i] > 0][:k]
            return np.asarray([order]), np.asarray([[1.0] * len(order)])
    fake_faiss = types.ModuleType("faiss")
    fake_faiss.IDSelectorBatch = Selector
    fake_faiss.SearchParametersIVF = Params
    monkeypatch.setitem(sys.modules, "faiss", fake_faiss)
    monkeypatch.setattr(bge, "load_pipeline_meta_and_indices", lambda: None)
    monkeypatch.setattr(bge, "get_bge_models", lambda: (_FakeEmbed(), None))
    monkeypatch.setattr(bge, "doc_ids", ["global-best", "wrong-date", "allowed", "wrong-product"])
    monkeypatch.setattr(bge, "req_reg_dates", ["2026-06-01", "2025-06-01", "2026-07-01", "2026-08-01"])
    monkeypatch.setattr(bge, "id_to_positions", {"global-best": [0], "wrong-date": [1], "allowed": [2], "wrong-product": [3]})
    monkeypatch.setattr(bge, "faiss_loaded", Index())
    monkeypatch.setattr(bge, "bm25_indexes", [(BM25(), 0)])
    result = bge.retrieve_hybrid_adaptive("semantic_query", ["wrong-date", "allowed"], ("2026-01-01", "2026-12-31"))
    assert result == ["allowed"]
    assert captured["weight_mask"].tolist() == [0.0, 0.0, 1.0, 0.0]


def test_followup_does_not_call_initial_greenplum(monkeypatch):
    session_id = "followup-contract"
    clear_session_extract(session_id)
    frame = pd.DataFrame([{"id": "12345"}])
    set_session_extract(session_id, frame, extra={"id_to_text_map": {"12345": {"id": "12345", "desc": "x", "dialogue": "y"}}, "hypothesis": "h"})
    monkeypatch.setattr(reports, "fetch_candidate_ids_by_product", lambda *args: pytest.fail("initial GP prefilter called"))
    monkeypatch.setattr(reports, "fetch_appeals_by_ids", lambda *args: pytest.fail("initial GP hydration called"))
    monkeypatch.setattr(reports, "answer_complaint_dialog", lambda *args, **kwargs: "follow-up")
    result = asyncio.run(reports.run_appeals_report(session_id, "поясни вывод"))
    assert result == "follow-up"


def test_pipeline_passes_same_date_range_to_gp_and_cache(monkeypatch):
    session_id = "date-wiring-contract"
    clear_session_extract(session_id)
    expected = ("2026-01-01", "2026-12-31")
    seen = {}
    hydrated = pd.DataFrame([{
        "id": "1", "date": "2026-05-01", "req_reg_date": "2026-05-01",
        "short_description": "a", "description": "a",
    }])
    monkeypatch.setattr(reports, "extract_search_params", lambda query: {"date_range": expected})
    def gp_prefilter(products, subproducts, channels=(), date_range=None):
        seen["channels"] = list(channels)
        seen["gp"] = date_range
        return ["1"]
    def retrieve(query, allowed_ids, date_range=None):
        seen["cache"] = date_range
        return ["1"]
    monkeypatch.setattr(reports, "fetch_candidate_ids_by_product", gp_prefilter)
    monkeypatch.setattr(reports, "retrieve_hybrid_adaptive", retrieve)
    monkeypatch.setattr(reports, "fetch_appeals_by_ids", lambda ids: hydrated)
    monkeypatch.setattr(reports, "rerank_dataframe", lambda query, frame: frame.assign(score=.9))
    monkeypatch.setattr(reports, "export_complaints_excel", lambda frame, query: {
        "xlsx_path": "x", "csv_path": "c", "name": "x", "count": len(frame),
    })
    monkeypatch.setattr(reports, "build_and_cache_small_index", lambda *args, **kwargs: True)
    async def narrative(*args, **kwargs):
        return "report"
    monkeypatch.setattr(reports, "generate_complaint_hypothesis_narrative", narrative)
    result = asyncio.run(reports.run_appeals_report(session_id, '"Кредиты", "", "IVR", "жалобы за 2026"'))
    assert result.startswith("report")
    assert seen == {"channels": ["IVR"], "gp": expected, "cache": expected}


def test_hydration_precedes_rerank_and_active_pipeline_skips_sva(monkeypatch):
    session_id = "pipeline-order-contract"
    clear_session_extract(session_id)
    calls = []
    hydrated = pd.DataFrame([
        {"id": "1", "short_description": "a", "description": "a"},
        {"id": "2", "short_description": "b", "description": "b"},
        {"id": "3", "short_description": "c", "description": "c"},
    ])
    monkeypatch.setattr(reports, "extract_search_params", lambda query: {"date_range": None})
    monkeypatch.setattr(reports, "retrieve_hybrid_adaptive", lambda *args: calls.append("retrieve") or ["1", "2", "3"])
    monkeypatch.setattr(reports, "fetch_appeals_by_ids", lambda ids: calls.append(("hydrate", list(ids))) or hydrated)
    def rerank(query, frame):
        calls.append(("rerank", list(frame.id)))
        result = frame.copy()
        result["score"] = [.9, .7, .1]
        return result
    monkeypatch.setattr(reports, "rerank_dataframe", rerank)
    monkeypatch.setattr(sva, "batch_classify_sva_metrics", lambda texts: pytest.fail("SVA classifier called"))
    monkeypatch.setattr(reports, "export_complaints_excel", lambda frame, query: calls.append(("export", list(frame.id))) or {"xlsx_path": "x", "csv_path": "c", "name": "x", "count": len(frame)})
    monkeypatch.setattr(reports, "build_and_cache_small_index", lambda session_id, mapping: calls.append(("index", list(mapping))) or True)
    async def narrative(*args, **kwargs):
        calls.append(("hypotheses", list(args[1].id)))
        return "report"
    monkeypatch.setattr(reports, "generate_complaint_hypothesis_narrative", narrative)
    result = asyncio.run(reports.run_appeals_report(session_id, '"", "", "", "query"'))
    assert result.startswith("report")
    assert calls == [
        "retrieve", ("hydrate", ["1", "2", "3"]),
        ("rerank", ["1", "2", "3"]), ("export", ["1", "2"]),
        ("index", ["1", "2"]), ("hypotheses", ["1", "2"]),
    ]
    assert not hasattr(reports, "batch_classify_sva_metrics")
    assert not hasattr(reports, "prepare_texts_for_metrics")


def test_full_final_dataset_is_exported_while_hypothesis_evidence_is_capped(monkeypatch):
    session_id = "full-export-contract"
    clear_session_extract(session_id)
    hydrated = pd.DataFrame({
        "id": [str(i) for i in range(500)],
        "short_description": ["short"] * 500,
        "description": ["full dialogue " * 100] * 500,
    })
    observed = {}
    monkeypatch.setattr(reports, "extract_search_params", lambda query: {"date_range": None})
    monkeypatch.setattr(reports, "retrieve_hybrid_adaptive", lambda *args: hydrated["id"].tolist())
    monkeypatch.setattr(reports, "fetch_appeals_by_ids", lambda ids: hydrated)
    monkeypatch.setattr(reports, "rerank_dataframe", lambda query, frame: frame.assign(score=.9))
    monkeypatch.setattr(reports, "export_complaints_excel", lambda frame, query: observed.update(export_count=len(frame)) or {"xlsx_path": "x", "csv_path": "c", "name": "x", "count": len(frame)})
    monkeypatch.setattr(reports, "build_and_cache_small_index", lambda *args, **kwargs: True)
    async def narrative(query, frame, export, total_db_count):
        sample = hypothesis.select_hypothesis_sample(frame)
        batches = hypothesis.build_evidence_batches(sample)
        observed.update(
            hypothesis_sample=len(sample),
            evidence_coverage=sum(len(batch["ids"]) for batch in batches),
            max_batch=max(len(batch["ids"]) for batch in batches),
        )
        return "report"
    monkeypatch.setattr(reports, "generate_complaint_hypothesis_narrative", narrative)

    result = asyncio.run(reports.run_appeals_report(session_id, '"", "", "", "query"'))

    assert result.startswith("report")
    assert observed == {
        "export_count": 500,
        "hypothesis_sample": 200,
        "evidence_coverage": 200,
        "max_batch": 20,
    }
    assert hydrated["description"].str.len().min() > 400


def test_depth_constants_are_preserved():
    assert (bge.CONFIG.faiss_k, bge.CONFIG.bm25_total_k) == (2500, 2000)
    assert (bge.CONFIG.rrf_k, bge.CONFIG.rrf_alpha) == (60, .3)
    assert (bge.CONFIG.score_threshold, bge.CONFIG.fallback_top_k) == (.5, 2048)


class _AppealsCursor:
    def __init__(self, rows=(("1",),), fail: Exception | None = None):
        self.description = [("id",)]
        self.rows = list(rows)
        self.fail = fail
        self.closed = False
        self.executed = None

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def execute(self, sql, params=None):
        self.executed = (sql, params)
        if self.fail is not None:
            raise self.fail

    def fetchall(self):
        return self.rows


class _AppealsConnection:
    def __init__(self, cursor):
        self._cursor = cursor

    def cursor(self):
        return self._cursor


def test_appeals_query_uses_canonical_shared_db_run(monkeypatch):
    expected_db = ROOT / "workspace/utils/db.py"
    assert Path(gp.db._shared_db.__file__).resolve() == expected_db.resolve()
    cursor = _AppealsCursor(rows=(("10",), ("20",)))
    connection = _AppealsConnection(cursor)
    calls = []

    def run(work):
        calls.append(work)
        return work(connection)

    monkeypatch.setattr(gp.db, "run", run)
    result = gp._run_sql("SELECT id FROM appeals WHERE id = %s", [10])

    assert len(calls) == 1
    assert result.to_dict("records") == [{"id": "10"}, {"id": "20"}]
    assert cursor.executed == ("SELECT id FROM appeals WHERE id = %s", (10,))
    assert cursor.closed is True


def test_appeals_sql_error_closes_cursor_and_next_job_can_run(monkeypatch):
    failing = _AppealsCursor(fail=ValueError("query failed"))
    succeeding = _AppealsCursor(rows=(("ok",),))
    connections = iter((_AppealsConnection(failing), _AppealsConnection(succeeding)))
    monkeypatch.setattr(gp.db, "run", lambda work: work(next(connections)))

    with pytest.raises(ValueError, match="query failed"):
        gp._run_sql("SELECT broken")
    result = gp._run_sql("SELECT healthy")

    assert failing.closed is True
    assert succeeding.closed is True
    assert result.loc[0, "id"] == "ok"


def test_hydration_uses_one_db_job_one_connection_and_all_4500_ids(monkeypatch):
    candidate_ids = [str(index) for index in range(4500)]
    connection = object()
    db_run_calls = []
    sql_calls = []
    builder_inputs = []
    state = {"inside_db_run": False}
    base = pd.DataFrame([{
        "source_year": 2026, "id": "1", "app_row_id": "app-1",
        "short_description": "appeal",
    }])
    dialogs = pd.DataFrame([{
        "source_year": 2026, "app_row_id": "app-1", "msg_pprb_chat": "dialog",
    }])
    tasks = pd.DataFrame([{
        "source_year": 2026, "app_row_id": "app-1", "task_id": "task-1",
    }])
    frames_by_label = {
        "hydration base": base,
        "hydration dialogs": dialogs,
        "hydration tasks": tasks,
    }

    def run(work):
        db_run_calls.append(work)
        state["inside_db_run"] = True
        try:
            return work(connection)
        finally:
            state["inside_db_run"] = False

    def execute_on_connection(conn, sql, params=None, *, label="query"):
        assert state["inside_db_run"] is True
        sql_calls.append((label, conn, sql))
        return frames_by_label[label]

    def build_base(ids, years=gp.DEFAULT_YEARS):
        builder_inputs.append(list(ids))
        return "BASE SQL"

    original_merge = gp.merge_hydration_frames

    def merge_after_release(*args):
        assert state["inside_db_run"] is False
        return original_merge(*args)

    monkeypatch.setattr(gp.db, "run", run)
    monkeypatch.setattr(gp, "_run_sql_on_connection", execute_on_connection)
    monkeypatch.setattr(gp, "build_hydration_sql", build_base)
    monkeypatch.setattr(gp, "build_dialog_hydration_sql", lambda ids: "DIALOGS SQL")
    monkeypatch.setattr(gp, "build_task_hydration_sql", lambda ids: "TASKS SQL")
    monkeypatch.setattr(gp, "merge_hydration_frames", merge_after_release)

    result = gp.fetch_appeals_by_ids(candidate_ids)

    assert len(db_run_calls) == 1
    assert builder_inputs == [candidate_ids]
    assert [label for label, _, _ in sql_calls] == [
        "hydration base", "hydration dialogs", "hydration tasks",
    ]
    assert all(conn is connection for _, conn, _ in sql_calls)
    assert result.loc[0, "id"] == "1"
    assert result.loc[0, "msg_pprb_chat"] == "dialog"
    assert result.loc[0, "task_id"] == "task-1"


def test_empty_hydration_base_skips_related_queries(monkeypatch):
    labels = []
    db_run_calls = []

    def run(work):
        db_run_calls.append(work)
        return work(object())

    def execute_on_connection(conn, sql, params=None, *, label="query"):
        labels.append(label)
        return pd.DataFrame()

    monkeypatch.setattr(gp.db, "run", run)
    monkeypatch.setattr(gp, "_run_sql_on_connection", execute_on_connection)
    monkeypatch.setattr(gp, "build_dialog_hydration_sql", lambda ids: pytest.fail("dialogs built"))
    monkeypatch.setattr(gp, "build_task_hydration_sql", lambda ids: pytest.fail("tasks built"))
    monkeypatch.setattr(gp, "merge_hydration_frames", lambda *args: pytest.fail("empty base merged"))

    result = gp.fetch_appeals_by_ids(["1", "2"])

    assert len(db_run_calls) == 1
    assert labels == ["hydration base"]
    assert result.empty


@pytest.mark.parametrize(
    "failed_label",
    ["hydration base", "hydration dialogs", "hydration tasks"],
)
def test_hydration_phase_errors_propagate_without_merge(monkeypatch, failed_label):
    labels = []
    base = pd.DataFrame([{
        "source_year": 2026, "id": "1", "app_row_id": "app-1",
    }])

    def execute_on_connection(conn, sql, params=None, *, label="query"):
        labels.append(label)
        if label == failed_label:
            raise ValueError(f"{label} failed")
        return base if label == "hydration base" else pd.DataFrame()

    run_calls = []
    monkeypatch.setattr(gp.db, "run", lambda work: run_calls.append(work) or work(object()))
    monkeypatch.setattr(gp, "_run_sql_on_connection", execute_on_connection)
    monkeypatch.setattr(gp, "merge_hydration_frames", lambda *args: pytest.fail("failed hydration merged"))

    with pytest.raises(ValueError, match="failed"):
        gp.fetch_appeals_by_ids(["1"])

    assert len(run_calls) == 1
    assert failed_label in labels


def test_parallel_appeals_queries_are_submitted_to_bounded_shared_runner(monkeypatch):
    active = 0
    maximum = 0
    lock = threading.Lock()
    slots = threading.Semaphore(4)

    def run(work):
        nonlocal active, maximum
        with slots:
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                time.sleep(0.02)
                return work(_AppealsConnection(_AppealsCursor()))
            finally:
                with lock:
                    active -= 1

    monkeypatch.setattr(gp.db, "run", run)
    with ThreadPoolExecutor(max_workers=10) as executor:
        frames = list(executor.map(lambda _: gp._run_sql("SELECT 1"), range(10)))

    assert len(frames) == 10
    assert 1 < maximum <= 4


def test_appeals_active_runtime_contains_no_private_connection_path():
    skill = ROOT / "workspace/skills/appeals-analyzer"
    sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in skill.rglob("*.py")
        if "tests" not in path.parts
    )
    assert "psycopg2.connect" not in sources
    assert "GREENPLUM_DSN" not in sources
    assert "PG_DSN" not in sources
    assert "DEFAULT_GP_DSN" not in sources
    assert "db._connect(" not in sources
    assert "ThreadedConnectionPool" not in sources
    assert "SimpleConnectionPool" not in sources
    assert "threading.Semaphore" not in sources
