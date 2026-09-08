"""scripts.serving.ollama_launcher - запуск ``ollama serve`` через subprocess.

Используется для локального теста на ноутбуке (Qwen3.5:9b / qwen3:9b через
ollama). На удалённом prod-сервере с A100 обычно используется sglang вместо
ollama — но ollama-launcher оставлен как самая дешёвая опция для разработки.

Зачем это вообще нужно:
    * ollama поднимает OpenAI-compatible HTTP API на ``localhost:11434/v1``.
    * Nanobot может подключиться к нему как к провайдеру ``ollama``
      (api_base=http://localhost:11434/v1 уже зашит в registry).
    * Это позволяет гонять e2e (gateway -> postgres channel -> ollama -> ответ)
      без GPU-сервера, на CPU/ноутбучной видяхе.

Команды:
    * ensure_ollama_installed(): скачать ollama CLI если нет (Windows через winget/choco,
      Linux через curl install script).
    * start_server(): subprocess.Popen ``ollama serve``.
    * ensure_model_pulled(): ``ollama pull <model>``.
    * stop(): SIGTERM по PID-файлу.
"""

from __future__ import annotations

import os
import platform
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from .config import OllamaSettings
from .sglang_launcher import (
    _pid_alive,
    _read_pid_file,
    _write_pid_file,
    is_running,
)


@dataclass
class OllamaHandle:
    proc: subprocess.Popen | None
    log_file: Path | None
    pid_file: Path
    settings: OllamaSettings


def _python_executable() -> str:
    import sys

    return sys.executable


def is_ollama_installed() -> bool:
    """Проверить, что ``ollama`` доступен в PATH или как модуль pip."""
    if shutil.which("ollama"):
        return True
    try:
        import ollama  # type: ignore  # noqa: F401

        return True
    except ImportError:
        return False


def ollama_version() -> str:
    try:
        out = subprocess.run(
            ["ollama", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return (out.stdout or out.stderr or "unknown").strip()
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return "unknown"


def ensure_ollama_installed(pretty: bool = True) -> None:
    """Если ollama нет — вывести инструкцию по установке.

    Автоматическая установка опасна (winget / choco требуют админ-прав, curl
    install-script делает root-привилегии). Поэтому только подсказка.
    """
    if is_ollama_installed():
        if pretty:
            print(f"[serving] ollama already installed ({ollama_version()})")
        return

    sysname = platform.system()
    msg_lines = [
        "ollama не найден. Установите вручную:",
    ]
    if sysname == "Windows":
        msg_lines.append("  winget install Ollama.Ollama")
        msg_lines.append("  или: https://ollama.com/download/windows")
    elif sysname == "Darwin":
        msg_lines.append("  brew install ollama")
        msg_lines.append("  или: https://ollama.com/download/mac")
    else:
        msg_lines.append("  curl -fsSL https://ollama.com/install.sh | sh")
    msg_lines.append("")
    msg_lines.append("После установки запустите: ollama serve")
    msg_lines.append("И снова: python gateway.py")
    raise RuntimeError("\n".join(msg_lines))


def ensure_model_pulled(settings: OllamaSettings, pretty: bool = True) -> None:
    """``ollama pull <model>`` если модель ещё не скачана."""
    cmd = [settings.executable, "pull", settings.model, *settings.extra_pull_args]
    if pretty:
        print(f"[serving] pulling model: {' '.join(cmd)}")
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=3600)
    if proc.returncode != 0:
        raise RuntimeError(
            f"ollama pull failed (exit {proc.returncode}): "
            f"{proc.stderr[-500:] or proc.stdout[-500:]}"
        )


def start_server(
    settings: OllamaSettings,
    bootstrap_dir: Path,
    pretty: bool = True,
) -> OllamaHandle:
    """Запустить ``ollama serve`` в фоне (если mode=spawn)."""
    pid_file = (bootstrap_dir / settings.pid_file).resolve()
    log_file = (bootstrap_dir / settings.log_dir / "ollama.log").resolve()
    log_file.parent.mkdir(parents=True, exist_ok=True)

    if is_running(pid_file):
        if pretty:
            print(f"[serving] ollama already running (pid={_read_pid_file(pid_file)})")
        return OllamaHandle(proc=None, log_file=log_file, pid_file=pid_file, settings=settings)

    if settings.source == "external":
        if pretty:
            print(f"[serving] mode=external; assuming ollama already up at {settings.host}:{settings.port}")
        return OllamaHandle(proc=None, log_file=log_file, pid_file=pid_file, settings=settings)

    if not is_ollama_installed():
        ensure_ollama_installed(pretty=pretty)

    env = os.environ.copy()
    env["OLLAMA_HOST"] = f"{settings.host}:{settings.port}"
    env["OLLAMA_KEEP_ALIVE"] = settings.keep_alive
    if settings.num_gpu is not None:
        env["OLLAMA_NUM_GPU"] = str(settings.num_gpu)

    log_fp = log_file.open("a", encoding="utf-8")
    log_fp.write(f"\n=== ollama start at {time.strftime('%Y-%m-%d %H:%M:%S')} ===\n")
    log_fp.flush()

    creationflags = 0
    if platform.system() == "Windows":
        DETACHED_PROCESS = 0x00000008
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        creationflags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP

    proc = subprocess.Popen(
        [settings.executable, "serve"],
        stdout=log_fp,
        stderr=subprocess.STDOUT,
        cwd=str(bootstrap_dir),
        env=env,
        creationflags=creationflags,
    )

    _write_pid_file(pid_file, proc.pid)
    if pretty:
        print(f"[serving] ollama started (pid={proc.pid}, log -> {log_file})")
    return OllamaHandle(proc=proc, log_file=log_file, pid_file=pid_file, settings=settings)


def stop(pid_file: Path, timeout_sec: float = 10.0, pretty: bool = True) -> bool:
    """Остановить ollama-сервер по PID-файлу."""
    import ctypes
    import signal as _signal

    pid = _read_pid_file(pid_file)
    if not pid or not _pid_alive(pid):
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
        return False

    if pretty:
        print(f"[serving] stopping ollama (pid={pid})...")

    try:
        if platform.system() == "Windows":
            PROCESS_TERMINATE = 0x0001
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
            if handle:
                kernel32.TerminateProcess(handle, 0)
                kernel32.CloseHandle(handle)
        else:
            os.kill(pid, _signal.SIGTERM)
    except (OSError, ProcessLookupError):
        pass

    deadline = time.monotonic() + timeout_sec
    while time.monotonic() < deadline and _pid_alive(pid):
        time.sleep(0.2)

    if _pid_alive(pid):
        try:
            if platform.system() == "Windows":
                kernel32 = ctypes.windll.kernel32
                PROCESS_TERMINATE = 0x0001
                handle = kernel32.OpenProcess(PROCESS_TERMINATE, False, pid)
                if handle:
                    kernel32.TerminateProcess(handle, 1)
                    kernel32.CloseHandle(handle)
            else:
                os.kill(pid, _signal.SIGKILL)
        except (OSError, ProcessLookupError):
            pass

    try:
        pid_file.unlink(missing_ok=True)
    except OSError:
        pass
    return True
