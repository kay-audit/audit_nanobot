"""Persistent single-GPU Osiris worker for global retrieval and reranking."""
from __future__ import annotations

import gc
import importlib
import importlib.util
import json
import os
import pickle
import queue
import sys
import threading
import time
import traceback
import uuid
from dataclasses import replace
from pathlib import Path

import numpy as np

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
from workspace.utils.osiris_runtime.worker import (
    WorkerActivity, claim_new_requests as shared_claim_new_requests,
    recover_processing_after_restart as shared_recover_processing,
    should_idle_stop,
)

_config_spec = importlib.util.spec_from_file_location(
    "appeals_worker_config", Path(__file__).resolve().parent / "utils/osiris_config.py",
)
osiris_config = importlib.util.module_from_spec(_config_spec)
_config_spec.loader.exec_module(osiris_config)
PROFILE = osiris_config.SERVICE
NFS_ROOT = osiris_config.NFS_ROOT
INBOX = NFS_ROOT / "inbox"
PROCESSING = NFS_ROOT / "processing"
FAILED = NFS_ROOT / "failed"
HEARTBEAT = osiris_config.HEARTBEAT_PATH
SCAN_INTERVAL_SEC = 0.25
RERANK_BATCH_SIZE = 4
request_queue = queue.Queue()
activity = WorkerActivity(PROFILE)
generation = None


def atomic_pickle(value, target):
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + f".tmp.{uuid.uuid4().hex}")
    try:
        with temporary.open("wb") as handle:
            pickle.dump(value, handle, protocol=pickle.HIGHEST_PROTOCOL)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def atomic_text(text, target):
    temporary = target.with_name(target.name + f".tmp.{uuid.uuid4().hex}")
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        temporary.write_text(text, encoding="utf-8")
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)


def write_heartbeat(status=None):
    snapshot = activity.snapshot(request_queue.qsize(), generation)
    if status is not None:
        snapshot["status"] = status
        snapshot["ready_gpus"] = PROFILE.num_gpus if status in {"ready", "busy"} else 0
    atomic_text(json.dumps(snapshot), HEARTBEAT)

class ExistingAppealsRetrieval:
    def __init__(self, embed):
        self.embed = embed
        self.search = None

    def retrieve_hybrid_adaptive(self, query, allowed_ids):
        if self.search is None:
            root = Path(__file__).resolve().parent
            package_name = "appeals_osiris_runtime"
            spec = importlib.util.spec_from_file_location(
                package_name, root / "__init__.py", submodule_search_locations=[str(root)],
            )
            package = importlib.util.module_from_spec(spec)
            sys.modules[package_name] = package
            spec.loader.exec_module(package)
            self.search = importlib.import_module(f"{package_name}.utils.bge_search_engine")
            self.search.initialize_retrieval(self.embed)
        return self.search.retrieve_hybrid_adaptive(query, allowed_ids)


def load_runtime():
    import torch
    from sentence_transformers import CrossEncoder, SentenceTransformer

    if not torch.cuda.is_available():
        raise RuntimeError("Existing Osiris worker requires CUDA for BGE-M3 and reranker")
    torch.cuda.set_device(0)
    model_root = _PROJECT_ROOT / "workspace/data_store/cache/caches_pipelines"
    embed_path = Path(os.environ.get("APPEALS_BGE_MODEL_PATH", str(model_root / "BAAI:bge-m3")))
    reranker_path = Path(os.environ.get("APPEALS_RERANKER_MODEL_PATH", str(model_root / "bge-reranker-v2-m3")))
    if not embed_path.is_dir() or not reranker_path.is_dir():
        raise RuntimeError("Local Osiris BGE-M3/reranker directories are unavailable")
    embed = SentenceTransformer(str(embed_path), device="cuda:0", local_files_only=True)
    try:
        reranker = CrossEncoder(str(reranker_path), device="cuda:0", local_files_only=True,
                                automodel_args={"torch_dtype": torch.float16})
    except TypeError:
        reranker = CrossEncoder(str(reranker_path), device="cuda:0", local_files_only=True)
        reranker.model.half()
    return ExistingAppealsRetrieval(embed), reranker


def embed_items(embed, items):
    ids = [item["id"] for item in items]
    if not items or len(ids) != len(set(ids)) or any(not isinstance(item["text"], str) for item in items):
        raise ValueError("Embedding requires unique IDs and text strings")
    matrix = np.asarray(embed.encode(
        [item["text"] for item in items], batch_size=32,
        normalize_embeddings=True, show_progress_bar=False,
    ), dtype="float32")
    if matrix.shape != (len(items), 1024) or not np.isfinite(matrix).all():
        raise ValueError("BGE-M3 returned invalid embeddings")
    return {"model": "BAAI/bge-m3", "vectors": [
        {"id": identity, "vector": vector.tolist()} for identity, vector in zip(ids, matrix)
    ]}


def predict_with_retry(model, pairs):
    import torch

    batch_size = RERANK_BATCH_SIZE
    while True:
        try:
            return np.asarray(model.predict(
                pairs, batch_size=batch_size, show_progress_bar=False,
                activation_fct=torch.nn.Identity(),
            )).reshape(-1)
        except Exception as exc:
            oom = isinstance(exc, torch.cuda.OutOfMemoryError) or (
                "cuda" in str(exc).lower() and "out of memory" in str(exc).lower())
            if not oom or batch_size <= 1:
                raise
            batch_size = max(1, batch_size // 2)
            gc.collect()
            torch.cuda.empty_cache()


def sigmoid(raw):
    return 1.0 / (1.0 + np.exp(-np.clip(np.asarray(raw, dtype="float64"), -50, 50)))


def _paths(manifest):
    request_id = manifest["request_id"]
    if len(request_id) != 32 or any(c not in "0123456789abcdef" for c in request_id):
        raise ValueError("Invalid request ID")
    paths = tuple(Path(manifest[key]) for key in ("input_path", "output_path", "error_path"))
    root = (NFS_ROOT / "sessions").resolve()
    for path, suffix in zip(paths, (".pkl", ".pkl", ".txt")):
        if not path.resolve().is_relative_to(root) or path.name != request_id + suffix:
            raise ValueError("Invalid request artifact path")
    return paths


def process_request(manifest_path, search, reranker):
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    input_path, output_path, error_path = _paths(manifest)
    expires_at = float(manifest["expires_at"])
    try:
        if time.time() >= expires_at:
            return
        if manifest.get("protocol_version") != osiris_config.PROTOCOL_VERSION:
            raise ValueError("Unsupported Appeals protocol version")
        if manifest.get("service_name", "appeals") != PROFILE.service_name:
            raise ValueError("Osiris manifest belongs to another service")
        with input_path.open("rb") as handle:
            payload = pickle.load(handle)
        kind = manifest["request_type"]
        if kind == "retrieve":
            result = search.retrieve_hybrid_adaptive(manifest["query"], payload["allowed_ids"])
            result = list(map(str, result))
        elif kind == "embed":
            if payload.get("model") != "BAAI/bge-m3":
                raise ValueError("Unsupported embedding model")
            result = embed_items(search.embed, payload["items"])
        elif kind == "rerank":
            items = payload["items"]
            ids = [item["id"] for item in items]
            if len(ids) != len(set(ids)):
                raise ValueError("Duplicate reranker IDs")
            raw = predict_with_retry(reranker, [(manifest["query"], item["text"]) for item in items])
            if len(raw) != len(ids) or not np.isfinite(raw).all():
                raise ValueError("Reranker returned invalid logits")
            if payload.get("response_format") == "records":
                if payload.get("model") != "BAAI/bge-reranker-v2-m3":
                    raise ValueError("Unsupported reranker model")
                result = {"model": "BAAI/bge-reranker-v2-m3", "scores": [
                    {"id": identity, "score": float(score)} for identity, score in zip(ids, sigmoid(raw))
                ]}
            else:
                import pandas as pd
                result = pd.DataFrame({"id": ids, "score": sigmoid(raw)})
        else:
            raise ValueError("Unsupported Appeals request_type")
        if time.time() < expires_at:
            atomic_pickle({"request_id": manifest["request_id"], "request_type": kind,
                           "result": result}, output_path)
    except Exception:
        detail = traceback.format_exc()
        if time.time() < expires_at:
            atomic_text(detail, error_path)
        print(detail, flush=True)
    finally:
        input_path.unlink(missing_ok=True)
        manifest_path.unlink(missing_ok=True)
        if time.time() >= expires_at:
            output_path.unlink(missing_ok=True)
            error_path.unlink(missing_ok=True)


def claim_new_requests():
    shared_claim_new_requests(PROFILE, request_queue)


def recover_processing_after_restart():
    shared_recover_processing(PROFILE)


def main():
    global generation, PROFILE, activity
    os.environ["TORCHDYNAMO"] = "0"
    PROFILE.prepare_nfs()
    try:
        metadata = json.loads(PROFILE.metadata_path.read_text(encoding="utf-8"))
        generation = metadata.get("generation")
        idle_timeout = metadata.get("idle_timeout_sec")
        if idle_timeout is not None:
            PROFILE = replace(PROFILE, idle_timeout_sec=float(idle_timeout))
            activity = WorkerActivity(PROFILE)
    except (OSError, ValueError):
        generation = None
    stop = threading.Event()

    def heartbeat_loop():
        while not stop.is_set():
            write_heartbeat()
            stop.wait(1.0)

    threading.Thread(target=heartbeat_loop, daemon=True, name="appeals-heartbeat").start()
    try:
        search, reranker = load_runtime()
        recover_processing_after_restart()
        activity.ready()
        write_heartbeat()
        while True:
            claim_new_requests()
            try:
                manifest_path = request_queue.get(timeout=SCAN_INTERVAL_SEC)
            except queue.Empty:
                if should_idle_stop(PROFILE, activity, request_queue):
                    write_heartbeat()
                    break
                continue
            activity.begin()
            try:
                process_request(manifest_path, search, reranker)
            except Exception:
                print(traceback.format_exc(), flush=True)
                if manifest_path.exists():
                    os.replace(manifest_path, FAILED / manifest_path.name)
            finally:
                activity.end()
                request_queue.task_done()
    finally:
        activity.stopped()
        stop.set()
        write_heartbeat("stopped")


if __name__ == "__main__":
    main()
