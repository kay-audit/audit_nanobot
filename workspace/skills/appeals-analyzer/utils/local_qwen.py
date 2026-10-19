"""LLM adapter for Appeals Analyzer: local OpenAI-compatible vLLM only.

Drop-in replacement for appeals-analyzer/utils/local_qwen.py.
Existing callers use def_ask_gigachat(messages); the historical name is kept,
but that function sends requests ONLY to the local vLLM endpoint.

Uses agent_endpoint() for the local provider, model and address. Explicit
APPEALS_VLLM_* / VLLM_* overrides are retained for standalone operation.

No automatic fallback to GigaChat, a remote LLM, or /generate.
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from contextvars import ContextVar
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import request as urllib_request
from urllib.error import HTTPError, URLError

try:
    import requests
except ImportError:
    requests = None

_PROJECT_ROOT = next(
    (p for p in Path(__file__).resolve().parents if (p / "lib" / "services").is_dir()),
    Path(__file__).resolve().parent,
)
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))

def strip_think_blocks(value: str | None) -> str:
    """Keep Qwen final content after </think>, never an unfinished reasoning block."""
    text = value or ""
    text = re.sub(r"<think>.*?</think>|```(?:xml-)?think\s*\n.*?```", "", text,
                  flags=re.DOTALL | re.IGNORECASE)
    # Qwen can omit the opening tag in message.content. Remove the marker,
    # not the final answer following it (the shared MiniMax cleaner did that).
    text = re.split(r"</think>", text, flags=re.IGNORECASE)[-1]
    text = re.split(r"<think>", text, flags=re.IGNORECASE)[0]
    return text.strip()

logger = logging.getLogger(__name__)
_LLM_STATUS = ContextVar("appeals_llm_status", default=(None, None))
_LLM_DIAGNOSTICS = ContextVar("appeals_llm_diagnostics", default=None)
_LLM_DEADLINE = ContextVar("appeals_llm_deadline", default=None)
_AGENT_ENDPOINT: Optional[Dict[str, Any]] = None


def last_llm_status():
    """Return (provider, HTTP status) for diagnostic code in Appeals."""
    return _LLM_STATUS.get()


def last_llm_diagnostics():
    """Safe metadata only; never includes prompts, response bodies or credentials."""
    return dict(_LLM_DIAGNOSTICS.get() or {})


@contextmanager
def generation_budget(seconds):
    token = _LLM_DEADLINE.set(time.monotonic() + seconds)
    try:
        yield
    finally:
        _LLM_DEADLINE.reset(token)


def _request_timeout(limit):
    deadline = _LLM_DEADLINE.get()
    remaining = limit if deadline is None else min(limit, deadline - time.monotonic())
    if remaining <= 0:
        raise TimeoutError('Appeals generation deadline elapsed')
    return remaining


def _record(reason, **fields):
    metadata = last_llm_diagnostics()
    metadata.update(reason=reason, **fields)
    _LLM_DIAGNOSTICS.set(metadata)
    logger.info('Appeals vLLM response %s', json.dumps(metadata))


def _enabled(name: str, default: bool = False) -> bool:
    text = os.getenv(name, "").strip().casefold()
    return default if not text else text in {"1", "true", "yes", "on"}


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(os.getenv(name, ""))
        return value if value > 0 else default
    except (TypeError, ValueError):
        return default


def _api_base(value: str) -> str:
    base = value.strip().rstrip("/")
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/models"):
        base = base[: -len("/models")]
    if base.endswith("/v1"):
        return base
    return base + "/v1"


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int):
    """Return (HTTP status, JSON object, body); never log body or credentials."""
    if requests is not None:
        # Connect quickly; read timeout may be overridden by APPEALS_VLLM_TIMEOUT_SEC.
        response = requests.post(url, headers=headers, json=payload,
                                 timeout=(min(5, timeout), timeout))
        try:
            parsed = response.json()
        except (ValueError, TypeError):
            parsed = {}
        return response.status_code, parsed if isinstance(parsed, dict) else {}, response.text

    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=timeout) as response:
            body = response.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(body)
            except ValueError:
                parsed = {}
            return response.status, parsed if isinstance(parsed, dict) else {}, body
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except ValueError:
            parsed = {}
        return error.code, parsed if isinstance(parsed, dict) else {}, body


def _agent_endpoint() -> Dict[str, Any]:
    """Resolve the same local model as D6; never send to a cloud provider."""
    global _AGENT_ENDPOINT
    if _AGENT_ENDPOINT is not None:
        return _AGENT_ENDPOINT

    try:
        from lib.services.llm_config import agent_endpoint
        configured = agent_endpoint()
    except Exception:
        if not (os.getenv("APPEALS_VLLM_API_URL") or os.getenv("VLLM_API_URL")):
            raise ValueError('Local agent endpoint is not configured') from None
        configured = {}

    configured_provider = str(configured.get("provider", "")).casefold().strip()
    is_local_provider = configured_provider in {"vllm", "sglang"}

    explicit_url = (os.getenv("APPEALS_VLLM_API_URL") or os.getenv("VLLM_API_URL") or "").strip()
    if not is_local_provider and not explicit_url:
        raise ValueError('Appeals requires a configured vllm/sglang provider')
    configured_url = str(configured.get("api_base") or "") if is_local_provider else ""
    if not explicit_url and not configured_url:
        raise ValueError('Local agent endpoint has no address')
    base = _api_base(explicit_url) if explicit_url else configured_url.rstrip('/')
    model = (os.getenv("APPEALS_VLLM_MODEL") or os.getenv("VLLM_MODEL_NAME")
             or (str(configured.get("model") or "") if is_local_provider else "")).strip()
    key = (os.getenv("APPEALS_VLLM_API_KEY") or os.getenv("VLLM_API_KEY")
           or (str(configured.get("api_key") or "") if is_local_provider else "")).strip()

    _AGENT_ENDPOINT = {
        "provider": "vllm", "chat_url": f"{base}/chat/completions",
        "models_url": f"{base}/models", "api_key": key, "model": model,
        "extra_body": dict(configured.get("extra_body") or {}) if is_local_provider else {},
    }
    return _AGENT_ENDPOINT


def _served_model(endpoint: Dict[str, Any], headers: Dict[str, str]) -> str:
    configured = endpoint.get("model")
    if configured:
        return str(configured)

    url = endpoint.get("models_url") or endpoint["chat_url"].rsplit("/chat/completions", 1)[0] + "/models"
    try:
        if requests is not None:
            res = requests.get(url, headers=headers, timeout=_request_timeout(5))
            if res.status_code != 200:
                raise RuntimeError(f"models endpoint HTTP {res.status_code}")
            result = res.json()
        else:
            with urllib_request.urlopen(urllib_request.Request(url, headers=headers), timeout=_request_timeout(5)) as res:
                result = json.loads(res.read().decode("utf-8"))
        models = result.get("data", [])
        if not models or not isinstance(models[0], dict) or not models[0].get("id"):
            raise ValueError("empty models list")
        # Model discovery can be shared for subsequent evidence batches.
        endpoint["model"] = str(models[0]["id"])
        return endpoint["model"]
    except Exception as exc:
        raise RuntimeError(
            "Не удалось определить модель локального vLLM. "
            "Задайте APPEALS_VLLM_MODEL/VLLM_MODEL_NAME или проверьте GET /v1/models"
        ) from exc


def _normalized_messages(messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """vLLM/Qwen must receive at least one nonempty user message."""
    clean = []
    for item in messages or []:
        if not isinstance(item, dict):
            continue
        role = str(item.get("role", "user")).strip().lower()
        if role not in {"system", "user", "assistant"}:
            role = "user"
        content = item.get("content")
        if isinstance(content, str) and content.strip():
            clean.append({"role": role, "content": content})
    if not any(m["role"] == "user" for m in clean):
        # Legacy D3 might call with only 'system'; don't send such a request to vLLM.
        system = "\n\n".join(m["content"] for m in clean if m["role"] == "system")
        if system:
            clean = [{"role": "user", "content": system}]
    return clean


def _wants_json(messages: List[Dict[str, str]]) -> bool:
    # Evidence and reducer prompts have an explicit strict JSON contract.
    # Do not enable JSON mode for the final four-hypothesis prose narrative.
    system = "\n".join(m["content"] for m in messages if m["role"] == "system").casefold()
    return "json" in system and ("строго" in system or "structured evidence" in system)


def call_agent_llm(messages: List[Dict[str, str]],
                   max_tokens: int = 8192,
                   temperature: float = 0.2) -> str:
    """Call local vLLM exactly once; never use GigaChat or remote fallback.

    Guided JSON is opt-in for older vLLM compatibility. Model reasoning is
    disabled by default *for Appeals only* to avoid spending the token budget
    on <think> while returning no evidence. Set APPEALS_VLLM_ENABLE_THINKING=1
    to opt in. An empty/invalid reply is reported as empty to existing D3
    fallback logic (no invented hypotheses).
    """
    _LLM_STATUS.set(("vllm", None))
    _LLM_DIAGNOSTICS.set({"provider": "vllm", "http_status": None,
        "finish_reason": None, "response_chars": 0, "final_chars": 0,
        "prompt_tokens": None, "completion_tokens": None})
    if _enabled("APPEALS_DISABLE_VLLM"):
        _record('disabled')
        return ""

    try:
        endpoint = _agent_endpoint()
    except Exception:
        _record('configuration_error')
        return ""
    messages = _normalized_messages(messages)
    if not messages or not any(msg["role"] == "user" for msg in messages):
        _record('empty_task')
        return ""

    headers = {"Content-Type": "application/json"}
    if endpoint.get("api_key"):
        headers["Authorization"] = "Bearer " + endpoint["api_key"]
    try:
        model = _served_model(endpoint, headers)
    except RuntimeError:
        _record('model_discovery_error')
        return ""

    token_cap = _positive_int("APPEALS_VLLM_MAX_TOKENS", max_tokens)
    json_task = _wants_json(messages)
    if json_task:
        token_cap = min(token_cap, _positive_int("APPEALS_VLLM_JSON_MAX_TOKENS", 4096))
    payload: Dict[str, Any] = {
        **dict(endpoint.get('extra_body') or {}),
        "model": model, "messages": messages, "max_tokens": token_cap,
        "temperature": float(os.getenv("APPEALS_VLLM_TEMPERATURE", temperature)),
    }
    payload['chat_template_kwargs'] = dict(payload.get('chat_template_kwargs') or {})
    payload['chat_template_kwargs']['enable_thinking'] = _enabled("APPEALS_VLLM_ENABLE_THINKING", False)
    if json_task and _enabled("APPEALS_VLLM_JSON_MODE", False):
        payload["response_format"] = {"type": "json_object"}

    timeout = _positive_int("APPEALS_VLLM_TIMEOUT_SEC", 45)
    logger.info("Appeals vLLM request: chars=%s max_tokens=%s json=%s",
                sum(len(m["content"]) for m in messages), token_cap, json_task)
    started = time.monotonic()
    try:
        status, response_json, body = _post_json(endpoint["chat_url"], headers, payload, _request_timeout(timeout))
        # Some older vLLM builds reject response_format; retry only that specific
        # capability error, never generic HTTP 400 (e.g. missing user text).
        if status == 400 and "response_format" in payload:
            error = response_json.get("error", {})
            description = (str(error.get("message", "")) if isinstance(error, dict) else str(error)).lower()
            if "response_format" in description or "guided" in description:
                logger.warning("Appeals vLLM: JSON mode unsupported; retrying without response_format")
                payload.pop("response_format")
                status, response_json, body = _post_json(endpoint["chat_url"], headers, payload, _request_timeout(timeout))
        _LLM_STATUS.set(("vllm", status))
        _LLM_DIAGNOSTICS.set({**last_llm_diagnostics(), 'http_status': status})
    except (OSError, TimeoutError, URLError) as exc:
        _record('network_error', exception_type=type(exc).__name__)
        return ""
    except Exception as exc:
        _record('network_error', exception_type=type(exc).__name__)
        return ""

    if not 200 <= status < 300:
        _record('http_error')
        return ""
    choices = response_json.get("choices")
    if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
        _record('empty_choices')
        return ""
    choice = choices[0]
    message = choice.get("message") or {}
    if not isinstance(message, dict):
        message = {}
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, str):
        content = str(choice.get("text") or "")
    answer = strip_think_blocks(content)
    finish = choice.get('finish_reason')
    finish = finish if finish is None or (isinstance(finish, str) and finish in {'stop', 'length', 'tool_calls', 'content_filter'}) else 'other'
    usage = response_json.get('usage') or {}
    if not isinstance(usage, dict):
        usage = {}
    tokens = {key: value if type(value) is int and value >= 0 else None
              for key, value in ((name, usage.get(name)) for name in ('prompt_tokens', 'completion_tokens'))}
    _LLM_DIAGNOSTICS.set({**last_llm_diagnostics(), **tokens, 'finish_reason': finish,
                          'response_chars': len(content), 'final_chars': len(answer)})
    if finish == 'length':
        _record('token_limit')
        return ''
    if not answer:
        _record('reasoning_only' if message.get('reasoning_content') or '<think>' in content.casefold() else 'empty_response')
        return ""
    _record('ok', elapsed_sec=round(time.monotonic()-started, 3))
    return answer


def call_vllm_http(messages: List[Dict[str, str]]) -> str:
    """Compatibility entry point for existing D3 calls."""
    return call_agent_llm(messages)


def def_ask_gigachat(messages: List[Dict[str, str]]) -> str:
    """Historical name used by appeals_hypothesis.py; vLLM ONLY, no GigaChat."""
    return call_agent_llm(messages)


# This function is retained **unchanged** for the existing regression contract.
# It is not called by def_ask_gigachat(), call_agent_llm(), or call_vllm_http().
# No analysis request can fall back to GigaChat.
GIGACHAT_API_URL = os.getenv("GIGACHAT_API_URL", "http://liveaccess/v1/gc/chat/completions")
JPY_API_TOKEN = os.getenv("JPY_API_TOKEN") or os.getenv("GIGACHAT_CREDENTIALS") or os.getenv("GIGACHAT_TOKEN") or ""
GIGACHAT_MODEL_NAME = os.getenv("GIGACHAT_MODEL_NAME", "GigaChat-3-Ultra")
_last_call_time = 0.0
_delay_sec = 1.0


def _wait_rate_limit():
    global _last_call_time
    elapsed = time.time() - _last_call_time
    if elapsed < _delay_sec:
        time.sleep(_delay_sec - elapsed)
    _last_call_time = time.time()


def call_gigachat_sber_api(messages: List[Dict[str, str]], temperature: float = 0.01) -> str:
    """
    Вызов GigaChat через http://liveaccess/v1/gc/chat/completions с поддержкой автоматического
    перебора доступных моделей при возникновении 403 RBAC Access Denied.
    """
    GIGACHAT_API_URL = "http://liveaccess/v1/gc/chat/completions"
    HEADERS = {
        "Authorization": f"Bearer {os.environ.get('JPY_API_TOKEN')}",
        "Content-Type": "application/json"
    }

    payload = {
        "model": "GigaChat-3-Ultra",
        "messages": messages,
        "n": 1,
        "temperature": temperature
    }

    try:
        _wait_rate_limit()

        response = requests.post(
            GIGACHAT_API_URL,
            headers=HEADERS,
            json=payload,
        )

        if response.ok:
            res_json = response.json()
            choices = res_json.get("choices", [])
            if choices:
                return strip_think_blocks(choices[0].get("message", {}).get("content", ""))

            logger.error(
                "Gigachat HTTP %s: %s",
                res_json,
            )
            return ""

        logger.error(
            "Gigachat HTTP %s: %s",
            response.status_code,
            response.text,
        )
    except requests.RequestException as e:
        logger.exception(
            "Gigachat request failed: %s",
            e,
        )
    except ValueError as e:
        logger.exception(
            "Gigachat returned invalid JSON: %s",
            e,
        )
    return ""
