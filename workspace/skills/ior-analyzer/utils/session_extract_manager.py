"""
session_extract_manager.py — Кэш активной выгрузки сессии.
Гарантирует, что в рамках одного session_id хранится СТРОГО 1 активный датасет (DataFrame/файл),
чтобы избежать дублирования и пложения файлов при уточнении вопросов.
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


import logging
from typing import Optional, Dict, Any
import pandas as pd

logger = logging.getLogger(__name__)

# Хранилище сессионных датасетов в памяти: session_id -> { "df": pd.DataFrame, "file_path": str, ... }
_SESSION_EXTRACTS: Dict[str, Dict[str, Any]] = {}

def get_session_extract(session_id: str) -> Optional[Dict[str, Any]]:
    """Возвращает текущую активную выгрузку для сессии."""
    if not session_id:
        return None
    return _SESSION_EXTRACTS.get(session_id)

def set_session_extract(
    session_id: str,
    df: pd.DataFrame,
    file_path: Optional[str] = None,
    skill_name: Optional[str] = None,
    metadata: Optional[dict] = None,
    extra: Optional[dict] = None,
    **kwargs
) -> Dict[str, Any]:
    """Сохраняет новую выгрузку для сессии, перезаписывая предыдущую (ограничение 1 выгрузка на сессию)."""
    if not session_id:
        session_id = "default_session"

    meta_dict = metadata or {}
    if extra:
        meta_dict.update(extra)

    extract_data = {
        "df": df,
        "file_path": file_path,
        "skill_name": skill_name,
        "rows": len(df) if df is not None else 0,
        "metadata": meta_dict
    }

    # Для прямого доступа к id_to_text_map, hypothesis и другим полям сессии
    for k, v in meta_dict.items():
        extract_data[k] = v
    for k, v in kwargs.items():
        extract_data[k] = v

    _SESSION_EXTRACTS[session_id] = extract_data
    logger.info(f"[session_extract_manager] Updated extract for session '{session_id}': {len(df) if df is not None else 0} rows.")
    return extract_data

def clear_session_extract(session_id: str) -> None:
    """Удаляет сохранённую выгрузку для сессии."""
    if session_id in _SESSION_EXTRACTS:
        del _SESSION_EXTRACTS[session_id]
        logger.info(f"[session_extract_manager] Cleared extract for session '{session_id}'.")
