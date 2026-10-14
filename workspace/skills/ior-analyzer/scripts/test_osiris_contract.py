"""Actual d3 NFS protocol with mock models, without SDK, CUDA or a network."""
from dataclasses import replace
import importlib.util
import json
from pathlib import Path
import sys
import types
import numpy as np
import pytest
from utils import osiris_client as adapter
from workspace.utils.osiris_runtime import client as transport
from workspace.utils.osiris_runtime.errors import OsirisRequestError,OsirisRequestTimeoutError

SPEC=importlib.util.spec_from_file_location('ior_osiris_worker_contract',Path(__file__).resolve().parents[1]/'rerank_osiris_worker.py')
worker=importlib.util.module_from_spec(SPEC); SPEC.loader.exec_module(worker)

class FakeIndex:
    def __init__(self,d): self.vectors=None
    def add(self,v): self.vectors=v.copy()
    def search(self,v,k):
        scores=v@self.vectors.T; ids=np.argsort(-scores,axis=1)[:,:k]
        return np.take_along_axis(scores,ids,axis=1),ids

@pytest.fixture
def runtime(monkeypatch):
    faiss=types.ModuleType('faiss'); faiss.IndexFlatIP=FakeIndex
    faiss.normalize_L2=lambda v:v.__setitem__(slice(None),v/np.maximum(np.linalg.norm(v,axis=1,keepdims=True),1e-9))
    monkeypatch.setitem(sys.modules,'faiss',faiss)
    embed=types.SimpleNamespace(encode=lambda texts:[[1.,0.] if 'перв' in t else [0.,1.] for t in texts])
    rank=types.SimpleNamespace(predict=lambda pairs:[2. for _ in pairs])
    return worker.IORRuntime(embed,rank)

@pytest.fixture
def nfs(tmp_path,monkeypatch,runtime):
    profile=replace(adapter.SERVICE,nfs_root=tmp_path)
    monkeypatch.setattr(adapter,'SERVICE',profile)
    monkeypatch.setattr(transport,'ensure_ready',lambda profile:None)
    def service(profile,session,kind,query,payload):
        rid=transport.submit_request(profile,session,kind,query,payload,1)
        path=profile.nfs_root/'inbox'/(rid+'.json')
        manifest=json.loads(path.read_text(encoding='utf-8'))
        assert manifest['session_id']==session
        assert manifest['protocol_version']==2 and manifest['service_name']=='ior'
        worker.process_request(profile,path,runtime)
        return transport.wait_result(profile,session,rid,kind,.1)
    monkeypatch.setattr(transport,'request',service)
    return profile

def test_embed_build_search_rerank_through_nfs(nfs,runtime):
    assert adapter.embeddings('session',['первый','второй'])==[[1.,0.],[0.,1.]]
    items=[{'id':'EVE-1','text':'первый'},{'id':'EVE-2','text':'второй'}]
    assert adapter.build_index('session','version1',items)=={'version':'version1','count':2}
    found=adapter.search('session','version1','первый',['EVE-1','EVE-2'],2,.7)
    assert [r['id'] for r in found]==['EVE-1']
    assert adapter.rerank('session','version1','query',found)[0]['score']==pytest.approx(.880797,abs=1e-5)
    assert not list(nfs.nfs_root.rglob('*.pkl'))

@pytest.mark.parametrize('session,version,ids',[('other','v',['EVE-1']),('session','old',['EVE-1']),('session','v',['EVE-2'])])
def test_identity_mismatch_fails_closed(nfs,runtime,session,version,ids):
    adapter.build_index('session','v',[{'id':'EVE-1','text':'первый'}])
    with pytest.raises(OsirisRequestError): adapter.search(session,version,'первый',ids,1,.7)

def test_failed_rebuild_invalidates_worker_index(nfs,runtime):
    adapter.build_index('session','v',[{'id':'EVE-1','text':'первый'}])
    with pytest.raises(OsirisRequestError): adapter.build_index('session','new',[{'id':'','text':'первый'}])
    assert 'session' not in runtime.indices
    with pytest.raises(OsirisRequestError): adapter.search('session','v','первый',['EVE-1'],1,.7)

@pytest.mark.parametrize('kind,bad',[('embed',[[float('nan')]]),('build_index',{'version':'wrong','count':1}),('search',{'version':'old','items':[]}),('rerank',{'version':'v','items':[{'id':'OUTSIDE','score':.9}]})])
def test_malformed_response_rejected(monkeypatch,kind,bad):
    monkeypatch.setattr(adapter,'request',lambda *args:bad)
    with pytest.raises(OsirisRequestError):
        if kind=='embed': adapter.embeddings('s',['text'])
        elif kind=='build_index': adapter.build_index('s','v',[{'id':'EVE-1','text':'text'}])
        elif kind=='search': adapter.search('s','v','q',['EVE-1'],1,.7)
        else: adapter.rerank('s','v','q',[{'id':'EVE-1','text':'text'}])

def test_transport_timeout_and_correlation_cleanup(tmp_path):
    profile=replace(adapter.SERVICE,nfs_root=tmp_path)
    rid=transport.submit_request(profile,'s','embed','',{'texts':['a']},.01)
    with pytest.raises(OsirisRequestTimeoutError): transport.wait_result(profile,'s',rid,'embed',.01)
    assert not list(tmp_path.rglob('*.pkl'))
    rid=transport.submit_request(profile,'s','embed','',{},1)
    _,dirs=transport.session_dirs(profile,'s')
    transport._atomic_pickle({'request_id':'wrong','request_type':'embed','result':[]},dirs['output']/(rid+'.pkl'))
    with pytest.raises(OsirisRequestError): transport.wait_result(profile,'s',rid,'embed',.01)

def test_gateway_cannot_load_local_models():
    from utils import bge_search_engine as gateway
    with pytest.raises(RuntimeError,match='Osiris'): gateway.get_bge_model()
    with pytest.raises(RuntimeError,match='Osiris'): gateway.get_reranker()
