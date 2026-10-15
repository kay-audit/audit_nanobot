"""Appeals-owned adapter contract; universal runtime is an optional integration dependency."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_shared_adapter_preserves_allowed_population_and_legacy_result(monkeypatch):
    pytest.importorskip('workspace.utils.osiris_runtime.dispatcher')
    path=Path(__file__).resolve().parents[1]/'workspace/skills/appeals-analyzer/rerank_osiris_worker.py'
    spec=importlib.util.spec_from_file_location('appeals_shared_contract_worker',path)
    worker=importlib.util.module_from_spec(spec); spec.loader.exec_module(worker)
    calls=[]
    def retrieve(query,allowed): calls.append((query,allowed)); return allowed
    search=SimpleNamespace(retrieve_hybrid_adaptive=retrieve)
    monkeypatch.setattr(worker,'load_runtime',lambda pool:(search,object()))
    handlers,legacy=worker.create_shared_handlers(object())
    context=SimpleNamespace(query='same text',session_id='A')
    assert handlers.invoke('retrieve',{'allowed_ids':['A-only']},context)==['A-only']
    assert legacy({'request_type':'retrieve','query':'same text'},{'allowed_ids':['B-only']})==['B-only']
    assert calls==[('same text',['A-only']),('same text',['B-only'])]


def test_standalone_worker_defers_cuda_and_sdk_import():
    import ast
    path=Path(__file__).resolve().parents[1]/'workspace/skills/appeals-analyzer/rerank_osiris_worker.py'
    tree=ast.parse(path.read_text(encoding='utf-8'))
    assert not any(isinstance(node,ast.Import) and any(alias.name in {'torch','sentence_transformers','osiris'}
        for alias in node.names) for node in tree.body)
