"""BGE inference through an existing READY Osiris worker, never a new GPU job."""
from __future__ import annotations

import math
import uuid
from pathlib import Path
from typing import Any, Mapping

from workspace.utils.osiris_runtime import ServiceProfile
from workspace.utils.osiris_runtime import client as transport
from workspace.utils.osiris_runtime.errors import OsirisRequestError, OsirisRequestTimeoutError
from workspace.utils.osiris_runtime.lifecycle import service_status

DEFAULT_NFS_ROOT = "/home/datalab/nfs/audit_nanobot/workspace/data_store/osiris/srb_d3"
DEFAULT_WORKER = "/home/datalab/nfs/audit_nanobot/workspace/skills/appeals-analyzer/rerank_osiris_worker.py"


class OsirisModelUnavailable(RuntimeError):
    code = "osiris_unavailable"


class OsirisInferenceError(RuntimeError):
    code = "osiris_request_error"


class OsirisInferenceTimeout(OsirisInferenceError):
    code = "osiris_request_timeout"


class OsirisModels:
    def __init__(self, settings: Mapping[str, Any] | None = None, *,
                 dense_model: str = "BAAI/bge-m3",
                 reranker_model: str = "BAAI/bge-reranker-v2-m3"):
        cfg = dict(settings or {})
        self.dense_model, self.reranker_model = dense_model, reranker_model
        self.batch_size = int(cfg.get("batch_size", 256))
        self.timeout = float(cfg.get("request_timeout_sec", 1800))
        if self.batch_size < 1 or not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("Osiris batch size and request timeout must be positive")
        self.settings = cfg
        self.session_id = "sql_assistant_" + uuid.uuid4().hex

    def _profile(self, capability: str) -> ServiceProfile:
        cfg = self.settings
        return ServiceProfile(
            service_name=str(cfg.get("service_name", "appeals")),
            job_name=str(cfg.get("job_name", "srbd3")),
            image="", pool="", worker_script=DEFAULT_WORKER, num_gpus=1,
            nfs_root=Path(cfg.get("nfs_root", DEFAULT_NFS_ROOT)),
            capabilities=(capability,),
            heartbeat_max_age_sec=float(cfg.get("heartbeat_max_age_sec", 12)),
        )

    def _request(self, capability: str, query: str, payload: dict[str, Any]) -> Any:
        profile = self._profile(capability)
        if not profile.heartbeat_ready(profile.read_heartbeat()):
            raise OsirisModelUnavailable(
                f"Existing Osiris worker is not READY or lacks {capability!r}; "
                "ask the operator to update/start the existing service. No job was created."
            )
        try:
            status = service_status(profile)
        except Exception:
            raise OsirisModelUnavailable("Cannot confirm the existing Osiris job state; no job was created") from None
        if not status.get("heartbeat ready") or status.get("Osiris state") != "running":
            raise OsirisModelUnavailable("Existing Osiris job is not confirmed Running/READY; no job was created")
        try:
            request_id = transport.submit_request(profile, self.session_id, capability, query, payload, self.timeout)
            return transport.wait_result(profile, self.session_id, request_id, capability,
                                         self.timeout, auto_recover=False)
        except OsirisRequestTimeoutError:
            raise OsirisInferenceTimeout("Existing Osiris request timed out; no local inference fallback") from None
        except (OsirisRequestError, OSError):
            raise OsirisInferenceError("Existing Osiris inference failed; inspect worker diagnostics") from None

    @staticmethod
    def _ordered(response: Any, items: list[dict[str, str]], model: str, field: str) -> list[Mapping[str, Any]]:
        if not isinstance(response, dict) or response.get("model") != model:
            raise OsirisInferenceError("Osiris model identity mismatch")
        records = response.get(field)
        if not isinstance(records, list) or len(records) != len(items):
            raise OsirisInferenceError("Osiris response count mismatch")
        by_id = {}
        for record in records:
            if not isinstance(record, dict) or not isinstance(record.get("id"), str) or record.get("id") in by_id:
                raise OsirisInferenceError("Osiris response contains duplicate or invalid IDs")
            by_id[record.get("id")] = record
        if set(by_id) != {item["id"] for item in items}:
            raise OsirisInferenceError("Osiris response IDs do not match input")
        return [by_id[item["id"]] for item in items]

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = []
        for start in range(0, len(texts), self.batch_size):
            items = [{"id": str(index), "text": text} for index, text in enumerate(texts[start:start + self.batch_size], start)]
            response = self._request("embed", "", {"model": self.dense_model, "items": items})
            for record in self._ordered(response, items, self.dense_model, "vectors"):
                vector = record.get("vector")
                if not isinstance(vector, (list, tuple)) or len(vector) != 1024:
                    raise OsirisInferenceError("BGE-M3 embedding dimension mismatch")
                try:
                    values = [float(value) for value in vector]
                except (ValueError, TypeError):
                    raise OsirisInferenceError("Invalid Osiris embedding values") from None
                norm = math.sqrt(sum(value * value for value in values))
                if not all(math.isfinite(value) for value in values) or not math.isfinite(norm) or norm <= 0:
                    raise OsirisInferenceError("Invalid or zero Osiris embedding")
                vectors.append([value / norm for value in values])
        return vectors

    def rerank(self, query: str, texts: list[str]) -> list[float]:
        scores = []
        for start in range(0, len(texts), self.batch_size):
            items = [{"id": str(index), "text": text} for index, text in enumerate(texts[start:start + self.batch_size], start)]
            response = self._request("rerank", query, {
                "model": self.reranker_model, "items": items, "response_format": "records",
            })
            for record in self._ordered(response, items, self.reranker_model, "scores"):
                try:
                    score = float(record["score"])
                except (KeyError, ValueError, TypeError):
                    raise OsirisInferenceError("Invalid Osiris reranker score") from None
                if not math.isfinite(score) or not 0 <= score <= 1:
                    raise OsirisInferenceError("Osiris reranker score is outside [0,1]")
                scores.append(score)
        return scores


def configured_models(*, dense_model: str = "BAAI/bge-m3",
                      reranker_model: str = "BAAI/bge-reranker-v2-m3") -> OsirisModels:
    from config import SETTINGS
    settings = SETTINGS.get("gateway", {}).get("kb_search", {}).get("osiris", {})
    return OsirisModels(settings, dense_model=dense_model, reranker_model=reranker_model)
