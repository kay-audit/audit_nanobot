"""scripts.serving.config - схема секции ``serving`` в config.json.

Конфиг живёт рядом с основным ``config.json`` в секции::

    {
      "serving": {
        "mode": "sglang" | "ollama" | "external" | "off",
        "provider_alias": "vllm",
        "model_name": "Qwen3-30B-A3B",
        "api_base": "http://localhost:30000/v1",
        "api_key": "EMPTY",
        "sglang": { ... },
        "ollama": { ... }
      }
    }

Если секции ``serving`` нет — bootstrap возвращает ``off`` и Nanobot стартует
штатно (используется для дев-сценариев без локального LLM).

Все параметры можно переопределить через env vars:
    SGLANG__MODE, SGLANG__PROVIDER_ALIAS, SGLANG__MODEL_NAME,
    SGLANG__API_BASE, SGLANG__API_KEY, SGLANG__SGLANG__*, SGLANG__OLLAMA__*
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

CONFIG_FILE = Path(__file__).resolve().parents[2] / "config.json"

ServingMode = Literal["off", "external", "sglang", "ollama"]
SglangBackend = Literal["auto", "transformers", "llama_cpp"]
OllamaSource = Literal["external", "spawn"]


@dataclass
class SglangSettings:
    """Параметры запуска sglang-сервера (mode=sglang).

    Значения по умолчанию рассчитаны на Qwen3-30B-A3B-Instruct на 1×A100-80GB
    с torch 2.5.1+cu124 (CUDA 12.4, driver 550.90.07). ``torch_compile=False``
    отключает torchinductor — это критично для фиксированной версии torch.
    """

    host: str = "0.0.0.0"
    port: int = 30000
    model_path: str = "/data/models/Qwen3-30B-A3B-Instruct"
    served_model_name: str = "Qwen3-30B-A3B"
    gpu_ids: str = "0"
    gpu_memory_utilization: float = 0.9
    max_model_len: int = 32768
    dtype: str = "bfloat16"
    trust_remote_code: bool = True
    torch_compile: bool = False
    quantization: str | None = None
    backend: SglangBackend = "auto"
    extra_args: list[str] = field(default_factory=list)
    log_dir: str = "logs"
    pid_file: str = "logs/sglang.pid"
    api_key: str = "EMPTY"


@dataclass
class OllamaSettings:
    """Параметры для ollama (mode=ollama).

    Используется для локального теста на ноутбуке (Qwen3.5:9b через ollama).
    На удалённом сервере с A100 имеет смысл использовать только sglang.
    """

    source: OllamaSource = "spawn"
    executable: str = "ollama"
    host: str = "127.0.0.1"
    port: int = 11434
    model: str = "qwen3:9b"
    keep_alive: str = "5m"
    num_gpu: int | None = None
    extra_pull_args: list[str] = field(default_factory=list)
    extra_run_args: list[str] = field(default_factory=list)
    log_dir: str = "logs"
    pid_file: str = "logs/ollama.pid"
    api_key: str = "ollama"


@dataclass
class ServingSettings:
    """Корневая секция ``serving`` в config.json.

    При ``mode=off`` ничего не происходит (Nanobot стартует штатно).
    При ``mode=external`` — только health-check; пользователь сам поднял
    LLM-сервер (например, вручную через sglang в tmux на GPU-сервере).
    При ``mode=sglang`` / ``mode=ollama`` — bootstrap запускает сервер сам.
    """

    mode: ServingMode = "off"
    provider_alias: str = "vllm"
    model_name: str = ""
    api_base: str = ""
    api_key: str = ""
    sglang: SglangSettings = field(default_factory=SglangSettings)
    ollama: OllamaSettings = field(default_factory=OllamaSettings)
    health_check_timeout_sec: float = 180.0
    health_check_interval_sec: float = 2.0
    install_deps: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _coerce_mode(value: Any) -> ServingMode:
    """Нормализовать значение ``serving.mode`` в один из литералов.

    ``Literal[...]`` нельзя использовать с ``isinstance``, поэтому сравниваем
    строковое представление напрямую.
    """
    s = str(value or "").strip().lower()
    if s in ("off", "none", "no", "false", "0", "skip", ""):
        return "off"
    if s in ("sglang", "sgl", "sglang-runtime"):
        return "sglang"
    if s in ("ollama",):
        return "ollama"
    if s in ("external", "ext", "remote", "already-up"):
        return "external"
    raise ValueError(f"Unknown serving.mode: {value!r}")


def _load_from_env(prefix: str = "SGLANG__") -> dict[str, Any]:
    """Собрать плоский dict из SGLANG__* env vars (SGLANG__MODE=sglang -> {mode: sglang})."""
    out: dict[str, Any] = {}
    for k, v in os.environ.items():
        if not k.startswith(prefix):
            continue
        path = k[len(prefix):].lower().split("__")
        cur: Any = out
        for part in path[:-1]:
            cur = cur.setdefault(part, {})
            if not isinstance(cur, dict):
                cur = {}
        cur[path[-1]] = v
    return out


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Рекурсивный merge: значения из override перебивают base (для dict - рекурсивно)."""
    out = dict(base)
    for k, v in override.items():
        if k in out and isinstance(out[k], dict) and isinstance(v, dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


def _build_sglang(raw: dict[str, Any] | None) -> SglangSettings:
    raw = raw or {}
    defaults = asdict(SglangSettings())
    merged = _deep_merge(defaults, raw)
    return SglangSettings(
        host=str(merged.get("host", "0.0.0.0")),
        port=int(merged.get("port", 30000)),
        model_path=str(merged.get("model_path", "/data/models/Qwen3-30B-A3B-Instruct")),
        served_model_name=str(merged.get("served_model_name", merged.get("model_path", "Qwen3-30B-A3B"))),
        gpu_ids=str(merged.get("gpu_ids", "0")),
        gpu_memory_utilization=float(merged.get("gpu_memory_utilization", 0.9)),
        max_model_len=int(merged.get("max_model_len", 32768)),
        dtype=str(merged.get("dtype", "bfloat16")),
        trust_remote_code=bool(merged.get("trust_remote_code", True)),
        torch_compile=bool(merged.get("torch_compile", False)),
        quantization=merged.get("quantization") if merged.get("quantization") else None,
        backend=str(merged.get("backend", "auto")),
        extra_args=list(merged.get("extra_args", []) or []),
        log_dir=str(merged.get("log_dir", "logs")),
        pid_file=str(merged.get("pid_file", "logs/sglang.pid")),
        api_key=str(merged.get("api_key", "EMPTY")),
    )


def _build_ollama(raw: dict[str, Any] | None) -> OllamaSettings:
    raw = raw or {}
    defaults = asdict(OllamaSettings())
    merged = _deep_merge(defaults, raw)
    num_gpu = merged.get("num_gpu")
    return OllamaSettings(
        source=str(merged.get("source", "spawn")),
        executable=str(merged.get("executable", "ollama")),
        host=str(merged.get("host", "127.0.0.1")),
        port=int(merged.get("port", 11434)),
        model=str(merged.get("model", "qwen3:9b")),
        keep_alive=str(merged.get("keep_alive", "5m")),
        num_gpu=int(num_gpu) if num_gpu is not None else None,
        extra_pull_args=list(merged.get("extra_pull_args", []) or []),
        extra_run_args=list(merged.get("extra_run_args", []) or []),
        log_dir=str(merged.get("log_dir", "logs")),
        pid_file=str(merged.get("pid_file", "logs/ollama.pid")),
        api_key=str(merged.get("api_key", "ollama")),
    )


def load_serving_config(config_path: Path | None = None) -> ServingSettings:
    """Прочитать секцию ``serving`` из config.json, переопределить через env.

    Порядок приоритетов (от низкого к высокому):
        1) SglangSettings / OllamaSettings defaults
        2) секция ``serving`` в config.json (если есть)
        3) env vars ``SGLANG__*`` (SGLANG__SGLANG__PORT=30001 -> serving.sglang.port)

    Секция ``serving`` в config.json опциональна. Если её нет — возвращаются
    defaults (``mode=off``); ``ensure_serving`` в этом случае ничего не делает.
    """
    path = Path(config_path) if config_path else CONFIG_FILE
    raw_section: dict[str, Any] = {}

    if path.exists():
        try:
            text = path.read_text(encoding="utf-8")
            doc = json.loads(text)
            section = doc.get("serving") if isinstance(doc, dict) else None
            if isinstance(section, dict):
                raw_section = dict(section)
        except (json.JSONDecodeError, OSError) as exc:
            print(f"[serving] WARN: failed to read {path}: {exc}; using defaults")

    env_overlay = _load_from_env("SGLANG__")
    if env_overlay:
        raw_section = _deep_merge(raw_section, env_overlay)

    return ServingSettings(
        mode=_coerce_mode(raw_section.get("mode", "off")),
        provider_alias=str(raw_section.get("provider_alias", "vllm")),
        model_name=str(raw_section.get("model_name", "")),
        api_base=str(raw_section.get("api_base", "")),
        api_key=str(raw_section.get("api_key", "")),
        sglang=_build_sglang(raw_section.get("sglang") if isinstance(raw_section.get("sglang"), dict) else None),
        ollama=_build_ollama(raw_section.get("ollama") if isinstance(raw_section.get("ollama"), dict) else None),
        health_check_timeout_sec=float(raw_section.get("health_check_timeout_sec", 180.0)),
        health_check_interval_sec=float(raw_section.get("health_check_interval_sec", 2.0)),
        install_deps=bool(raw_section.get("install_deps", True)),
    )
