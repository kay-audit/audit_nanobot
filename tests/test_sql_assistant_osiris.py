from __future__ import annotations

import importlib.util
import json
import pickle
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import numpy as np

from workspace.skills.sql_assistant.scripts import osiris_adapter as module
from workspace.skills.sql_assistant.scripts.osiris_adapter import (
    OsirisModels, OsirisInferenceError, OsirisModelUnavailable, OsirisInferenceTimeout,
)
from workspace.utils.osiris_runtime import ServiceProfile
from workspace.utils.osiris_runtime import client as transport
from workspace.utils.osiris_runtime.errors import OsirisRequestTimeoutError


class TestOsirisAdapter(unittest.TestCase):
    def setUp(self):
        self.models = OsirisModels({"batch_size": 2})

    def test_embedding_batches_correlate_ids_and_normalize_vectors(self):
        calls = []
        def response(kind, query, payload):
            calls.append(payload)
            return {"model": "BAAI/bge-m3", "vectors": [
                {"id": item["id"], "vector": [2.0] + [0.0] * 1023} for item in reversed(payload["items"])
            ]}
        with patch.object(self.models, "_request", side_effect=response):
            vectors = self.models.embed(["one", "two", "three"])
        self.assertEqual(len(vectors), 3)
        self.assertEqual([len(call["items"]) for call in calls], [2, 1])
        self.assertEqual(vectors[0], [1.0] + [0.0] * 1023)
        self.assertEqual(calls[1]["items"][0]["id"], "2")

    def test_reranker_correlates_shuffled_scores(self):
        def response(kind, query, payload):
            self.assertEqual(kind, "rerank")
            self.assertEqual(query, "monthly")
            return {"model": "BAAI/bge-reranker-v2-m3", "scores": [
                {"id": item["id"], "score": (int(item["id"]) + 1) / 10} for item in reversed(payload["items"])
            ]}
        with patch.object(self.models, "_request", side_effect=response):
            self.assertEqual(self.models.rerank("monthly", ["a", "b", "c"]), [0.1, 0.2, 0.3])

    def test_bad_response_ids_dimension_and_values_are_rejected(self):
        responses = [
            {"model": "wrong", "vectors": []},
            {"model": "BAAI/bge-m3", "vectors": [{"id": "other", "vector": [1.0] * 1024}]},
            {"model": "BAAI/bge-m3", "vectors": [{"id": "0", "vector": [1.0]}]},
            {"model": "BAAI/bge-m3", "vectors": [{"id": "0", "vector": [0.0] * 1024}]},
            {"model": "BAAI/bge-m3", "vectors": [{"id": "0", "vector": [float("nan")] * 1024}]},
        ]
        for response in responses:
            with self.subTest(response=response["model"]):
                with patch.object(self.models, "_request", return_value=response):
                    with self.assertRaises(OsirisInferenceError):
                        self.models.embed(["x"])

    def test_duplicate_ids_and_scores_outside_range_are_rejected(self):
        for records in ([{"id": "0", "score": 0.5}] * 2, [{"id": "0", "score": -1}, {"id": "1", "score": 0.5}]):
            with patch.object(self.models, "_request", return_value={"model": "BAAI/bge-reranker-v2-m3", "scores": records}):
                with self.assertRaises(OsirisInferenceError):
                    self.models.rerank("x", ["a", "b"])

    def test_missing_or_old_worker_never_starts_a_job_or_submits(self):
        with patch.object(ServiceProfile, "read_heartbeat", return_value=None), patch.object(transport, "submit_request") as submit, patch("workspace.utils.osiris_runtime.lifecycle.ensure_ready") as start:
            with self.assertRaises(OsirisModelUnavailable):
                self.models.embed(["x"])
        start.assert_not_called()
        submit.assert_not_called()

    def test_request_uses_existing_transport_without_auto_recovery(self):
        with patch.object(module, "service_status", return_value={"heartbeat ready": True, "Osiris state": "running"}), patch.object(ServiceProfile, "heartbeat_ready", return_value=True), patch.object(ServiceProfile, "read_heartbeat", return_value={}), patch.object(transport, "submit_request", return_value="request") as submit, patch.object(transport, "wait_result", return_value={"fake": True}) as wait:
            self.assertEqual(self.models._request("embed", "", {"items": []}), {"fake": True})
        self.assertEqual(submit.call_args.args[0].job_name, "srbd3")
        self.assertFalse(wait.call_args.kwargs["auto_recover"])

    def test_timeout_has_no_local_model_fallback(self):
        with patch.object(module, "service_status", return_value={"heartbeat ready": True, "Osiris state": "running"}), patch.object(ServiceProfile, "heartbeat_ready", return_value=True), patch.object(ServiceProfile, "read_heartbeat", return_value={}), patch.object(transport, "submit_request", return_value="request"), patch.object(transport, "wait_result", side_effect=OsirisRequestTimeoutError("timeout")):
            with self.assertRaises(OsirisInferenceTimeout):
                self.models.embed(["x"])

    def test_fresh_heartbeat_with_nonrunning_sdk_state_cannot_submit(self):
        with patch.object(ServiceProfile, "heartbeat_ready", return_value=True), patch.object(ServiceProfile, "read_heartbeat", return_value={}), patch.object(module, "service_status", return_value={"heartbeat ready": False, "Osiris state": "stopped"}), patch.object(transport, "submit_request") as submit:
            with self.assertRaises(OsirisModelUnavailable):
                self.models.embed(["x"])
        submit.assert_not_called()

    def test_empty_batches_do_not_touch_service(self):
        with patch.object(self.models, "_request") as request:
            self.assertEqual(self.models.embed([]), [])
            self.assertEqual(self.models.rerank("x", []), [])
        request.assert_not_called()


class TestExistingWorker(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        path = Path(__file__).resolve().parents[1] / "workspace/skills/appeals-analyzer/rerank_osiris_worker.py"
        spec = importlib.util.spec_from_file_location("sql_assistant_test_existing_worker", path)
        cls.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(cls.worker)

    def test_embed_handler_reuses_loaded_model(self):
        model = SimpleNamespace(encode=Mock(return_value=np.array([[1.0] + [0.0] * 1023])))
        result = self.worker.embed_items(model, [{"id": "one", "text": "source"}])
        self.assertEqual(result["vectors"][0]["id"], "one")
        model.encode.assert_called_once()
        self.assertTrue(model.encode.call_args.kwargs["normalize_embeddings"])

    def test_worker_routes_embed_and_rerank_in_same_nfs_service(self):
        for kind in ("embed", "rerank"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                profile = ServiceProfile("appeals", "srbd3", "", "", "existing-worker", 1, root, ("embed", "rerank"))
                payload = {"model": "BAAI/bge-m3" if kind == "embed" else "BAAI/bge-reranker-v2-m3",
                           "items": [{"id": "0", "text": "monthly"}], "response_format": "records"}
                request_id = transport.submit_request(profile, "test", kind, "query", payload)
                manifest = root / "inbox" / (request_id + ".json")
                model = SimpleNamespace(encode=Mock(return_value=np.array([[1.0] + [0.0] * 1023])))
                with patch.object(self.worker, "NFS_ROOT", root), patch.object(self.worker, "PROFILE", profile), patch.object(self.worker, "predict_with_retry", return_value=np.array([0.0])):
                    self.worker.process_request(manifest, SimpleNamespace(embed=model), object())
                result = transport.wait_result(profile, "test", request_id, kind, timeout_sec=1, auto_recover=False)
                self.assertEqual(result["model"], payload["model"])
                if kind == "rerank":
                    self.assertEqual(result["scores"][0]["score"], 0.5)
                self.assertFalse(manifest.exists())

    def test_heartbeat_without_embed_is_not_ready_for_embed_client(self):
        profile = self.models_profile()
        heartbeat = {"timestamp": time.time(), "service_name": "appeals", "protocol_version": 2,
                     "capabilities": ["retrieve", "rerank"], "visible_gpus": 1, "ready_gpus": 1, "status": "ready"}
        self.assertFalse(profile.heartbeat_ready(heartbeat))

    @staticmethod
    def models_profile():
        return OsirisModels()._profile("embed")


if __name__ == "__main__":
    unittest.main()
