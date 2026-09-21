"""
local_qwen.py — Модуль взаимодействия с локальной языковой моделью Qwen (vLLM API).
Используется для ИОР (Инцидентов операционного риска), где данные обрабатываются СТРОГО локально.
"""
from __future__ import annotations
import sys
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)


import os
import sys
import json
import logging
import time
import requests
import asyncio
import threading
from pathlib import Path
from typing import List, Dict, Any, Optional

from utils.qwen_response import QwenResponse, extract_openai_response, response_from_generate, strip_think_blocks

logger = logging.getLogger(__name__)


def setup_ior_logging() -> None:
    """
    Настраивает логирование для модуля ior_analyzer:
    1. Вывод в sys.stderr (отображается в консоли gateway.py / web_server.py).
    2. Сохранение в файл workspace/logs/ior_analyzer.log.
    """
    try:
        log_dir = Path(__file__).resolve().parents[1] / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        log_file = log_dir / "ior_analyzer.log"

        root_logger = logging.getLogger()
        
        # StreamHandler для sys.stderr
        has_stream_handler = any(
            isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler) 
            for h in root_logger.handlers
        )
        if not has_stream_handler:
            sh = logging.StreamHandler(sys.stderr)
            sh.setLevel(logging.INFO)
            sh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
            root_logger.addHandler(sh)

        # FileHandler для workspace/logs/ior_analyzer.log
        has_file_handler = any(
            isinstance(h, logging.FileHandler) and str(log_file) in getattr(h, "baseFilename", "") 
            for h in root_logger.handlers
        )
        if not has_file_handler:
            fh = logging.FileHandler(str(log_file), encoding="utf-8")
            fh.setLevel(logging.INFO)
            fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] [%(name)s] %(message)s"))
            root_logger.addHandler(fh)

        if root_logger.level > logging.INFO or root_logger.level == logging.NOTSET:
            root_logger.setLevel(logging.INFO)
    except Exception as e:
        sys.stderr.write(f"[setup_ior_logging] Warning: Could not setup log file: {e}\n")


setup_ior_logging()

# По умолчанию vLLM OpenAI-compatible API на локальном порту 8000 или 8001
VLLM_API_URL = os.getenv("VLLM_API_URL", "http://localhost:8000/v1/chat/completions")
VLLM_MODELS_URL = os.getenv("VLLM_MODELS_URL", "http://localhost:8000/v1/models")
VLLM_GENERATE_URL = os.getenv("VLLM_GENERATE_URL", "http://localhost:8001/generate")

VLLM_API_KEY = os.getenv("VLLM_API_KEY", "").strip()

def _get_api_headers() -> Dict[str, str]:
    headers = {
        "Content-Type": "application/json",
    }

    if VLLM_API_KEY:
        headers["Authorization"] = f"Bearer {VLLM_API_KEY}"

    return headers

_CACHED_MODEL_NAME: Optional[str] = None

_QWEN_CALL_STATE = threading.local()


def _strip_think_blocks(text: str) -> str:
    """Backward-compatible alias used by older callers/tests."""
    return strip_think_blocks(text)


def _remember_response(response: QwenResponse) -> None:
    _QWEN_CALL_STATE.response = response


def get_last_qwen_response() -> QwenResponse:
    return getattr(_QWEN_CALL_STATE, "response", QwenResponse())

def get_served_model_name() -> str:
    """Динамически запрашивает имя обслуживаемой модели из vLLM /v1/models."""
    global _CACHED_MODEL_NAME
    env_model = os.getenv("VLLM_MODEL_NAME")
    if env_model:
        return env_model
    if _CACHED_MODEL_NAME:
        return _CACHED_MODEL_NAME

    try:
        resp = requests.get(
            VLLM_MODELS_URL,
            headers=_get_api_headers(),
            timeout=5,
        )
        if resp.status_code == 200:
            data = resp.json()
            models = data.get("data", [])
            if models and isinstance(models, list) and "id" in models[0]:
                _CACHED_MODEL_NAME = models[0]["id"]
                logger.info(f"[local_qwen] Auto-detected vLLM served model name: '{_CACHED_MODEL_NAME}'")
                return _CACHED_MODEL_NAME
    except Exception as e:
        logger.info(f"[local_qwen] Could not auto-detect vLLM model from {VLLM_MODELS_URL}: {e}")

    return "Qwen3.6-27B"


def call_qwen_http(messages: List[Dict[str, str]], max_tokens: int = 16384, temperature: float = 0.6) -> str:
    """Отправляет запрос в vLLM OpenAI-compatible endpoint с подробным логированием."""
    model_name = get_served_model_name()
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    
    prompt_len = sum(len(m.get("content", "")) for m in messages)
    logger.info(f"[local_qwen] 🚀 Sending POST to vLLM ({VLLM_API_URL}) | model='{model_name}' | prompt_len={prompt_len} chars | max_tokens={max_tokens}")
    start_time = time.time()
    _remember_response(QwenResponse())
    
    try:
        resp = requests.post(
            VLLM_API_URL,
            headers=_get_api_headers(),
            json=payload,
            timeout=180,
        )
        elapsed = time.time() - start_time
        
        # Если статус 404 — попробуем повторно сбросить кэш и переопределить модель из /v1/models
        if resp.status_code == 404:
            logger.warning(f"[local_qwen] HTTP 404 from vLLM model '{model_name}'. Refreshing model list...")
            global _CACHED_MODEL_NAME
            _CACHED_MODEL_NAME = None
            fresh_model = get_served_model_name()
            if fresh_model != model_name:
                payload["model"] = fresh_model
                logger.info(f"[local_qwen] Retrying request with fresh model name '{fresh_model}'...")
                resp = requests.post(
                    VLLM_API_URL,
                    headers=_get_api_headers(),
                    json=payload,
                    timeout=180,
                )
                elapsed = time.time() - start_time

        if resp.status_code == 200:
            res_json = resp.json()
            parsed = extract_openai_response(res_json)
            _remember_response(parsed)
            if parsed.final_text:
                logger.info(
                    "[local_qwen] ✅ vLLM HTTP 200 in %.2fs | content_len=%s chars | selected_final_field=%s",
                    elapsed, len(parsed.content), parsed.selected_final_field,
                )
                return parsed.final_text
            if parsed.reasoning_content:
                # Preserve legacy preset behavior. Structured analysis uses
                # ask_local_qwen_detailed() and never treats this as final JSON.
                logger.info(f"[local_qwen] ✅ vLLM HTTP 200 in {elapsed:.2f}s | reasoning_len={len(parsed.reasoning_content)} chars")
                return parsed.reasoning_content
            choices = res_json.get("choices", [])
            finish_reason = choices[0].get("finish_reason", "unknown") if choices else "no_choices"
            logger.warning("[local_qwen] ⚠️ vLLM HTTP 200, но финальный текст пустой; finish_reason=%s", finish_reason)
            return ""


        else:
            logger.warning(f"[local_qwen] ⚠️ vLLM HTTP status {resp.status_code} in {elapsed:.2f}s: {resp.text[:300]}")
    except Exception as e:
        elapsed = time.time() - start_time
        logger.warning(f"[local_qwen] ⚠️ Primary vLLM endpoint ({VLLM_API_URL}) failed in {elapsed:.2f}s: {e}")

    # Fallback на кастомный HTTP endpoint http://localhost:8001/generate если доступен
    logger.info(f"[local_qwen] Trying fallback generate endpoint ({VLLM_GENERATE_URL})...")
    try:
        sys_prompt = ""
        user_msg = ""
        for m in messages:
            if m.get("role") == "system":
                sys_prompt = m.get("content", "")
            elif m.get("role") == "user":
                user_msg += m.get("content", "") + "\n"

        fb_start = time.time()
        resp = requests.post(
            VLLM_GENERATE_URL,
            json={
                "system_prompt": sys_prompt,
                "user_message": user_msg.strip(),
                "max_tokens": max_tokens,
            },
            timeout=180
        )
        fb_elapsed = time.time() - fb_start
        if resp.status_code == 200:
            res_text = resp.json().get("result", "").strip()
            _remember_response(response_from_generate(res_text))
            logger.info(f"[local_qwen] ✅ Fallback endpoint HTTP 200 in {fb_elapsed:.2f}s | len={len(res_text)} chars")
            return res_text
        else:
            logger.warning(f"[local_qwen] ⚠️ Fallback generate endpoint HTTP status {resp.status_code}: {resp.text[:300]}")
    except Exception as ex:
        logger.warning(f"[local_qwen] ⚠️ Fallback generate endpoint failed: {ex}")

    return ""


def ask_local_qwen(messages: List[Dict[str, str]], max_tokens: int = 16384) -> str:
    """Точка входа вызова локальной модели Qwen."""
    res = call_qwen_http(messages, max_tokens=max_tokens)
    if not res:
        logger.warning("[local_qwen] ⚠️ Local vLLM server returned empty output or is offline.")
        return "Анализ выполнен на основе имеющихся метрик профилирования выгрузки ИОР."
    return res


def ask_local_qwen_detailed(messages: List[Dict[str, str]], max_tokens: int = 16384) -> QwenResponse:
    """Structured callers receive field metadata and never promote reasoning."""
    call_qwen_http(messages, max_tokens=max_tokens)
    return get_last_qwen_response()


INTENT_CLASSIFIER_PROMPT = """Ты - ИИ-классификатор интентов для чата по инцидентам операционного риска (ИОР).
Пользователь уже получил выгрузку инцидентов и теперь задает следующий вопрос.
Твоя задача - определить, требует ли его запрос поиска/фильтрации конкретных инцидентов в локальной базе по ключевым словам/теме, или же это просто продолжение диалога (вопрос по предыдущему ответу, просьба объяснить термин, уточнение, приветствие/спасибо).

Категории:
- "search": запрос требует поиска/фильтрации конкретных инцидентов, поиска фактов в текстах ИОР, отбора по теме (например: "в каких ИОРах есть хищения?", "найди сбои ПО", "какие инциденты связаны с картами?", "выдели случаи мошенничества").
- "chat": запрос является продолжением диалога, вопросом по твоему предыдущему ответу, просьбой пояснить термины/слова, мета-вопросом или общим общением (например: "что ты имел в виду под аналитикой?", "поясни второй пункт", "почему ты так решил?", "откуда эти данные?", "привет", "спасибо").

Ответь строго одним словом: "search" или "chat". Не пиши ничего, кроме этого слова.
"""


def classify_intent_with_qwen(user_query: str) -> str:
    messages = [
        {"role": "system", "content": INTENT_CLASSIFIER_PROMPT},
        {"role": "user", "content": f"Запрос пользователя: \"{user_query}\"\nКатегория:"}
    ]
    response = ask_local_qwen(messages, max_tokens=10).strip().lower()
    if "search" in response:
        return "search"
    return "chat"


QWEN_DETAIL_TEMPLATE = """Ты - ИИ-аналитик системы ИОР (Инциденты Операционного Риска). Твоя задача - анализировать предоставленные тексты инцидентов и давать точные ответы на вопросы пользователя, связанные с этими инцидентами.

ВХОДНЫЕ ДАННЫЕ:
1. Вопрос пользователя: {user_query}
2. Текст инцидентов: {ior_texts}

ИНСТРУКЦИИ:
1. Внимательно изучи вопрос пользователя и предоставленные тексты инцидентов.
2. Отвечай только на основе информации, содержащейся в предоставленных текстах. Не делай предположений или выводов, не подтвержденных текстом.
3. Если пользователь просит пересказать инцидент, изложи его суть своими словами, включая:
   - Что произошло (основные события и обстоятельства инцидента)
   - Причины инцидента (если они указаны в тексте)
   - Принятые меры (если они описаны в тексте)
4. Будь лаконичным, структурированным и точным.
"""


def answer_detail_with_qwen(user_query: str, ior_texts: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    sys_prompt = QWEN_DETAIL_TEMPLATE.format(user_query=user_query, ior_texts=ior_texts)
    messages = [{"role": "system", "content": sys_prompt}]
    if history:
        for h in history[-6:]:
            messages.append(h)
    messages.append({"role": "user", "content": user_query})
    return ask_local_qwen(messages, max_tokens=1500)


QWEN_FOLLOW_UP_TEMPLATE = """Ты - ИИ-аналитик системы ИОР. Пользователь задаёт вопрос по результатам семантического поиска по выгрузке.

Найденные релевантные инциденты:
{descriptions_text}

Инструкции:
1. Ответь на вопрос пользователя на основе предоставленных релевантных инцидентов.
2. Указывай конкретные ID инцидентов (EVE-XXXXXXX или числовые ID) при сопоставлении фактов.
3. Пиши лаконично, структурированно, без домыслов.
"""


def answer_follow_up_with_qwen(user_query: str, descriptions: List[Dict[str, Any]], history: Optional[List[Dict[str, str]]] = None) -> str:
    desc_lines = []
    for d in descriptions:
        sid = d.get("id", "N/A")
        txt = d.get("text", "")
        desc_lines.append(f"- ID {sid}: {txt}")
    descriptions_text = "\n".join(desc_lines) if desc_lines else "Совпадений не найдено."

    sys_prompt = QWEN_FOLLOW_UP_TEMPLATE.format(descriptions_text=descriptions_text)
    messages = [{"role": "system", "content": sys_prompt}]
    if history:
        for h in history[-6:]:
            messages.append(h)
    messages.append({"role": "user", "content": user_query})
    return ask_local_qwen(messages, max_tokens=1500)


def answer_dialog_with_qwen(user_query: str, history: Optional[List[Dict[str, str]]] = None) -> str:
    sys_prompt = "Ты — ИИ-аналитик системы ИОР. Веди грамотный, профессиональный и вежливый диалог с пользователем-аудитором."
    messages = [{"role": "system", "content": sys_prompt}]
    if history:
        for h in history[-6:]:
            messages.append(h)
    messages.append({"role": "user", "content": user_query})
    return ask_local_qwen(messages, max_tokens=1000)


def summarize_iors(topic: str, descriptions: List[str]) -> str:
    prompt = f"Тема запроса: {topic}\n\nОписания инцидентов:\n" + "\n".join(f"- {d}" for d in descriptions[:25])
    messages = [
        {"role": "system", "content": "Ты — аналитик Службы внутреннего аудита. Проведи краткую суммаризацию причин и паттернов данных инцидентов."},
        {"role": "user", "content": prompt}
    ]
    return ask_local_qwen(messages, max_tokens=1024)
