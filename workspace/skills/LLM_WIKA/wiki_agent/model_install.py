"""Reuse an existing BGE-M3 first; download only if no local model exists."""

from __future__ import annotations

import json
import re
from pathlib import Path

from .config import EMBEDDING_DIRECTORY, EMBEDDING_REPO, Settings, embedding_files_present, find_local_embedding_model
from .errors import ConfigurationError
from .semantic import _prepare_runtime_directory

INSTALL_MARKER = "llm_wiki_model.json"


def install_embedding_model(settings: Settings, *, revision: str = "main") -> Path:
    """Resolve the selected revision to an immutable commit before downloading.

    No custom Python from the model repository is downloaded or executed.
    Complete existing models are reused, not copied or overwritten.
    """
    target = settings.root / EMBEDDING_DIRECTORY
    configured = Path(settings.embedding_model).expanduser()
    if embedding_files_present(configured):
        return configured
    existing = find_local_embedding_model(settings.root)
    if existing is not None:
        return existing
    if Path(settings.embedding_model).resolve() != target.resolve():
        raise ConfigurationError(
            "model install устанавливает BGE-M3 в стандартную папку. "
            "Уберите LLM_WIKI_EMBEDDING_MODEL из .env или задайте стандартный путь."
        )
    _prepare_runtime_directory(target, "локальная embedding-модель", settings.root)
    marker = target / INSTALL_MARKER
    if marker.exists():
        metadata = read_install_metadata(target)
        if (metadata["repo_id"] == EMBEDDING_REPO
                and embedding_files_present(target)):
            return target
        raise ConfigurationError("Локальная установка модели неполная; восстановите файлы snapshot указанного commit.")
    if not revision or not re.fullmatch(r"[A-Za-z0-9_.\-/]+", revision):
        raise ConfigurationError("Некорректная редакция модели.")
    try:
        from huggingface_hub import HfApi, snapshot_download
    except ImportError as exc:
        raise ConfigurationError("Установите requirements.txt перед model install.") from exc
    try:
        commit = HfApi().model_info(EMBEDDING_REPO, revision=revision).sha
        if not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{40}", commit):
            raise ConfigurationError("Hugging Face не вернул точный commit модели.")
        snapshot_download(
            repo_id=EMBEDDING_REPO,
            revision=commit,
            local_dir=str(target),
            allow_patterns=["config.json", "config_sentence_transformers.json", "modules.json",
                            "sentence_bert_config.json", "tokenizer*", "special_tokens_map.json",
                            "sentencepiece.bpe.model", "model.safetensors", "pytorch_model.bin",
                            "1_Pooling/config.json"],
        )
    except ConfigurationError:
        raise
    except Exception as exc:
        # Не выводим HTTP headers/токен Hugging Face из исключений.
        raise ConfigurationError(
            f"Не удалось скачать BGE-M3 ({type(exc).__name__}). "
            "Проверьте доступ к huggingface.co и повторите model install."
        ) from exc
    if not embedding_files_present(target):
        raise ConfigurationError("Скачивание неполное: нет конфигурации или весов.")
    metadata = {"repo_id": EMBEDDING_REPO, "revision": commit}
    with marker.open("x", encoding="utf-8") as stream:
        json.dump(metadata, stream, indent=2)
    return target


def read_install_metadata(path: Path) -> dict:
    marker = path / INSTALL_MARKER
    try:
        if marker.is_symlink():
            raise ValueError("symlink")
        metadata = json.loads(marker.read_text(encoding="utf-8"))
        if metadata.get("repo_id") != EMBEDDING_REPO or not re.fullmatch(r"[0-9a-f]{40}", metadata.get("revision", "")):
            raise ValueError("invalid metadata")
        return metadata
    except (OSError, ValueError, TypeError, AttributeError) as exc:
        raise ConfigurationError("Некорректная установка модели; выполните python3.12 -m wiki_agent model install.") from exc
