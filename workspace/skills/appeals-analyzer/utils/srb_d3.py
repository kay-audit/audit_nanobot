"""Appeals retrieve/rerank adapter over the shared Osiris lifecycle and NFS client."""
from __future__ import annotations

import pandas as pd

from workspace.utils.osiris_runtime import OsirisRequestError, OsirisUnavailableError
from workspace.utils.osiris_runtime import client as transport
from workspace.utils.osiris_runtime.lifecycle import ensure_ready

from .appeal_text import canonical_appeal_text, normalize_text
from .osiris_config import SERVICE

DEFAULT_TIMEOUT_SEC = transport.DEFAULT_REQUEST_TIMEOUT_SEC


def ensure_srb_d3_ready():
    """Auto-start once when absent and wait up to the profile startup deadline."""
    return ensure_ready(SERVICE)


def submit_request(session_id, request_type, query, payload, timeout_sec=DEFAULT_TIMEOUT_SEC):
    return transport.submit_request(SERVICE, session_id, request_type, query, payload, timeout_sec)


def wait_result(session_id, request_id, request_type, timeout_sec=DEFAULT_TIMEOUT_SEC, *, auto_recover=False):
    return transport.wait_result(SERVICE, session_id, request_id, request_type, timeout_sec,
                                 auto_recover=auto_recover)


def _session_dirs(session_id):
    return transport.session_dirs(SERVICE, session_id)


_atomic_pickle = transport._atomic_pickle


def retrieve_via_srb_d3(session_id, query, allowed_ids=None, timeout_sec=DEFAULT_TIMEOUT_SEC):
    if allowed_ids is not None and not len(allowed_ids):
        return []
    ensure_srb_d3_ready()
    request_id = submit_request(session_id, "retrieve", query,
                                {"allowed_ids": allowed_ids}, timeout_sec)
    result = wait_result(session_id, request_id, "retrieve", timeout_sec, auto_recover=True)
    if not isinstance(result, list) or any(not isinstance(cid, str) or not cid for cid in result):
        raise OsirisRequestError("Invalid Osiris candidate IDs")
    if len(result) != len(set(result)):
        raise OsirisRequestError("Duplicate Osiris candidate IDs")
    if allowed_ids is not None and not set(result).issubset(set(map(str, allowed_ids))):
        raise OsirisRequestError("Osiris returned IDs outside the allowed population")
    return result


def rerank_via_srb_d3(session_id, query, df, timeout_sec=DEFAULT_TIMEOUT_SEC):
    if df.empty:
        return df.assign(score=pd.Series(dtype=float))
    ids = df["app_row_id"].map(normalize_text) if "app_row_id" in df else df["id"].map(normalize_text)
    if not ids.all() or ids.duplicated().any():
        raise OsirisRequestError("Reranking requires unique canonical appeal IDs")
    items = [{"id": cid, "text": canonical_appeal_text(row)}
             for cid, (_, row) in zip(ids, df.iterrows())]
    ensure_srb_d3_ready()
    request_id = submit_request(session_id, "rerank", query, {"items": items}, timeout_sec)
    scores = wait_result(session_id, request_id, "rerank", timeout_sec, auto_recover=True)
    if not isinstance(scores, pd.DataFrame) or not {"id", "score"}.issubset(scores.columns):
        raise OsirisRequestError("Invalid Osiris reranker response")
    scores = scores.copy()
    scores["id"] = scores["id"].map(normalize_text)
    scores["score"] = pd.to_numeric(scores["score"], errors="coerce")
    if (scores["id"].duplicated().any() or set(scores["id"]) != set(ids)
            or not scores["score"].between(0, 1).all()):
        raise OsirisRequestError("Invalid Osiris reranker IDs or scores")
    base = df.drop(columns="score", errors="ignore").copy()
    base["id"] = ids
    return (base.merge(scores[["id", "score"]], on="id", how="left", validate="one_to_one")
            .sort_values("score", ascending=False, kind="stable").reset_index(drop=True))
