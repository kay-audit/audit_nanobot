"""
local_qwen.py — Модуль взаимодействия с GigaChat в корпоративной сети Сбера с автоподбором разрешенной RBAC-модели.
"""
from __future__ import annotations

import os
import json
import logging
import time
try:
    import requests
except ImportError:  # closed contour may expose only the Python stdlib
    requests = None
import ssl
from urllib import request as urllib_request
from urllib.error import HTTPError
from pathlib import Path
from typing import List, Dict, Any, Optional

logger = logging.getLogger(__name__)

# Точные настройки подключения GigaChat из ior_assistant
GIGACHAT_API_URL = os.getenv("GIGACHAT_API_URL", "http://liveaccess/v1/gc/chat/completions")
JPY_API_TOKEN = os.getenv("JPY_API_TOKEN") or os.getenv("GIGACHAT_CREDENTIALS") or os.getenv("GIGACHAT_TOKEN") or ""
GIGACHAT_MODEL_NAME = os.getenv("GIGACHAT_MODEL_NAME", "GigaChat-3-Ultra")

VLLM_API_URL = os.getenv("VLLM_API_URL", "http://localhost:8000/v1/chat/completions")
VLLM_GENERATE_URL = os.getenv("VLLM_GENERATE_URL", "http://localhost:8001/generate")

_last_call_time = 0.0
_delay_sec = 1.0


def _post_json(url: str, headers: Dict[str, str], payload: Dict[str, Any], timeout: int):
    """Return (status, parsed_json, body); uses requests when present, urllib otherwise."""
    if requests is not None:
        response = requests.post(url, headers=headers, json=payload, timeout=timeout, verify=False)
        try:
            parsed = response.json()
        except Exception:
            parsed = {}
        return response.status_code, parsed, response.text
    data = json.dumps(payload).encode("utf-8")
    req = urllib_request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib_request.urlopen(req, timeout=timeout, context=ssl._create_unverified_context()) as response:
            body = response.read().decode("utf-8", errors="replace")
            return response.status, json.loads(body), body
    except HTTPError as error:
        body = error.read().decode("utf-8", errors="replace")
        try:
            parsed = json.loads(body)
        except Exception:
            parsed = {}
        return error.code, parsed, body


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
    token = os.getenv("JPY_API_TOKEN") or os.getenv("GIGACHAT_CREDENTIALS") or os.getenv("GIGACHAT_TOKEN") or JPY_API_TOKEN
    api_url = os.getenv("GIGACHAT_API_URL") or GIGACHAT_API_URL

    headers = {
        "Content-Type": "application/json",
        "Accept": "application/json"
    }
    if token:
        clean_token = token.replace("Bearer ", "").strip()
        headers["Authorization"] = f"Bearer {clean_token}"

    models_to_try = [
        os.getenv("GIGACHAT_MODEL_NAME", "GigaChat-3-Ultra"),
        "GigaChat-Pro",
        "GigaChat",
        "GigaChat-Plus",
        "GigaChat-Lite"
    ]
    models_to_try = list(dict.fromkeys(models_to_try))

    for model in models_to_try:
        payload = {
            "model": model,
            "messages": messages,
            "n": 1,
            "temperature": temperature
        }
        _wait_rate_limit()
        try:
            logger.info("Calling corporate GigaChat HTTP API with model=%s", model)
            status, res_json, response_text = _post_json(api_url, headers, payload, timeout=120)
            if 200 <= status < 300:
                choices = res_json.get("choices", [])
                if choices and "message" in choices[0]:
                    msg = choices[0]["message"]
                    content = (msg.get("content") or "").strip()
                    if content:
                        logger.info("GigaChat HTTP response received: model=%s chars=%s", model, len(content))
                        return content
                elif "result" in res_json:
                    content = str(res_json["result"]).strip()
                    logger.info("GigaChat HTTP response received: model=%s chars=%s", model, len(content))
                    return content
            elif status == 403:
                logger.warning(f"[GigaChat Sber API] Model '{model}' returned 403 RBAC Access Denied. Trying next model...")
                continue
            else:
                logger.warning(f"[GigaChat Sber API] Model '{model}' HTTP {status}: {response_text[:200]}")
        except Exception as e:
            logger.warning(f"[GigaChat Sber API] Error with model '{model}': {e}")

    return ""


def call_gigachat_sdk(messages: List[Dict[str, str]]) -> str:
    """Вызов GigaChat через официальный Python SDK (gigachat), если установлен."""
    try:
        logger.info("Calling GigaChat SDK fallback")
        from gigachat import GigaChat
        creds = os.getenv("JPY_API_TOKEN") or os.getenv("GIGACHAT_CREDENTIALS") or os.getenv("GIGACHAT_TOKEN") or ""
        clean_creds = creds.replace("Bearer ", "").strip()
        with GigaChat(credentials=clean_creds, verify_ssl_certs=False, model=GIGACHAT_MODEL_NAME) as giga:
            formatted_msgs = [{"role": m.get("role", "user"), "content": m.get("content", "")} for m in messages]
            response = giga.chat({"messages": formatted_msgs})
            if response and response.choices:
                return (response.choices[0].message.content or "").strip()
    except Exception as exc:
        logger.debug("GigaChat SDK unavailable: %s", exc)
    return ""


def call_vllm_http(messages: List[Dict[str, str]]) -> str:
    """Fallback на локальный vLLM если поднят."""
    if os.getenv("APPEALS_DISABLE_VLLM", "").strip().casefold() in {"1", "true", "yes", "on"}:
        logger.info("vLLM fallback is disabled by APPEALS_DISABLE_VLLM")
        return ""
    try:
        logger.info("Calling local vLLM fallback: %s", VLLM_API_URL)
        payload = {
            "model": "Qwen3.6-27B",
            "messages": messages,
            "max_tokens": 2048,
            "temperature": 0.6,
        }
        status, response_json, _ = _post_json(VLLM_API_URL, {"Content-Type": "application/json"}, payload, timeout=10)
        if 200 <= status < 300:
            choices = response_json.get("choices", [])
            if choices and "message" in choices[0]:
                return (choices[0]["message"].get("content") or "").strip()
    except Exception as exc:
        logger.debug("Local vLLM fallback unavailable: %s", exc)
    return ""


def def_ask_gigachat(messages: List[Dict[str, str]]) -> str:
    """
    Главная универсальная точка вызова GigaChat.
    Приоритет:
    1. Прямой HTTP API GigaChat Сбера с ротацией моделей при 403 RBAC
    2. GigaChat SDK (gigachat)
    3. vLLM (localhost:8000)
    """
    # 1. Запрос к GigaChat Сбера (с автоподбором RBAC-моделей)
    res_sber = call_gigachat_sber_api(messages)
    if res_sber:
        return res_sber

    # 2. Запрос к GigaChat SDK
    res_sdk = call_gigachat_sdk(messages)
    if res_sdk:
        return res_sdk

    # 3. Запрос к vLLM
    res_vllm = call_vllm_http(messages)
    if res_vllm:
        return res_vllm

    logger.warning("All configured LLM backends returned an empty response")
    return ""
