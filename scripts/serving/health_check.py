"""scripts.serving.health_check - проверка готовности OpenAI-compatible HTTP API.

Используется bootstrap'ом для проверки, что sglang / ollama сервер поднялся
и обслуживает запросы. Эндпоинты ``/v1/models`` (OpenAI-стандарт) и ``/health``
(sglang и ollama возвращают 200 OK после загрузки модели).

Никаких зависимостей кроме ``requests`` / ``urllib`` — модуль импортируется
ДО ``requests`` может быть недоступен, поэтому используется ``urllib``.
"""

from __future__ import annotations

import json
import socket
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_TIMEOUT_SEC = 5.0
DEFAULT_INTERVAL_SEC = 1.0


@dataclass
class HealthResult:
    ok: bool
    detail: str
    models: list[str]

    def __bool__(self) -> bool:
        return self.ok


def _port_open(host: str, port: int, timeout_sec: float = 1.0) -> bool:
    """Быстрая TCP-проверка: порт слушается (без HTTP-запроса)."""
    try:
        with socket.create_connection((host, port), timeout=timeout_sec):
            return True
    except (OSError, socket.timeout):
        return False


def _http_get(url: str, headers: dict[str, str] | None = None, timeout_sec: float = 5.0) -> tuple[int, str]:
    """GET-запрос через urllib (без requests). Возвращает (status, body)."""
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
            data = resp.read().decode("utf-8", errors="replace")
            return int(resp.status), data
    except urllib.error.HTTPError as e:
        try:
            body = e.read().decode("utf-8", errors="replace")
        except Exception:
            body = str(e)
        return int(e.code), body
    except (urllib.error.URLError, OSError, TimeoutError) as e:
        return 0, str(e)


def fetch_models(api_base: str, api_key: str = "", timeout_sec: float = 5.0) -> HealthResult:
    """GET {api_base}/models. Возвращает HealthResult с models=[] при ошибке.

    OpenAI-совместимый формат ответа:
        {"object": "list", "data": [{"id": "<model>", ...}, ...]}
    """
    url = api_base.rstrip("/") + "/models"
    headers: dict[str, str] = {}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    status, body = _http_get(url, headers=headers, timeout_sec=timeout_sec)
    if status != 200:
        return HealthResult(False, f"GET /models -> HTTP {status}: {body[:200]}", [])
    try:
        doc = json.loads(body)
        data = doc.get("data") if isinstance(doc, dict) else None
        models = [str(m.get("id", "")) for m in (data or []) if isinstance(m, dict) and m.get("id")]
    except (ValueError, AttributeError) as exc:
        return HealthResult(False, f"/models body parse: {exc}", [])
    return HealthResult(True, "ok", models)


def fetch_health(api_base: str, timeout_sec: float = 5.0) -> HealthResult:
    """GET {api_base}/health (или ../health для base, заканчивающихся на /v1)."""
    base = api_base.rstrip("/")
    for suffix in ("/health", "/v1/health"):
        url = base + suffix if suffix == "/health" else base.rstrip("/v1") + "/health"
        status, body = _http_get(url, timeout_sec=timeout_sec)
        if status == 200:
            return HealthResult(True, body[:200], [])
    return HealthResult(False, "no /health endpoint", [])


def wait_until_ready(
    api_base: str,
    api_key: str = "",
    expected_model: str = "",
    timeout_sec: float = 180.0,
    interval_sec: float = 2.0,
    pretty: bool = True,
) -> HealthResult:
    """Ждать готовности OpenAI-compatible сервера.

    Args:
        api_base: базовый URL API (например, ``http://localhost:30000/v1``).
        api_key: bearer-токен (если требуется).
        expected_model: имя модели, которое должно появиться в /v1/models.
        timeout_sec: максимальное время ожидания.
        interval_sec: пауза между попытками.
        pretty: выводить точки прогресса в stdout.

    Returns:
        HealthResult. ``ok=True`` если /models вернул 200 и (опц.) expected_model в списке.
    """
    from urllib.parse import urlparse

    parsed = urlparse(api_base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)

    deadline = time.monotonic() + timeout_sec
    attempt = 0
    last_detail = ""
    while time.monotonic() < deadline:
        attempt += 1
        if _port_open(host, port, timeout_sec=0.5):
            result = fetch_models(api_base, api_key=api_key, timeout_sec=3.0)
            last_detail = result.detail
            if result.ok:
                if expected_model and expected_model not in result.models:
                    if pretty:
                        print(f"[serving] /models ok, но {expected_model!r} нет в {result.models}")
                else:
                    if pretty:
                        print(f"[serving] ready in {attempt} attempt(s); models={result.models}")
                    return result
        else:
            last_detail = f"port {host}:{port} not open"
        if pretty:
            print(".", end="", flush=True)
        time.sleep(interval_sec)

    if pretty:
        print(f"\n[serving] timeout after {timeout_sec:.0f}s; last status: {last_detail}")
    return HealthResult(False, f"timeout: {last_detail}", [])


def probe(api_base: str, api_key: str = "") -> dict[str, Any]:
    """Одноразовая проверка и печать статуса (для CLI)."""
    from urllib.parse import urlparse

    parsed = urlparse(api_base)
    host = parsed.hostname or "127.0.0.1"
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    out: dict[str, Any] = {
        "api_base": api_base,
        "host": host,
        "port": port,
        "port_open": _port_open(host, port),
        "models": None,
        "ok": False,
    }
    if out["port_open"]:
        res = fetch_models(api_base, api_key=api_key, timeout_sec=3.0)
        out["models"] = res.models
        out["ok"] = res.ok
        out["detail"] = res.detail
    return out
