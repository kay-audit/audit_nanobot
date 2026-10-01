"""Offline contracts for the reusable Osiris lifecycle, transport and idle worker."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from unittest.mock import Mock, patch

import appeals_osiris_job
import osiris_job
from workspace.utils.osiris_runtime import OsirisUnavailableError, ServiceProfile
from workspace.utils.osiris_runtime import client, lifecycle, worker


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        root = Path(temporary.name)
        script = root / "worker.py"
        script.write_text("", encoding="utf-8")
        self.profile = ServiceProfile("test-service", "test-job", "image", "pool", str(script),
                                      1, root / "nfs", ("retrieve", "rerank"),
                                      idle_timeout_sec=.04, startup_wait_timeout_sec=.2)
        self.states = {}
        self.names = []
        self.sdk = Mock()
        self.sdk.list.side_effect = lambda: list(self.names)
        self.sdk.state.side_effect = lambda name: self.states.get(name, "Not found")
        self.sdk.status.side_effect = lambda name: self.states.get(name, "Not found")

    def heartbeat(self, *, status="ready", generation=None, service_name=None, **changes):
        value = dict(timestamp=time.time(), status=status, service_name=service_name or self.profile.service_name,
                     protocol_version=2, capabilities=["retrieve", "rerank"], ready_gpus=1,
                     visible_gpus=1, queue_size=0, active_requests=0, last_activity_at=time.time(),
                     idle_for_sec=0.0, generation=generation)
        value.update(changes)
        self.profile.prepare_nfs()
        self.profile.heartbeat_path.write_text(json.dumps(value), encoding="utf-8")

    def existing(self, state="Running", name="operator-test-job", *, ready=True):
        self.names.append(name)
        self.states[name] = state
        lifecycle._write_meta(self.profile, job_name=name)
        if ready:
            self.heartbeat()
        return name

    def create_ready(self, **kwargs):
        self.assertFalse(kwargs["restart"])
        name = "operator-test-job"
        self.names.append(name)
        self.states[name] = "Running"
        self.heartbeat(generation=lifecycle._read_meta(self.profile)["generation"])
        return {"job_name": name}

    def test_ready_does_not_create_and_cli_is_thin(self):
        name = self.existing()
        self.assertEqual(lifecycle.ensure_ready(self.profile, self.sdk), name)
        self.sdk.create.assert_not_called()
        self.assertEqual(appeals_osiris_job.main.__name__, "main")

    def test_operator_cli_calls_shared_lifecycle(self):
        with patch.object(osiris_job, "load_profile", return_value=self.profile), \
                patch.object(osiris_job, "ensure_ready", return_value="operator-test-job") as start, \
                patch.object(osiris_job, "service_status", return_value={"Osiris state": "running"}) as status, \
                patch.object(osiris_job, "stop_service", return_value="operator-test-job") as stop:
            self.assertEqual(appeals_osiris_job.main(["start"]), 0)
            self.assertEqual(osiris_job.main(["status"]), 0)
            self.assertEqual(osiris_job.main(["stop"]), 0)
        start.assert_called_once()
        status.assert_called_once_with(self.profile)
        stop.assert_called_once_with(self.profile, timeout=120.0)

    def test_imports_do_not_start_threads_or_sdk(self):
        code = """import sys, threading
from pathlib import Path
threading.Thread.start = lambda *a, **k: (_ for _ in ()).throw(AssertionError('thread'))
Path.mkdir = lambda *a, **k: (_ for _ in ()).throw(AssertionError('mkdir'))
import osiris_job, appeals_osiris_job
from workspace.utils.osiris_runtime import lifecycle, client
assert 'osiris' not in sys.modules
"""
        result = subprocess.run([sys.executable, "-B", "-c", code], capture_output=True,
                                text=True, cwd=Path(__file__).resolve().parents[1])
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_missing_creates_once_without_restart(self):
        self.sdk.create.side_effect = self.create_ready
        self.assertEqual(lifecycle.ensure_ready(self.profile, self.sdk), "operator-test-job")
        self.assertEqual(lifecycle.ensure_ready(self.profile, self.sdk), "operator-test-job")
        self.sdk.create.assert_called_once()

    def test_create_result_can_precede_scheduler_visibility(self):
        def create(**kwargs):
            name = "operator-test-job"
            self.names.append(name)
            self.states[name] = "Not found"
            def become_running():
                time.sleep(.025)
                self.states[name] = "Running"
                self.heartbeat(generation=lifecycle._read_meta(self.profile)["generation"])
            threading.Thread(target=become_running).start()
            return {"job_name": name}
        self.sdk.create.side_effect = create
        with patch.object(lifecycle, "POLL_SEC", .005):
            self.assertEqual(lifecycle.ensure_ready(self.profile, self.sdk), "operator-test-job")
        self.sdk.create.assert_called_once()

    def test_pending_waits_and_does_not_duplicate(self):
        name = self.existing("Pending", ready=False)
        def ready_later():
            time.sleep(.025)
            self.states[name] = "Running"
            self.heartbeat()
        threading.Thread(target=ready_later).start()
        with patch.object(lifecycle, "POLL_SEC", .005):
            self.assertEqual(lifecycle.ensure_ready(self.profile, self.sdk), name)
        self.sdk.create.assert_not_called()

    def test_running_starting_heartbeat_waits_for_ready(self):
        name = self.existing(ready=False)
        self.heartbeat(status="starting", ready_gpus=0)
        def ready_later():
            time.sleep(.025)
            self.heartbeat()
        threading.Thread(target=ready_later).start()
        with patch.object(lifecycle, "POLL_SEC", .005):
            self.assertEqual(lifecycle.ensure_ready(self.profile, self.sdk), name)
        self.sdk.create.assert_not_called()

    def test_startup_timeout_is_typed_and_not_request_timeout(self):
        self.existing(ready=False)
        with patch.object(lifecycle, "POLL_SEC", .005):
            with self.assertRaises(OsirisUnavailableError):
                lifecycle.ensure_ready(self.profile, self.sdk, timeout=.025)
        self.assertEqual(client.DEFAULT_REQUEST_TIMEOUT_SEC, 1800)

    def test_two_callers_share_one_create(self):
        def slow_create(**kwargs):
            time.sleep(.025)
            return self.create_ready(**kwargs)
        self.sdk.create.side_effect = slow_create
        with patch.object(lifecycle, "POLL_SEC", .005), ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(lifecycle.ensure_ready, self.profile, self.sdk) for _ in range(2)]
            self.assertEqual([future.result() for future in futures], ["operator-test-job"] * 2)
        self.sdk.create.assert_called_once()

    def test_unknown_sdk_state_never_creates(self):
        self.sdk.list.side_effect = RuntimeError("network unavailable")
        with patch.object(lifecycle, "POLL_SEC", .005):
            with self.assertRaises(OsirisUnavailableError):
                lifecycle.ensure_ready(self.profile, self.sdk, timeout=.025)
        self.sdk.create.assert_not_called()

    def test_unconfirmed_create_does_not_duplicate(self):
        self.sdk.create.side_effect = RuntimeError("response lost")
        with self.assertRaises(OsirisUnavailableError):
            lifecycle.ensure_ready(self.profile, self.sdk)
        self.assertTrue(lifecycle._read_meta(self.profile)["create_pending"])
        with patch.object(lifecycle, "POLL_SEC", .005):
            with self.assertRaises(OsirisUnavailableError):
                lifecycle.ensure_ready(self.profile, self.sdk, timeout=.025)
        self.sdk.create.assert_called_once()

    def test_stale_pending_with_confirmed_terminal_recovers(self):
        lifecycle._write_meta(self.profile, job_name="old-test-job", create_pending=True)
        self.states["old-test-job"] = "Failed"
        self.sdk.create.side_effect = self.create_ready
        self.assertEqual(lifecycle.ensure_ready(self.profile, self.sdk), "operator-test-job")
        self.sdk.create.assert_called_once()

    def test_dead_owner_lock_is_reclaimed(self):
        self.profile.prepare_nfs()
        self.profile.lock_path.write_text(json.dumps({"host": lifecycle.socket.gethostname(), "pid": 99999999}))
        self.sdk.create.side_effect = self.create_ready
        with patch.object(lifecycle.os, "kill", side_effect=ProcessLookupError):
            lifecycle.ensure_ready(self.profile, self.sdk)
        self.assertFalse(self.profile.lock_path.exists())

    def test_status_is_read_only_and_terminal_wins_over_fresh_heartbeat(self):
        name = self.existing(state="Finished")
        before = self.profile.heartbeat_path.read_bytes(), self.profile.metadata_path.read_bytes()
        status = lifecycle.service_status(self.profile, self.sdk)
        self.assertEqual(status["actual job name"], name)
        self.assertFalse(status["heartbeat ready"])
        self.assertEqual(before, (self.profile.heartbeat_path.read_bytes(), self.profile.metadata_path.read_bytes()))
        self.sdk.create.assert_not_called()

    def test_auto_stopped_job_is_started_again(self):
        self.sdk.create.side_effect = self.create_ready
        lifecycle.ensure_ready(self.profile, self.sdk)
        self.states["operator-test-job"] = "Finished"
        self.profile.heartbeat_path.unlink()
        lifecycle.ensure_ready(self.profile, self.sdk)
        self.assertEqual(self.sdk.create.call_count, 2)

    def test_running_job_with_stale_heartbeat_is_deleted_before_recreate(self):
        name = self.existing()
        self.heartbeat(timestamp=time.time() - 60)
        self.sdk.create.side_effect = self.create_ready
        self.sdk.delete.side_effect = lambda actual: self.states.__setitem__(actual, "Not found")
        with patch.object(lifecycle, "POLL_SEC", .005):
            self.assertEqual(lifecycle.ensure_ready(self.profile, self.sdk), name)
        self.sdk.delete.assert_called_once_with(name)
        self.sdk.create.assert_called_once()

    def test_delete_uses_actual_name_and_clears_only_after_confirmation(self):
        name = self.existing()
        def delete(actual):
            self.assertEqual(actual, name)
            self.assertTrue(self.profile.metadata_path.exists())
            self.states[actual] = "Not found"
        self.sdk.delete.side_effect = delete
        self.assertEqual(lifecycle.stop_service(self.profile, self.sdk), name)
        self.sdk.delete.assert_called_once_with(name)
        self.assertFalse(self.profile.metadata_path.exists())
        self.assertFalse(self.profile.heartbeat_path.exists())
        self.names.clear()
        lifecycle.stop_service(self.profile, self.sdk)
        self.sdk.delete.assert_called_once()

    def test_stop_timeout_retains_metadata(self):
        self.existing()
        with patch.object(lifecycle, "POLL_SEC", .005):
            with self.assertRaises(OsirisUnavailableError):
                lifecycle.stop_service(self.profile, self.sdk, timeout=.025)
        self.assertTrue(self.profile.metadata_path.exists())

    def test_prefixed_name_and_profile_isolation(self):
        self.existing()
        other = replace(self.profile, service_name="other", job_name="other-job", nfs_root=self.profile.nfs_root / "other")
        name, state, _ = lifecycle.discover(other, self.sdk)
        self.assertEqual((name, state), (None, "not_found"))
        self.assertFalse(other.heartbeat_ready(self.profile.read_heartbeat()))


class WorkerIdleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = ServiceProfile("test", "job", "image", "pool", "worker.py", 1,
                                      Path(temporary.name), ("retrieve",), idle_timeout_sec=.025)
        self.profile.prepare_nfs()
        self.activity = worker.WorkerActivity(self.profile)
        self.activity.ready()
        self.tasks = queue.Queue()

    def test_manual_start_idles_out(self):
        time.sleep(.035)
        self.assertTrue(worker.should_idle_stop(self.profile, self.activity, self.tasks))
        self.assertEqual(self.activity.snapshot(0)["status"], "idle_stopping")

    def test_request_before_ttl_extends_it_and_long_work_is_protected(self):
        time.sleep(.015)
        self.activity.begin()
        time.sleep(.035)
        self.assertFalse(worker.should_idle_stop(self.profile, self.activity, self.tasks))
        self.activity.end()
        self.assertFalse(worker.should_idle_stop(self.profile, self.activity, self.tasks))
        time.sleep(.035)
        self.assertTrue(worker.should_idle_stop(self.profile, self.activity, self.tasks))

    def test_each_request_resets_ttl_and_pending_manifest_prevents_stop(self):
        for _ in range(2):
            time.sleep(.015)
            self.activity.begin()
            self.activity.end()
            self.assertFalse(self.activity.idle_expired(0))
        time.sleep(.035)
        (self.profile.nfs_root / "inbox" / "pending.json").write_text('{"expires_at":9999999999}')
        self.assertFalse(worker.should_idle_stop(self.profile, self.activity, self.tasks))
        self.assertEqual(self.tasks.qsize(), 1)

    def test_processing_is_recovered_and_expired_is_discarded(self):
        request_id = client.submit_request(self.profile, "session", "retrieve", "query", {})
        worker.claim_new_requests(self.profile, self.tasks)
        self.assertEqual(self.tasks.qsize(), 1)
        worker.recover_processing_after_restart(self.profile)
        self.assertTrue((self.profile.nfs_root / "inbox" / f"{request_id}.json").exists())
        expired = self.profile.nfs_root / "processing" / "expired.json"
        expired.write_text('{"expires_at":1}')
        worker.recover_processing_after_restart(self.profile)
        self.assertFalse(expired.exists())


class TransportRecoveryTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.profile = ServiceProfile("test", "job", "image", "pool", "worker.py", 1,
                                      Path(temporary.name), ("retrieve",))

    def test_request_starts_first_then_submits_with_separate_timeout(self):
        calls = []
        with patch.object(client, "ensure_ready", side_effect=lambda profile: calls.append("ready")), \
                patch.object(client, "submit_request", side_effect=lambda *args: calls.append("submit") or "id"), \
                patch.object(client, "wait_result", side_effect=lambda *args, **kwargs: calls.append("wait") or ["ok"]):
            result = client.request(self.profile, "session", "retrieve", "query", {}, timeout_sec=17)
        self.assertEqual(result, ["ok"])
        self.assertEqual(calls, ["ready", "submit", "wait"])

    def test_worker_death_after_submit_gets_one_recovery_attempt(self):
        request_id = client.submit_request(self.profile, "session", "retrieve", "query", {})
        _, dirs = client.session_dirs(self.profile, "session")
        def recover(profile):
            client._atomic_pickle({"request_id": request_id, "request_type": "retrieve", "result": ["ok"]},
                                  dirs["output"] / f"{request_id}.pkl")
        with patch.object(ServiceProfile, "read_heartbeat", return_value={"status": "stopped"}), \
                patch.object(client, "ensure_ready", side_effect=recover) as start:
            result = client.wait_result(self.profile, "session", request_id, "retrieve", .2, auto_recover=True)
        self.assertEqual(result, ["ok"])
        start.assert_called_once_with(self.profile)

    def test_request_timeout_is_distinct_and_cleans_manifest(self):
        request_id = client.submit_request(self.profile, "session", "retrieve", "query", {})
        with self.assertRaisesRegex(TimeoutError, "retrieve timeout"):
            client.wait_result(self.profile, "session", request_id, "retrieve", .001)
        self.assertFalse((self.profile.nfs_root / "inbox" / f"{request_id}.json").exists())


if __name__ == "__main__":
    unittest.main()
