"""IOR adapter over d3's shared profile/lifecycle/NFS transport; no local ML."""
import math
from workspace.utils.osiris_runtime import client as transport
from workspace.utils.osiris_runtime.errors import OsirisRequestError
from .osiris_config import SERVICE

def request(session_id,kind,query,payload):
    return transport.request(SERVICE,session_id,kind,query,payload)

def embeddings(session_id,texts):
    result=request(session_id,'embed','',{'texts':list(texts)})
    if (not isinstance(result,list) or len(result)!=len(texts) or
        any(not isinstance(row,list) or not row or not all(isinstance(x,(int,float)) and math.isfinite(x) for x in row) for row in result) or
        len({len(row) for row in result})>1):
        raise OsirisRequestError('Invalid Osiris embedding response')
    return result

def build_index(session_id,version,items):
    result=request(session_id,'build_index','',{'version':version,'items':items})
    if not isinstance(result,dict) or result.get('version')!=version or result.get('count')!=len(items):
        raise OsirisRequestError('Invalid Osiris index registration')
    return result

def search(session_id,version,query,allowed_ids,top_k,threshold):
    result=request(session_id,'search',query,{'version':version,'allowed_ids':allowed_ids,'top_k':top_k,'threshold':threshold})
    if not isinstance(result,dict) or result.get('version')!=version or not isinstance(result.get('items'),list):
        raise OsirisRequestError('Invalid Osiris search version')
    items=result['items']
    ids=[row.get('id') for row in items if isinstance(row,dict)]
    if (len(ids)!=len(items) or len(set(ids))!=len(ids) or not set(ids).issubset(set(allowed_ids)) or
        any(not isinstance(row.get('text'),str) or not isinstance(row.get('score'),(int,float)) or not math.isfinite(row['score']) or row['score']<threshold or row['score']>1.00001 for row in items)):
        raise OsirisRequestError('Invalid Osiris search population or scores')
    return items

def rerank(session_id,version,query,items):
    result=request(session_id,'rerank',query,{'version':version,'items':items})
    if not isinstance(result,dict) or result.get('version')!=version or not isinstance(result.get('items'),list):
        raise OsirisRequestError('Invalid Osiris rerank version')
    scored=result['items']
    ids=[row.get('id') for row in scored if isinstance(row,dict)]
    if (len(ids)!=len(scored) or len(ids)!=len(set(ids)) or set(ids)!={row['id'] for row in items} or
        any(not isinstance(row.get('score'),(int,float)) or not math.isfinite(row['score']) or not 0<=row['score']<=1 for row in scored)):
        raise OsirisRequestError('Invalid Osiris rerank IDs or scores')
    return scored
