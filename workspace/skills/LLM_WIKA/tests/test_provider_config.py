from __future__ import annotations

import os
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import PropertyMock, call, patch

import requests

from wiki_agent.config import Settings, load_env_file
from wiki_agent.errors import (
    ConfigurationError,
    ProviderUnavailableError,
)
from wiki_agent.provider import (
    InternalGigaChatProvider,
    LangChainGigaChatProvider,
    _sanitize_error,
    _validated_internal_url,
    _validated_https_url,
)


class ProviderAndConfigTests(unittest.TestCase):
    def test_external_context_requires_explicit_opt_in(self) -> None:
        settings = Settings(root=Path.cwd(), allow_external_context=False)
        with self.assertRaises(ProviderUnavailableError):
            LangChainGigaChatProvider(settings)

    def test_credentials_are_required_after_opt_in(self) -> None:
        settings = Settings(root=Path.cwd(), allow_external_context=True)
        with patch.dict(
            os.environ,
            {
                "GIGACHAT_CREDENTIALS": "",
                "GIGACHAT_ACCESS_TOKEN": "",
            },
            clear=False,
        ):
            with self.assertRaises(ProviderUnavailableError):
                LangChainGigaChatProvider(settings)

    def test_error_redacts_both_supported_secrets(self) -> None:
        with patch.dict(
            os.environ,
            {
                "GIGACHAT_CREDENTIALS": "credential-secret",
                "GIGACHAT_ACCESS_TOKEN": "token-secret",
                "JPY_API_TOKEN": "internal-token-secret",
            },
            clear=False,
        ):
            result = _sanitize_error(
                "credential-secret, token-secret and internal-token-secret "
                "must not leak"
            )
        self.assertNotIn("credential-secret", result)
        self.assertNotIn("token-secret", result)
        self.assertNotIn("internal-token-secret", result)
        self.assertEqual(result.count("<redacted>"), 3)

    def test_custom_endpoints_require_https_without_userinfo(self) -> None:
        for value in (
            "http://internal.example/api",
            "https://user:password@internal.example/api",
            "not-a-url",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ProviderUnavailableError):
                    _validated_https_url("GIGACHAT_BASE_URL", value)
        self.assertEqual(
            _validated_https_url(
                "GIGACHAT_BASE_URL",
                "https://internal.example/api",
            ),
            "https://internal.example/api",
        )

    def test_internal_endpoint_explicitly_accepts_http(self) -> None:
        self.assertEqual(
            _validated_internal_url(
                "GIGACHAT_API_URL",
                "http://internal.example/v1/chat/completions",
            ),
            "http://internal.example/v1/chat/completions",
        )
        for value in (
            "ftp://internal.example/api",
            "http://user:password@internal.example/api",
            "http://internal.example/api?token=secret",
        ):
            with self.subTest(value=value):
                with self.assertRaises(ProviderUnavailableError):
                    _validated_internal_url("GIGACHAT_API_URL", value)

    def test_internal_provider_requires_explicit_api_url(self) -> None:
        settings = Settings(
            root=Path.cwd(),
            provider="gigachat_internal",
            allow_external_context=True,
        )
        with patch.dict(
            os.environ,
            {
                "JPY_API_TOKEN": "internal-secret",
                "GIGACHAT_API_URL": "None",
            },
            clear=False,
        ):
            with self.assertRaisesRegex(
                ProviderUnavailableError, "Не задан GIGACHAT_API_URL"
            ):
                InternalGigaChatProvider(settings)

    def test_internal_provider_retries_timeout_and_temporary_status(self) -> None:
        timeout = requests.exceptions.Timeout("slow")
        limited = types.SimpleNamespace(
            status_code=429,
            ok=False,
            text="busy",
        )
        success = types.SimpleNamespace(
            status_code=200,
            ok=True,
            text="",
            json=lambda: {
                "choices": [{"message": {"content": " OK "}}]
            },
        )
        settings = Settings(
            root=Path.cwd(),
            provider="gigachat_internal",
            model="GigaChat-3-Ultra",
            allow_external_context=True,
        )
        with (
            patch.dict(
                os.environ,
                {
                    "JPY_API_TOKEN": "internal-secret",
                    "GIGACHAT_API_URL": (
                        "http://internal.example/v1/chat/completions"
                    ),
                    "GIGACHAT_RETRY_MAX_ATTEMPTS": "3",
                    "GIGACHAT_RETRY_DELAY_STEP": "0.5",
                },
                clear=False,
            ),
            patch(
                "requests.post",
                side_effect=[timeout, limited, success],
            ) as post,
            patch("wiki_agent.provider.time.sleep") as sleep,
        ):
            result = InternalGigaChatProvider(settings).complete(
                types.SimpleNamespace(
                    system_prompt="system",
                    user_prompt="user",
                )
            )
        self.assertEqual(result.content, "OK")
        self.assertEqual(post.call_count, 3)
        self.assertEqual(sleep.call_args_list, [call(0.5), call(1.0)])
        headers = post.call_args.kwargs["headers"]
        self.assertEqual(headers["Authorization"], "Bearer internal-secret")
        self.assertEqual(
            post.call_args.kwargs["json"]["model"],
            "GigaChat-3-Ultra",
        )

    def test_internal_provider_does_not_retry_authorization_error(self) -> None:
        denied = types.SimpleNamespace(
            status_code=401,
            ok=False,
            text="denied",
        )
        settings = Settings(
            root=Path.cwd(),
            provider="gigachat_internal",
            allow_external_context=True,
        )
        with (
            patch.dict(
                os.environ,
                {
                    "JPY_API_TOKEN": "internal-secret",
                    "GIGACHAT_API_URL": (
                        "http://internal.example/v1/chat/completions"
                    ),
                },
                clear=False,
            ),
            patch("requests.post", return_value=denied) as post,
            patch("wiki_agent.provider.time.sleep") as sleep,
        ):
            with self.assertRaisesRegex(
                ProviderUnavailableError, "токен истёк"
            ):
                InternalGigaChatProvider(settings).complete(
                    types.SimpleNamespace(
                        system_prompt="system",
                        user_prompt="user",
                    )
                )
        post.assert_called_once()
        sleep.assert_not_called()

    def test_closed_contour_settings_use_local_bge_without_download(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(
                os.environ,
                {
                    "LLM_WIKI_PROVIDER": "gigachat_internal",
                    "LLM_WIKI_EMBEDDING_MODEL": (
                        "/home/datalab/nfs/rag/bge/BAAI:bge-m3"
                    ),
                    "LLM_WIKI_EMBEDDING_DEVICE": "cuda",
                    "LLM_WIKI_ALLOW_EMBEDDING_DOWNLOAD": "false",
                },
                clear=True,
            ):
                settings = Settings.from_root(root)
        self.assertEqual(settings.model, "GigaChat-3-Ultra")
        self.assertEqual(settings.embedding_device, "cuda")
        self.assertFalse(settings.allow_embedding_download)
        self.assertEqual(
            settings.embedding_model,
            "/home/datalab/nfs/rag/bge/BAAI:bge-m3",
        )

    def test_constructor_error_is_controlled_and_redacted(self) -> None:
        secret = "do-not-print"
        messages = types.ModuleType("langchain_core.messages")
        messages.HumanMessage = object
        messages.SystemMessage = object
        package = types.ModuleType("langchain_core")
        package.messages = messages
        adapter = types.ModuleType("langchain_gigachat")

        class BrokenClient:
            def __init__(self, **kwargs: object) -> None:
                del kwargs
                raise ValueError(f"bad credentials {secret}")

        adapter.GigaChat = BrokenClient
        settings = Settings(root=Path.cwd(), allow_external_context=True)
        with (
            patch.dict(
                os.environ,
                {"GIGACHAT_CREDENTIALS": secret},
                clear=False,
            ),
            patch.dict(
                "sys.modules",
                {
                    "langchain_core": package,
                    "langchain_core.messages": messages,
                    "langchain_gigachat": adapter,
                },
            ),
            patch.object(
                Settings,
                "python_supported_by_agent_stack",
                new_callable=PropertyMock,
                return_value=True,
            ),
        ):
            with self.assertRaises(ProviderUnavailableError) as caught:
                LangChainGigaChatProvider(settings)
        self.assertNotIn(secret, str(caught.exception))
        self.assertIn("<redacted>", str(caught.exception))

    def test_env_file_does_not_override_process_environment(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / ".env"
            path.write_text(
                "GIGACHAT_SCOPE=GIGACHAT_API_CORP\n",
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"GIGACHAT_SCOPE": "GIGACHAT_API_PERS"},
                clear=False,
            ):
                load_env_file(path)
                self.assertEqual(
                    os.environ["GIGACHAT_SCOPE"],
                    "GIGACHAT_API_PERS",
                )

    def test_env_symlink_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            target = root / "actual.env"
            target.write_text("A=B\n", encoding="utf-8")
            link = root / ".env"
            link.symlink_to(target)
            with self.assertRaises(ConfigurationError):
                load_env_file(link)


if __name__ == "__main__":
    unittest.main()
