"""scripts.serving.serving_bootstrap - единая точка входа для поднятия LLM.

Вызывается из ``gateway.py:main()`` ДО ``ApplicationContext.create()``.
Если секции ``serving`` в config.json нет — bootstrap ничего не делает
(Nanobot стартует штатно). Если есть — выполняет:

    1) install deps (sglang / ollama) если ``install_deps=true``;
    2) запускает выбранный сервер (sglang / ollama / external);
    3) ждёт ``/v1/models`` health-check;
    4) патчит ``providers.<alias>.apiBase`` + ``agents.defaults.model``;
    5) возвращает информацию о запущенном сервере для логов.

При любой ошибке — печатает stacktrace, но НЕ бросает исключение (чтобы
не ломать штатный старт gateway в dev-режиме без serving).
"""

from __future__ import annotations

import traceback
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import ollama_launcher, sglang_launcher
from .config import ServingSettings, load_serving_config
from .health_check import wait_until_ready
from .render_config import ConfigPatch, render


@dataclass
class ServingResult:
    mode: str
    api_base: str
    model_name: str
    patch: ConfigPatch | None
    started: bool
    healthy: bool
    detail: str = ""
    extras: dict[str, Any] = field(default_factory=dict)

    def __bool__(self) -> bool:
        return self.healthy


def _bootstrap_sglang(
    cfg: ServingSettings,
    config_path: Path,
    pretty: bool,
) -> ServingResult:
    """mode=sglang: pip install + subprocess + health-check."""
    import os

    cwd = Path(os.getcwd())
    if cfg.install_deps:
        sglang_launcher.ensure_sglang_installed(pretty=pretty)

    handle = sglang_launcher.start_server(cfg.sglang, bootstrap_dir=cwd, pretty=pretty)

    api_base = cfg.api_base or f"http://localhost:{cfg.sglang.port}/v1"
    api_key = cfg.api_key or cfg.sglang.api_key
    model_name = cfg.model_name or cfg.sglang.served_model_name

    health = wait_until_ready(
        api_base=api_base,
        api_key=api_key,
        expected_model=cfg.sglang.served_model_name,
        timeout_sec=cfg.health_check_timeout_sec,
        interval_sec=cfg.health_check_interval_sec,
        pretty=pretty,
    )
    if not health:
        return ServingResult(
            mode="sglang",
            api_base=api_base,
            model_name=model_name,
            patch=None,
            started=handle.proc is not None,
            healthy=False,
            detail=f"sglang health-check failed: {health.detail}",
        )

    settings = ServingSettings(
        **{
            **cfg.__dict__,
            "api_base": api_base,
            "api_key": api_key,
            "model_name": model_name,
        }
    )
    patch = render(config_path, settings, backup=True, pretty=pretty)
    return ServingResult(
        mode="sglang",
        api_base=api_base,
        model_name=model_name,
        patch=patch,
        started=True,
        healthy=True,
        detail=f"sglang ok, models={health.models}",
        extras={"pid": handle.proc.pid if handle.proc else None},
    )


def _bootstrap_ollama(
    cfg: ServingSettings,
    config_path: Path,
    pretty: bool,
) -> ServingResult:
    """mode=ollama: проверка/установка + serve + pull + health-check."""
    cwd = Path.cwd()
    handle = ollama_launcher.start_server(cfg.ollama, bootstrap_dir=cwd, pretty=pretty)
    if cfg.install_deps:
        try:
            ollama_launcher.ensure_model_pulled(cfg.ollama, pretty=pretty)
        except Exception as exc:
            return ServingResult(
                mode="ollama",
                api_base=f"http://{cfg.ollama.host}:{cfg.ollama.port}/v1",
                model_name=cfg.ollama.model,
                patch=None,
                started=True,
                healthy=False,
                detail=f"ollama pull failed: {exc}",
            )

    api_base = cfg.api_base or f"http://{cfg.ollama.host}:{cfg.ollama.port}/v1"
    api_key = cfg.api_key or cfg.ollama.api_key
    model_name = cfg.model_name or cfg.ollama.model

    health = wait_until_ready(
        api_base=api_base,
        api_key=api_key,
        expected_model=cfg.ollama.model,
        timeout_sec=cfg.health_check_timeout_sec,
        interval_sec=cfg.health_check_interval_sec,
        pretty=pretty,
    )
    if not health:
        return ServingResult(
            mode="ollama",
            api_base=api_base,
            model_name=model_name,
            patch=None,
            started=True,
            healthy=False,
            detail=f"ollama health-check failed: {health.detail}",
        )

    settings = ServingSettings(
        **{
            **cfg.__dict__,
            "api_base": api_base,
            "api_key": api_key,
            "model_name": model_name,
        }
    )
    patch = render(config_path, settings, backup=True, pretty=pretty)
    return ServingResult(
        mode="ollama",
        api_base=api_base,
        model_name=model_name,
        patch=patch,
        started=True,
        healthy=True,
        detail=f"ollama ok, models={health.models}",
        extras={"pid": handle.proc.pid if handle.proc else None},
    )


def _bootstrap_external(
    cfg: ServingSettings,
    config_path: Path,
    pretty: bool,
) -> ServingResult:
    """mode=external: только health-check существующего сервера (например,
    sglang вручную запущенный на GPU-сервере через tmux)."""
    if not cfg.api_base:
        return ServingResult(
            mode="external",
            api_base="",
            model_name=cfg.model_name,
            patch=None,
            started=False,
            healthy=False,
            detail="mode=external but serving.api_base is empty",
        )
    health = wait_until_ready(
        api_base=cfg.api_base,
        api_key=cfg.api_key,
        expected_model=cfg.model_name,
        timeout_sec=cfg.health_check_timeout_sec,
        interval_sec=cfg.health_check_interval_sec,
        pretty=pretty,
    )
    if not health:
        return ServingResult(
            mode="external",
            api_base=cfg.api_base,
            model_name=cfg.model_name,
            patch=None,
            started=False,
            healthy=False,
            detail=f"external server not healthy: {health.detail}",
        )
    patch = render(config_path, cfg, backup=True, pretty=pretty)
    return ServingResult(
        mode="external",
        api_base=cfg.api_base,
        model_name=cfg.model_name,
        patch=patch,
        started=False,
        healthy=True,
        detail=f"external server ok, models={health.models}",
    )


def ensure_serving(
    config_path: Path | None = None,
    pretty: bool = True,
) -> ServingResult | None:
    """Главная точка входа. Возвращает None если mode=off.

    Никогда не бросает исключение (ошибки печатаются в stderr); Nanobot
    должен стартовать даже если LLM-сервер не поднялся.
    """
    try:
        cfg = load_serving_config(config_path=config_path)
    except Exception as exc:
        if pretty:
            print(f"[serving] failed to load config: {exc}")
        return ServingResult(
            mode="error",
            api_base="",
            model_name="",
            patch=None,
            started=False,
            healthy=False,
            detail=f"config load: {exc}",
        )

    if cfg.mode == "off":
        if pretty:
            print("[serving] mode=off; skipping LLM bootstrap")
        return None

    config_path = Path(config_path) if config_path else (
        Path(__file__).resolve().parents[2] / "config.json"
    )

    if pretty:
        print(f"[serving] mode={cfg.mode}; bootstrapping...")

    try:
        if cfg.mode == "sglang":
            return _bootstrap_sglang(cfg, config_path, pretty)
        if cfg.mode == "ollama":
            return _bootstrap_ollama(cfg, config_path, pretty)
        if cfg.mode == "external":
            return _bootstrap_external(cfg, config_path, pretty)
        return ServingResult(
            mode=cfg.mode,
            api_base="",
            model_name="",
            patch=None,
            started=False,
            healthy=False,
            detail=f"unsupported mode: {cfg.mode}",
        )
    except Exception as exc:
        traceback.print_exc()
        return ServingResult(
            mode=cfg.mode,
            api_base="",
            model_name="",
            patch=None,
            started=False,
            healthy=False,
            detail=f"exception: {exc}",
        )


def stop_serving(config_path: Path | None = None, pretty: bool = True) -> dict[str, bool]:
    """Остановить sglang/ollama (если запускались). Для CLI и shutdown."""
    cfg = load_serving_config(config_path=config_path)
    out: dict[str, bool] = {}
    cwd = Path.cwd()
    if cfg.mode in ("sglang", "auto") or cfg.sglang.pid_file:
        out["sglang"] = sglang_launcher.stop(
            cwd / cfg.sglang.pid_file, pretty=pretty
        )
    if cfg.mode == "ollama":
        out["ollama"] = ollama_launcher.stop(
            cwd / cfg.ollama.pid_file, pretty=pretty
        )
    return out
