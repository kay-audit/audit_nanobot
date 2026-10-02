from __future__ import annotations

import json
import os
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import requests

from wiki_agent.config import EMBEDDING_DIRECTORY, EMBEDDING_REPO, Settings
from wiki_agent.errors import ConfigurationError, ProviderUnavailableError
from wiki_agent.model_install import install_embedding_model
from wiki_agent.models import LLMRequest
from wiki_agent.provider import MiniMaxProvider, provider_from_settings
from wiki_agent.semantic import SentenceTransformerEmbedder


class ExternalTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {
            "MINIMAX_API_KEY": "test-key-not-a-real-secret", "MINIMAX_BASE_URL": "https://api.minimax.io/v1",
            "MINIMAX_MAX_ATTEMPTS": "2", "MINIMAX_TIMEOUT": "20", "MINIMAX_MAX_TOKENS": "8192",
        }, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        self.settings = Settings(root=Path.cwd(), provider="minimax", model="MiniMax-M2.7", allow_external_context=True)
        self.request = LLMRequest(system_prompt="Return JSON", user_prompt="test", operation="test")

    def response(self, content='<think>private reasoning</think>{"ok":true}', status=200, finish="stop"):
        return types.SimpleNamespace(status_code=status, ok=status == 200,
            json=lambda: {"choices": [{"finish_reason": finish, "message": {"content": content}}]})

    def test_api_key_cannot_be_loaded_from_env_file(self):
        from wiki_agent.config import load_env_file
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / ".env"
            path.write_text("MINIMAX_API_KEY=not-a-real-key\n", encoding="utf-8")
            with self.assertRaises(ConfigurationError):
                load_env_file(path)
            self.assertEqual(os.environ["MINIMAX_API_KEY"], "test-key-not-a-real-secret")

    def test_post_contract_and_reasoning_removed(self):
        with patch("requests.post", return_value=self.response()) as post:
            result = provider_from_settings(self.settings).complete(self.request)
        self.assertEqual(result.content, '{"ok":true}')
        args, kw = post.call_args
        self.assertEqual(args[0], "https://api.minimax.io/v1/chat/completions")
        self.assertTrue(kw["verify"])
        self.assertFalse(kw["allow_redirects"])
        self.assertEqual(kw["timeout"], (10, 20))
        self.assertEqual(kw["json"]["model"], "MiniMax-M2.7")
        self.assertNotIn("tools", kw["json"])
        self.assertNotIn("test-key", str(kw["json"]))

    def test_errors_do_not_leak_token_or_source_and_400_not_retried(self):
        response = self.response(status=400)
        response.text = "test-key-not-a-real-secret confidential source"
        with patch("requests.post", return_value=response) as post:
            with self.assertRaises(ProviderUnavailableError) as error:
                MiniMaxProvider(self.settings).complete(self.request)
        self.assertIn("400", str(error.exception))
        self.assertNotIn("confidential", str(error.exception))
        self.assertNotIn("test-key", str(error.exception))
        self.assertEqual(post.call_count, 1)

    def test_retries_bounded(self):
        with patch("requests.post", side_effect=requests.exceptions.Timeout), patch("wiki_agent.provider.time.sleep"):
            with self.assertRaises(ProviderUnavailableError):
                MiniMaxProvider(self.settings).complete(self.request)

    def test_429_then_success(self):
        with patch("requests.post", side_effect=[self.response(status=429), self.response()]) as post, patch("wiki_agent.provider.time.sleep"):
            self.assertEqual(MiniMaxProvider(self.settings).complete(self.request).content, '{"ok":true}')
        self.assertEqual(post.call_count, 2)

    def test_truncated_response_rejected(self):
        with patch("requests.post", return_value=self.response(finish="length")):
            with self.assertRaises(ProviderUnavailableError):
                MiniMaxProvider(self.settings).complete(self.request)

    def test_explicit_consent_and_key_required(self):
        with self.assertRaises(ProviderUnavailableError):
            MiniMaxProvider(Settings(root=Path.cwd(), provider="minimax"))
        os.environ.pop("MINIMAX_API_KEY")
        with self.assertRaises(ProviderUnavailableError):
            MiniMaxProvider(self.settings)

    def test_no_secret_to_other_host(self):
        for url in ["http://api.minimax.io/v1", "https://other.example/v1", "https://api.minimax.io/v1?token=x"]:
            os.environ["MINIMAX_BASE_URL"] = url
            with self.assertRaises(ProviderUnavailableError):
                MiniMaxProvider(self.settings)

    def test_config_default_external_local_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            with patch("wiki_agent.config.find_local_embedding_model", return_value=None):
                settings = Settings.from_root(root)
            self.assertEqual(settings.provider, "minimax")
            self.assertEqual(settings.model, "MiniMax-M2.7")
            self.assertEqual(Path(settings.embedding_model).resolve(), (root / EMBEDDING_DIRECTORY).resolve())
            self.assertFalse(settings.allow_external_context)
            self.assertFalse(settings.allow_embedding_download)

    def test_minimax_python_312_supported(self):
        with patch("wiki_agent.config.sys.version_info", (3, 12, 12)), patch("importlib.metadata.metadata", return_value={"Requires-Python": ">=3.10"}):
            self.assertTrue(self.settings.python_supported_by_agent_stack)

    def test_minimax_reports_actual_package_python_restriction(self):
        with patch("wiki_agent.config.sys.version_info", (3, 12, 12)), patch("importlib.metadata.metadata", return_value={"Requires-Python": ">=3.13"}):
            self.assertFalse(self.settings.python_supported_by_agent_stack)

    def test_minimax_python_below_project_minimum_rejected(self):
        with patch("wiki_agent.config.sys.version_info", (3, 9, 9)):
            self.assertFalse(self.settings.python_supported_by_agent_stack)

    def test_minimax_python_314_rejected_by_explicit_project_requirement(self):
        with patch("wiki_agent.config.sys.version_info", (3, 14, 3)):
            self.assertFalse(self.settings.python_supported_by_agent_stack)

    def test_installer_pins_commit_and_reuses_installation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / EMBEDDING_DIRECTORY
            settings = Settings(root=root, embedding_model=str(target))
            commit = "a" * 40
            def download(**kw):
                self.assertEqual(kw["repo_id"], EMBEDDING_REPO)
                self.assertEqual(kw["revision"], commit)
                (target / "config.json").write_text('{}')
                (target / "tokenizer.json").write_text('{}')
                (target / "model.safetensors").write_bytes(b"test")
            with patch("wiki_agent.model_install.find_local_embedding_model", return_value=None), patch("huggingface_hub.HfApi") as api, patch("huggingface_hub.snapshot_download", side_effect=download) as snapshot:
                api.return_value.model_info.return_value.sha = commit
                self.assertEqual(install_embedding_model(settings), target)
                self.assertEqual(install_embedding_model(settings), target)
                self.assertEqual(snapshot.call_count, 1)
            self.assertEqual(json.loads((target / "llm_wiki_model.json").read_text())["revision"], commit)

    def test_bge_plain_query_and_local_only(self):
        import numpy as np
        calls = []
        class Model:
            def __init__(self, name, **kw):
                calls.append((name, kw))
            def encode(self, texts, **kw):
                calls.append((texts, kw))
                return np.array([[1., 0.] for _ in (texts if isinstance(texts, list) else [texts])])
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            target = root / EMBEDDING_DIRECTORY
            target.mkdir(parents=True)
            (target / "config.json").write_text('{}')
            (target / "llm_wiki_model.json").write_text(json.dumps({"repo_id": EMBEDDING_REPO, "revision": "a" * 40}))
            fake_st = types.ModuleType("sentence_transformers")
            fake_st.SentenceTransformer = Model
            fake_logging = types.SimpleNamespace(disable_progress_bar=lambda: None)
            fake_transformers = types.ModuleType("transformers")
            fake_utils = types.ModuleType("transformers.utils")
            fake_utils.logging = fake_logging
            fake_transformers.utils = fake_utils
            fake_torch = types.SimpleNamespace(float32="float32", bfloat16="bfloat16", cuda=types.SimpleNamespace(is_available=lambda: False))
            with patch.dict(sys.modules, {"sentence_transformers": fake_st, "transformers": fake_transformers,
                                         "transformers.utils": fake_utils, "torch": fake_torch}):
                embedder = SentenceTransformerEmbedder(str(target), model_cache_dir=root / '.cache/models', workspace_root=root, allow_download=False, device="cpu")
                embedder.encode_documents(["Document"])
                embedder.encode_query("Question")
        self.assertTrue(calls[0][1]["local_files_only"])
        self.assertFalse(calls[0][1]["trust_remote_code"])
        self.assertEqual(calls[1][0], ["Document"])
        self.assertEqual(calls[2][0], "Question")

    def test_installer_reuses_local_bge_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            cached = root / "local-bge"
            cached.mkdir()
            (cached / "config.json").write_text('{}')
            (cached / "tokenizer.json").write_text('{}')
            (cached / "pytorch_model.bin").write_bytes(b"test")
            settings = Settings(root=root, embedding_model=str(cached))
            with patch("huggingface_hub.HfApi") as api, patch("huggingface_hub.snapshot_download") as download:
                self.assertEqual(install_embedding_model(settings), cached)
                api.assert_not_called()
                download.assert_not_called()


if __name__ == "__main__":
    unittest.main()
