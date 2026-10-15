"""IOR-owned shared adapter contracts; no CUDA/SDK imports during gateway bootstrap."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest


def test_ior_shared_adapter_keeps_consumer_session_and_extract_version(monkeypatch):
    import sys
    shared=pytest.importorskip('workspace.utils.osiris_runtime.shared_worker')
    root=Path(__file__).resolve().parents[1]/'workspace/skills/ior-analyzer'
    before=list(sys.path)
    worker=shared.load_skill('ior_contract',root)
    assert not any(str(p) in sys.path and str(p) not in before for p in (root,root/'scripts',root/'utils'))
    calls=[]
    def dispatch(session,operation,query,payload):
        calls.append((session,operation,payload['version']))
        return payload
    monkeypatch.setattr(worker,'load_runtime',lambda pool:SimpleNamespace(dispatch=dispatch))
    handlers,legacy=worker.create_shared_handlers(object())
    context=SimpleNamespace(session_id='A',consumer_id='nanobot',query='same')
    assert handlers.invoke('search',{'version':'extract-A'},context)=={'version':'extract-A'}
    assert legacy(dict(session_id='B',consumer_id='python',request_type='search',query='same'),
                  {'version':'extract-B'})=={'version':'extract-B'}
    assert calls==[(('A','nanobot'),'search','extract-A'),(('B','python'),'search','extract-B')]


def test_ior_worker_imports_no_cuda_or_sdk_at_module_scope():
    root=Path(__file__).resolve().parents[1]/'workspace/skills/ior-analyzer/rerank_osiris_worker.py'
    tree=ast.parse(root.read_text(encoding='utf-8'))
    assert not any(isinstance(n,ast.Import) and any(a.name in {'torch','sentence_transformers','osiris'}
        for a in n.names) for n in tree.body)
