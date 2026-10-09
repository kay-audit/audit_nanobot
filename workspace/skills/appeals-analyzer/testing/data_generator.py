"""Генератор тестовых данных appeals-analyzer.

Раньше набор лежал JSON-файлом. Теперь единственный источник —
PostgreSQL, схема ``test_d3`` (см. ``sql/test_d3_schema.sql``): требование
PRT8 из ``bridge_skills.md`` — тестовые данные фичи обязаны жить в БД.

Запуск::

    python workspace/skills/appeals-analyzer/testing/data_generator.py --force

DSN берётся из ``TEST_PG_DSN``, иначе из ``DATABASE_URL``.
"""
from __future__ import annotations

import argparse
import os
import random
from datetime import date, timedelta
from pathlib import Path

DEFAULT_SEED = 20260921
RECORD_COUNT = 1000
TEST_SCHEMA = "test_d3"

_SCHEMA_SQL = Path(__file__).resolve().parents[4] / "sql" / "test_d3_schema.sql"

TOPICS = [
    ("Мошенничество", "Потребительский кредит", "Мобильный банк", "Клиент оформил кредит под влиянием мошенников и после выдачи перевёл деньги злоумышленникам."),
    ("Кредиты", "Образовательный кредит", "Офис", "Клиент просит разъяснить условия образовательного кредита и порядок подтверждения обучения."),
    ("Автокредиты", "Автокредит с субсидией", "Офис", "Клиент сообщает о неверном отражении государственной субсидии по автокредиту."),
    ("Переводы", "Блокировка перевода", "Мобильный банк", "Перевод заблокирован проверкой безопасности, клиент просит уточнить срок снятия ограничения."),
    ("Комиссии", "Ошибочная комиссия", "Контакт-центр", "Клиент оспаривает ошибочно списанную комиссию и просит вернуть денежные средства."),
    ("Карты", "Дебетовая карта", "Веб", "Не проходит оплата дебетовой картой, хотя баланс и срок действия карты в норме."),
    ("Переводы", "Межбанковский перевод", "API", "Межбанковский перевод задержан, получатель не видит деньги в ожидаемый срок."),
    ("Кредиты", "Потребительский кредит", "Офис", "Клиент не согласен с графиком платежей и просит проверить расчёт процентов."),
    ("Мобильный банк", "Авторизация", "Мобильный банк", "После обновления приложения клиент не может войти в мобильный банк."),
    ("Прочее", "Справка", "Контакт-центр", "Клиент уточняет режим работы офиса и порядок получения стандартной справки."),
]

SUFFIXES = [
    "Обращение направлено на проверку профильному подразделению.",
    "Клиент ожидает письменное разъяснение и корректировку операции.",
    "Требуется проверить историю операции и сообщить результат.",
]


def _dsn_from_secrets_env() -> str:
    """Прочитать DSN из ``.secrets.env`` там, где файл реально лежит.

    ``audit_bridge`` выполняет pre-flight в распакованном архиве ветки, а он
    собран из git и не содержит ``.secrets.env`` (файл в .gitignore). Поэтому
    ищем не только корень проекта, но и рабочие копии рядом: конфиг лежит в
    том каталоге, где запущен nanobot. Тот же источник, что ``${DATABASE_URL}``
    в project.json для канала PostgresChannel.
    """
    here = Path(__file__).resolve()
    # Корни, где может лежать .secrets.env. audit_bridge распаковывает ветку в
    # <bridge>/worktrees/<branch>, а конфиг живёт в рабочей копии audit_nanobot,
    # лежащей рядом с audit_bridge. Поиск ограничен этими корнями: подниматься
    # дальше и искать наугад нельзя — это риск подхватить чужой конфиг
    # с секретами.
    extract = here.parents[4]
    tree = extract.parent.parent.parent          # общий корень рабочих копий
    roots = [extract, here.parents[3],
             tree, tree / "audit_nanobot"]
    candidates: list[Path] = []
    for root in roots:
        candidates.append(root / ".secrets.env")
    for candidate in candidates:
        if not candidate.is_file():
            continue
        try:
            content = candidate.read_text(encoding="utf-8")
        except OSError:
            continue
        for line in content.splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key.strip() in {"DATABASE_URL", "TEST_PG_DSN", "channels__postgres__dsn"}:
                value = value.strip()
                if value:
                    return value
    return ""


def resolve_dsn() -> str:
    """DSN тестовой БД. Пустой DSN — явная ошибка, а не молчаливый пустой набор."""
    dsn = (
        os.environ.get("TEST_PG_DSN")
        or os.environ.get("DATABASE_URL")
        or _dsn_from_secrets_env()
    ).strip()
    if not dsn:
        raise RuntimeError(
            "Не задан DSN тестовой БД: установите TEST_PG_DSN (или DATABASE_URL) "
            "либо заполните DATABASE_URL в .secrets.env — для схемы test_d3"
        )
    return dsn


def _connect(dsn: str):
    import psycopg2

    return psycopg2.connect(dsn)


def ensure_schema(conn) -> None:
    """Создать схему и таблицы test_d3, если их ещё нет."""
    with conn.cursor() as cur:
        cur.execute(_SCHEMA_SQL.read_text(encoding="utf-8"))
    conn.commit()


def generate_records(rng_seed: int = DEFAULT_SEED, count: int = RECORD_COUNT) -> list[dict]:
    """Детерминированный синтетический набор обращений.

    Форма записи — та же, что ждёт раннер (``appeal_id``/``date``/``text``);
    соответствие колонкам ``test_d3`` делает :func:`seed`. Генератор не знает
    про БД: он чистый, поэтому тестируется без подключения к PostgreSQL.
    """
    rng = random.Random(rng_seed)
    start = date(2024, 1, 1)
    rows = []
    for index in range(1, count + 1):
        prd, s_prd, chnl, text = TOPICS[(index - 1) % len(TOPICS)]
        suffix = rng.choice(SUFFIXES)
        rows.append({
            "appeal_id": f"APPEAL-TEST-{index:04d}",
            # ISO-строка, а не date: раннер сравнивает даты со строками,
            # а psycopg2 отдаёт TIMESTAMP как datetime.
            "date": (start + timedelta(days=rng.randint(0, 1095))).isoformat(),
            "prd": prd,
            "s_prd": s_prd,
            "chnl": chnl,
            "text": f"{text} {suffix} Номер синтетического кейса {index:04d}.",
        })
    return rows


def _seed_dialogs_and_tasks(row: dict, rng: random.Random) -> tuple[list, list]:
    appeal_id = row["appeal_id"]
    dialogs = [
        (appeal_id, 1, "Клиент", row["text"]),
        (appeal_id, 2, "Оператор", "Обращение принято в работу, уточняются детали операции."),
    ]
    due_date = date.fromisoformat(row["date"]) + timedelta(days=rng.randint(3, 30))
    tasks = [
        (appeal_id, 1, f"Исполнитель-{rng.randint(1, 5):02d}",
         "Проверить историю операции и подготовить письменный ответ клиенту.",
         due_date),
    ]
    return dialogs, tasks


def seed(conn, *, rng_seed: int = DEFAULT_SEED, count: int = RECORD_COUNT,
        force: bool = False) -> int:
    """Заполнить test_d3 синтетическими обращениями. Возвращает число записей."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT to_regclass(%s), (SELECT count(*) FROM test_d3.appeals_structural)",
            (f"{TEST_SCHEMA}.appeals_structural",),
        )
        relation, existing = cur.fetchone()
        if relation is not None and existing and not force:
            return int(existing)

    records = generate_records(rng_seed, count)
    rng = random.Random(rng_seed + 1)
    with conn.cursor() as cur:
        cur.execute(f"TRUNCATE {TEST_SCHEMA}.appeal_task, {TEST_SCHEMA}.appeal_dialogs, "
                    f"{TEST_SCHEMA}.appeal_body, {TEST_SCHEMA}.appeals_structural")
        cur.executemany(
            f"INSERT INTO {TEST_SCHEMA}.appeals_structural "
            "(app_row_id, req_reg_date, prd, s_prd, chnl) VALUES (%s, %s, %s, %s, %s)",
            [(r["appeal_id"], r["date"], r["prd"], r["s_prd"], r["chnl"])
             for r in records],
        )
        cur.executemany(
            f"INSERT INTO {TEST_SCHEMA}.appeal_body (app_row_id, body) VALUES (%s, %s)",
            [(r["appeal_id"], r["text"]) for r in records],
        )
        dialogs: list = []
        tasks: list = []
        for row in records:
            row_dialogs, row_tasks = _seed_dialogs_and_tasks(row, rng)
            dialogs.extend(row_dialogs)
            tasks.extend(row_tasks)
        cur.executemany(
            f"INSERT INTO {TEST_SCHEMA}.appeal_dialogs "
            "(app_row_id, turn_no, speaker, text) VALUES (%s, %s, %s, %s)",
            dialogs,
        )
        cur.executemany(
            f"INSERT INTO {TEST_SCHEMA}.appeal_task "
            "(app_row_id, task_no, assignee, task_text, due_date) VALUES (%s, %s, %s, %s, %s)",
            tasks,
        )
    conn.commit()
    return len(records)


def ensure_testing_data(*, force: bool = False, rng_seed: int = DEFAULT_SEED,
                        count: int = RECORD_COUNT, dsn: str | None = None) -> int:
    """Подготовить test_d3 и вернуть число записей."""
    connection = _connect(dsn or resolve_dsn())
    try:
        ensure_schema(connection)
        return seed(connection, rng_seed=rng_seed, count=count, force=force)
    finally:
        connection.close()


def load_records(dsn: str | None = None) -> list[dict]:
    """Прочитать набор из PostgreSQL в прежней форме (dict'ы для раннера).

    Структурный слой и текст соединяются по app_row_id — так же, как
    продакшн-код соединяет Greenplum-структуру с гидратацией.
    """
    connection = _connect(dsn or resolve_dsn())
    try:
        with connection.cursor() as cur:
            cur.execute(
                f"""
                SELECT s.app_row_id,
                       to_char(s.req_reg_date, 'YYYY-MM-DD') AS req_reg_date,
                       s.prd, s.s_prd, s.chnl,
                       b.body
                FROM {TEST_SCHEMA}.appeals_structural s
                JOIN {TEST_SCHEMA}.appeal_body b USING (app_row_id)
                ORDER BY s.app_row_id
                """
            )
            rows = cur.fetchall()
        return [
            {
                "appeal_id": row[0],
                "date": row[1],
                "prd": row[2],
                "s_prd": row[3],
                "chnl": row[4],
                "text": row[5],
            }
            for row in rows
        ]
    finally:
        connection.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--count", type=int, default=RECORD_COUNT)
    args = parser.parse_args()
    total = ensure_testing_data(force=args.force, rng_seed=args.seed, count=args.count)
    print(f"{TEST_SCHEMA}: {total} обращений")