"""scripts.serving.render_config - патч config.json / project.json.

После успешного поднятия LLM-сервера нужно подменить ``apiBase`` в
``providers.<provider_alias>`` и ``model`` в ``agents.defaults`` — чтобы
Nanobot начал слать запросы на наш сервер.

Файл config.json редактируется аккуратно (через ``json`` round-trip, без
PowerShell-encoding багов), атомарно через ``temp + os.replace``.

ВАЖНО: scripts.serving НЕ МОДИФИЦИРУЕТ код агента и не подменяет провайдера в
реестре nanobot. Используем существующего ``vllm``/``ollama``/``custom``
провайдера — просто перенаправляем его apiBase.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .config import ServingSettings


@dataclass
class ConfigPatch:
    config_path: Path
    provider_alias: str
    api_base: str
    api_key: str
    model_name: str
    backup_path: Path | None

    def __str__(self) -> str:
        return (
            f"ConfigPatch(config={self.config_path}, "
            f"provider={self.provider_alias!r}, api_base={self.api_base}, "
            f"model={self.model_name!r})"
        )


def _read_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{path}: invalid JSON: {exc}") from exc


def _write_json_atomic(path: Path, doc: dict[str, Any]) -> None:
    """Записать JSON атомарно (temp + os.replace). Без BOM, LF-only."""
    payload = json.dumps(doc, ensure_ascii=False, indent=2) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=f".{path.name}.",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(payload)
            f.flush()
            try:
                os.fsync(f.fileno())
            except OSError:
                pass
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


def render(
    config_path: Path,
    settings: ServingSettings,
    backup: bool = True,
    pretty: bool = True,
) -> ConfigPatch:
    """Подставить api_base/api_key/model в config.json.

    Создаёт бэкап ``config.json.bak-<ts>`` если ``backup=True`` и файл меняется.
    Если файл не существует — создаёт пустой ``{}`` и пишет минимальный конфиг
    с одной лишь секцией ``providers.<alias>`` (Nanobot без остального не
    стартанёт, но bootstrap это и не предполагает).
    """
    config_path = Path(config_path)
    doc = _read_json(config_path)

    backup_path: Path | None = None
    if backup and config_path.exists():
        import time

        ts = time.strftime("%Y%m%d-%H%M%S")
        backup_path = config_path.with_suffix(config_path.suffix + f".bak-{ts}")
        shutil.copy2(config_path, backup_path)
        if pretty:
            print(f"[serving] backup -> {backup_path}")

    providers = doc.setdefault("providers", {})
    if not isinstance(providers, dict):
        providers = {}
        doc["providers"] = providers

    alias = settings.provider_alias or "vllm"
    target = providers.setdefault(alias, {})
    if not isinstance(target, dict):
        target = {}
        providers[alias] = target

    target["apiBase"] = settings.api_base
    target["apiKey"] = settings.api_key or target.get("apiKey") or "EMPTY"

    agents = doc.setdefault("agents", {})
    if not isinstance(agents, dict):
        agents = {}
        doc["agents"] = agents
    defaults = agents.setdefault("defaults", {})
    if not isinstance(defaults, dict):
        defaults = {}
        agents["defaults"] = defaults
    if settings.model_name:
        defaults["model"] = settings.model_name

    _write_json_atomic(config_path, doc)
    if pretty:
        print(f"[serving] patched {config_path}:")
        print(f"  providers.{alias}.apiBase = {settings.api_base}")
        print(f"  providers.{alias}.apiKey  = {target['apiKey']}")
        if settings.model_name:
            print(f"  agents.defaults.model     = {settings.model_name}")
    return ConfigPatch(
        config_path=config_path,
        provider_alias=alias,
        api_base=settings.api_base,
        api_key=str(target["apiKey"]),
        model_name=settings.model_name,
        backup_path=backup_path,
    )


def restore_backup(backup_path: Path, config_path: Path, pretty: bool = True) -> bool:
    """Восстановить config.json из бэкапа (для CLI rollback)."""
    if not backup_path.exists():
        return False
    shutil.copy2(backup_path, config_path)
    if pretty:
        print(f"[serving] restored {config_path} from {backup_path}")
    return True
