"""Real CLI transport and Pydantic schema; only the nanobot base is stubbed."""
from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
import runpy
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT))


@unittest.skipUnless(importlib.util.find_spec("pydantic"), "Requires project dependency pydantic")
class HistoryTransportTests(unittest.TestCase):
    def load_tool(self):
        module = types.ModuleType("nanobot.agent.tools.base")
        module.Tool = type("Tool", (), {})
        def parameters(schema):
            def decorate(cls):
                cls.test_schema = schema
                return cls
            return decorate
        module.tool_parameters = parameters
        with patch.dict(sys.modules, {"nanobot.agent.tools.base": module}):
            return runpy.run_path(str(ROOT / "workspace/tools/legal_summarizer_history.py"))

    def test_public_tool_has_no_pagination_and_runs_cli(self):
        namespace = self.load_tool()
        cls = namespace["LegalSummarizerHistoryTool"]
        self.assertFalse({"offset", "limit", "field"} & cls.test_schema["properties"].keys())
        from nanobot.agent.tools.context import RequestContext, request_context
        sessions = ROOT / "workspace/data_store/cache/sessions"
        sessions.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=sessions) as temp:
            root = ROOT
            session = Path(temp).name
            attachments = Path(temp) / "attachments"
            attachments.mkdir(parents=True)
            tool = cls(config=namespace["HistoryConfig"](workspace_root=str(root)))
            with request_context(RequestContext(channel="test", chat_id="test", session_key=session, sender_id="u")):
                output = asyncio.run(tool.execute(inputs=[str(attachments)]))
        payload = json.loads(output)
        self.assertEqual(payload["status"], "needs_input")
        self.assertNotIn("changes", payload)

    def test_legacy_pagination_rejected(self):
        ns = self.load_tool()
        tool = ns["LegalSummarizerHistoryTool"](config=ns["HistoryConfig"]())
        output = asyncio.run(tool.execute(inputs=["unused"], offset=5))
        self.assertEqual(json.loads(output)["status"], "error")

    def test_project_history_schema_and_runtime_defaults(self):
        from config import SETTINGS
        from lib.core.project_settings import SkillSettings, SkillHistorySettings
        from pydantic import ValidationError
        from dataclasses import asdict
        sys.path.insert(0, str(ROOT / "workspace/skills/legal_summarizer/scripts"))
        from llm.history import HistoryPolicy
        value = SkillSettings.model_validate(dict(SETTINGS["skills"]["legal_summarizer"]))
        self.assertEqual(value.history.model_dump(), asdict(HistoryPolicy()))
        with self.assertRaises(ValidationError):
            SkillHistorySettings.model_validate({"max_output_tokens": 8192})
        with self.assertRaises(ValidationError):
            SkillHistorySettings.model_validate({"typo": 1})


if __name__ == "__main__":
    unittest.main()
