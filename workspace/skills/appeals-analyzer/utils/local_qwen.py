"""
local_qwen.py — Модуль взаимодействия с GigaChat в корпоративной сети Сбера с автоподбором разрешенной RBAC-модели.
"""
from __future__ import annotations

import os
import sys
import json
import logging
import time
from contextvars import ContextVar
try:
    import requests
except ImportError:  # closed contour may expose only the Python stdlib
    requests = None
import ssl
from urllib import request as urllib_request
from urllib.error import HTTPError
from pathlib import Path
from typing import List, Dict, Any, Optional

# Сквозной переключатель рассуждений. Канонический источник —
# lib/services/llm_reasoning.py; переменная окружения одна и та же.
# Импорт защищённый: закрытый контур этого навыка по контракту умеет только
# stdlib (см. ветку ImportError у requests выше), и падать из-за отсутствия
# модуля ему нельзя. Запасной вариант читает ту же переменную с тем же
# значением по умолчанию.
_PROJECT_ROOT = Path(__file__).resolve().parents[4]
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
try:
    from lib.services.llm_reasoning import strip_think_blocks
except ImportError:  # закрытый контур: правила повторяем локально
    import re as _re

    _THINK_BLOCKS = _re.compile(
        r"<think>.*?</think>"
        r"|\A\s*</think>.*?(?=\n\s*\n|\Z)"
        r"|```xml-think\s*\n.*?```",
        _re.DOTALL | _re.IGNORECASE,
    )

    def strip_think_blocks(text: str) -> str:
        if not text:
            return ""
        return _THINK_BLOCKS.sub("", text).strip()

logger = logging.getLogger(__name__)
_LLM_STATUS = ContextVar("appeals_llm_status", default=(None, None))


def last_llm_status():
    """Diagnostics only; never changes provider selection or HTTP payloads."""
    return _LLM_STATUS.get()

# Точные настройки подключения GigaChat из ior_assistant
GIGACHAT_API_URL = os.getenv("GIGACHAT_API_URL", "http://liveaccess/v1/gc/chat/completions")
JPY_API_TOKEN = os.getenv("JPY_API_TOKEN") or os.getenv("GIGACHAT_CREDENTIALS") or os.getenv("GIGACHAT_TOKEN") or ""
GIGACHAT_MODEL_NAME = os.getenv("GIGACHAT_MODEL_NAME", "GigaChat-3-Ultra")

# Адрес основной модели НЕ зашит: см. _agent_endpoint() — он читается из
# agents.defaults + providers.* (config.json поверх .secrets.env).

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


_AGENT_ENDPOINT: Optional[Dict[str, Any]] = None


def _agent_endpoint() -> Dict[str, Any]:
    """Модель агента из конфигурации: provider/api_base/api_key/model/extra_body.

    Навык НЕ зашит ни на GigaChat, ни на vLLM, ни на localhost: адрес, ключ,
    модель и поле управления рассуждениями берутся из ``agents.defaults`` и
    ``providers.*`` (config.json поверх .secrets.env). Переключение
    MiniMax -> локальный vLLM делается правкой одного файла конфигурации.
    """
    global _AGENT_ENDPOINT
    if _AGENT_ENDPOINT is not None:
        return _AGENT_ENDPOINT

    from lib.services.llm_config import agent_endpoint

    ep = agent_endpoint()
    api_base = ep["api_base"]
    _AGENT_ENDPOINT = {
        "provider": ep["provider"],
        "chat_url": f"{api_base}/chat/completions",
        "api_key": ep.get("api_key") or "",
        "model": ep["model"],
        "extra_body": dict(ep["extra_body"]),
    }
    logger.info("Эндпоинт: provider=%s url=%s model=%s extra_body=%s",
                _AGENT_ENDPOINT["provider"], _AGENT_ENDPOINT["chat_url"],
                _AGENT_ENDPOINT["model"],
                json.dumps(_AGENT_ENDPOINT["extra_body"], ensure_ascii=False))
    return _AGENT_ENDPOINT


def call_agent_llm(messages: List[Dict[str, str]],
                   max_tokens: int = 8192,
                   temperature: float = 0.6) -> str:
    """Основной путь: модель агента через OpenAI-совместимый эндпоинт."""
    # Выключатель стоит здесь, а не на точке входа: сетевых входов два, и
    # пока он жил только на историческом имени, основной путь уходил в сеть
    # при APPEALS_DISABLE_VLLM=1. Навык запускают standalone, где сеть может
    # не быть вовсе, и пайплайн должен уметь завершиться тихо.
    _LLM_STATUS.set((None, None))
    if os.getenv("APPEALS_DISABLE_VLLM", "").strip().casefold() in {"1", "true", "yes", "on"}:
        logger.info("Запрос к модели выключен APPEALS_DISABLE_VLLM")
        return ""
    endpoint = _agent_endpoint()
    _LLM_STATUS.set((endpoint["provider"], None))
    payload = {
        "model": endpoint["model"],
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    # Поле рассуждения выведено из имени провайдера: у minimax это
    # reasoning_split, у vLLM — chat_template_kwargs.enable_thinking.
    for field, value in endpoint["extra_body"].items():
        payload[field] = value

    headers = {"Content-Type": "application/json"}
    if endpoint["api_key"]:
        headers["Authorization"] = "Bearer %s" % endpoint["api_key"]

    logger.info("Calling %s: %s model=%s", endpoint["provider"],
                endpoint["chat_url"], endpoint["model"])
    status, response_json, body = _post_json(
        endpoint["chat_url"], headers, payload, timeout=120)
    _LLM_STATUS.set((endpoint["provider"], status))
    if not (200 <= status < 300):
        logger.warning("appeals_request llm_provider=%s llm_http_status=%s", endpoint["provider"], status)
        return ""
    choices = response_json.get("choices", [])
    if not choices or "message" not in choices[0]:
        logger.warning("[%s] empty choices", endpoint["provider"])
        return ""
    msg = choices[0]["message"]
    # Рассуждение может прийти и в content — отдаём только ответ.
    return (strip_think_blocks(msg.get("content"))
            or strip_think_blocks(msg.get("reasoning_content")))


def call_vllm_http(messages: List[Dict[str, str]]) -> str:
    """Историческая точка входа. Оставлена как алиас на общий путь.

    Имя выключателя прежнее — ``APPEALS_DISABLE_VLLM``: навык запускают
    standalone (вне нанобота), где готовые команды и скрипты задают его
    именно так. Сам выключатель проверяется в ``call_agent_llm``, поэтому
    оба входа ведут себя одинаково.
    """
    return call_agent_llm(messages)


def def_ask_gigachat(messages: List[Dict[str, str]]) -> str:
    """Главная точка вызова LLM.

    Приоритет:
    1. Модель агента из конфигурации (MiniMax на этом ПК, vLLM на ПРОМе).
    2. Корпоративный GigaChat — только если явно разрешён
       ``APPEALS_USE_GIGACHAT=1`` (корпоративный контур, недоступен локально).
    """
    res_agent = call_agent_llm(messages)
    if res_agent:
        return res_agent

    if os.getenv("APPEALS_USE_GIGACHAT", "").strip().casefold() not in {"1", "true", "yes", "on"}:
        logger.warning(
            "Модель агента не ответила; GigaChat пропущен (включается "
            "APPEALS_USE_GIGACHAT=1 в корпоративном контуре)")
        return ""

    logger.info("Основной эндпоинт не ответил, пробуем GigaChat")

    # Запрос к GigaChat Сбера (с автоподбором RBAC-моделей)
    _LLM_STATUS.set(("gigachat", None))
    res_sber = call_gigachat_sber_api(messages)
    if res_sber:
        return res_sber

    # 2. Запрос к GigaChat SDK
    _LLM_STATUS.set(("gigachat_sdk", None))
    res_sdk = call_gigachat_sdk(messages)
    if res_sdk:
        return res_sdk

    logger.warning("Ни один из настроенных LLM-бэкендов не вернул ответ")
    return ""
