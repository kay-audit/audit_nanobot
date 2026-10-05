"""Загрузить синтетический корпус актов в ЖИВОЕ хранилище навыка follow_up.

Зачем
-----
В dev ``GP_ENABLED=false``, поэтому PG-таблицы ``t_fu_act_docs`` /
``t_fu_act_chunks`` / ``t_fu_deviations`` остаются мёртвыми: их читает
GreenplumStore, а навык в dev-mode работает через свой SQLite-стор
(``followup.db``). Из-за этого ``mcp_follow_up_status`` отдаёт
«0 проверок / 0 актов / 0 чанков / 0 отклонений», и навык не может
искать по актам, цитатам и отклонениям — работает только ветка
поручений (``public.t_fu_poruch_data``).

Что делает
----------
Берёт ``corpus.json`` (синтетический корпус из ``follow_up_testkit``) и
записывает его в навык теми же функциями, что и штатная индексация:
``build_indexes`` (эмбеддинги BGE-моделью навыка) →
``Document`` / ``ChunkRepo.insert_bulk`` / ``DeviationRepo.insert_bulk`` →
производные индексы (``derived.build_entity_index``,
``build_deviation_embeddings``).

В отличие от ``follow_up_testkit/fixture_loader.py`` намеренно НЕ
перенаправляет каталоги во временную папку: цель — наполнить рабочий
корпус навыка, чтобы его было видно в боте.

Корпус с данными **не хранится в репозитории**. Путь к нему передаётся
оператором: ``--corpus`` либо переменная ``FOLLOW_UP_CORPUS_PATH``; если
не задано — ищем ``follow_up_testkit/fixtures/corpus.json`` вверх от
корня репозитория (каталог лежит рядом с ним, вне git).

Запуск (из корня репозитория audit_nanobot):
    python tools/seed_follow_up_corpus.py --corpus path/to/corpus.json
    python tools/seed_follow_up_corpus.py --list
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SKILL_DIR = REPO_ROOT / "workspace" / "skills" / "follow_up"
CORPUS_ENV = "FOLLOW_UP_CORPUS_PATH"


def _resolve_corpus(explicit: str | None) -> Path:
    """Путь к корпусу: аргумент → env → поиск рядом с репозиторием."""
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p
        raise SystemExit(f"корпус не найден по аргументу: {p}")
    env = os.environ.get(CORPUS_ENV, "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
        raise SystemExit(f"корпус не найден по {CORPUS_ENV}: {p}")
    for parent in [REPO_ROOT, *REPO_ROOT.parents]:
        candidate = parent / "follow_up_testkit" / "fixtures" / "corpus.json"
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "корпус не найден: передайте --corpus <path> или задайте "
        f"{CORPUS_ENV}. Ожидается corpus.json от follow_up_testkit."
    )


def _bootstrap() -> None:
    """Поднять sys.path и окружение как это делает лаунчер навыка.

    ``.secrets.env`` обязателен: без него навык берёт дефолтный
    ``bge_model_path`` (``models/bge-m3-russian-legal``), которого нет
    на диске, и ``build_indexes`` падает на загрузке эмбеддера.
    """
    os.environ.setdefault("PYTHONUTF8", "1")
    for p in (str(REPO_ROOT), str(SKILL_DIR)):
        if p not in sys.path:
            sys.path.insert(0, p)

    secrets = REPO_ROOT / ".secrets.env"
    if not secrets.exists():
        raise SystemExit(
            f"нет {secrets} — без него навык не найдёт BGE-модель и DSN"
        )
    sys.path.insert(0, str(REPO_ROOT))
    from config import load_env

    flat = load_env(secrets)
    for key, val in _flatten(flat).items():
        os.environ.setdefault(key, str(val))


def _flatten(tree, prefix: str = "") -> dict:
    out: dict[str, str] = {}
    for k, v in tree.items():
        key = f"{prefix}_{k}" if prefix else str(k)
        if isinstance(v, dict):
            out.update(_flatten(v, key))
        else:
            out[key.upper()] = "" if v is None else str(v)
    return out


def _status() -> int:
    _bootstrap()
    from backend.config import get_settings
    from backend.storage.database import (
        Chunk,
        Deviation,
        Document,
        get_db,
        init_db,
    )

    init_db()
    with get_db() as db:
        docs = db.query(Document).count()
        chunks = db.query(Chunk).count()
        devs = db.query(Deviation).count()
    print(json.dumps({
        "documents": docs,
        "chunks": chunks,
        "deviations": devs,
        "index_dir": str(get_settings().index_dir),
        "bge_model": str(get_settings().bge_model_path),
    }, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--corpus", help=f"путь к corpus.json (иначе — {CORPUS_ENV} или поиск рядом)")
    ap.add_argument("--list", action="store_true",
                    help="Показать текущее состояние корпуса и выйти")
    ap.add_argument("--reset", action="store_true",
                    help="Очистить документы/чанки/отклонения перед загрузкой")
    args = ap.parse_args()

    if args.list:
        return _status()

    t0 = time.time()
    _bootstrap()

    from backend.config import get_settings
    from backend.core import boot

    # Модели/пути — как у сервера навыка (важно для эмбеддингов).
    boot.adopt_agent_models()
    cfg = get_settings()

    from backend.indexing.index_builder import build_indexes
    from backend.storage.database import (
        Chunk,
        ChunkRepo,
        Deviation,
        DeviationRepo,
        Document,
        get_db,
        init_db,
    )

    corpus = json.loads(_resolve_corpus(args.corpus).read_text(encoding="utf-8"))
    docs_in = corpus["documents"]
    init_db()

    # Индексы первыми: build_indexes раздаёт faiss_id, их пишем в чанки.
    chunks: list[dict] = []
    for d in docs_in:
        for i, c in enumerate(d.get("chunks", [])):
            chunks.append({
                "file_id": d["file_id"],
                "chunk_index": i,
                "text": c["text"],
                "header_path": c.get("header_path", ""),
            })
    build_indexes(chunks)

    n_docs = n_devs = 0
    with get_db() as db:
        if args.reset:
            db.query(Document).delete()
            db.query(Chunk).delete()
            db.query(Deviation).delete()
            db.flush()
            print("corpus reset done")

        for d in docs_in:
            doc = Document(
                file_id=d["file_id"],
                filename=d["filename"],
                check_id=d["check_id"],
                topic=d.get("topic"),
                original_path=f"testkit://{d['file_id']}",
                md_path="",
            )
            db.add(doc)
            db.flush()
            db_rows = [
                {
                    "document_id": doc.id,
                    "chunk_index": c["chunk_index"],
                    "faiss_id": c["faiss_id"],
                    "text": c["text"],
                    "header_path": c["header_path"],
                    "char_count": len(c["text"]),
                }
                for c in chunks if c["file_id"] == d["file_id"]
            ]
            ChunkRepo.insert_bulk(db, db_rows)
            n_docs += 1

            devs = []
            for v in d.get("deviations", []):
                row = {"document_id": doc.id, "check_id": d["check_id"]}
                for k in ("category", "description", "severity",
                          "financial_impact_rub", "affected_count",
                          "responsible_unit", "recommendation",
                          "source_chunk_index"):
                    row[k] = v.get(k)
                for k in ("affected_systems", "regulation_refs"):
                    row[k] = json.dumps(v.get(k) or [], ensure_ascii=False)
                devs.append(row)
            DeviationRepo.insert_bulk(db, devs)
            n_devs += len(devs)

    from backend.indexing import derived

    derived.build_entity_index()
    derived.build_deviation_embeddings()
    try:
        from backend.core import identity

        identity.invalidate()
    except Exception:  # noqa: BLE001
        pass

    print(json.dumps({
        "ok": True,
        "documents": n_docs,
        "chunks": len(chunks),
        "deviations": n_devs,
        "index_dir": str(cfg.index_dir),
        "embedder": str(cfg.bge_model_path),
        "seconds": round(time.time() - t0, 1),
    }, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())