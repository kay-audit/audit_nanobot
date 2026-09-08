"""scripts.serving.sglang_launcher - запуск sglang-сервера через subprocess.

Используется bootstrap'ом (serving_bootstrap) для поднятия локального
LLM-сервера перед стартом Nanobot. НЕ лезет в код Nanobot — только
запускает ``python -m sglang.launch_server`` (или ``sglang serve``) и
ждёт готовности.

Зачем это отдельно от vllm:
    * На сервере с torch 2.5.1+cu124 vLLM падает на torchinductor и
      qwen_3_5_moe (несовместимая архитектура модели).
    * sglang поддерживает Qwen3-30B-A3B-Instruct без этих проблем.
    * vllm-механизм Nanobot остаётся как fallback — мы только подменяем
      apiBase через render_config (scripts.serving.render_config).

Команды:
    * ensure_sglang_installed(): pip install sglang[all] если sglang не найден.
    * install_deps(): установка (вызывается из bootstrap с проверкой install_deps).
    * start_server(): subprocess.Popen sglang, возвращает (proc, log_file, pid_file).
    * is_running(): проверить PID-файл и живость процесса.
    * stop(): SIGTERM/SIGKILL по PID-файлу.
"""

from __future__ import annotations

import os
import platform
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import SglangSettings


@dataclass
class SglangHandle:
    proc: subprocess.Popen | None
    log_file: Path | None
    pid_file: Path
    settings: SglangSettings


def _python_executable() -> str:
    """Python из текущего venv (sys.executable). Используется для запуска sglang."""
    return sys.executable


def is_sglang_installed() -> bool:
    """Проверить, что пакет sglang доступен в текущем Python."""
    try:
        import sglang  # noqa: F401

        return True
    except ImportError:
        return False


def sglang_version() -> str:
    """Версия sglang или 'unknown'."""
    try:
        import sglang

        return str(getattr(sglang, "__version__", "unknown"))
    except Exception:
        return "unknown"


def ensure_sglang_installed(
    extra_index: bool = False,
    timeout_sec: float = 600.0,
    pretty: bool = True,
) -> None:
    """Установить sglang[all] через pip если он ещё не установлен.

    На удалённом сервере с CUDA 12.4 и torch 2.5.1+cu124 — sglang сам
    подтянет нужные колёса (он опубликован на PyPI с поддержкой CUDA 12.x).

    Args:
        extra_index: если True — добавляет ``--extra-index-url
            https://download.pytorch.org/whl/cu124`` (на случай если дефолтный
            индекс не содержит совместимые колёса).
        timeout_sec: максимум ожидания pip.
        pretty: печатать ход установки.
    """
    if is_sglang_installed():
        if pretty:
            print(f"[serving] sglang {sglang_version()} already installed")
        return

    cmd: list[str] = [
        _python_executable(),
        "-m",
        "pip",
        "install",
        "--upgrade",
        "sglang[all]",
    ]
    if extra_index:
        cmd.extend(["--extra-index-url", "https://download.pytorch.org/whl/cu124"])

    if pretty:
        print(f"[serving] installing sglang: {' '.join(cmd)}")

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout_sec,
    )
    if proc.returncode != 0:
        msg = (
            f"sglang install failed (exit {proc.returncode})\n"
            f"stdout (tail):\n{proc.stdout[-1000:]}\n"
            f"stderr (tail):\n{proc.stderr[-1000:]}"
        )
        raise RuntimeError(msg)
    if pretty:
        print(f"[serving] sglang installed: {sglang_version()}")


def _sglang_command(settings: SglangSettings) -> list[str]:
    """Собрать CLI-команду для запуска sglang-сервера.

    Поддерживаем оба варианта (на разных версиях sglang модуль мог называться
    по-разному): ``python -m sglang.launch_server`` и ``sglang serve``.
    """
    args: list[str] = [
        "--model-path",
        settings.model_path,
        "--host",
        settings.host,
        "--port",
        str(settings.port),
        "--served-model-name",
        settings.served_model_name,
        "--mem-fraction-static",
        str(settings.gpu_memory_utilization),
        "--context-length",
        str(settings.max_model_len),
        "--dtype",
        settings.dtype,
    ]
    if settings.gpu_ids and settings.gpu_ids != "all":
        args.extend(["--gpu-id", settings.gpu_ids.split(",")[0].strip()])
    if settings.trust_remote_code:
        args.append("--trust-remote-code")
    if not settings.torch_compile:
        args.extend(["--disable-torch-compile"])
    if settings.quantization:
        args.extend(["--quantization", settings.quantization])
    if settings.backend and settings.backend != "auto":
        args.extend(["--backend", settings.backend])
    if settings.api_key and settings.api_key != "EMPTY":
        args.extend(["--api-key", settings.api_key])
    args.extend(settings.extra_args)

    # Пробуем разные entrypoints.
    py = _python_executable()
    candidates: list[list[str]] = [
        [py, "-m", "sglang.launch_server", *args],
        [py, "-m", "sglang.srt.server", *args],
    ]
    return candidates[0]  # вернуть первый; если не работает — fallback в start_server


def _write_pid_file(pid_file: Path, pid: int) -> None:
    pid_file.parent.mkdir(parents=True, exist_ok=True)
    pid_file.write_text(f"{pid}\n", encoding="utf-8")


def _read_pid_file(pid_file: Path) -> int | None:
    if not pid_file.exists():
        return None
    try:
        text = pid_file.read_text(encoding="utf-8").strip()
        return int(text.split()[0])
    except (ValueError, OSError):
        return None


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        if platform.system() == "Windows":
            # На Windows signal.SIGTERM недоступен, используем kill через ctypes.
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            STILL_ACTIVE = 259
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
            if not handle:
                return False
            try:
                code = ctypes.c_ulong()
                ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
                return bool(ok) and code.value == STILL_ACTIVE
            finally:
                kernel32.CloseHandle(handle)
        else:
            os.kill(pid, 0)
            return True
    except (OSError, ProcessLookupError, PermissionError):
        return False


def is_running(pid_file: Path) -> bool:
    """Проверить, что процесс из pid-файла жив."""
    pid = _read_pid_file(pid_file)
    return bool(pid) and _pid_alive(pid)


def start_server(
    settings: SglangSettings,
    bootstrap_dir: Path,
    pretty: bool = True,
) -> SglangHandle:
    """Запустить sglang-сервер в фоне. Возвращает handle (proc + log + pid).

    Если процесс уже запущен (по pid_file) — возвращает handle с proc=None.
    Если pip install требуется — выполнит его перед стартом (если bootstrap
    предварительно не сделал install_deps()).
    """
    pid_file = (bootstrap_dir / settings.pid_file).resolve()
    log_file = (bootstrap_dir / settings.log_dir / "sglang.log").resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    if is_running(pid_file):
        if pretty:
            print(f"[serving] sglang already running (pid={_read_pid_file(pid_file)})")
        return SglangHandle(proc=None, log_file=log_file, pid_file=pid_file, settings=settings)

    if not is_sglang_installed():
        raise RuntimeError(
            "sglang not installed. Set serving.install_deps=true or run "
            "`python -m scripts.serving.bootstrap install-deps` first."
        )

    cmd_args = _sglang_command(settings)
    if pretty:
        print(f"[serving] starting sglang: {' '.join(cmd_args[:6])}...")
        print(f"[serving] log -> {log_file}")

    log_fp = log_file.open("a", encoding="utf-8")
    log_fp.write(f"\n=== sglang start at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    log_fp.flush()

    env = os.environ.copy()
    env.setdefault("PYTHONUTF8", "1")
    env.setdefault("PYTHONIOENCODING", "utf-8")

    # На Windows нужен creationflags, чтобы не открывать консольное окно.
    creationflags = 0
    if platform.system() == "Windows":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        cmd_args,
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        cwd=str(bootstrap_dir),
        env=env,
        creationflags=creationflags,
    )

    _write_pid_file(pid_file, proc.pid)
    return SglangHandle(proc=proc, log_file=log_file, pid_file=pid_file, settings=settings)


def stop(pid_file: Path, timeout_sec: float = 10.0, pretty: bool = True) -> bool:
    """Остановить sglang-сервер по PID-файлу. Возвращает True если процесс был."""
    pid = _read_pid_file(pid_file)
    if not pid:
        if pretty:
            print(f"[serving] no pid file at {pid_file}")
        return False
    if not _pid_alive(pid):
        if pretty:
            print(f"[serving] pid {pid} not alive")
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    if pretty:
        print(f"[serving] stopping sglang (pid={pid})...")

    try:
        if platform.system() == "Windows":
            import ctypes

            PROCESS_TERMINATE = 0x0001
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
        else:
            os.kill(pid, signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)

    if _pid_alive(pid):
        if pretty:
            print(f"[serving] sglang still alive, sending SIGKILL")
        try:
            if platform.system() == "Windows":
                import ctypes

                PROCESS_TERMINATE = 0x0001
                kernel32 = ctypes.windll.kernel32
                handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if handle:
                    kernel32.TerminateProcess(handle, 1)
                    kernel32.CloseHandle(handle)
            else:
                os.kill(pid, signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass
    return True


def install_deps(extra_index: bool = False, pretty: bool = True) -> None:
    """Явная установка sglang через pip (для CLI)."""
    ensure_sglang_installed(extra_index=extra_index, pretty=pretty)
