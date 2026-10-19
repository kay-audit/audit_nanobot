import sys
import types

import numpy as np
import pytest

from lib.services.hybrid_search import HybridIndex, build_index, clear_index_cache, load_index, prepare_from_frame


def test_prepare_is_deterministic_and_drops_duplicate_ids():
    docs = prepare_from_frame([{"id": 2, "text": "beta"}, {"id": 1, "text": "alpha"}, {"id": 2, "text": "duplicate"}], text_fields=("text",))
    assert [d.id for d in docs] == ["2", "1"]
    assert docs[0].text == "beta"


def test_rank_ids_never_escapes_allowed_set():
    docs = prepare_from_frame([{"id": 1, "text": "appeals monthly"}, {"id": 2, "text": "loans"}, {"id": 3, "text": "appeals daily"}], text_fields=("text",))
    hits = HybridIndex(docs).rank_ids("appeals", [2, 3])
    assert {hit.id for hit in hits} <= {"2", "3"}
    assert hits[0].id == "3"


def test_group_collapse_keeps_one_replica():
    docs = prepare_from_frame([{"id": 1, "text": "payments", "group": "g"}, {"id": 2, "text": "payments", "group": "g"}], text_fields=("text",), group_field="group")
    assert len(HybridIndex(docs).search("payments", top_k=5)) == 1


def test_dense_and_lexical_are_combined_with_rrf():
    docs = prepare_from_frame([{"id": 1, "text": "lexical match"}, {"id": 2, "text": "semantic"}], text_fields=("text",))
    vectors = {"lexical match": [0.0, 1.0], "semantic": [1.0, 0.0], "question": [1.0, 0.0]}
    hits = HybridIndex(docs, embedder=lambda texts: [vectors[text] for text in texts]).search("question", top_k=2)
    assert {hit.id for hit in hits} == {"1", "2"}


def test_reranker_sees_only_shortlist():
    docs=prepare_from_frame([{"id":i,"text":f"payments {i}"} for i in range(5)],text_fields=("text",))
    calls=[]
    def reranker(query,texts): calls.append((query,list(texts))); return [1.0-(i/10) for i in range(len(texts))]
    HybridIndex(docs,reranker=reranker,rerank_k=2).search("payments",top_k=1,score_floor=0.0)
    assert len(calls) == 1 and len(calls[0][1]) == 2


def test_manifest_current_and_dropped_rows_contract(tmp_path):
    docs=prepare_from_frame([{"id":1,"text":"one"}],text_fields=("text",))
    manifest=build_index(tmp_path,"tables",docs,source_signature="abc",dropped_rows=2,dense_vectors={"1":[1.0,0.0]})
    loaded,read_manifest=load_index(tmp_path,"tables")
    loaded_again,_=load_index(tmp_path,"tables")
    assert (tmp_path/"tables"/"CURRENT").read_text()==manifest["build_id"]
    assert read_manifest["dropped_rows"]==2 and loaded.faiss_index.ntotal==1
    assert loaded_again is loaded
    with pytest.raises(ValueError): build_index(tmp_path,"tables",docs,source_signature="abc",dropped_rows=-1)


def test_zero_evidence_is_not_a_confident_result():
    docs=prepare_from_frame([{"id":1,"text":"payments"}],text_fields=("text",))
    outcome=HybridIndex(docs).search_with_diagnostics("unrelated",score_floor=0.4)
    assert outcome.hits == []
    assert outcome.low_confidence is False


def test_floor_counter_does_not_include_group_collapse_or_top_k():
    docs=prepare_from_frame([{"id":1,"text":"payments","g":"x"},{"id":2,"text":"payments","g":"x"},{"id":3,"text":"payments","g":"y"}],text_fields=("text",),group_field="g")
    outcome=HybridIndex(docs,rerank_k=3).search_with_diagnostics("payments",top_k=1,score_floor=0.0)
    assert outcome.dropped_below_floor == 0
    assert outcome.dropped_by_group_collapse == 1
    assert outcome.candidates_total == 3


def test_failed_rebuild_does_not_switch_current(tmp_path, monkeypatch):
    docs=prepare_from_frame([{"id":1,"text":"one"}],text_fields=("text",))
    first=build_index(tmp_path,"columns",docs,source_signature="one")
    monkeypatch.setattr("lib.services.hybrid_search.storage._build_bm25",lambda *_a,**_k: (_ for _ in ()).throw(RuntimeError("boom")))
    with pytest.raises(RuntimeError,match="boom"):
        build_index(tmp_path,"columns",docs,source_signature="two")
    assert (tmp_path/"columns"/"CURRENT").read_text() == first["build_id"]


def test_persistent_bm25_search_survives_process_cache_reset(tmp_path):
    docs=prepare_from_frame([{"id":1,"text":"payments amount"},{"id":2,"text":"clients name"}],text_fields=("text",))
    build_index(tmp_path,"columns",docs,source_signature="snapshot")
    clear_index_cache()
    loaded,_=load_index(tmp_path,"columns")
    assert loaded.search("payments",top_k=1,score_floor=0.1)[0].id == "1"


def test_filtered_dense_search_ranks_inside_allowed_set_not_global_top_k():
    docs=prepare_from_frame([{"id":1,"text":"one"},{"id":2,"text":"two"},{"id":3,"text":"three"}],text_fields=("text",))
    class Faiss:
        ntotal=3; d=2
        vectors=[np.asarray([1.0,0.0],dtype="float32"),np.asarray([1.0,0.0],dtype="float32"),np.asarray([0.0,1.0],dtype="float32")]
        def search(self,_query,_k): return np.asarray([[1.0]],dtype="float32"),np.asarray([[0]])
        def reconstruct(self,position): return self.vectors[position]
    bm25=types.SimpleNamespace(vocab_dict={})
    index=HybridIndex(docs,embedder=lambda _texts:[[0.0,1.0]],faiss_index=Faiss(),bm25_index=bm25,faiss_k=1)
    hits=index.search("semantic",ids=[3],top_k=1,score_floor=0.1)
    assert [hit.id for hit in hits] == ["3"]


def test_model_cache_dir_is_forwarded_to_both_loaders(monkeypatch):
    import lib.services.hybrid_search.models as models
    calls=[]
    class SentenceTransformer:
        def __init__(self,*args,**kwargs): calls.append(("dense",args,kwargs))
        def encode(self,texts,**kwargs): return [[1.0,0.0] for _ in texts]
    class CrossEncoder:
        def __init__(self,*args,**kwargs): calls.append(("reranker",args,kwargs))
        def predict(self,pairs): return [0.9 for _ in pairs]
    monkeypatch.setitem(sys.modules,"sentence_transformers",types.SimpleNamespace(SentenceTransformer=SentenceTransformer,CrossEncoder=CrossEncoder))
    models._MODELS.clear()
    models.make_bge_embedder("dense-local",device="cpu",cache_dir="C:/models")(["x"])
    models.make_bge_reranker("reranker-local",device="cpu",cache_dir="C:/models")("q",["x"])
    assert [call[2]["cache_folder"] for call in calls] == ["C:/models","C:/models"]
    assert all(call[2]["local_files_only"] is True for call in calls)
