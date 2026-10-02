"""Isolated adapter contract tests; nanobot is replaced with a test double."""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import subprocess
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

TOOL_PATH = Path(__file__).resolve().parents[3] / "tools" / "llm_wiki.py"


class AdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        base = types.ModuleType("nanobot.agent.tools.base")
        base.Tool = type("Tool", (), {})
        base.tool_parameters = lambda schema: lambda cls: cls
        spec = importlib.util.spec_from_file_location("llm_wiki_adapter_test", TOOL_PATH)
        cls.module = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"nanobot.agent.tools.base": base}):
            spec.loader.exec_module(cls.module)

    def tool(self):
        return self.module.LlmWikiTool(config=self.module.LlmWikiToolConfig())

    def test_create_and_enable(self):
        ctx = types.SimpleNamespace(_settings_ref={"tools": {"llm_wiki": {"timeout_sec": 45}},
                                                   "skills": {"llm_wiki": {"enabled": True}}})
        self.assertTrue(self.module.LlmWikiTool.enabled(ctx))
        self.assertEqual(self.module.LlmWikiTool.create(ctx).config.timeout_sec, 45)
        ctx._settings_ref["skills"]["llm_wiki"]["enabled"] = False
        self.assertFalse(self.module.LlmWikiTool.enabled(ctx))

    def test_success_and_subprocess_boundary(self):
        done = subprocess.CompletedProcess([], 0, '{"status":"ok","result":{"answer":"Ответ"}}', "")
        with patch.object(sys, "version_info", (3, 12)), patch.object(
                self.module.subprocess, "run", return_value=done) as call:
            result = json.loads(asyncio.run(self.tool().execute(question="Вопрос")))
        self.assertEqual(result["result"]["answer"], "Ответ")
        self.assertFalse(call.call_args.kwargs["shell"])
        self.assertEqual(call.call_args.args[0][0], sys.executable)
        self.assertEqual(json.loads(call.call_args.kwargs["input"])["question"], "Вопрос")

    def test_domain_error(self):
        done = subprocess.CompletedProcess([], 2, '{"status":"error","error_type":"ValidationError","message":"Нет JSON"}', "")
        with patch.object(sys, "version_info", (3, 12)), patch.object(
                self.module.subprocess, "run", return_value=done):
            result = json.loads(asyncio.run(self.tool().execute(action="prepare")))
        self.assertEqual(result["error_type"], "ValidationError")

    def test_timeout_does_not_leak_stderr(self):
        with patch.object(sys, "version_info", (3, 12)), patch.object(
                self.module.subprocess, "run",
                side_effect=subprocess.TimeoutExpired([], 600, stderr="sensitive")):
            result = asyncio.run(self.tool().execute(action="status"))
        self.assertNotIn("sensitive", result)
        self.assertEqual(json.loads(result)["error_type"], "timeout")

    def test_invalid_json_does_not_echo_process_output(self):
        done = subprocess.CompletedProcess([], 0, "sensitive", "sensitive")
        with patch.object(sys, "version_info", (3, 12)), patch.object(
                self.module.subprocess, "run", return_value=done):
            result = asyncio.run(self.tool().execute(action="status"))
        self.assertNotIn("sensitive", result)
        self.assertEqual(json.loads(result)["error_type"], "cli_failed")

    def test_key_redaction(self):
        with patch.dict(os.environ, {"MINIMAX_API_KEY": "test-only-secret"}):
            self.assertNotIn("test-only-secret", self.module._safe_json({"message": "test-only-secret"}))

    def test_apply_and_secret_arguments_rejected(self):
        self.assertEqual(json.loads(asyncio.run(self.tool().execute(action="apply")))["status"], "error")
        self.assertEqual(json.loads(asyncio.run(self.tool().execute(token="not-accepted")))["status"], "error")

    def test_python_version_error(self):
        with patch.object(sys, "version_info", (3, 14)):
            self.assertEqual(json.loads(asyncio.run(self.tool().execute(action="status")))["error_type"], "python_version")


if __name__ == "__main__":
    unittest.main()
