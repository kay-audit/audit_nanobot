"""IOR domain handlers using the d3 shared Osiris lifecycle/NFS worker loop.

Run only inside the configured CUDA Osiris image. Gateway imports no ML.
"""
import importlib
import json
import os
from pathlib import Path
import pickle
import queue
import sys
import threading
import time
import traceback
from dataclasses import replace

ROOT=Path(__file__).resolve().parents[3]
sys.path.insert(0,str(ROOT))
sys.path.insert(0,str(Path(__file__).parent))
from utils.osiris_config import SERVICE
from workspace.utils.osiris_runtime.client import _atomic_json, _atomic_pickle
from workspace.utils.osiris_runtime.worker import (
    WorkerActivity,claim_new_requests,recover_processing_after_restart,should_idle_stop)

class IORRuntime:
    """Models belong to this worker, and indices are bound to extract versions."""
    def __init__(self,embed,reranker):
        self.embed=embed
        self.reranker=reranker
        self.indices={}

    def encode(self,texts):
        import numpy as np
        vectors=np.asarray(self.embed.encode(texts),dtype='float32')
        if vectors.ndim!=2 or len(vectors)!=len(texts) or not np.isfinite(vectors).all():
            raise ValueError('Invalid embeddings')
        return vectors

    def dispatch(self,session,kind,query,payload):
        import numpy as np
        version=payload.get('version')
        if kind=='embed':
            return self.encode(payload['texts']).tolist()
        if kind=='build_index':
            # Invalidate before any embedding/index operation can fail.
            self.indices.pop(session,None)
            items=payload['items']
            ids=[row['id'] for row in items]
            if not version or not ids or any(not isinstance(i,str) or not i.strip() for i in ids) or len(ids)!=len(set(ids)):
                raise ValueError('Index requires a version and unique nonempty IDs')
            vectors=self.encode([row['text'] for row in items])
            import faiss
            faiss.normalize_L2(vectors)
            index=faiss.IndexFlatIP(vectors.shape[1])
            index.add(vectors)
            self.indices[session]={'version':version,'items':items,'index':index}
            return {'version':version,'count':len(items)}
        if kind=='search':
            state=self.indices.get(session)
            if not state or state['version']!=version:
                raise ValueError('Extract/index version mismatch')
            allowed=set(payload['allowed_ids'])
            if allowed!={row['id'] for row in state['items']}:
                raise ValueError('Extract/index population mismatch')
            vectors=self.encode([query])
            import faiss
            faiss.normalize_L2(vectors)
            k=min(max(1,int(payload['top_k'])),len(state['items']))
            scores,positions=state['index'].search(vectors,k)
            return {'version':version,'items':[dict(state['items'][int(pos)],score=float(score))
                for pos,score in zip(positions[0],scores[0]) if pos>=0 and float(score)>=float(payload['threshold'])]}
        if kind=='rerank':
            items=payload['items']
            state=self.indices.get(session)
            if not state or state['version']!=version or not {row['id'] for row in items}.issubset({row['id'] for row in state['items']}):
                raise ValueError('Extract/index version or population mismatch')
            raw=self.reranker.predict([(query,row['text']) for row in items])
            raw=np.asarray(raw).reshape(-1)
            if len(raw)!=len(items) or not np.isfinite(raw).all():
                raise ValueError('Invalid reranker result')
            scores=1/(1+np.exp(-np.clip(raw,-50,50)))
            return {'version':version,'items':[{'id':row['id'],'score':float(score)} for row,score in zip(items,scores)]}
        if kind=='retrieve':
            search=importlib.import_module('utils.osiris_search_runtime')
            return search.search_pipeline(query,**payload)
        raise ValueError('Unsupported IOR request type')

def load_runtime():
    import torch
    from sentence_transformers import SentenceTransformer,CrossEncoder
    if not torch.cuda.is_available():
        raise RuntimeError('IOR Osiris requires CUDA; no local/CPU model fallback')
    torch.cuda.set_device(0)
    search=importlib.import_module('utils.osiris_search_runtime')
    embed=SentenceTransformer(str(search.model_dir()),device='cuda:0')
    reranker=CrossEncoder(str(search.reranker_dir()),device='cuda:0')
    search._MODEL_CACHE.update(embed=embed,reranker=reranker)
    return IORRuntime(embed,reranker)

def process_request(profile,path,runtime):
    manifest=json.loads(path.read_text(encoding='utf-8'))
    rid=manifest['request_id']
    if len(rid)!=32 or any(c not in '0123456789abcdef' for c in rid):
        raise ValueError('Invalid request ID')
    artifacts=[Path(manifest[k]) for k in ('input_path','output_path','error_path')]
    expected=profile.nfs_root/'sessions'/manifest['safe_session_id']
    for artifact,part,suffix in zip(artifacts,('input','output','error'),('.pkl','.pkl','.txt')):
        if artifact.resolve()!=(expected/part/(rid+suffix)).resolve() or not artifact.resolve().is_relative_to((profile.nfs_root/'sessions').resolve()):
            raise ValueError('Invalid request artifact path')
    source,output,error=artifacts
    try:
        if time.time()>=manifest['expires_at']:
            return
        if manifest['protocol_version']!=profile.protocol_version or manifest['service_name']!=profile.service_name:
            raise ValueError('Osiris profile mismatch')
        with source.open('rb') as handle:
            payload=pickle.load(handle)
        result=runtime.dispatch(manifest['session_id'],manifest['request_type'],manifest['query'],payload)
        if time.time()<manifest['expires_at']:
            _atomic_pickle({'request_id':rid,'request_type':manifest['request_type'],'result':result},output)
    except Exception:
        if time.time()<manifest['expires_at']:
            error.write_text(traceback.format_exc(),encoding='utf-8')
    finally:
        source.unlink(missing_ok=True)
        path.unlink(missing_ok=True)

def main():
    profile=SERVICE
    profile.prepare_nfs()
    generation=None
    try:
        metadata=json.loads(profile.metadata_path.read_text(encoding='utf-8'))
        generation=metadata.get('generation')
        profile=replace(profile,idle_timeout_sec=float(metadata.get('idle_timeout_sec',profile.idle_timeout_sec)))
    except (OSError,ValueError):
        pass
    activity=WorkerActivity(profile)
    requests=queue.Queue()
    stop=threading.Event()
    def heartbeat():
        while not stop.is_set():
            _atomic_json(activity.snapshot(requests.qsize(),generation),profile.heartbeat_path)
            stop.wait(1)
    threading.Thread(target=heartbeat,daemon=True).start()
    try:
        runtime=load_runtime()
        recover_processing_after_restart(profile)
        activity.ready()
        while True:
            claim_new_requests(profile,requests)
            try:
                path=requests.get(timeout=.5)
            except queue.Empty:
                if should_idle_stop(profile,activity,requests):
                    break
                continue
            activity.begin()
            try:
                process_request(profile,path,runtime)
            except Exception:
                print(traceback.format_exc(),flush=True)
                if path.exists():
                    os.replace(path,profile.nfs_root/'failed'/path.name)
            finally:
                activity.end()
                requests.task_done()
    finally:
        activity.stopped()
        stop.set()
        _atomic_json(activity.snapshot(0,generation),profile.heartbeat_path)

if __name__=='__main__':
    main()
