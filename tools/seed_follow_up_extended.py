"""Поручения для follow_up выводятся из внешнего корпуса актов.

Назначение
----------
Витрина поручений (``public.t_fu_poruch_data`` и/или поручения в корпусе
навыка) — это **данные**, а не код. Данные не хранятся в репозитории:
этот скрипт содержит только алгоритм их построения, а сам корпус
подкладывает оператор.

Почему так
----------
Ранее поручения были зашиты в скрипт (21 запись в ``EXTRA_PORUCH``) плюс
абсолютный путь к корпусу на конкретной машине. Это плохо по трём
причинам: данные уезжали в git, путь не работает на другой машине, а
правка «текста поручения» требовала коммита кода.

Как читать корпус
-----------------
``--corpus <path>`` → переменная ``FOLLOW_UP_CORPUS_PATH`` → поиск
``follow_up_testkit/fixtures/corpus.json`` вверх от корня репозитория.
Если корпус не найден — понятная ошибка, а не молчаливый пустой seed.

Формат корпуса (``corpus.json`` от follow_up_testkit)
----------------------------------------------------
``{"documents": [{"file_id", "filename", "check_id", "topic",
"chunks": [{"header_path", "text"}],
"deviations": [{"category", "description", "severity",
"financial_impact_rub", "affected_count", "responsible_unit",
"recommendation", "affected_systems", "source_chunk_index"}]}]}``

Что строим
----------
1. **По одному поручению на каждое отклонение**: problem = description,
   assignment = recommendation, статус — по severity, подразделение —
   responsible_unit. Это основной объём.
2. **По одному поручению на акт** из чанка с рекомендациями (если чанк
   найден по ``header_path``): problem = тема проверки, assignment =
   текст рекомендаций, статус «в работе».
3. **Опционально** — файл ``data/fixtures/poruch_fixture.json`` навыка
   (он вне git, ``/data/`` в ``.gitignore``), если существует.

Запуск
------
    python tools/seed_follow_up_extended.py --corpus path/to/corpus.json
    python tools/seed_follow_up_extended.py --dry-run
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_FIXTURE = REPO_ROOT / "workspace/skills/follow_up/data/fixtures/poruch_fixture.json"

#: Куда статус поручения выводится из критичности отклонения.
#: Критичное не закрывают до полного исполнения, формальное — мелочь,
#: которую обычно закрывают частично.
SEVERITY_TO_STATUS = {
    "критичное": "в работе",
    "высокое": "в работе",
    "существенное": "в работе",
    "среднее": "частично исполнено",
    "формальное": "частично исполнено",
    "низкое": "частично исполнено",
}
DEFAULT_STATUS = "в работе"

#: Признаки чанка с рекомендациями (регистронезависимо, по header_path).
RECOMMENDATION_MARKERS = ("рекомендац", "предложен", "выводы", "3.")


def _corpus_path(explicit: str | None) -> Path:
    """Путь к корпусу: аргумент → env → поиск от корня репозитория."""
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_file():
            return p
        raise SystemExit(f"корпус не найден по аргументу: {p}")

    env = os.environ.get("FOLLOW_UP_CORPUS_PATH", "").strip()
    if env:
        p = Path(env).expanduser()
        if p.is_file():
            return p
        raise SystemExit(f"корпус не найден по FOLLOW_UP_CORPUS_PATH: {p}")

    for parent in [REPO_ROOT, *REPO_ROOT.parents]:
        candidate = parent / "follow_up_testkit" / "fixtures" / "corpus.json"
        if candidate.is_file():
            return candidate
    raise SystemExit(
        "корпус не найден. Передайте --corpus <path> или задайте "
        "FOLLOW_UP_CORPUS_PATH. Ожидается corpus.json от follow_up_testkit "
        "(documents[].deviations[])."
    )


def poruch_key(km_id: str, doc_reg_num: str | None, assignment: str | None) -> str:
    """Совпадает с ``backend.storage.gp.poruch_key`` и
    ``backend.storage.postgres_fixture.poruch_key`` — стабильный id строки."""
    a_hash = hashlib.md5((assignment or "").encode("utf-8")).hexdigest()
    raw = f"{km_id or ''}|{doc_reg_num or ''}|{a_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _from_deviation(dev: dict, check_id: str, doc_reg_num: str, year: str) -> dict:
    desc = (dev.get("description") or "").strip()
    if not desc:
        return {}
    assignment = (dev.get("recommendation") or "").strip()
    if not assignment:
        # Рекомендации нет — поручение нечего выдавать, но нарушение есть.
        # Формулируем действие из категории, чтобы поручение было actionable.
        cat = (dev.get("category") or "выявленное отклонение").strip()
        assignment = f"Устранить выявленное отклонение: {cat}."
    unit = (dev.get("responsible_unit") or "").strip() or None
    actions_bits = []
    if dev.get("affected_count") is not None:
        actions_bits.append(f"затронуто объектов: {dev['affected_count']}")
    if dev.get("financial_impact_rub") is not None:
        actions_bits.append(f"финансовый эффект: {dev['financial_impact_rub']}")
    systems = dev.get("affected_systems") or []
    if systems:
        actions_bits.append("системы: " + ", ".join(systems))
    return {
        "km_id": check_id,
        "doc_reg_num": doc_reg_num,
        "problem": desc,
        "assignment_": assignment,
        "poruch_status": SEVERITY_TO_STATUS.get(
            (dev.get("severity") or "").strip().lower(), DEFAULT_STATUS
        ),
        "close_fact": None,
        "actions": "; ".join(actions_bits) or f"выявлено в проверке {check_id} {year}",
        "block_unit": unit,
    }


def _from_recommendation_chunk(doc: dict) -> dict:
    """Поручение «выполнить рекомендации акта» — из чанка с рекомендациями."""
    check_id = (doc.get("check_id") or "").strip()
    if not check_id:
        return {}
    for ch in doc.get("chunks") or []:
        header = (ch.get("header_path") or "").lower()
        if not any(m in header for m in RECOMMENDATION_MARKERS):
            continue
        text = (ch.get("text") or "").strip()
        if not text:
            continue
        topic = (doc.get("topic") or "").strip() or check_id
        return {
            "km_id": check_id,
            "doc_reg_num": f"REC-{doc.get('file_id') or check_id}"[:100],
            "problem": f"Рекомендации по теме «{topic}» не подтверждены как исполненные.",
            "assignment_": text[:800],
            "poruch_status": DEFAULT_STATUS,
            "close_fact": None,
            "actions": f"источник: акт {doc.get('filename') or ''}".strip(),
            "block_unit": None,
        }
    return {}


def _from_skill_fixture(path: Path) -> list[dict]:
    """Опционально: локальный fixture навыка (вне git)."""
    if not path.is_file():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        if not r.get("km_id") or not r.get("assignment_"):
            continue
        out.append({**r, "close_fact": r.get("close_fact")})
    return out


def build_rows(corpus_path: Path, *, include_skill_fixture: bool = True) -> list[dict]:
    """Алгоритм: корпус → список строк витрины поручений."""
    corpus = json.loads(corpus_path.read_text(encoding="utf-8"))
    docs = corpus.get("documents") or []
    year = "2026"
    rows: list[dict] = []
    seen: set[str] = set()

    for doc in docs:
        check_id = (doc.get("check_id") or "").strip()
        if not check_id:
            continue
        # год из чанка/имени не разбираем — дефолт, он же в БД не важен
        devs = doc.get("deviations") or []
        for i, dev in enumerate(devs, start=1):
            row = _from_deviation(dev, check_id, f"DEV-{i}-{check_id}"[:100], year)
            if not row:
                continue
            key = poruch_key(row["km_id"], row["doc_reg_num"], row["assignment_"])
            if key in seen:
                continue
            seen.add(key)
            rows.append({**row, "poruch_key": key})
        rec = _from_recommendation_chunk(doc)
        if rec:
            key = poruch_key(rec["km_id"], rec["doc_reg_num"], rec["assignment_"])
            if key not in seen:
                seen.add(key)
                rows.append({**rec, "poruch_key": key})

    if include_skill_fixture:
        for row in _from_skill_fixture(DEFAULT_FIXTURE):
            key = poruch_key(row.get("km_id"), row.get("doc_reg_num"),
                             row.get("assignment_"))
            if key in seen:
                continue
            seen.add(key)
            rows.append({**row, "poruch_key": key})
    return rows


def _dsn() -> str:
    sys.path.insert(0, str(REPO_ROOT))
    import config as botcfg

    botcfg._initialize_settings("test")
    flat = botcfg._flatten_env(botcfg._load_secrets_override())
    for key in ("CHANNELS_POSTGRES_DSN", "DATABASE_URL", "CHANNELS_POSTGRES__DSN"):
        val = flat.get(key)
        if val:
            return val
    raise SystemExit("PG-источник не задан (CHANNELS_POSTGRES_DSN / DATABASE_URL)")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--corpus", help="путь к corpus.json (иначе — поиск/FOLLOW_UP_CORPUS_PATH)")
    ap.add_argument("--dry-run", action="store_true", help="только показать, что построится")
    ap.add_argument("--no-skill-fixture", action="store_true",
                    help="не добавлять локальный fixture навыка")
    args = ap.parse_args()

    corpus = _corpus_path(args.corpus)
    rows = build_rows(corpus, include_skill_fixture=not args.no_skill_fixture)
    rows = [r for r in rows if r.get("poruch_key") and r.get("km_id") and r.get("assignment_")]

    by_km: dict[str, int] = {}
    for r in rows:
        by_km[r["km_id"]] = by_km.get(r["km_id"], 0) + 1
    print(json.dumps({
        "corpus": str(corpus),
        "rows": len(rows),
        "by_km": by_km,
        "dry_run": bool(args.dry_run),
    }, ensure_ascii=False))
    if args.dry_run:
        for r in rows[:5]:
            print("  пример:", r["km_id"], "|", r["problem"][:80])
        return 0

    import psycopg2

    dsn = _dsn()
    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        for r in rows:
            cur.execute(
                "INSERT INTO public.t_fu_poruch_data "
                "(km_id, doc_reg_num, problem, assignment_, poruch_status, "
                " close_fact, actions, block_unit, poruch_key, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (poruch_key) DO UPDATE SET "
                "km_id=EXCLUDED.km_id, doc_reg_num=EXCLUDED.doc_reg_num, "
                "problem=EXCLUDED.problem, assignment_=EXCLUDED.assignment_, "
                "poruch_status=EXCLUDED.poruch_status, "
                "close_fact=EXCLUDED.close_fact, actions=EXCLUDED.actions, "
                "block_unit=EXCLUDED.block_unit, updated_at=now()",
                (r["km_id"], r.get("doc_reg_num"), r["problem"], r["assignment_"],
                 r["poruch_status"], r.get("close_fact"), r.get("actions"),
                 r.get("block_unit"), r["poruch_key"]),
            )
        conn.commit()
        cur.execute("SELECT count(*) FROM public.t_fu_poruch_data")
        total = cur.fetchone()[0]
    print(f"upserted: {len(rows)}, всего в таблице: {total}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())