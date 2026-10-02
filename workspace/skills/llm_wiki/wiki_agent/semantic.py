"""Локальный семантический поиск по Wiki и производным поисковым карточкам."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, Sequence

from .errors import ConfigurationError, ValidationError
from .wiki import WikiDocument


INDEX_FORMAT_VERSION = 2
INDEX_FILENAME = "pages.faiss"
METADATA_FILENAME = "pages.json"
SEARCHABLE_PREFIXES = (
    "wiki/pages/",
    ".cache/llm-wiki/jira-confluence/cards/",
)


class Embedder(Protocol):
    """Минимальный интерфейс локальной модели эмбеддингов."""

    model_name: str

    def encode_documents(self, texts: Sequence[str]) -> Any:
        """Вернуть нормализованные float32-векторы документов."""

    def encode_query(self, text: str) -> Any:
        """Вернуть один нормализованный float32-вектор запроса."""


@dataclass(frozen=True)
class SemanticHit:
    path: str
    title: str
    score: float


@dataclass(frozen=True)
class IndexStatus:
    state: str
    message: str
    document_count: int = 0
    reused_embeddings: int = 0
    updated_embeddings: int = 0


class SentenceTransformerEmbedder:
    """Локальный multilingual Sentence Transformer без передачи Wiki наружу."""

    def __init__(
        self,
        model_name: str,
        *,
        model_cache_dir: Path,
        workspace_root: Path,
        allow_download: bool,
        device: str | None = None,
    ) -> None:
        try:
            from sentence_transformers import SentenceTransformer
            from transformers.utils import logging as transformers_logging
        except ImportError as exc:
            raise ConfigurationError(
                "Не установлен sentence-transformers. Выполните "
                "`python3.12 -m pip install -r requirements-agent.txt`."
            ) from exc

        transformers_logging.disable_progress_bar()
        self.model_name = model_name
        _prepare_runtime_directory(
            model_cache_dir,
            "кэш embedding-модели",
            workspace_root,
        )
        model_kwargs: dict[str, Any] = {
            "cache_folder": str(model_cache_dir),
            "local_files_only": not allow_download,
            "trust_remote_code": False,
        }
        if device is not None:
            model_kwargs["device"] = device
        try:
            self._model = SentenceTransformer(model_name, **model_kwargs)
        except Exception as exc:
            mode = (
                "загрузить или открыть"
                if allow_download
                else "открыть из локального кэша"
            )
            raise ConfigurationError(
                f"Не удалось {mode} embedding-модель {model_name!r}. "
                "Для первого запуска выполните "
                "`python3.12 -m wiki_agent model install`, затем index build. "
                f"Причина: {type(exc).__name__}."
            ) from exc

    def encode_documents(self, texts: Sequence[str]) -> Any:
        method = getattr(self._model, "encode_document", self._model.encode)
        vectors = method(
            list(texts),
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=4,
        )
        return _as_normalized_matrix(vectors)

    def encode_query(self, text: str) -> Any:
        method = getattr(self._model, "encode_query", self._model.encode)
        vector = method(
            text,
            convert_to_numpy=True,
            normalize_embeddings=True,
            show_progress_bar=False,
            batch_size=4,
        )
        return _as_normalized_matrix(vector)[0]


class FaissPageIndex:
    """Персистентный FAISS IndexFlatIP с проверяемой картой строк."""

    def __init__(
        self,
        *,
        workspace_root: Path,
        cache_dir: Path,
        model_cache_dir: Path,
        model_name: str,
        vector_cache_dir: Path | None = None,
        embedder: Embedder | None = None,
        allow_model_download: bool = False,
        embedding_device: str | None = None,
    ) -> None:
        self.workspace_root = Path(os.path.abspath(workspace_root))
        self.cache_dir = Path(os.path.abspath(cache_dir))
        self.model_cache_dir = Path(os.path.abspath(model_cache_dir))
        self.vector_cache_dir = Path(
            os.path.abspath(
                vector_cache_dir
                if vector_cache_dir is not None
                else self.cache_dir.parent / "embeddings"
            )
        )
        self.model_name = model_name
        self._embedder = embedder
        self.allow_model_download = allow_model_download
        self.embedding_device = embedding_device

    @property
    def index_path(self) -> Path:
        return self.cache_dir / INDEX_FILENAME

    @property
    def metadata_path(self) -> Path:
        return self.cache_dir / METADATA_FILENAME

    def status(self, pages: Sequence[WikiDocument]) -> IndexStatus:
        try:
            _reject_symlink_chain(
                self.cache_dir,
                "каталог FAISS",
                self.workspace_root,
            )
        except ValidationError as exc:
            return IndexStatus("invalid", str(exc))
        if not self.index_path.is_file() or not self.metadata_path.is_file():
            return IndexStatus(
                "missing",
                "FAISS-индекс ещё не построен",
            )
        try:
            metadata = self._read_metadata()
        except ValidationError as exc:
            return IndexStatus("invalid", str(exc))
        if metadata["model"] != self.model_name:
            return IndexStatus(
                "stale",
                "embedding-модель изменилась; индекс нужно перестроить",
                len(metadata["documents"]),
            )
        if metadata["corpus_sha256"] != _corpus_sha256(
            pages, self.model_name
        ):
            return IndexStatus(
                "stale",
                "тематические страницы изменились; индекс нужно перестроить",
                len(metadata["documents"]),
            )
        return IndexStatus(
            "current",
            "FAISS-индекс актуален",
            len(metadata["documents"]),
        )

    def build(self, pages: Sequence[WikiDocument]) -> IndexStatus:
        ordered = _ordered_pages(pages)
        if not ordered:
            raise ValidationError(
                "Нельзя построить FAISS-индекс: в wiki/pages нет страниц"
            )
        self._prepare_cache_dir()
        embedder = self._get_embedder()
        vectors, reused_count, updated_count = self._document_vectors(
            ordered, embedder
        )
        if len(vectors) != len(ordered) or len(vectors.shape) != 2:
            raise ValidationError(
                "Embedding-модель вернула некорректную матрицу документов"
            )
        # На macOS сначала выполняем Torch inference, затем импортируем FAISS:
        # обратный порядок может конфликтовать в нативных runtime-библиотеках.
        faiss = _import_faiss()
        index = faiss.IndexFlatIP(int(vectors.shape[1]))
        index.add(vectors)

        metadata = {
            "version": INDEX_FORMAT_VERSION,
            "model": self.model_name,
            "dimension": int(vectors.shape[1]),
            "corpus_sha256": _corpus_sha256(ordered, self.model_name),
            "documents": [
                {
                    "path": page.path,
                    "title": page.title,
                    "sha256": hashlib.sha256(
                        page.content.encode("utf-8")
                    ).hexdigest(),
                }
                for page in ordered
            ],
        }
        index_temp = _temporary_path(self.cache_dir, ".faiss")
        metadata_temp = _temporary_path(self.cache_dir, ".json")
        try:
            faiss.write_index(index, str(index_temp))
            metadata_temp.write_text(
                json.dumps(metadata, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            os.replace(index_temp, self.index_path)
            os.replace(metadata_temp, self.metadata_path)
        finally:
            index_temp.unlink(missing_ok=True)
            metadata_temp.unlink(missing_ok=True)
        return IndexStatus(
            "current",
            "FAISS-индекс обновлён",
            len(ordered),
            reused_embeddings=reused_count,
            updated_embeddings=updated_count,
        )

    def _document_vectors(
        self,
        documents: Sequence[WikiDocument],
        embedder: Embedder,
    ) -> tuple[Any, int, int]:
        """Reuse vectors by embedding-input SHA-256 and model name."""

        try:
            import numpy as np
        except ImportError as exc:
            raise ConfigurationError(
                "Не установлен numpy; переустановите requirements-agent.txt"
            ) from exc

        model_key = hashlib.sha256(self.model_name.encode("utf-8")).hexdigest()
        model_dir = self.vector_cache_dir / model_key
        _prepare_runtime_directory(
            model_dir,
            "кэш векторов",
            self.workspace_root,
        )
        texts = [_document_embedding_text(item) for item in documents]
        text_hashes = [
            hashlib.sha256(text.encode("utf-8")).hexdigest()
            for text in texts
        ]
        vectors: list[Any | None] = [None] * len(documents)
        missing_positions: list[int] = []
        for position, text_hash in enumerate(text_hashes):
            vector_path = model_dir / f"{text_hash}.npy"
            if vector_path.is_symlink():
                raise ValidationError(
                    "Файлы кэша векторов не должны быть symlink"
                )
            try:
                cached = np.load(vector_path, allow_pickle=False)
                vectors[position] = _as_normalized_matrix(cached)[0]
            except (OSError, ValueError, ValidationError):
                missing_positions.append(position)

        if missing_positions:
            calculated = embedder.encode_documents(
                [texts[position] for position in missing_positions]
            )
            if len(calculated) != len(missing_positions):
                raise ValidationError(
                    "Embedding-модель вернула некорректную матрицу документов"
                )
            calculated = _as_normalized_matrix(calculated)
            for row, position in enumerate(missing_positions):
                vector = calculated[row]
                vectors[position] = vector
                vector_path = model_dir / f"{text_hashes[position]}.npy"
                vector_temp = _temporary_path(model_dir, ".npy")
                try:
                    np.save(vector_temp, vector, allow_pickle=False)
                    os.replace(vector_temp, vector_path)
                finally:
                    vector_temp.unlink(missing_ok=True)

        if any(vector is None for vector in vectors):
            raise ValidationError("Не удалось собрать векторы документов")
        matrix = _as_normalized_matrix(np.stack(vectors))
        return (
            matrix,
            len(documents) - len(missing_positions),
            len(missing_positions),
        )

    def search(
        self,
        pages: Sequence[WikiDocument],
        question: str,
        *,
        limit: int,
        min_score: float,
    ) -> list[SemanticHit]:
        if not question.strip():
            raise ValidationError("Поисковый запрос не может быть пустым")
        current = self.status(pages)
        if current.state != "current":
            raise ConfigurationError(
                f"{current.message}. Выполните "
                "`python3.12 -m wiki_agent index build`."
            )
        metadata = self._read_metadata()
        query_vector = self._get_embedder().encode_query(question)
        query_matrix = _as_normalized_matrix(query_vector)
        # См. комментарий в build: FAISS импортируется после Torch inference.
        faiss = _import_faiss()
        try:
            index = faiss.read_index(str(self.index_path))
        except Exception as exc:
            raise ValidationError(
                "FAISS-индекс повреждён; выполните "
                "`python3.12 -m wiki_agent index build`"
            ) from exc
        documents = metadata["documents"]
        if index.ntotal != len(documents):
            raise ValidationError(
                "FAISS-индекс не совпадает с картой документов; "
                "выполните `python3.12 -m wiki_agent index build`"
            )

        if int(query_matrix.shape[1]) != int(metadata["dimension"]):
            raise ValidationError(
                "Размерность embedding-модели не совпадает с индексом; "
                "перестройте FAISS-индекс"
            )
        count = min(max(limit, 1), len(documents))
        scores, identifiers = index.search(query_matrix, count)
        hits: list[SemanticHit] = []
        for score, identifier in zip(scores[0], identifiers[0]):
            row = int(identifier)
            value = float(score)
            if row < 0 or value < min_score:
                continue
            item = documents[row]
            hits.append(
                SemanticHit(
                    path=item["path"],
                    title=item["title"],
                    score=value,
                )
            )
        return hits

    def _get_embedder(self) -> Embedder:
        if self._embedder is None:
            self._embedder = SentenceTransformerEmbedder(
                self.model_name,
                model_cache_dir=self.model_cache_dir,
                workspace_root=self.workspace_root,
                allow_download=self.allow_model_download,
                device=self.embedding_device,
            )
        return self._embedder

    def _prepare_cache_dir(self) -> None:
        _prepare_runtime_directory(
            self.cache_dir,
            "каталог FAISS",
            self.workspace_root,
        )

    def _read_metadata(self) -> dict[str, Any]:
        if self.metadata_path.is_symlink() or self.index_path.is_symlink():
            raise ValidationError("Файлы FAISS-индекса не должны быть symlink")
        try:
            value = json.loads(
                self.metadata_path.read_text(encoding="utf-8")
            )
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise ValidationError(
                "Метаданные FAISS-индекса повреждены"
            ) from exc
        if not isinstance(value, dict):
            raise ValidationError("Некорректные метаданные FAISS-индекса")
        if value.get("version") != INDEX_FORMAT_VERSION:
            raise ValidationError(
                "Версия FAISS-индекса не поддерживается; перестройте индекс"
            )
        if not isinstance(value.get("model"), str):
            raise ValidationError("В метаданных FAISS отсутствует model")
        if not isinstance(value.get("dimension"), int):
            raise ValidationError("В метаданных FAISS отсутствует dimension")
        if not isinstance(value.get("corpus_sha256"), str):
            raise ValidationError(
                "В метаданных FAISS отсутствует corpus_sha256"
            )
        documents = value.get("documents")
        if not isinstance(documents, list) or not all(
            isinstance(item, dict)
            and isinstance(item.get("path"), str)
            and item["path"].startswith(SEARCHABLE_PREFIXES)
            and isinstance(item.get("title"), str)
            and isinstance(item.get("sha256"), str)
            for item in documents
        ):
            raise ValidationError(
                "Некорректная карта документов FAISS-индекса"
            )
        return value


def _document_embedding_text(document: WikiDocument) -> str:
    aliases = ", ".join(document.aliases)
    tags = ", ".join(document.tags)
    return (
        f"title: {document.title}\n"
        f"aliases: {aliases}\n"
        f"tags: {tags}\n\n"
        f"{document.body[:40_000]}"
    )


def _ordered_pages(
    pages: Sequence[WikiDocument],
) -> list[WikiDocument]:
    return sorted(
        (
            page
            for page in pages
            if page.path.startswith(SEARCHABLE_PREFIXES)
        ),
        key=lambda page: page.path,
    )


def _corpus_sha256(
    pages: Sequence[WikiDocument],
    model_name: str,
) -> str:
    digest = hashlib.sha256()
    digest.update(f"{INDEX_FORMAT_VERSION}\0{model_name}\0".encode())
    for page in _ordered_pages(pages):
        digest.update(page.path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(page.content.encode("utf-8"))
        digest.update(b"\0")
    return digest.hexdigest()


def _as_normalized_matrix(value: Any) -> Any:
    try:
        import numpy as np
    except ImportError as exc:
        raise ConfigurationError(
            "Не установлен numpy; переустановите requirements-agent.txt"
        ) from exc
    matrix = np.asarray(value, dtype="float32")
    if matrix.ndim == 1:
        matrix = matrix.reshape(1, -1)
    if matrix.ndim != 2 or matrix.shape[1] < 1:
        raise ValidationError("Embedding-модель вернула пустой вектор")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    if np.any(norms == 0):
        raise ValidationError("Embedding-модель вернула нулевой вектор")
    return np.ascontiguousarray(matrix / norms, dtype="float32")


def _import_faiss() -> Any:
    try:
        import faiss
    except ImportError as exc:
        raise ConfigurationError(
            "Не установлен faiss-cpu. Выполните "
            "`python3.12 -m pip install -r requirements-agent.txt`."
        ) from exc
    return faiss


def _temporary_path(directory: Path, suffix: str) -> Path:
    descriptor, raw_path = tempfile.mkstemp(
        prefix=".building-",
        suffix=suffix,
        dir=directory,
    )
    os.close(descriptor)
    return Path(raw_path)


def _prepare_runtime_directory(
    path: Path,
    label: str,
    workspace_root: Path,
) -> None:
    absolute = Path(os.path.abspath(path))
    _reject_symlink_chain(absolute, label, workspace_root)
    absolute.mkdir(parents=True, exist_ok=True)
    if not absolute.is_dir():
        raise ValidationError(f"{label} не является каталогом: {absolute}")


def _reject_symlink_chain(
    path: Path,
    label: str,
    workspace_root: Path,
) -> None:
    absolute = Path(os.path.abspath(path))
    boundary = Path(os.path.abspath(workspace_root))
    if not absolute.is_relative_to(boundary):
        raise ValidationError(f"{label} должен находиться внутри workspace")
    candidate = absolute
    while True:
        if candidate.is_symlink():
            raise ValidationError(f"{label} не должен проходить через symlink")
        if candidate == boundary:
            break
        candidate = candidate.parent
