"""
artifact_manager.py — Унифицированный менеджер артефактов (отчётов Excel/CSV и графиков PNG/SVG)
для передачи в Greenplum (conversation_messages), Redis (outbox) и FastAPI Gateway (/api/files/download).
"""

from __future__ import annotations
import sys
from pathlib import Path

_FILE_PATH = Path(__file__).resolve()
_SKILL_DIR = _FILE_PATH.parent
while _SKILL_DIR.parent != _SKILL_DIR:
    if (_SKILL_DIR / "SKILL.md").exists() or _SKILL_DIR.name == "ior-analyzer":
        break
    _SKILL_DIR = _SKILL_DIR.parent

_SCRIPTS_DIR = _SKILL_DIR / "scripts"
_UTILS_DIR = _SKILL_DIR / "utils"

for _dir in (_SKILL_DIR, _SCRIPTS_DIR, _UTILS_DIR):
    _sdir = str(_dir)
    if _sdir not in sys.path:
        sys.path.insert(0, _sdir)


import base64
import logging
import mimetypes
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

logger = logging.getLogger(__name__)

_WORKSPACE_DIR = Path(__file__).resolve().parents[1]
_ROOT_DIR = Path(__file__).resolve().parents[2]

SEARCH_DIRS = [
    _WORKSPACE_DIR / "data_store" / "generated_charts",
    _WORKSPACE_DIR / "data_store" / "generated_files",
    _WORKSPACE_DIR / "data_store",
    _ROOT_DIR / "data" / "generated_files",
    _ROOT_DIR / "data",
]


def resolve_artifact_path(path_or_name: Union[str, Path]) -> Optional[Path]:
    """Находит существующий файл на сервере по относительному пути или имени."""
    if not path_or_name:
        return None

    cand = Path(path_or_name)
    if cand.exists() and cand.is_file():
        return cand.resolve()

    raw_name = cand.name
    for base_dir in SEARCH_DIRS:
        t = base_dir / raw_name
        if t.exists() and t.is_file():
            return t.resolve()
        t_sub = base_dir / path_or_name
        if t_sub.exists() and t_sub.is_file():
            return t_sub.resolve()

    return None


def file_to_base64(file_path: Path) -> Optional[str]:
    """Кодирует файл в Base64 строку."""
    try:
        if file_path and file_path.exists() and file_path.is_file():
            return base64.b64encode(file_path.read_bytes()).decode("ascii")
    except Exception as e:
        logger.warning(f"[artifact_manager] Failed to base64 encode {file_path}: {e}")
    return None


def get_mime_type(file_path: Path) -> str:
    """Определяет MIME тип файла."""
    ext = file_path.suffix.lower()
    mapping = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".svg": "image/svg+xml",
        ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".csv": "text/csv; charset=utf-8",
        ".pdf": "application/pdf",
        ".json": "application/json",
    }
    return mapping.get(ext, mimetypes.guess_type(str(file_path))[0] or "application/octet-stream")


def create_chart_artifact(file_path: Path) -> Dict[str, Any]:
    """Создает структурированный объект графического артефакта с Base64."""
    resolved = resolve_artifact_path(file_path)
    if not resolved:
        raw_name = Path(file_path).name
        return {
            "name": raw_name,
            "path": f"generated_charts/{raw_name}",
            "url": f"/api/files/download?path={raw_name}",
            "base64": None,
            "data_url": None,
            "mime": "image/png",
            "size_bytes": 0,
        }

    mime = get_mime_type(resolved)
    b64 = file_to_base64(resolved)
    data_url = f"data:{mime};base64,{b64}" if b64 else None

    return {
        "name": resolved.name,
        "path": f"generated_charts/{resolved.name}",
        "url": f"/api/files/download?path={resolved.name}",
        "base64": b64,
        "data_url": data_url,
        "mime": mime,
        "size_bytes": resolved.stat().st_size if resolved.exists() else 0,
    }


def create_file_artifact(file_path: Path, max_b64_kb: int = 300) -> Dict[str, Any]:
    """Создает структурированный объект документа/отчета (Excel/CSV/PDF)."""
    resolved = resolve_artifact_path(file_path)
    if not resolved:
        raw_name = Path(file_path).name
        ext = Path(file_path).suffix.lower()
        file_type = "excel" if ext == ".xlsx" else ("csv" if ext == ".csv" else "file")
        return {
            "name": raw_name,
            "path": f"generated_files/{raw_name}",
            "url": f"/api/files/download?path={raw_name}",
            "type": file_type,
            "mime": get_mime_type(Path(file_path)),
            "size_bytes": 0,
            "content_base64": None,
        }

    ext = resolved.suffix.lower()
    file_type = "excel" if ext == ".xlsx" else ("csv" if ext == ".csv" else "file")
    size_bytes = resolved.stat().st_size if resolved.exists() else 0

    content_b64 = None
    if 0 < size_bytes <= max_b64_kb * 1024:
        content_b64 = file_to_base64(resolved)

    return {
        "name": resolved.name,
        "path": f"generated_files/{resolved.name}",
        "url": f"/api/files/download?path={resolved.name}",
        "type": file_type,
        "mime": get_mime_type(resolved),
        "size_bytes": size_bytes,
        "content_base64": content_b64,
    }


def extract_and_attach_artifacts(
    metadata: Dict[str, Any],
    content: str = "",
    recent_time_window_s: float = 300.0,
) -> Dict[str, Any]:
    """Ищет артефакты в контенте и директориях, кодирует графики в Base64 и обогащает metadata."""
    if metadata is None:
        metadata = {}

    artifacts = metadata.setdefault("artifacts", {"files": [], "charts": []})
    if not isinstance(artifacts, dict):
        artifacts = {"files": [], "charts": []}
        metadata["artifacts"] = artifacts

    artifacts.setdefault("files", [])
    artifacts.setdefault("charts", [])

    existing_chart_paths = {c.get("name") for c in artifacts.get("charts", []) if isinstance(c, dict)}
    existing_file_paths = {f.get("name") for f in artifacts.get("files", []) if isinstance(f, dict)}

    # 1. Поиск ссылок и имен файлов в тексте content
    found_filenames = set()
    if content:
        # Ручка скачивания /api/files/download?path=...
        for m in re.finditer(r'/api/files/download\?path=([^\s&"\'\)]+)', content):
            found_filenames.add(m.group(1))
        # Относительные пути generated_charts/ or generated_files/
        for m in re.finditer(r'(generated_charts|generated_files)/([^\s"\'\)]+)', content):
            found_filenames.add(m.group(2))
        # Ссылки формата Markdown ![alt](url) или [alt](url)
        for m in re.finditer(r'!\[.*?\]\((.*?)\)|\[.*?\]\((.*?)\)', content):
            url = m.group(1) or m.group(2)
            if url:
                if "path=" in url:
                    found_filenames.add(url.split("path=")[-1].split("&")[0])
                elif any(url.endswith(e) for e in ('.png', '.jpg', '.jpeg', '.svg', '.xlsx', '.csv', '.pdf')):
                    found_filenames.add(Path(url).name)

    # 2. Сканирование директорий на недавно созданные артефакты (в пределах recent_time_window_s)
    now = time.time()
    for search_dir in SEARCH_DIRS:
        if not search_dir.exists():
            continue
        try:
            for item in search_dir.iterdir():
                if item.is_file() and (now - item.stat().st_mtime) <= recent_time_window_s:
                    found_filenames.add(item.name)
        except Exception:
            pass

    # 3. Классификация и создание объектов артефактов
    for fname in found_filenames:
        resolved = resolve_artifact_path(fname)
        if not resolved or not resolved.exists():
            continue

        ext = resolved.suffix.lower()
        if ext in (".png", ".jpg", ".jpeg", ".svg"):
            if resolved.name not in existing_chart_paths:
                chart_art = create_chart_artifact(resolved)
                artifacts["charts"].append(chart_art)
                existing_chart_paths.add(resolved.name)
        elif ext in (".xlsx", ".csv", ".pdf", ".txt"):
            if resolved.name not in existing_file_paths:
                file_art = create_file_artifact(resolved)
                artifacts["files"].append(file_art)
                existing_file_paths.add(resolved.name)

    # 4. Установка удобных верхнеуровневых полей в metadata для прямого использования
    if artifacts.get("charts"):
        first_chart = artifacts["charts"][0]
        if isinstance(first_chart, dict) and first_chart.get("base64"):
            metadata["chart_base64"] = first_chart["base64"]
            metadata["chart_data_url"] = first_chart.get("data_url")

    if artifacts.get("files"):
        first_file = artifacts["files"][0]
        if isinstance(first_file, dict) and first_file.get("url"):
            metadata["file_url"] = first_file["url"]

    return metadata
