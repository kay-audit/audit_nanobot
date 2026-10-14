"""Gateway-side IOR search. Models and indexes run only in Osiris."""
import logging
import os
from pathlib import Path
from threading import RLock
import pandas as pd
from utils import osiris_client

logger=logging.getLogger(__name__)
_LOCK=RLock()
_SMALL_FAISS_SESSION_CACHE={}
SESSION_SCORE_THRESHOLD=.7
_PIPELINES_DIR=Path(__file__).resolve().parents[3]/'data_store/cache/caches_pipelines'
def cache_dir(): return Path(os.getenv('IOR_RAG_CACHE_DIR',str(_PIPELINES_DIR/'cache_final')))
def model_dir(): return Path(os.getenv('IOR_BGE_MODEL_PATH',str(_PIPELINES_DIR/'BAAI:bge-m3')))
def reranker_dir(): return Path(os.getenv('IOR_RERANKER_MODEL_PATH',str(_PIPELINES_DIR/'bge-reranker-v2-m3')))

def invalidate_small_index(session_id):
    with _LOCK:
        _SMALL_FAISS_SESSION_CACHE.pop(session_id,None)

def _items(df):
    col=next((c for c in ('incdnt_full_descr_txt','incdnt_summary_descr_txt','подробное описание') if c in df),None)
    if 'incdnt_sid' not in df or col is None:
        return []
    items=[]
    for row in df.drop_duplicates('incdnt_sid').to_dict('records'):
        if pd.isna(row['incdnt_sid']) or pd.isna(row[col]) or not str(row[col]).strip():
            continue
        items.append({'id':str(row['incdnt_sid']),'text':str(row[col])})
    return items

def build_and_cache_small_index(session_id,df_or_map):
    from utils.session_extract_manager import get_session_extract
    invalidate_small_index(session_id)
    extract=get_session_extract(session_id)
    if not extract or not isinstance(df_or_map,pd.DataFrame) or df_or_map.empty:
        return False
    version=extract['version']
    items=_items(df_or_map)
    if not items:
        return False
    try:
        osiris_client.build_index(session_id,version,items)
        with _LOCK:
            current=get_session_extract(session_id)
            if not current or current['version']!=version:
                return False
            _SMALL_FAISS_SESSION_CACHE[session_id]={'version':version,'ids':[r['id'] for r in items]}
        return True
    except Exception:
        logger.exception('IOR Osiris index build unavailable')
        return False

def search_small_index(session_id,query,top_k=5,threshold=SESSION_SCORE_THRESHOLD):
    from utils.session_extract_manager import get_session_extract
    with _LOCK:
        extract=get_session_extract(session_id)
        index=_SMALL_FAISS_SESSION_CACHE.get(session_id)
        if not extract or not index or index['version']!=extract['version']:
            return []
        version=index['version']
        ids=list(index['ids'])
    try:
        items=osiris_client.search(session_id,version,query,ids,top_k,threshold)
        if not items:
            return []
        ranked=osiris_client.rerank(session_id,version,query,items)
        with _LOCK:
            current=get_session_extract(session_id)
            if not current or current['version']!=version:
                return []
        texts={row['id']:row['text'] for row in items}
        return [f"ИОР {row['id']}: {texts[row['id']]}" for row in sorted(ranked,key=lambda r:r['score'],reverse=True)]
    except Exception:
        invalidate_small_index(session_id)
        logger.exception('IOR Osiris search unavailable')
        return []

def search_pipeline(query,session_id='ior-global',**kwargs):
    return osiris_client.request(session_id,'retrieve',query,kwargs)

def get_bge_model():
    raise RuntimeError('BGE is loaded only in the Osiris worker')

def get_reranker():
    raise RuntimeError('Reranker is loaded only in the Osiris worker')
