"""Full offline acceptance tests with a mock LLM, including detached CLI worker."""
from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from dataclasses import asdict, replace
from io import StringIO
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time
import types
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
ROOT = SCRIPTS.parents[3]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(SCRIPTS))
from application.history import analyze
from application.history_pipeline import prepare, run_operation, session_key, start, status
from cache.history import directory, file_lock, fingerprint, read, write
from llm.history import HistoryPolicy, HistoryRequests, explain_batch, request_meanings, validate_response
from output.history import render
from test_doc_history import LAW, REVIEW, TARGET, docx, sample

CFG = {"model": "test", "provider": "local", "api_base": "http://unused/v1", "api_key": "not-persisted"}
POLICY = HistoryPolicy()
SETTINGS = (CFG, 40768, POLICY)


class FakeLLM:
    def __init__(self):
        self.requests = []

    def __call__(self, messages, **kwargs):
        self.requests.append((messages, kwargs))
        events = json.loads(messages[1]["content"])["events"]
        return json.dumps({"results": [{"id": e["id"], "meaning": "Тестовое пояснение изменения."} for e in reversed(events)]})


def event(i, size=100):
    return {"id": f"e{i}", "before": "Срок 10 дней. " * size, "after": "Срок 20 дней. " * size,
            "revision_date": "2020-01-01", "effective_date": None, "provision": "Статья 1",
            "missing": [], "source_text": "DO_NOT_SEND_RAW_SOURCE", "revision_basis": "DO_NOT_SEND_BASIS"}


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        self.inputs = self.root / "uploads"
        self.inputs.mkdir()
        (self.inputs / TARGET).write_bytes(docx([TARGET[:-5], "Список изменяющих документов"]))
        (self.inputs / REVIEW).write_bytes(docx(sample()))
        self.fake = FakeLLM()

    def tearDown(self):
        self.temp.cleanup()

    def run_history(self, **kwargs):
        with redirect_stderr(StringIO()):
            return start([str(self.inputs)], target=TARGET, root=self.root, session="test",
                         settings=SETTINGS, call=self.fake, **kwargs)

    def test_full_report_facts_preserved_compact_result(self):
        result = self.run_history()
        self.assertEqual(result["total_changes"], 2)
        self.assertEqual(result["explained_changes"], 2)
        self.assertNotIn("changes", result)
        self.assertNotIn("next_offset", result)
        self.assertLess(len(json.dumps(result).encode()), 6000)
        saved = json.loads((self.root / "workspace" / result["result_json_path"]).read_text())
        original = analyze([str(self.inputs)], target=TARGET, _full=True)
        for expected, actual in zip(original["changes"], saved["changes"]):
            for key in expected:
                self.assertEqual(actual[key], expected[key])
        report = (self.root / "workspace" / result["result_path"]).read_text()
        self.assertEqual(report.count("\n## "), 2)
        self.assertNotIn(CFG["api_key"], saved.__repr__())
        self.assertEqual(len(self.fake.requests), 1)

    def test_needs_input_no_llm_no_config_no_cache(self):
        with patch("application.history_pipeline.runtime_settings", side_effect=AssertionError("config touched")):
            result = start([str(self.inputs)], root=self.root)
        self.assertEqual(result["status"], "needs_input")
        self.assertFalse((self.root / "workspace").exists())

    def test_renamed_latest_does_not_silently_select_old(self):
        fresh = self.inputs / (LAW + ".docx")
        (self.inputs / TARGET).rename(fresh)
        old = TARGET.replace("04.08.2026", "01.01.2020")
        (self.inputs / old).write_bytes(b"unreadable old file")
        result = analyze([str(self.inputs)], target=fresh.name)
        self.assertEqual(result["status"], "needs_input")

    def test_dynamic_batches_bound_input_and_output(self):
        requests = HistoryRequests(40768, POLICY)
        small = list(requests.batches([event(i, 1) for i in range(60)]))
        large = list(requests.batches([event(i, 400) for i in range(60)]))
        self.assertGreater(len(large), len(small))
        for batch in large + small:
            messages = requests.messages(batch)
            self.assertEqual(len(messages), 2)
            tokens = requests.estimator.estimate_many([m["content"] for m in messages])
            self.assertLess(tokens + requests.output_tokens(len(batch)) + 128, 40768 * .85)
            self.assertLessEqual(requests.output_tokens(len(batch)), 4096)

    def test_no_sources_or_previous_batches_in_prompt(self):
        requests = HistoryRequests(40768, POLICY)
        for batch in requests.batches([event(i, 1) for i in range(50)]):
            explain_batch(batch, requests, CFG, lambda rows: None, call=self.fake)
        sets = []
        for messages, _ in self.fake.requests:
            text = json.dumps(messages)
            self.assertNotIn("DO_NOT_SEND", text)
            self.assertNotIn("Тестовое пояснение", text)
            sets.append({v["id"] for v in json.loads(messages[1]["content"])["events"]})
        self.assertEqual(sum(map(len, sets)), 50)
        self.assertEqual(len(set.union(*sets)), 50)

    def test_compaction_keeps_changed_middle_without_mutating_facts(self):
        text = "Общий контекст. " * 10000
        value = event(1)
        value.update(before=text + "старый_срок" + text, after=text + "новый_срок" + text)
        original = dict(value)
        view = HistoryRequests(40768, POLICY).view(value)
        self.assertTrue(view["llm_view_compacted"])
        self.assertIn("старый_срок", view["before"])
        self.assertIn("новый_срок", view["after"])
        self.assertEqual(value, original)

    def test_targeted_retry_keeps_valid_ids(self):
        requests = HistoryRequests(40768, POLICY)
        views = [requests.view(event(i, 1)) for i in range(3)]
        received = []
        def call(messages, **kwargs):
            ids = [v["id"] for v in json.loads(messages[1]["content"])["events"]]
            received.append(ids)
            returned = ids[:1] if len(received) == 1 else ids
            return json.dumps({"results": [{"id": k, "meaning": "Пояснение"} for k in returned]})
        result = explain_batch(views, requests, CFG, lambda rows: None, call=call)
        self.assertEqual(set(result), {"e0", "e1", "e2"})
        self.assertTrue(all("e0" not in ids for ids in received[1:]))

    def test_bad_json_retry_is_bounded_and_report_partial(self):
        calls = []
        def bad(messages, **kwargs):
            calls.append(messages)
            return "broken json"
        self.fake = bad
        result = self.run_history()
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["explained_changes"], 0)
        self.assertEqual(result["total_changes"], 2)
        self.assertLessEqual(len(calls), 6)
        report = (self.root / "workspace" / result["result_path"]).read_text()
        self.assertEqual(report.count("\n## "), 2)

    def test_unknown_duplicate_missing_ids_validation(self):
        with self.assertRaises(ValueError):
            validate_response('{"results":[{"id":"foreign","meaning":"x"}]}', {"e1"})
        repeated = '{"results":[{"id":"e1","meaning":"x"},{"id":"e1","meaning":"y"}]}'
        self.assertEqual(validate_response(repeated, {"e1"}), {})
        self.assertEqual(validate_response('{"results":[]}', {"e1"}), {})

    def test_resume_only_failed_events(self):
        op, _ = prepare([str(self.inputs)], TARGET, None, self.root, "test", settings=SETTINGS)
        path = directory(self.root, "test", op)
        parsed = read(path / "parsed.json")
        first = parsed["changes"][0]["id"]
        write(path / "explanations.json", {first: {"meaning": "Уже готово", "needs_review": False,
                                                   "reason": "", "explanation_status": "completed"}})
        result = self.run_history()
        self.assertEqual(result["explained_changes"], 2)
        sent = json.loads(self.fake.requests[0][0][1]["content"])["events"]
        self.assertEqual(len(sent), 1)
        self.assertNotEqual(sent[0]["id"], first)

    def test_repeat_completed_no_llm_and_same_artifacts(self):
        first = self.run_history()
        count = len(self.fake.requests)
        second = self.run_history()
        self.assertEqual(first, second)
        self.assertEqual(len(self.fake.requests), count)

    def test_operation_identity_sources_settings_prompt(self):
        parsed = analyze([str(self.inputs)], target=TARGET, _full=True)
        base = fingerprint(parsed, CFG, 40768, asdict(POLICY), "prompt")
        for cfg, context, policy, prompt in (({**CFG, "model": "other"}, 40768, asdict(POLICY), "prompt"),
                                            (CFG, 32000, asdict(POLICY), "prompt"),
                                            (CFG, 40768, asdict(replace(POLICY, max_retries=1)), "prompt"),
                                            (CFG, 40768, asdict(POLICY), "new prompt")):
            self.assertNotEqual(base, fingerprint(parsed, cfg, context, policy, prompt))
        self.assertEqual(base, fingerprint(parsed, {**CFG, "api_key": "rotated"}, 40768, asdict(POLICY), "prompt"))
        changed = {**parsed, "source_hashes": {**parsed["source_hashes"], "target": "different"}}
        self.assertNotEqual(base, fingerprint(changed, CFG, 40768, asdict(POLICY), "prompt"))

    def test_single_flight_shared_boundary(self):
        active = 0
        peak = 0
        def fake_call(*args, **kwargs):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            time.sleep(.01)
            active -= 1
            return '{"results":[]}'
        module = types.ModuleType("lib.services.llm_client")
        module.call_llm = fake_call
        with patch.dict(sys.modules, {"lib.services.llm_client": module}):
            threads = [threading.Thread(target=request_meanings, args=([],), kwargs=dict(cfg=CFG, max_tokens=512, policy=POLICY)) for _ in range(5)]
            for thread in threads: thread.start()
            for thread in threads: thread.join()
        self.assertEqual(peak, 1)

    def test_active_operation_is_not_started_twice(self):
        op, _ = prepare([str(self.inputs)], TARGET, None, self.root, "test", settings=SETTINGS)
        with file_lock(directory(self.root, "test", op) / "run.lock"):
            result = run_operation(op, self.root, "test", settings=SETTINGS, call=self.fake)
        self.assertEqual(result["status"], "running")
        self.assertEqual(self.fake.requests, [])

    def test_session_paths_and_invalid_operation(self):
        with self.assertRaises(ValueError):
            session_key(["/server/data_store/cache/sessions/user42/attachments/x.docx"])
        self.assertEqual(session_key([], "user42"), "user42")
        with self.assertRaises(ValueError):
            directory(self.root, "test", "../escape")

    def test_cli_default_needs_input_one_json(self):
        result = subprocess.run([sys.executable, str(SCRIPTS / "cli_history.py"), "--input", str(self.inputs)], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "needs_input")

    def test_cli_full_stdout_one_json_despite_logs(self):
        import cli_history
        out, err = StringIO(), StringIO()
        def noisy(*args, **kwargs):
            print("some library diagnostic")
            return FakeLLM()(*args, **kwargs)
        # Patch the orchestration call, preserving real pipeline/persistence.
        from application.history_pipeline import start as actual
        def injected(inputs, **kwargs):
            return actual(inputs, **kwargs, settings=SETTINGS, call=noisy)
        with patch("application.history_pipeline.start", injected), redirect_stdout(out), redirect_stderr(err):
            code = cli_history.main(["--input", str(self.inputs), "--target", TARGET,
                                     "--workspace-root", str(self.root), "--session-key", "test"])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(out.getvalue())["total_changes"], 2)
        self.assertIn("diagnostic", err.getvalue())

    def test_background_cli_with_mock_server_module(self):
        # An isolated child process using the real CLI, resolver and storage.
        # The HTTP boundary alone is replaced; no running vLLM is needed.
        site = self.root / "site"
        site.mkdir()
        (site / "sitecustomize.py").write_text('''import sys, types, json
config = types.ModuleType("config")
config.ConfigurationError = RuntimeError
config.SETTINGS = {"skills":{"legal_summarizer":{}},"agents":{"defaults":{"provider":"local","model":"test","contextWindowTokens":40768}},"providers":{"local":{"apiBase":"http://unused/v1","apiKey":"not-persisted"}}}
sys.modules["config"] = config
llm = types.ModuleType("lib.services.llm_client")
def call(messages, **kwargs):
    events=json.loads(messages[1]["content"])["events"]
    return json.dumps({"results":[{"id":e["id"],"meaning":"Тестовое пояснение."} for e in events]})
llm.call_llm=call
sys.modules["lib.services.llm_client"]=llm
''', encoding="utf-8")
        env = {**os.environ, "PYTHONPATH": str(site)}
        args = [sys.executable, str(SCRIPTS / "cli_history.py"), "--input", str(self.inputs),
                "--target", TARGET, "--workspace-root", str(self.root), "--session-key", "test"]
        process = subprocess.run(args + ["--background"], env=env, capture_output=True, timeout=10)
        self.assertEqual(process.returncode, 0, (process.stdout, process.stderr))
        result = json.loads(process.stdout)
        self.assertEqual(result["status"], "running")
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            current = status(result["operation_id"], self.root, "test")
            if current["status"] in ("completed", "partial", "error"):
                break
            time.sleep(.05)
        self.assertEqual(current["status"], "completed", (current, (directory(self.root, "test", result["operation_id"]) / "worker.log").read_text(encoding="utf-8")))
        self.assertNotIn("changes", current)
        check = subprocess.run(args + ["--status", result["operation_id"]], env=env, capture_output=True, timeout=10)
        self.assertEqual(json.loads(check.stdout)["result_path"], current["result_path"])


class RealPipelineTests(unittest.TestCase):
    def test_both_full_reports_and_compact_handoff(self):
        fixtures = Path(os.environ.get("DOC_HISTORY_FIXTURES", str(ROOT.parent / "Consultant")))
        if not fixtures.is_dir():
            self.skipTest("Set DOC_HISTORY_FIXTURES")
        for folder, expected in (("38fz", 368), ("353fz", 179)):
            inputs = fixtures / folder
            target = next(p for p in inputs.glob("*.docx") if "(ред. от 04.08.2026)" in p.name)
            fake = FakeLLM()
            with tempfile.TemporaryDirectory() as temp, redirect_stderr(StringIO()):
                root = Path(temp)
                compact = start([str(inputs)], target=target.name, root=root, session="real",
                                settings=SETTINGS, call=fake)
                full = json.loads((root / "workspace" / compact["result_json_path"]).read_text())
                report = (root / "workspace" / compact["result_path"]).read_text()
                self.assertEqual(compact["total_changes"], expected)
                self.assertEqual(len(full["changes"]), expected)
                self.assertEqual(report.count("\n## "), expected)
                self.assertEqual(compact["explained_changes"], expected)
                self.assertLess(len(json.dumps(compact).encode()), 6000)
                self.assertNotIn("changes", compact)
                sent_ids = [e["id"] for messages, _ in fake.requests for e in json.loads(messages[1]["content"])["events"]]
                self.assertEqual(len(sent_ids), expected)
                self.assertEqual(len(set(sent_ids)), expected)
                self.assertLess(len(fake.requests), (expected + 4) // 5)
                self.assertEqual([e["sort_date"] for e in full["changes"]], sorted(e["sort_date"] for e in full["changes"]))
                requests = HistoryRequests(40768, POLICY)
                for messages, kwargs in fake.requests:
                    count = requests.estimator.estimate_many([m["content"] for m in messages])
                    self.assertLess(count + kwargs["max_tokens"] + 128, 40768 * .85)


if __name__ == "__main__":
    unittest.main()
