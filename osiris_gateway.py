"""osiris_gateway - standalone entrypoint для запуска Nanobot + SGLang на голой ноде с GPU.

Предназначен ТОЛЬКО для production-серверов со следующим стеком:
    - 1-4 GPU NVIDIA A100-SXM4-80GB
    - driver >= 550.90.07
    - CUDA 12.4 (NVCC)
    - torch 2.5.1+cu124 (ЗАФИКСИРОВАНО, менять НЕЛЬЗЯ)
    - модель Qwen3.6 35B-A3B (MoE: 35B общих, 3B активных)

Запуск:
    python osiris_gateway.py
    python osiris_gateway.py --model-path /data/models/Qwen3.6-35B-A3B-Instruct
    python osiris_gateway.py --port 30000 --num-gpus 4
    python osiris_gateway.py --dry-run            # только проверить окружение
    python osiris_gateway.py --skip-install       # пропустить pip install

Что делает:
    1) Проверяет наличие NVIDIA GPU через nvidia-smi (или переменную OSIRIS_NUM_GPUS).
    2) Проверяет версию CUDA через nvcc (или torch).
    3) Устанавливает torch==2.5.1+cu124, flashinfer-python, sglang[all]==X.Y.Z,
       затем зависимости Nanobot из requirements.txt.
    4) Патчит config.json: добавляет секцию serving (mode=sglang) и
       providers.vllm.apiBase/apiKey чтобы Nanobot знал куда стучаться.
    5) Запускает sglang-сервер в фоне через scripts.serving.sglang_launcher.
    6) Ждёт health-check на /v1/models.
    7) Запускает Nanobot pipeline через gateway.main() — он подхватывает
       уже запущенный sglang (mode=sglang, install_deps=False).
    8) Nanobot читает вопросы из public.agent_conversation_messages,
       обрабатывает их через sglang и пишет ответы обратно.

На ноутбуке без NVIDIA GPU:
    python osiris_gateway.py --skip-install --skip-gpu-check --dry-run
    # создать отдельный venv и установить CPU-only deps (см. docs/osiris/TESTING_ON_LAPTOP.md)

Зачем это отдельно от gateway.py:
    - gateway.py — обычный entrypoint Nanobot; serving-bootstrap в нём опциональный
      (если нет scripts.serving, fallback на vLLM-пакет).
    - osiris_gateway.py — production entrypoint для голой ноды:
      * ставит torch+CUDA+sglang из правильного wheel-index;
      * проверяет GPU/driver;
      * запускает sglang как отдельный процесс ДО старта Nanobot;
      * управляет всем пайплайном (sglang + Nanobot) как одной единицей.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any

# === UTF-8 for Windows PowerShell (cp1251/OEM) ===
os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("PYTHONIOENCODING", "utf-8")
if sys.platform != "win32":
    os.environ.setdefault("LC_ALL", "C.UTF-8")
    os.environ.setdefault("LANG", "C.UTF-8")

# === ЗАФИКСИРОВАННЫЕ ВЕРСИИ для CUDA 12.4 + torch 2.5.1+cu124 + Qwen3.6 35B-A3B ===
# НЕ МЕНЯТЬ без подтверждения совместимости (qwen_3_5_moe + torchinductor ломается
# при других версиях torch, см. docs/serving/SGLANG.md).
TORCH_VERSION = "2.5.1"
TORCH_INDEX_URL = "https://download.pytorch.org/whl/cu124"
TORCHAUDIO_VERSION = "2.5.1"
TORCHVISION_VERSION = "0.20.1"

# sglang 0.4.3 - последняя версия с гарантированной поддержкой torch 2.5.1;
# 0.4.4+ может требовать torch 2.6.x.
SGLANG_VERSION = "0.4.3"

# flashinfer для ускорения MoE (Qwen3.6 35B-A3B - MoE архитектура).
FLASHINFER_PYTHON_VERSION = "0.2.5"

# transformers + accelerate для Qwen3 (нужны для sglang-бэкенда и загрузки весов).
TRANSFORMERS_VERSION = "4.46.3"
ACCELERATE_VERSION = "1.1.0"

# === Параметры модели/сервера по умолчанию ===
DEFAULT_MODEL_PATH = "/data/models/Qwen3.6-35B-A3B-Instruct"
DEFAULT_SERVED_MODEL_NAME = "Qwen3.6-35B-A3B"
DEFAULT_SGLANG_HOST = "0.0.0.0"
DEFAULT_SGLANG_PORT = 30000
DEFAULT_MAX_MODEL_LEN = 32768
DEFAULT_GPU_MEMORY_UTILIZATION = 0.9
# torch.compile ОТКЛЮЧЁН: qwen_3_5_moe + torchinductor несовместимы на torch 2.5.1.
# True = включить (НЕ делаем), False = выключить (используем).
TORCH_COMPILE_ENABLED = False
# Минимальная версия драйвера NVIDIA для CUDA 12.4 (GA).
MIN_DRIVER_MAJOR = 550


# ============================================================
# Logging helpers
# ============================================================

def _log(level: str, msg: str) -> None:
    tag = {"step": "*", "ok": "+", "warn": "!", "err": "x"}.get(level, "*")
    stream = sys.stderr if level == "err" else sys.stdout
    print(f"[osiris][{tag}] {msg}", file=stream, flush=True)


def _step(msg: str) -> None:
    _log("step", msg)


def _ok(msg: str) -> None:
    _log("ok", msg)


def _warn(msg: str) -> None:
    _log("warn", msg)


def _err(msg: str) -> None:
    _log("err", msg)


# ============================================================
# Environment checks
# ============================================================

def detect_gpus() -> list[dict[str, str]]:
    """Получить список GPU через nvidia-smi. Пустой список если не найдено."""
    nvidia_smi = shutil.which("nvidia-smi")
    if not nvidia_smi:
        return []
    try:
        out = subprocess.run(
            [nvidia_smi, "--query-gpu=index,name,memory.total,driver_version",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=15, check=True,
        ).stdout.strip()
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError) as exc:
        _warn(f"nvidia-smi failed: {exc}")
        return []

    gpus: list[dict[str, str]] = []
    for line in out.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 4:
            gpus.append({
                "index": parts[0],
                "name": parts[1],
                "memory_mib": parts[2],
                "driver": parts[3],
            })
    return gpus


def check_driver(driver: str) -> bool:
    """Проверить что driver >= MIN_DRIVER_MAJOR (для CUDA 12.4)."""
    try:
        major = int(driver.split(".")[0])
    except (ValueError, IndexError):
        return False
    return major >= MIN_DRIVER_MAJOR


def check_cuda_version() -> str | None:
    """Получить версию CUDA через nvcc или torch. None если недоступно."""
    nvcc = shutil.which("nvcc")
    if nvcc:
        try:
            out = subprocess.run([nvcc, "--version"], capture_output=True, text=True, timeout=10, check=True).stdout
            for line in out.splitlines():
                if "release" in line.lower():
                    return line.split("release")[-1].strip().split(",")[0].strip()
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
            pass
    # Fallback на torch (если уже установлен)
    try:
        import torch  # noqa: PLC0415
        return torch.version.cuda
    except ImportError:
        pass
    return None


def check_environment(skip_gpu: bool = False) -> dict[str, Any]:
    """Проверить GPU + driver + CUDA. Возвращает dict с результатами.

    Raises:
        RuntimeError: если нет GPU (или driver/CUDA не подходят) и skip_gpu=False.
    """
    result: dict[str, Any] = {
        "gpus": [],
        "driver": "",
        "cuda": None,
        "torch": None,
        "sglang": None,
        "flashinfer": None,
    }

    if not skip_gpu:
        gpus = detect_gpus()
        if not gpus:
            raise RuntimeError(
                "no NVIDIA GPU detected via nvidia-smi; cannot run sglang. "
                "Set --skip-gpu-check if you only want to install deps or run dry-run."
            )
        result["gpus"] = gpus
        result["driver"] = gpus[0]["driver"]
        if not check_driver(gpus[0]["driver"]):
            _warn(f"driver {gpus[0]['driver']} < {MIN_DRIVER_MAJOR}; CUDA 12.4 may not work")

        cuda_ver = check_cuda_version()
        result["cuda"] = cuda_ver
        if cuda_ver and not cuda_ver.startswith("12.4"):
            _warn(f"CUDA {cuda_ver} != 12.4; torch 2.5.1+cu124 built for CUDA 12.4")

        is_a100 = all("A100" in g["name"] for g in gpus)
        if not is_a100:
            _warn(f"GPU names: {[g['name'] for g in gpus]}; this script targets A100-SXM4-80GB")

        _ok(f"{len(gpus)}x GPU detected (driver {result['driver']}, CUDA {cuda_ver or 'unknown'})")
        for g in gpus:
            _step(f"  GPU {g['index']}: {g['name']} {g['memory_mib']} MiB")

    # Сообщить о текущих версиях библиотек (если уже установлены)
    try:
        import torch  # noqa: PLC0415
        result["torch"] = torch.__version__
        _step(f"torch installed: {torch.__version__} (CUDA available={torch.cuda.is_available()})")
    except ImportError:
        pass
    try:
        import sglang  # noqa: PLC0415
        result["sglang"] = getattr(sglang, "__version__", "unknown")
        _step(f"sglang installed: {result['sglang']}")
    except ImportError:
        pass
    try:
        import flashinfer  # noqa: PLC0415
        result["flashinfer"] = getattr(flashinfer, "__version__", "unknown")
        _step(f"flashinfer installed: {result['flashinfer']}")
    except ImportError:
        pass

    return result


# ============================================================
# Install steps
# ============================================================

def _pip_install(cmd: list[str], timeout_sec: int = 1800) -> None:
    """Запустить pip install с логированием. Raises на ненулевой exit code."""
    _step(f"running: {' '.join(cmd)}")
    # Use Popen чтобы видеть прогресс (и не терять хвост)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    assert proc.stdout is not None
    last_log_ts = 0.0
    try:
        for line in proc.stdout:
            line = line.rstrip()
            if not line:
                continue
            now = time.monotonic()
            # Каждые 5 секунд печатаем хвост; иначе фильтруем "Requirement already satisfied"
            if "Requirement already satisfied" in line:
                continue
            if "Collecting" in line or "Downloading" in line or "Installing" in line:
                if now - last_log_ts > 5.0:
                    _step(f"  pip: {line[:120]}")
                    last_log_ts = now
                continue
            if "Successfully installed" in line or "Successfully uninstalled" in line:
                _ok(f"pip: {line}")
                continue
            # Ошибки и warnings
            if line.startswith(("ERROR", "WARNING", "  ERROR", "  WARNING")):
                _warn(f"pip: {line}")
                continue
            # Прочий вывод — логировать на DEBUG
            if now - last_log_ts > 10.0:
                _step(f"  pip: {line[:120]}")
                last_log_ts = now
    finally:
        proc.wait(timeout=timeout_sec)
    if proc.returncode != 0:
        raise RuntimeError(f"pip install failed (exit {proc.returncode})")


def ensure_torch() -> None:
    """Установить torch==2.5.1+cu124 (ЗАФИКСИРОВАНО)."""
    try:
        import torch  # noqa: PLC0415
        ver = torch.__version__
        if TORCH_VERSION in ver and "+cu124" in ver:
            _ok(f"torch {ver} already installed")
            if not torch.cuda.is_available():
                _warn("torch installed but CUDA not available; check driver/CUDA install")
            return
        _warn(f"torch {ver} != {TORCH_VERSION}+cu124; reinstalling")
    except ImportError:
        _step(f"torch not installed; installing torch=={TORCH_VERSION}+cu124")

    cmd = [
        sys.executable, "-m", "pip", "install", "--upgrade",
        f"torch=={TORCH_VERSION}", f"torchvision=={TORCHVISION_VERSION}",
        f"torchaudio=={TORCHAUDIO_VERSION}",
        "--index-url", TORCH_INDEX_URL,
    ]
    _pip_install(cmd, timeout_sec=2400)


def ensure_flashinfer() -> None:
    """Установить flashinfer для ускорения MoE моделей (опционально)."""
    try:
        import flashinfer  # noqa: PLC0415, F401
        _ok(f"flashinfer already installed")
        return
    except ImportError:
        pass

    _step(f"installing flashinfer-python=={FLASHINFER_PYTHON_VERSION} (optional)")
    try:
        cmd = [
            sys.executable, "-m", "pip", "install", "--upgrade",
            f"flashinfer-python=={FLASHINFER_PYTHON_VERSION}",
            "flashinfer-cubin",
        ]
        _pip_install(cmd, timeout_sec=900)
        _ok(f"flashinfer-python=={FLASHINFER_PYTHON_VERSION} installed")
    except Exception as exc:
        # flashinfer не критичен; sglang может работать без него (медленнее)
        _warn(f"flashinfer install failed ({exc}); sglang will run without flashinfer attention")


def ensure_sglang() -> None:
    """Установить sglang[all]==SGLANG_VERSION."""
    try:
        import sglang  # noqa: PLC0415, F401
        v = getattr(sglang, "__version__", "unknown")
        if SGLANG_VERSION in v:
            _ok(f"sglang {v} already installed")
            return
        _warn(f"sglang {v} != {SGLANG_VERSION}; reinstalling")
    except ImportError:
        _step(f"sglang not installed; installing sglang[all]=={SGLANG_VERSION} (large download)")

    cmd = [
        sys.executable, "-m", "pip", "install", "--upgrade",
        f"sglang[all]=={SGLANG_VERSION}",
    ]
    _pip_install(cmd, timeout_sec=1800)
    _ok(f"sglang[all]=={SGLANG_VERSION} installed")


def ensure_transformers_and_accelerate() -> None:
    """Установить совместимые transformers + accelerate."""
    _step(f"installing transformers=={TRANSFORMERS_VERSION}, accelerate=={ACCELERATE_VERSION}")
    cmd = [
        sys.executable, "-m", "pip", "install", "--upgrade",
        f"transformers=={TRANSFORMERS_VERSION}",
        f"accelerate=={ACCELERATE_VERSION}",
    ]
    try:
        _pip_install(cmd, timeout_sec=900)
    except Exception as exc:
        _warn(f"transformers/accelerate pin failed ({exc}); continuing with whatever is installed")


def ensure_nanobot_deps() -> None:
    """Установить зависимости Nanobot из requirements.txt."""
    req_file = Path(__file__).parent / "requirements.txt"
    if not req_file.exists():
        _err(f"requirements.txt not found at {req_file}")
        raise RuntimeError("requirements.txt missing")
    _step(f"installing Nanobot deps from {req_file}")
    _pip_install([sys.executable, "-m", "pip", "install", "-r", str(req_file)], timeout_sec=900)


# ============================================================
# Config preparation
# ============================================================

def build_serving_section(
    *,
    model_path: str,
    served_model_name: str,
    host: str,
    port: int,
    num_gpus: int,
    max_model_len: int,
    gpu_memory_utilization: float,
    trust_remote_code: bool,
    quantization: str | None,
    extra_args: list[str],
) -> dict[str, Any]:
    """Собрать секцию serving для config.json.

    Args:
        num_gpus: количество GPU для tensor-parallel (1 = single-GPU).
        extra_args: дополнительные CLI-аргументы sglang (например, --tp-size N).
    """
    # tp-size передаётся через extra_args, не через gpu_ids
    tp_size = num_gpus if num_gpus > 1 else 1
    args = list(extra_args)
    if tp_size > 1 and not any(a.startswith("--tp") for a in args):
        args.extend(["--tp-size", str(tp_size)])

    return {
        "mode": "sglang",
        "provider_alias": "vllm",
        "model_name": served_model_name,
        "api_base": f"http://{host}:{port}/v1",
        "api_key": "EMPTY",
        "health_check_timeout_sec": 600.0,
        "health_check_interval_sec": 2.0,
        "install_deps": True,  # bootstrap не будет pip install (мы уже)
        "sglang": {
            "host": host,
            "port": port,
            "model_path": model_path,
            "served_model_name": served_model_name,
            "gpu_ids": "0",  # всегда GPU 0 для запуска; tp-size разнесёт по остальным
            "gpu_memory_utilization": gpu_memory_utilization,
            "max_model_len": max_model_len,
            "dtype": "bfloat16",
            "trust_remote_code": trust_remote_code,
            "torch_compile": TORCH_COMPILE_ENABLED,
            "quantization": quantization,
            "api_key": "EMPTY",
            "extra_args": args,
            "log_dir": "logs",
            "pid_file": "logs/sglang.pid",
        },
    }


def patch_config(serving_section: dict[str, Any]) -> Path:
    """Вставить/обновить секции serving + providers.vllm + agents.defaults в config.json.

    Делает atomic write (temp + os.replace, UTF-8 без BOM).
    """
    config_path = Path(__file__).parent / "config.json"
    if config_path.exists():
        try:
            doc = json.loads(config_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            _err(f"{config_path}: invalid JSON: {exc}")
            raise
    else:
        _warn(f"{config_path} not found; creating minimal")
        doc = {}

    # serving
    doc["serving"] = serving_section

    # providers.vllm — Nanobot использует vllm-провайдер (OpenAI-compat) для sglang
    providers = doc.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        doc["providers"] = providers
    vllm = providers.setdefault("vllm", {})
    if not isinstance(vllm, dict):
        vllm = {}
        providers["vllm"] = vllm
    vllm["apiBase"] = serving_section["api_base"]
    vllm["apiKey"] = serving_section["api_key"]

    # agents.defaults.provider/model
    agents = doc.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        doc["agents"] = agents
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults
    defaults["provider"] = "vllm"
    defaults["model"] = serving_section["model_name"]

    # atomic write
    fd, tmp_name = tempfile.mkstemp(prefix=".config.json.", suffix=".tmp", dir=str(config_path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_name, config_path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    _ok(f"patched {config_path}")
    _step(f"  providers.vllm.apiBase = {serving_section['api_base']}")
    _step(f"  providers.vllm.apiKey  = {serving_section['api_key']}")
    _step(f"  agents.defaults.model  = {serving_section['model_name']}")
    _step(f"  serving.mode           = {serving_section['mode']}")
    return config_path


# ============================================================
# sglang launch
# ============================================================

def start_sglang(serving_section: dict[str, Any], logs_dir: Path) -> int:
    """Запустить sglang-сервер в фоне. Возвращает PID.

    Использует scripts.serving.sglang_launcher для subprocess и health-check.
    Raises RuntimeError если sglang не отвечает за health_check_timeout_sec.
    """
    from scripts.serving.config import (  # noqa: PLC0415
        ServingSettings, _build_sglang, _coerce_mode,
    )
    from scripts.serving.sglang_launcher import start_server  # noqa: PLC0415
    from scripts.serving.health_check import wait_until_ready  # noqa: PLC0415

    raw = serving_section
    sglang_cfg = _build_sglang(raw.get("sglang"))
    cfg = ServingSettings(
        mode=_coerce_mode(raw.get("mode", "off")),
        provider_alias=raw.get("provider_alias", "vllm"),
        model_name=raw.get("model_name", ""),
        api_base=raw.get("api_base", ""),
        api_key=raw.get("api_key", ""),
        sglang=sglang_cfg,
        install_deps=False,  # мы уже установили
        health_check_timeout_sec=float(raw.get("health_check_timeout_sec", 600.0)),
        health_check_interval_sec=float(raw.get("health_check_interval_sec", 2.0)),
    )

    handle = start_server(cfg.sglang, bootstrap_dir=Path.cwd(), pretty=True)

    api_base = cfg.api_base or f"http://{sglang_cfg.host}:{sglang_cfg.port}/v1"
    health = wait_until_ready(
        api_base=api_base,
        api_key=cfg.api_key or sglang_cfg.api_key,
        expected_model=sglang_cfg.served_model_name,
        timeout_sec=cfg.health_check_timeout_sec,
        interval_sec=cfg.health_check_interval_sec,
        pretty=True,
    )
    if not health:
        raise RuntimeError(f"sglang health-check failed: {health.detail}")

    pid = handle.proc.pid if handle.proc else 0
    _ok(f"sglang ready at {api_base} (models={health.models}, pid={pid})")
    return pid


def stop_sglang(config_path: Path) -> None:
    """Остановить sglang через scripts.serving.serving_bootstrap.stop_serving."""
    try:
        from scripts.serving.serving_bootstrap import stop_serving  # noqa: PLC0415
        stop_serving(config_path, pretty=True)
    except Exception as exc:
        _warn(f"failed to stop sglang cleanly: {exc}")


# ============================================================
# Nanobot pipeline launch
# ============================================================

def start_nanobot_pipeline() -> None:
    """Запустить Nanobot через gateway.main().

    gateway.main() вызывает _bootstrap_serving() внутри, но если mode=sglang и
    install_deps=False и сервер уже живой — bootstrap сделает health-check
    и вернёт ok, не пытаясь запустить sglang ещё раз (см. serving_bootstrap).
    """
    from gateway import main as gateway_main  # noqa: PLC0415
    _step("starting Nanobot gateway pipeline...")
    gateway_main()


# ============================================================
# Main
# ============================================================

def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Osiris gateway: standalone sglang + Nanobot launcher for GPU nodes",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--model-path", default=os.environ.get("OSIRIS_MODEL_PATH", DEFAULT_MODEL_PATH),
                   help="path to Qwen3.6 35B-A3B weights (HuggingFace cache dir or local)")
    p.add_argument("--served-model-name", default=os.environ.get("OSIRIS_SERVED_MODEL_NAME", DEFAULT_SERVED_MODEL_NAME),
                   help="model name exposed via OpenAI-compat API")
    p.add_argument("--host", default=DEFAULT_SGLANG_HOST, help="sglang bind host")
    p.add_argument("--port", type=int, default=DEFAULT_SGLANG_PORT, help="sglang bind port")
    p.add_argument("--num-gpus", type=int, default=None,
                   help="number of GPUs for tensor-parallel (default: auto-detect via nvidia-smi)")
    p.add_argument("--max-model-len", type=int, default=DEFAULT_MAX_MODEL_LEN, help="context length")
    p.add_argument("--gpu-memory-utilization", type=float, default=DEFAULT_GPU_MEMORY_UTILIZATION,
                   help="fraction of GPU memory sglang can use")
    p.add_argument("--quantization", default=None,
                   help="quantization method (e.g. awq-marlin, fp8, gptq); None = bf16")
    p.add_argument("--no-trust-remote-code", action="store_true", help="disable --trust-remote-code")
    p.add_argument("--extra-args", action="append", default=[],
                   help="extra CLI args for sglang (repeatable)")
    p.add_argument("--skip-install", action="store_true", help="skip pip install (deps assumed installed)")
    p.add_argument("--skip-flashinfer", action="store_true", help="skip flashinfer install")
    p.add_argument("--skip-gpu-check", action="store_true",
                   help="skip nvidia-smi check (use --num-gpus to fake GPU count)")
    p.add_argument("--skip-sglang", action="store_true",
                   help="don't start sglang; assume it's already running externally (mode=external)")
    p.add_argument("--dry-run", action="store_true",
                   help="print actions without executing (env check + install + launch are no-ops)")
    p.add_argument("--no-banner", action="store_true", help="suppress startup banner")
    return p.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if not args.no_banner:
        print("=" * 70)
        print("  Osiris Gateway v1.0")
        print(f"  Stack: torch {TORCH_VERSION}+cu124 · sglang {SGLANG_VERSION} · Qwen3.6 35B-A3B")
        print(f"  Model: {args.model_path}")
        print(f"  sglang endpoint: http://{args.host}:{args.port}/v1")
        print("=" * 70)

    # 1) Проверка окружения
    try:
        env = check_environment(skip_gpu=args.skip_gpu_check)
    except RuntimeError as exc:
        _err(str(exc))
        return 1

    if args.num_gpus is not None:
        num_gpus = args.num_gpus
    elif env["gpus"]:
        num_gpus = len(env["gpus"])
    else:
        num_gpus = 1
    _step(f"using {num_gpus} GPU(s)")

    # 2) Установка зависимостей
    if args.dry_run:
        _step("dry-run: would install torch==2.5.1+cu124, sglang[all]==0.4.3, "
              "flashinfer, transformers, accelerate, requirements.txt")
    elif not args.skip_install:
        ensure_torch()
        if not args.skip_flashinfer:
            ensure_flashinfer()
        ensure_transformers_and_accelerate()
        ensure_sglang()
        ensure_nanobot_deps()
    else:
        _step("--skip-install set; assuming torch/sglang/flashinfer/nanobot deps present")

    # 3) Патч config.json
    serving = build_serving_section(
        model_path=args.model_path,
        served_model_name=args.served_model_name,
        host=args.host,
        port=args.port,
        num_gpus=num_gpus,
        max_model_len=args.max_model_len,
        gpu_memory_utilization=args.gpu_memory_utilization,
        trust_remote_code=not args.no_trust_remote_code,
        quantization=args.quantization,
        extra_args=args.extra_args,
    )
    if args.dry_run:
        _step(f"dry-run: would patch config.json with serving.mode={serving['mode']}, "
              f"api_base={serving['api_base']}")
    else:
        config_path = patch_config(serving)
        _step(f"  log dir: {(config_path.parent / 'logs').resolve()}")

    if args.dry_run:
        if args.skip_sglang:
            _step("dry-run: would skip sglang launch (--skip-sglang)")
        else:
            _step(f"dry-run: would start sglang on port {args.port} with tp={num_gpus}")
        _step("dry-run: would start Nanobot pipeline (gateway.main)")
        _ok("dry-run completed")
        return 0

    # 4) Запуск sglang (если не --skip-sglang)
    sglang_pid = 0
    if args.skip_sglang:
        # Меняем serving.mode на external чтобы bootstrap не пытался запустить sglang
        serving["mode"] = "external"
        patch_config(serving)
        _step("--skip-sglang: expecting sglang externally at " + serving["api_base"])
    else:
        try:
            sglang_pid = start_sglang(serving, logs_dir=Path.cwd() / "logs")
            _ok(f"sglang started (pid={sglang_pid}); logs -> logs/sglang.log")
        except Exception as exc:
            _err(f"sglang startup failed: {exc}")
            import traceback; traceback.print_exc()
            return 2

    # 5) Запуск Nanobot pipeline
    exit_code = 0
    try:
        start_nanobot_pipeline()
    except KeyboardInterrupt:
        _ok("KeyboardInterrupt received; shutting down...")
    except SystemExit as exc:
        exit_code = int(exc.code) if isinstance(exc.code, int) else 0
    except Exception as exc:
        _err(f"Nanobot pipeline crashed: {exc}")
        import traceback; traceback.print_exc()
        exit_code = 3
    finally:
        # Cleanup: остановить sglang если мы его запускали
        if sglang_pid and not args.skip_sglang:
            _step("cleanup: stopping sglang...")
            stop_sglang(Path.cwd() / "config.json")

    return exit_code


if __name__ == "__main__":
    sys.exit(main())
