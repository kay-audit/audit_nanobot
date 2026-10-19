from __future__ import annotations

import importlib.util
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from workspace.skills.sql_assistant.scripts import osiris_adapter

pytestmark = pytest.mark.skipif(importlib.util.find_spec("workspace.utils.osiris_runtime.deployment") is None,
                                reason="Shared deployment is available in finalize_nanobot")


def test_d1_d3_d6_share_one_worker_with_real_correlated_transport(tmp_path, monkeypatch):
    from workspace.utils.osiris_runtime import client, lifecycle
    from workspace.utils.osiris_runtime.deployment import load_deployment, bind_profile
    from workspace.utils.osiris_runtime.dispatcher import HandlerRegistry
    from workspace.utils.osiris_runtime.shared_worker import SharedWorker

    config = tmp_path / "deployment.json"
    config.write_text(json.dumps({"deployment": {"name": "nanobot_gpu", "job_name": "nanobot-shared",
        "image": "image", "pool": "common", "worker_script": str(tmp_path / "worker.py"),
        "nfs_root": str(tmp_path / "nfs")}, "services": {"appeals": {}, "ior": {}}}), encoding="utf-8")
    monkeypatch.setenv("OSIRIS_SHARED_CONFIG", str(config))
    deployment, _ = load_deployment()
    models = osiris_adapter.OsirisModels({"job_name": "obsolete", "nfs_root": "obsolete", "request_timeout_sec": 5})
    assert models.session_id != osiris_adapter.OsirisModels().session_id
    models.session_id = "d1"
    appeal = models._profile("embed")
    assert appeal.job_name == "nanobot-shared" and appeal.image and appeal.pool
    assert appeal.deployment == deployment
    assert appeal.nfs_root == deployment.profile.nfs_root / "services" / "appeals"
    appeal = replace(appeal, capabilities=("embed", "rerank", "retrieve"))
    ior = bind_profile(replace(deployment.profile, service_name="ior", capabilities=("echo",)))
    deployment.profile.prepare_nfs()
    client._atomic_json({"requested_name": "nanobot-shared", "service_name": "nanobot_gpu", "generation": "a" * 32}, deployment.profile.metadata_path)

    def infer(payload, context):
        if context.operation == "embed":
            return {"model": "BAAI/bge-m3", "vectors": [{"id": row["id"], "vector": [1.] + [0.] * 1023} for row in payload["items"]]}
        return {"model": "BAAI/bge-reranker-v2-m3", "scores": [{"id": row["id"], "score": .5} for row in payload["items"]]}

    handlers = HandlerRegistry().register("embed", infer).register("rerank", infer).register("retrieve", lambda payload, context: {"service": context.service_name})
    ior_handlers = HandlerRegistry().register("echo", lambda payload, context: {"service": context.service_name, "payload": payload})
    def legacy_appeals(manifest, payload):
        if manifest["request_type"] == "retrieve":
            return {"service": "appeals"}
        return infer(payload, SimpleNamespace(operation=manifest["request_type"]))

    worker = SharedWorker(deployment, [appeal, ior], {"appeals": (handlers, legacy_appeals), "ior": (ior_handlers, lambda manifest, payload: {"service": "ior", "payload": payload})}, generation="a" * 32)
    worker.prepare()
    worker.activity.ready()
    client._atomic_json(worker.snapshot(), deployment.profile.heartbeat_path)
    monkeypatch.setattr(osiris_adapter, "service_status", lambda profile: {"heartbeat ready": True, "Osiris state": "running"})
    start = Mock(side_effect=AssertionError("D1 must not create or recover a GPU job"))
    monkeypatch.setattr(lifecycle, "ensure_ready", start)
    stop = threading.Event()

    def work():
        while not stop.wait(.01):
            worker.step()

    thread = threading.Thread(target=work, daemon=True)
    thread.start()
    try:
        with ThreadPoolExecutor(max_workers=4) as pool:
            dense = pool.submit(models.embed, ["D1"])
            rerank = pool.submit(models.rerank, "D1", ["x"])
            d3 = client.submit_request(appeal, "d3", "retrieve", "D3", {}, 5)
            d6 = client.submit_request(ior, "d6", "echo", "D6", {"source": "D6"}, 5)
            assert dense.result(timeout=8)[0][0] == 1.
            assert rerank.result(timeout=8) == [.5]
            assert client.wait_result(appeal, "d3", d3, "retrieve", 5, auto_recover=False) == {"service": "appeals"}
            assert client.wait_result(ior, "d6", d6, "echo", 5, auto_recover=False)["service"] == "ior"
            assert d3 != d6
        start.assert_not_called()
    finally:
        stop.set()
        thread.join(timeout=3)


def test_native_loader_registers_d1_alongside_d3_d5_d6():
    from pathlib import Path
    from config import AttrDict
    from lib.services.project_tool_loader import register_project_tools
    from lib.services.runtime_inventory import canonical_project_tools
    from nanobot.agent.tools.registry import ToolRegistry

    names = {"sql_analyzer", "kb_search", "kb_describe", "sql_generate", "sql_validate", "sql_facts"}
    settings = AttrDict(gateway={name: {"enable": True} for name in names | {"appeals_analyzer", "ior_analyzer"}},
                        tools={"follow_up": {"enable": True}})
    workspace = Path(__file__).resolve().parents[1] / "workspace"
    agent = SimpleNamespace(tools=ToolRegistry(), tools_config=SimpleNamespace(), workspace=str(workspace))
    result = register_project_tools(agent, workspace, settings=settings,
                                    cache_store=SimpleNamespace(execute_readonly=lambda *args, **kwargs: {}))
    expected = names | {"appeals_analyzer", "ior_analyzer", "mcp_follow_up_ask", "mcp_follow_up_hypotheses",
                        "mcp_follow_up_deviations", "mcp_follow_up_status", "mcp_follow_up_card_start",
                        "mcp_follow_up_card_status", "mcp_follow_up_forget"}
    assert expected <= set(result.registered), result
    assert names <= {spec.name for spec in canonical_project_tools()}
