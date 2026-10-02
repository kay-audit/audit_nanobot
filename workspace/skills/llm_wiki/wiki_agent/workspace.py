"""Единственная точка контролируемого доступа к workspace."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
import tempfile
import unicodedata
from pathlib import Path
from typing import Iterable

from .errors import SecurityError, ValidationError


READ_ONLY_SOURCE_ROOT = "raw/sources"
INGEST_WRITE_ROOTS = ("wiki/pages", "wiki/sources")
INGEST_WRITE_FILES = ("wiki/index.md", "wiki/log.md")


class Workspace:
    """Безопасные чтение, проверка путей и атомарные записи."""

    def __init__(self, root: Path, *, max_file_chars: int = 500_000) -> None:
        self.root = root.resolve()
        self.max_file_chars = max_file_chars
        self._validate_layout()

    @classmethod
    def discover(
        cls, start: Path | None = None, *, max_file_chars: int = 500_000
    ) -> "Workspace":
        current = (start or Path.cwd()).resolve()
        for candidate in (current, *current.parents):
            if (
                (candidate / "AGENTS.md").is_file()
                and (candidate / "wiki/index.md").is_file()
            ):
                return cls(candidate, max_file_chars=max_file_chars)
        raise ValidationError(
            "Не найден корень LLM-Wiki: ожидаются AGENTS.md и wiki/index.md"
        )

    def _validate_layout(self) -> None:
        required = (
            "AGENTS.md",
            "schema/operations/query.md",
            "schema/operations/lint.md",
            "schema/operations/ingest.md",
            "wiki/index.md",
            "wiki/log.md",
            "wiki/pages",
            "wiki/sources",
            "raw/sources",
            "raw/extracted",
            "proposals",
            "reports/lint",
        )
        missing = [item for item in required if not (self.root / item).exists()]
        if missing:
            raise ValidationError(
                "Неполная структура LLM-Wiki: отсутствуют "
                + ", ".join(missing)
            )

    def resolve(
        self,
        relative: str | Path,
        *,
        must_exist: bool = False,
        allowed_roots: Iterable[str] | None = None,
    ) -> Path:
        raw_path = Path(relative)
        if raw_path.is_absolute():
            raise SecurityError(f"Абсолютный путь запрещён: {relative}")
        if not raw_path.parts or any(part == ".." for part in raw_path.parts):
            raise SecurityError(f"Выход за workspace запрещён: {relative}")

        self._reject_path_alias(raw_path.as_posix())
        candidate = self.root / raw_path
        try:
            resolved = candidate.resolve(strict=must_exist)
        except FileNotFoundError as exc:
            raise ValidationError(f"Файл не найден: {relative}") from exc
        if not resolved.is_relative_to(self.root):
            raise SecurityError(f"Путь выходит за workspace: {relative}")

        if allowed_roots is not None:
            allowed = [self.root / Path(item) for item in allowed_roots]
            if not any(
                resolved == base.resolve()
                or resolved.is_relative_to(base.resolve())
                for base in allowed
            ):
                raise SecurityError(
                    f"Путь не входит в разрешённую область: {relative}"
                )
        return resolved

    def relative(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(self.root).as_posix()
        except ValueError as exc:
            raise SecurityError(f"Путь вне workspace: {path}") from exc

    def read_text(
        self,
        relative: str | Path,
        *,
        allowed_roots: Iterable[str] | None = None,
        max_chars: int | None = None,
    ) -> str:
        path = self.resolve(
            relative, must_exist=True, allowed_roots=allowed_roots
        )
        if not path.is_file():
            raise ValidationError(f"Ожидался файл: {relative}")
        limit = max_chars if max_chars is not None else self.max_file_chars
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise ValidationError(
                f"Файл не является читаемым UTF-8 текстом: {relative}"
            ) from exc
        if len(text) > limit:
            raise ValidationError(
                f"Файл превышает лимит {limit} символов: {relative}"
            )
        return text

    def read_text_prefix(
        self,
        relative: str | Path,
        *,
        allowed_roots: Iterable[str] | None = None,
        max_chars: int,
    ) -> str:
        """Прочитать только начало UTF-8 файла, не загружая его целиком."""

        path = self.resolve(
            relative, must_exist=True, allowed_roots=allowed_roots
        )
        if not path.is_file():
            raise ValidationError(f"Ожидался файл: {relative}")
        try:
            with path.open("r", encoding="utf-8") as handle:
                return handle.read(max_chars)
        except UnicodeDecodeError as exc:
            raise ValidationError(
                f"Файл не является читаемым UTF-8 текстом: {relative}"
            ) from exc

    def list_markdown(self, relative_dir: str) -> list[str]:
        directory = self.resolve(relative_dir, must_exist=True)
        if not directory.is_dir():
            raise ValidationError(f"Ожидался каталог: {relative_dir}")
        result: list[str] = []
        for path in sorted(directory.glob("*.md")):
            if path.is_symlink():
                raise SecurityError(f"Symlink в Wiki запрещён: {path}")
            resolved = path.resolve()
            if not resolved.is_relative_to(directory.resolve()):
                raise SecurityError(f"Symlink выходит из каталога: {path}")
            result.append(self.relative(resolved))
        return result

    def source_path(self, relative: str | Path) -> Path:
        raw = str(relative)
        normalized = Path(raw).as_posix()
        parts = Path(normalized).parts
        if raw != normalized or len(parts) != 3 or parts[:2] != ("raw", "sources"):
            raise SecurityError(
                "Источник должен быть прямым файлом raw/sources/<имя>: "
                f"{relative}"
            )
        _validate_filename(parts[2], label="имя источника")
        path = self.resolve(
            normalized,
            must_exist=True,
            allowed_roots=(READ_ONLY_SOURCE_ROOT,),
        )
        if not path.is_file():
            raise ValidationError(f"Источник не является файлом: {relative}")
        return path

    def source_text_path(self, source_relative: str) -> str:
        source = self.source_path(source_relative)
        suffix = source.suffix.lower()
        if suffix in {".md", ".markdown", ".txt"}:
            return self.relative(source)
        extracted = self.root / "raw/extracted" / f"{source.name}.md"
        if extracted.is_symlink():
            raise SecurityError(
                "Текстовая копия не должна быть symlink: "
                f"raw/extracted/{source.name}.md"
            )
        if not extracted.is_file():
            raise ValidationError(
                "Для бинарного источника нет текстовой копии: "
                f"raw/extracted/{source.name}.md. Запустите ingest для "
                "автоматического извлечения или диагностическую команду "
                "python3.12 -m wiki_agent.extraction "
                f"{source_relative}"
            )
        extracted_relative = self.relative(extracted)
        extracted_header = self.read_text_prefix(
            extracted_relative,
            allowed_roots=("raw/extracted",),
            max_chars=10_000,
        )
        original_path = _extracted_original_path(extracted_header)
        if original_path != source_relative:
            raise ValidationError(
                "Текстовая копия не соответствует оригиналу: "
                f"{extracted_relative} указывает original_path="
                f"{original_path!r}, ожидалось {source_relative!r}"
            )
        original_sha256 = _extracted_original_sha256(extracted_header)
        if (
            re.search(
                r"^original_sha256:",
                extracted_header,
                re.MULTILINE,
            )
            and original_sha256 is None
        ):
            raise ValidationError(
                f"Некорректный original_sha256 в {extracted_relative}"
            )
        if (
            original_sha256 is not None
            and original_sha256 != sha256_file(source)
        ):
            raise ValidationError(
                "Текстовая копия устарела: "
                f"{extracted_relative} создана для другого содержимого "
                f"{source_relative}. Не перезаписывайте источник; добавьте "
                "новую версию отдельным файлом."
            )
        return self.relative(extracted)

    def snapshot_sources(self) -> dict[str, str]:
        source_root = self.resolve(READ_ONLY_SOURCE_ROOT, must_exist=True)
        snapshot: dict[str, str] = {}
        for path in sorted(source_root.rglob("*")):
            if path.is_symlink():
                raise SecurityError(f"Symlink в raw/sources запрещён: {path}")
            if path.is_file():
                snapshot[self.relative(path)] = sha256_file(path)
        return snapshot

    def assert_sources_unchanged(self, before: dict[str, str]) -> None:
        after = self.snapshot_sources()
        if before != after:
            missing = sorted(set(before) - set(after))
            added = sorted(set(after) - set(before))
            changed = sorted(
                key
                for key in set(before) & set(after)
                if before[key] != after[key]
            )
            details = []
            if missing:
                details.append("удалены: " + ", ".join(missing))
            if added:
                details.append("добавлены: " + ", ".join(added))
            if changed:
                details.append("изменены: " + ", ".join(changed))
            raise SecurityError(
                "raw/sources изменился во время операции: "
                + "; ".join(details)
            )

    def validate_ingest_target(self, relative: str) -> Path:
        raw = str(relative)
        normalized = Path(raw).as_posix()
        if raw != normalized:
            raise SecurityError(
                f"Путь должен быть каноническим без ./ или //: {relative}"
            )
        parts = Path(normalized).parts
        self._reject_symlink_relative(normalized)
        if normalized in INGEST_WRITE_FILES:
            target = self.resolve(normalized)
            return target
        if (
            len(parts) == 3
            and "/".join(parts[:2]) in INGEST_WRITE_ROOTS
            and parts[2].endswith(".md")
            and parts[2] != ".md"
        ):
            _validate_filename(parts[2], label="имя Wiki-файла")
            target = self.resolve(normalized)
            return target
        raise SecurityError(
            "Ingest может изменять только wiki/pages/*.md, "
            "wiki/sources/*.md, wiki/index.md и wiki/log.md: "
            f"{relative}"
        )

    def _reject_symlink_relative(self, relative: str) -> None:
        current = self.root
        for part in Path(relative).parts:
            current = current / part
            if current.is_symlink():
                raise SecurityError(
                    f"Symlink в пути записи запрещён: {self.relative(current)}"
                )

    def write_text(
        self,
        relative: str,
        content: str,
        *,
        allowed_roots: Iterable[str],
        must_not_exist: bool = False,
    ) -> Path:
        path = self.resolve(relative, allowed_roots=allowed_roots)
        if path == self.resolve(READ_ONLY_SOURCE_ROOT) or path.is_relative_to(
            self.resolve(READ_ONLY_SOURCE_ROOT)
        ):
            raise SecurityError("Запись в raw/sources запрещена")
        if must_not_exist and path.exists():
            raise ValidationError(f"Файл уже существует: {relative}")
        path.parent.mkdir(parents=True, exist_ok=True)
        if must_not_exist:
            atomic_create_text(path, content)
        else:
            atomic_replace_text(path, content)
        return path

    def unique_markdown_path(self, directory: str, stem: str) -> str:
        base_dir = self.resolve(directory, must_exist=True)
        candidate = base_dir / f"{stem}.md"
        counter = 2
        while candidate.exists():
            candidate = base_dir / f"{stem}-{counter}.md"
            counter += 1
        return self.relative(candidate)

    def path_identity_key(self, relative: str) -> str:
        return unicodedata.normalize("NFC", relative).casefold()

    def _reject_path_alias(self, relative: str) -> None:
        """Отклонить другое написание case/NFC уже существующего пути."""

        current = self.root
        for part in Path(relative).parts:
            if not current.is_dir():
                break
            entries = list(current.iterdir())
            exact = next(
                (entry for entry in entries if entry.name == part),
                None,
            )
            if exact is not None:
                current = exact
                continue
            identity = unicodedata.normalize("NFC", part).casefold()
            aliases = [
                entry.name
                for entry in entries
                if unicodedata.normalize("NFC", entry.name).casefold()
                == identity
            ]
            if aliases:
                raise SecurityError(
                    "Регистр или Unicode-нормализация пути не совпадает "
                    f"с именем на диске: {part!r}; найдено {aliases!r}"
                )
            current = current / part


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def sha256_text(content: str) -> str:
    return hashlib.sha256(content.encode("utf-8")).hexdigest()


def atomic_replace_text(path: Path, content: str) -> None:
    """Записать UTF-8 через временный файл в том же каталоге."""

    path.parent.mkdir(parents=True, exist_ok=True)
    mode = (
        stat.S_IMODE(path.stat().st_mode)
        if path.exists()
        else 0o644
    )
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, mode)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def atomic_create_text(path: Path, content: str) -> None:
    """Опубликовать полностью записанный файл только если цели ещё нет."""

    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".create.tmp", dir=path.parent
    )
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o644)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError as exc:
            raise ValidationError(f"Файл уже существует: {path}") from exc
    finally:
        temporary.unlink(missing_ok=True)


def _extracted_original_path(header: str) -> str | None:
    match = re.search(r"^original_path:\s*(.+?)\s*$", header, re.MULTILINE)
    if not match:
        return None
    value = match.group(1).strip()
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        return decoded if isinstance(decoded, str) else None
    if (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] == "'"
    ):
        return value[1:-1]
    return value


def _extracted_original_sha256(header: str) -> str | None:
    match = re.search(
        r"^original_sha256:\s*(.+?)\s*$",
        header,
        re.MULTILINE,
    )
    if not match:
        return None
    value = match.group(1).strip()
    if value.startswith('"'):
        try:
            decoded = json.loads(value)
        except json.JSONDecodeError:
            return None
        value = decoded if isinstance(decoded, str) else ""
    elif (
        len(value) >= 2
        and value[0] == value[-1]
        and value[0] == "'"
    ):
        value = value[1:-1]
    return value if re.fullmatch(r"[0-9a-f]{64}", value) else None


def _validate_filename(value: str, *, label: str) -> None:
    if unicodedata.normalize("NFC", value) != value:
        raise SecurityError(
            f"{label.capitalize()} должно быть в Unicode NFC"
        )
    if value != value.strip():
        raise SecurityError(f"{label.capitalize()} не должно начинаться или "
                            f"заканчиваться пробелом: {value!r}")
    if any(unicodedata.category(character) in {"Cc", "Cf"} for character in value):
        raise SecurityError(
            f"{label.capitalize()} содержит управляющий или скрытый символ"
        )
