"""Сменные LLM-провайдеры: MiniMax, безопасная заглушка и legacy GigaChat."""

from __future__ import annotations

import math
import os
import re
import time
from collections import deque
from typing import Any, Protocol
from urllib.parse import urlparse

from .config import Settings
from .errors import ProviderUnavailableError
from .models import LLMRequest, LLMResponse


class LLMProvider(Protocol):
    """Минимальный интерфейс, необходимый skills."""

    @property
    def name(self) -> str: ...

    def complete(self, request: LLMRequest) -> LLMResponse: ...


class StubProvider:
    """Заглушка без сети и без неявных ответов."""

    name = "stub"

    def complete(self, request: LLMRequest) -> LLMResponse:
        del request
        raise ProviderUnavailableError(
            "LLM-провайдер работает в режиме заглушки. "
            "Для реального вызова установите requirements.txt, задайте "
            "MINIMAX_API_KEY, LLM_WIKI_PROVIDER=minimax и явно разрешите передачу выбранного "
            "контекста: LLM_WIKI_ALLOW_EXTERNAL_CONTEXT=true."
        )


class FakeProvider:
    """Детерминированный провайдер для тестов без сети."""

    name = "fake"

    def __init__(self, responses: list[str]) -> None:
        self._responses = deque(responses)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if not self._responses:
            raise ProviderUnavailableError("FakeProvider: ответы закончились")
        return LLMResponse(content=self._responses.popleft())


class LangChainGigaChatProvider:
    """Ленивый адаптер официального пакета langchain-gigachat."""

    name = "gigachat"

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.allow_external_context:
            raise ProviderUnavailableError(
                "Передача Wiki во внешний LLM отключена. После согласования "
                "политики данных задайте "
                "LLM_WIKI_ALLOW_EXTERNAL_CONTEXT=true."
            )
        if not settings.credentials_configured:
            raise ProviderUnavailableError(
                "Не задан GIGACHAT_CREDENTIALS или GIGACHAT_ACCESS_TOKEN."
            )
        if not settings.python_supported_by_agent_stack:
            raise ProviderUnavailableError(
                "Полный стек агента требует Python 3.10–3.13 "
                "(SDK: 3.8–3.13, langchain-gigachat: 3.10+). Создайте "
                "Python 3.12 и зависимости requirements.txt."
            )

        try:
            from langchain_core.messages import HumanMessage, SystemMessage
            from langchain_gigachat import GigaChat
        except ImportError as exc:
            raise ProviderUnavailableError(
                "Не установлен langchain-gigachat. Установите зависимости из "
                "requirements.txt для Python 3.12."
            ) from exc

        kwargs: dict[str, object] = {
            "model": settings.model,
            "verify_ssl_certs": settings.verify_ssl_certs,
        }
        if settings.ca_bundle_file is not None:
            kwargs["ca_bundle_file"] = str(settings.ca_bundle_file)
        scope = os.getenv("GIGACHAT_SCOPE", "").strip()
        if scope:
            if scope not in {
                "GIGACHAT_API_PERS",
                "GIGACHAT_API_B2B",
                "GIGACHAT_API_CORP",
            }:
                raise ProviderUnavailableError(
                    "GIGACHAT_SCOPE должен быть GIGACHAT_API_PERS, "
                    "GIGACHAT_API_B2B или GIGACHAT_API_CORP"
                )
            kwargs["scope"] = scope
        base_url = os.getenv("GIGACHAT_BASE_URL", "").strip()
        if base_url:
            kwargs["base_url"] = _validated_https_url(
                "GIGACHAT_BASE_URL", base_url
            )
        auth_url = os.getenv("GIGACHAT_AUTH_URL", "").strip()
        if auth_url:
            kwargs["auth_url"] = _validated_https_url(
                "GIGACHAT_AUTH_URL", auth_url
            )
        timeout = os.getenv("GIGACHAT_TIMEOUT", "").strip()
        if timeout:
            try:
                timeout_value = float(timeout)
            except ValueError as exc:
                raise ProviderUnavailableError(
                    "GIGACHAT_TIMEOUT должен быть числом"
                ) from exc
            if (
                not math.isfinite(timeout_value)
                or timeout_value <= 0
                or timeout_value > 300
            ):
                raise ProviderUnavailableError(
                    "GIGACHAT_TIMEOUT должен быть конечным числом "
                    "от 0 до 300 секунд"
                )
            kwargs["timeout"] = timeout_value

        self._human_message = HumanMessage
        self._system_message = SystemMessage
        try:
            self._client = GigaChat(**kwargs)
        except Exception as exc:
            raise ProviderUnavailableError(
                _sanitize_error(str(exc))
            ) from exc

    def complete(self, request: LLMRequest) -> LLMResponse:
        try:
            message = self._client.invoke(
                [
                    self._system_message(content=request.system_prompt),
                    self._human_message(content=request.user_prompt),
                ]
            )
        except Exception as exc:
            raise ProviderUnavailableError(
                _sanitize_error(str(exc))
            ) from exc
        content = getattr(message, "content", "")
        if isinstance(content, list):
            content = "\n".join(str(item) for item in content)
        if not isinstance(content, str) or not content.strip():
            raise ProviderUnavailableError("GigaChat вернул пустой ответ")
        return LLMResponse(content=content.strip())


class InternalGigaChatProvider:
    """Прямой адаптер внутреннего OpenAI-подобного GigaChat API."""

    name = "gigachat_internal"
    _RETRYABLE_STATUSES = {408, 425, 429, 502, 503, 504}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.allow_external_context:
            raise ProviderUnavailableError(
                "Передача выбранного контекста во внутренний LLM отключена. "
                "После согласования политики данных задайте "
                "LLM_WIKI_ALLOW_EXTERNAL_CONTEXT=true."
            )
        token = os.getenv("JPY_API_TOKEN", "").strip()
        if not token:
            raise ProviderUnavailableError("Не задан JPY_API_TOKEN.")
        try:
            import requests
        except ImportError as exc:
            raise ProviderUnavailableError(
                "Не установлен requests. Установите зависимости из "
                "requirements-closed-contour.txt."
            ) from exc

        self._requests = requests
        self._token = token
        api_url = os.getenv("GIGACHAT_API_URL", "").strip()
        if not api_url or api_url.casefold() == "none":
            raise ProviderUnavailableError(
                "Не задан GIGACHAT_API_URL. Подставьте внутренний endpoint "
                "в локальный .env."
            )
        self._url = _validated_internal_url("GIGACHAT_API_URL", api_url)
        self._timeout = _env_positive_float("GIGACHAT_TIMEOUT", 60.0)
        self._retry_delay_step = _env_positive_float(
            "GIGACHAT_RETRY_DELAY_STEP", 0.5
        )
        self._max_attempts = _env_nonnegative_int(
            "GIGACHAT_RETRY_MAX_ATTEMPTS", 20
        )

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": self.settings.model,
            "messages": [
                {
                    "role": "system",
                    "content": request.system_prompt,
                },
                {
                    "role": "user",
                    "content": request.user_prompt,
                },
            ],
            "n": 1,
            "temperature": 0.01,
        }
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Content-Type": "application/json",
        }
        attempt = 0
        while True:
            attempt += 1
            try:
                kwargs: dict[str, Any] = {
                    "headers": headers,
                    "json": payload,
                    "timeout": self._timeout,
                }
                if self._url.startswith("https://"):
                    kwargs["verify"] = (
                        str(self.settings.ca_bundle_file)
                        if self.settings.ca_bundle_file is not None
                        else True
                    )
                response = self._requests.post(self._url, **kwargs)
            except self._requests.exceptions.Timeout as exc:
                if self._retry_exhausted(attempt):
                    raise ProviderUnavailableError(
                        "Ошибка GigaChat API: превышено число повторов "
                        "после таймаута"
                    ) from exc
                time.sleep(self._retry_delay(attempt))
                continue
            except self._requests.exceptions.RequestException as exc:
                raise ProviderUnavailableError(
                    _sanitize_error(str(exc))
                ) from exc

            if response.status_code in self._RETRYABLE_STATUSES:
                if self._retry_exhausted(attempt):
                    raise ProviderUnavailableError(
                        "Ошибка GigaChat API: превышено число повторов; "
                        f"последний HTTP-статус {response.status_code}"
                    )
                time.sleep(self._retry_delay(attempt))
                continue
            if not response.ok:
                raise ProviderUnavailableError(
                    _internal_http_error(response.status_code, response.text)
                )
            try:
                content = response.json()["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError) as exc:
                raise ProviderUnavailableError(
                    "Ошибка GigaChat API: ответ не содержит "
                    "choices[0].message.content"
                ) from exc
            if not isinstance(content, str) or not content.strip():
                raise ProviderUnavailableError("GigaChat вернул пустой ответ")
            return LLMResponse(content=content.strip())

    def _retry_exhausted(self, attempt: int) -> bool:
        return self._max_attempts != 0 and attempt >= self._max_attempts

    def _retry_delay(self, attempt: int) -> float:
        return min(attempt * self._retry_delay_step, 30.0)


class MiniMaxProvider:
    """MiniMax Chat Completions; text only, no tools or uploaded files."""

    name = "minimax"
    _RETRYABLE_STATUSES = {408, 425, 429, 500, 502, 503, 504}

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        if not settings.allow_external_context:
            raise ProviderUnavailableError(
                "Передача выбранных текстов MiniMax отключена. "
                "Разрешите её явно: LLM_WIKI_ALLOW_EXTERNAL_CONTEXT=true."
            )
        self._token = os.getenv("MINIMAX_API_KEY", "").strip()
        if not self._token:
            raise ProviderUnavailableError("Не задан MINIMAX_API_KEY в .env или окружении.")
        base = os.getenv("MINIMAX_BASE_URL", "https://api.minimax.io/v1").strip().rstrip("/")
        try:
            parsed = urlparse(base)
            port = parsed.port
        except ValueError as exc:
            raise ProviderUnavailableError("MINIMAX_BASE_URL должен быть https://api.minimax.io/v1") from exc
        if (parsed.scheme != "https" or parsed.hostname != "api.minimax.io"
                or port not in {None, 443} or parsed.path != "/v1"
                or parsed.username or parsed.password or parsed.query or parsed.fragment):
            raise ProviderUnavailableError("MINIMAX_BASE_URL должен быть https://api.minimax.io/v1")
        self._url = base + "/chat/completions"
        self._timeout = _env_positive_float("MINIMAX_TIMEOUT", 60)
        self._max_attempts = _env_nonnegative_int("MINIMAX_MAX_ATTEMPTS", 3)
        if not 1 <= self._max_attempts <= 5:
            raise ProviderUnavailableError("MINIMAX_MAX_ATTEMPTS должен быть от 1 до 5")
        self._max_tokens = _env_nonnegative_int("MINIMAX_MAX_TOKENS", 8192)
        if not 256 <= self._max_tokens <= 65536:
            raise ProviderUnavailableError("MINIMAX_MAX_TOKENS должен быть от 256 до 65536")
        try:
            import requests
        except ImportError as exc:
            raise ProviderUnavailableError("Установите зависимости: python3.12 -m pip install -r requirements.txt") from exc
        self._requests = requests

    def complete(self, request: LLMRequest) -> LLMResponse:
        payload = {
            "model": self.settings.model,
            "messages": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.user_prompt},
            ],
            "stream": False,
            "temperature": 1.0,
            "max_tokens": self._max_tokens,
        }
        for attempt in range(1, self._max_attempts + 1):
            try:
                response = self._requests.post(
                    self._url,
                    headers={"Authorization": f"Bearer {self._token}", "Content-Type": "application/json"},
                    json=payload,
                    timeout=(min(10, self._timeout), self._timeout),
                    verify=str(self.settings.ca_bundle_file) if self.settings.ca_bundle_file else True,
                    allow_redirects=False,
                )
            except (self._requests.exceptions.Timeout, self._requests.exceptions.ConnectionError) as exc:
                if attempt == self._max_attempts:
                    raise ProviderUnavailableError("MiniMax: таймаут или ошибка подключения; повторы исчерпаны.") from exc
                time.sleep(min(attempt * 0.5, 2))
                continue
            except self._requests.exceptions.RequestException as exc:
                raise ProviderUnavailableError("MiniMax: ошибка HTTP-клиента.") from exc
            if response.status_code in self._RETRYABLE_STATUSES and attempt < self._max_attempts:
                time.sleep(min(attempt * 0.5, 2))
                continue
            if not response.ok:
                labels = {400: "некорректный запрос, модель или лимит контекста",
                          401: "неверный или истёкший API-ключ", 403: "нет доступа к модели/тарифу",
                          429: "превышен лимит запросов/тарифа"}
                # Не печатаем ответ сервера: он может содержать ключ или исходный контекст.
                raise ProviderUnavailableError(
                    f"MiniMax HTTP {response.status_code}: {labels.get(response.status_code, 'ошибка API')}."
                )
            try:
                data = response.json()
                base_resp = data.get("base_resp", {})
                if base_resp.get("status_code", 0) != 0:
                    code = base_resp["status_code"]
                    safe_code = str(code) if isinstance(code, int) else "неизвестен"
                    raise ProviderUnavailableError(f"MiniMax: ошибка API, код {safe_code}.")
                choice = data["choices"][0]
                if choice.get("finish_reason") == "length":
                    raise ProviderUnavailableError("MiniMax: ответ обрезан; увеличьте MINIMAX_MAX_TOKENS или уменьшите контекст.")
                content = choice["message"]["content"]
            except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
                raise ProviderUnavailableError("MiniMax: некорректная структура ответа API.") from exc
            if not isinstance(content, str):
                raise ProviderUnavailableError("MiniMax: ответ не является текстом.")
            # M2.x может возвращать reasoning внутри content; контроллеру нужен только итог.
            content = re.sub(r"<think>.*?</think>", "", content, flags=re.S).strip()
            if "<think>" in content or not content:
                raise ProviderUnavailableError("MiniMax: итоговый ответ отсутствует или reasoning обрезан.")
            return LLMResponse(content=content)
        raise ProviderUnavailableError("MiniMax: повторы исчерпаны.")


def provider_from_settings(settings: Settings) -> LLMProvider:
    if settings.provider == "minimax":
        return MiniMaxProvider(settings)
    if settings.provider == "stub":
        return StubProvider()
    if settings.provider == "gigachat":
        return LangChainGigaChatProvider(settings)
    if settings.provider == "gigachat_internal":
        return InternalGigaChatProvider(settings)
    raise ProviderUnavailableError(
        f"Неизвестный LLM-провайдер: {settings.provider}"
    )


def _sanitize_error(message: str) -> str:
    """Не допустить попадания ключа в stderr."""

    sanitized = message
    for name in (
        "GIGACHAT_CREDENTIALS",
        "GIGACHAT_ACCESS_TOKEN",
        "JPY_API_TOKEN",
        "MINIMAX_API_KEY",
    ):
        secret = os.getenv(name, "")
        if secret:
            sanitized = sanitized.replace(secret, "<redacted>")
    return f"Ошибка GigaChat API: {sanitized}"


def _validated_https_url(name: str, value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "https"
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ProviderUnavailableError(
            f"{name} должен быть HTTPS URL без логина и пароля"
        )
    return value


def _validated_internal_url(name: str, value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.netloc
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ProviderUnavailableError(
            f"{name} должен быть HTTP(S) URL без логина, пароля, "
            "query и fragment"
        )
    return value


def _env_positive_float(name: str, default: float) -> float:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ProviderUnavailableError(f"{name} должен быть числом") from exc
    if not math.isfinite(value) or value <= 0 or value > 300:
        raise ProviderUnavailableError(
            f"{name} должен быть конечным числом от 0 до 300"
        )
    return value


def _env_nonnegative_int(name: str, default: int) -> int:
    raw = os.getenv(name, "").strip()
    if not raw:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ProviderUnavailableError(
            f"{name} должен быть целым числом"
        ) from exc
    if value < 0 or value > 10_000:
        raise ProviderUnavailableError(
            f"{name} должен быть от 0 до 10000"
        )
    return value


def _internal_http_error(status_code: int, message: str) -> str:
    labels = {
        400: "ошибка в параметрах запроса",
        401: "токен истёк или не предоставлен",
        403: "доступ запрещён",
        404: "endpoint или модель не найдены",
        405: "метод запроса не поддерживается",
        413: "превышен максимальный размер входных данных",
        500: "внутренняя ошибка сервера",
    }
    label = labels.get(status_code, "непредвиденная ошибка")
    detail = _sanitize_error(message).removeprefix("Ошибка GigaChat API: ")
    return (
        f"Ошибка GigaChat API: {label} "
        f"(HTTP {status_code}): {detail}"
    )
