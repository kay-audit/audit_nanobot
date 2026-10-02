"""Конфигурация из окружения без хранения секретов в коде."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path

from .errors import ConfigurationError

EMBEDDING_REPO = "BAAI/bge-m3"
EMBEDDING_DIRECTORY = ".cache/llm-wiki/models/bge-m3"


def embedding_files_present(path: Path) -> bool:
    """Check a local snapshot without contacting Hugging Face."""
    try:
        return ((path / "config.json").is_file()
                and (path / "tokenizer.json").is_file()
                and any((path / name).is_file() and (path / name).stat().st_size > 0
                        for name in ("model.safetensors", "pytorch_model.bin")))
    except OSError:
        return False


def find_local_embedding_model(root: Path) -> Path | None:
    local = root / EMBEDDING_DIRECTORY
    if embedding_files_present(local):
        return local
    hf_home = Path(os.getenv("HF_HOME", str(Path.home() / ".cache/huggingface"))).expanduser()
    cache = Path(os.getenv("HF_HUB_CACHE", os.getenv("HUGGINGFACE_HUB_CACHE", str(hf_home / "hub")))).expanduser()
    repository = cache / "models--BAAI--bge-m3"
    ref = repository / "refs/main"
    try:
        revision = ref.read_text(encoding="utf-8").strip()
        if len(revision) == 40 and all(c in "0123456789abcdef" for c in revision):
            snapshot = repository / "snapshots" / revision
            if embedding_files_present(snapshot):
                return snapshot
        for snapshot in sorted((repository / "snapshots").iterdir()):
            if snapshot.is_dir() and embedding_files_present(snapshot):
                return snapshot
    except OSError:
        pass
    return None


def load_env_file(path: Path) -> None:
    """Загрузить простой KEY=VALUE файл, не выполняя shell-код."""

    if not path.exists():
        return
    if path.is_symlink():
        raise ConfigurationError(".env не должен быть символической ссылкой")
    for number, raw_line in enumerate(
        path.read_text(encoding="utf-8").splitlines(), 1
    ):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            raise ConfigurationError(
                f"Некорректная строка {number} в {path.name}: ожидается KEY=VALUE"
            )
        key, value = line.split("=", 1)
        key = key.strip()
        if key == "MINIMAX_API_KEY":
            raise ConfigurationError("MINIMAX_API_KEY нельзя хранить в .env; введите ключ через терминал.")
        value = value.strip()
        if not key or not key.replace("_", "").isalnum():
            raise ConfigurationError(
                f"Некорректное имя переменной в строке {number}: {key!r}"
            )
        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]
        os.environ.setdefault(key, value)


@dataclass(frozen=True)
class Settings:
    """Настройки контроллера и LLM-провайдера."""

    root: Path
    provider: str = "stub"
    model: str = "GigaChat-2-Pro"
    allow_external_context: bool = False
    verify_ssl_certs: bool = True
    ca_bundle_file: Path | None = None
    max_file_chars: int = 500_000
    max_context_chars: int = 120_000
    max_query_pages: int = 6
    max_hops: int = 3
    query_search: str = "faiss"
    embedding_model: str = (
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
    )
    embedding_device: str | None = None
    allow_embedding_download: bool = False
    faiss_top_k: int = 4
    faiss_min_score: float = 0.15

    @classmethod
    def from_root(cls, root: Path) -> "Settings":
        resolved_root = root.resolve()
        load_env_file(resolved_root / ".env")

        provider = os.getenv("LLM_WIKI_PROVIDER", "minimax").strip().lower()
        if provider not in {"stub", "gigachat", "gigachat_internal", "minimax"}:
            raise ConfigurationError(
                "LLM_WIKI_PROVIDER должен быть stub, gigachat "
                "gigachat_internal или minimax"
            )
        query_search = os.getenv(
            "LLM_WIKI_QUERY_SEARCH", "faiss"
        ).strip().lower()
        if query_search not in {"faiss", "lexical"}:
            raise ConfigurationError(
                "LLM_WIKI_QUERY_SEARCH должен быть faiss или lexical"
            )

        ca_name = "MINIMAX_CA_BUNDLE_FILE" if provider == "minimax" else "GIGACHAT_CA_BUNDLE_FILE"
        ca_raw = os.getenv(ca_name, "").strip()
        ca_bundle = Path(ca_raw).expanduser().resolve() if ca_raw else None
        if ca_bundle is not None and not ca_bundle.is_file():
            raise ConfigurationError(
                f"{ca_name} не найден: {ca_bundle}"
            )

        embedding_device_raw = os.getenv(
            "LLM_WIKI_EMBEDDING_DEVICE", "auto"
        ).strip().lower()
        if embedding_device_raw not in {"auto", "cpu", "cuda"}:
            raise ConfigurationError(
                "LLM_WIKI_EMBEDDING_DEVICE должен быть auto, cpu или cuda"
            )

        default_model = (
            "MiniMax-M2.7" if provider == "minimax" else
            "GigaChat-3-Ultra"
            if provider == "gigachat_internal"
            else "GigaChat-2-Pro"
        )
        default_embedding = find_local_embedding_model(resolved_root) or resolved_root / EMBEDDING_DIRECTORY
        return cls(
            root=resolved_root,
            provider=provider,
            model=os.getenv("MINIMAX_MODEL" if provider == "minimax" else "GIGACHAT_MODEL", default_model).strip()
            or default_model,
            allow_external_context=_env_bool(
                "LLM_WIKI_ALLOW_EXTERNAL_CONTEXT", False
            ),
            verify_ssl_certs=_env_bool("GIGACHAT_VERIFY_SSL_CERTS", True),
            ca_bundle_file=ca_bundle,
            max_file_chars=_env_int(
                "LLM_WIKI_MAX_FILE_CHARS", 500_000, minimum=10_000
            ),
            max_context_chars=_env_int(
                "LLM_WIKI_MAX_CONTEXT_CHARS", 120_000, minimum=20_000
            ),
            max_query_pages=_env_int(
                "LLM_WIKI_MAX_QUERY_PAGES", 6, minimum=1, maximum=30
            ),
            max_hops=_env_int(
                "LLM_WIKI_MAX_HOPS", 3, minimum=1, maximum=5
            ),
            query_search=query_search,
            embedding_model=os.getenv(
                "LLM_WIKI_EMBEDDING_MODEL",
                str(default_embedding),
            ).strip()
            or str(default_embedding),
            embedding_device=(
                None
                if embedding_device_raw == "auto"
                else embedding_device_raw
            ),
            allow_embedding_download=_env_bool(
                "LLM_WIKI_ALLOW_EMBEDDING_DOWNLOAD", False
            ),
            faiss_top_k=_env_int(
                "LLM_WIKI_FAISS_TOP_K", 4, minimum=1, maximum=30
            ),
            faiss_min_score=_env_float(
                "LLM_WIKI_FAISS_MIN_SCORE",
                0.15,
                minimum=-1.0,
                maximum=1.0,
            ),
        )

    @property
    def faiss_cache_dir(self) -> Path:
        return self.root / ".cache" / "llm-wiki" / "faiss"

    @property
    def embedding_cache_dir(self) -> Path:
        return self.root / ".cache" / "llm-wiki" / "models"

    @property
    def embedding_vector_cache_dir(self) -> Path:
        return self.root / ".cache" / "llm-wiki" / "embeddings"

    @property
    def credentials_configured(self) -> bool:
        if self.provider == "minimax":
            return bool(os.getenv("MINIMAX_API_KEY", "").strip())
        if self.provider == "gigachat_internal":
            return bool(os.getenv("JPY_API_TOKEN", "").strip())
        return bool(
            os.getenv("GIGACHAT_CREDENTIALS", "").strip()
            or os.getenv("GIGACHAT_ACCESS_TOKEN", "").strip()
        )

    @property
    def python_supported_by_official_sdk(self) -> bool:
        return (3, 8) <= sys.version_info[:2] <= (3, 13)

    @property
    def python_supported_by_agent_stack(self) -> bool:
        if self.provider == "minimax":
            # MiniMax uses requests, not the legacy GigaChat SDK. Determine
            # compatibility from installed packages and the project's runtime.
            if sys.version_info[:2] != (3, 12):
                return False
            from importlib.metadata import PackageNotFoundError, metadata
            try:
                from packaging.specifiers import InvalidSpecifier, SpecifierSet
                from packaging.version import Version
            except ImportError:
                return False
            version = Version(".".join(str(part) for part in sys.version_info[:3]))
            for package in ("requests", "torch", "transformers", "sentence-transformers",
                            "faiss-cpu", "huggingface-hub", "numpy", "safetensors",
                            "sentencepiece", "pypdf", "python-pptx", "python-docx"):
                try:
                    required = metadata(package).get("Requires-Python")
                except PackageNotFoundError:
                    continue  # Missing dependencies are reported separately.
                try:
                    if required and version not in SpecifierSet(required):
                        return False
                except InvalidSpecifier:
                    return False
            return True
        return (3, 10) <= sys.version_info[:2] <= (3, 13)


def _env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    normalized = raw.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ConfigurationError(
        f"{name} должен быть true/false, получено: {raw!r}"
    )


def _env_int(
    name: str,
    default: int,
    *,
    minimum: int,
    maximum: int | None = None,
) -> int:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} должен быть целым числом") from exc
    if value < minimum or (maximum is not None and value > maximum):
        upper = f" и не больше {maximum}" if maximum is not None else ""
        raise ConfigurationError(
            f"{name} должен быть не меньше {minimum}{upper}"
        )
    return value


def _env_float(
    name: str,
    default: float,
    *,
    minimum: float,
    maximum: float,
) -> float:
    raw = os.getenv(name)
    if raw is None or not raw.strip():
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ConfigurationError(f"{name} должен быть числом") from exc
    if not minimum <= value <= maximum:
        raise ConfigurationError(
            f"{name} должен быть от {minimum} до {maximum}"
        )
    return value
