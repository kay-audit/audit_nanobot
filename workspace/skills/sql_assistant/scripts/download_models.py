from __future__ import annotations
import argparse, json
from pathlib import Path

def main():
    p=argparse.ArgumentParser(description="Explicit offline model cache warm-up; never installs packages"); p.add_argument("--dense-model",default="BAAI/bge-m3"); p.add_argument("--reranker-model",default="BAAI/bge-reranker-v2-m3"); p.add_argument("--cache-dir",required=True); p.add_argument("--skip-reranker",action="store_true"); args=p.parse_args()
    try: from huggingface_hub import snapshot_download
    except ImportError: print(json.dumps({"status":"unavailable","error":"huggingface_hub is not installed; no pip install was attempted"})); return 3
    cache_dir=str(Path(args.cache_dir).expanduser().resolve())
    paths={"dense":snapshot_download(args.dense_model,cache_dir=cache_dir)}
    if not args.skip_reranker: paths["reranker"]=snapshot_download(args.reranker_model,cache_dir=cache_dir)
    print(json.dumps({"status":"ok","model_cache_dir":cache_dir,"paths":paths},indent=2)); return 0
if __name__=="__main__": raise SystemExit(main())
