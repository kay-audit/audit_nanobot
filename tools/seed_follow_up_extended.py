"""Расширить poruch_fixture данными из follow_up_testkit/fixtures/corpus.json.

Testkit хранит акты + deviations (9 штук на 3 КМ). Для тестов follow_up
нужны поручения — таблица public.t_fu_poruch_data. Каждое deviation
превращаем в поручение + добавляем ещё ~21, выведенные из chunks актов
(темы: лимиты, залоги, ИБ-учётки, обращения клиентов и т.п.).

Итого 30 поручений на 5 КМ (99-12345..99-12349).

Запуск:
  python tools/seed_follow_up_data.py --truncate \\
              --fixture data/fixtures/poruch_fixture_extended.json
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

import psycopg2


ROOT = Path(__file__).resolve().parent.parent
TESTKIT = Path("C:/Users/pasco/opencode_projects/audit_point/follow_up_testkit")
CORPUS = TESTKIT / "fixtures" / "corpus.json"
OUT_FIXTURE = ROOT / "workspace/skills/follow_up/data/fixtures/poruch_fixture_extended.json"


def _poruch_key(km_id, doc_reg_num, assignment_):
    a_hash = hashlib.md5((assignment_ or "").encode("utf-8")).hexdigest()
    raw = f"{km_id or ''}|{doc_reg_num or ''}|{a_hash}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


# ───────────────────────────────────────────────────────────────────
# Маппинг severity → poruch_status. severity (testkit) ↔ состояние поручения.
#   критичное   → «в работе» — обычно не закрывают критичные до исполнения.
#   существенное → «в работе» — то же.
#   формальное  → «частично исполнено» — мелочь, частично закрыли.
# ───────────────────────────────────────────────────────────────────
SEV_TO_STATUS = {
    "критичное": "в работе",
    "существенное": "в работе",
    "формальное": "частично исполнено",
}


def _from_deviation(d: dict, km_id: str, doc_reg_num: str, idx: int) -> dict:
    return {
        "km_id": km_id,
        "doc_reg_num": doc_reg_num,
        "problem": d["description"],
        "assignment_": d.get("recommendation")
                      or f"Устранить {d['category']} (источник: stratum={idx + 1}).",
        "poruch_status": SEV_TO_STATUS.get(d["severity"], "в работе"),
        "close_fact": None if d["severity"] == "критичное" else "2026-12-31",
        "actions": f"Зафиксировано {d.get('affected_count', 'н/д')} объектов; "
                   f"план-график согласован с {d.get('responsible_unit')}.",
        "block_unit": d.get("responsible_unit"),
    }


# ───────────────────────────────────────────────────────────────────
# Дополнительные поручения, выведенные из текстов актов.
# Чтобы дотянуть до ~30, и чтобы каждое поручение имело уникальную
# `assignment_` (формула ключа — md5 от assignment).
# ───────────────────────────────────────────────────────────────────
EXTRA_PORUCH = {
    "КМ-99-12345": [
        # кредитный модуль / МСБ
        {"doc": "DOC-001-КМ-99-12345", "problem":
         "Задержки внесения данных об осмотрах залогов в АС «Кредитный модуль» "
         "(по 9 договорам сведения не вносились сроком более 6 месяцев).",
         "assignment_": "Внедрить обязательное заполнение карточек осмотров в АС "
                        "««Кредитный модуль»» при проведении выезда; отчёт по "
                        "незаполненным карточкам — еженедельно руководителю подразделения.",
         "status": "в работе", "close": None, "unit": "Дивизион Альфа"},
        {"doc": "DOC-002-КМ-99-12345", "problem":
         "Отсутствуют подписи ответственного сотрудника на заключениях кредитного "
         "инспектора в 5 кредитных досье.",
         "assignment_": "Провести разъяснительную работу с кредитными инспекторами; "
                        "обеспечить контроль наличия подписи при приёме досье.",
         "status": "исполнено", "close": "2026-09-30", "unit": "Дивизион Альфа"},
        {"doc": "DOC-003-КМ-99-12345", "problem":
         "Не применяется автоматический контроль давности финансовой отчётности "
         "заёмщика при одобрении кредитного лимита.",
         "assignment_": "Внедрить блокирующее правило в АС ««Кредитный модуль»» — "
                        "отказ в одобрении при отсутствии отчётности моложе 12 месяцев.",
         "status": "в работе", "close": None, "unit": "Дивизион Альфа"},
        {"doc": "DOC-004-КМ-99-12345", "problem":
         "Отсутствует автоматический контроль сроков мониторинга залогов.",
         "assignment_": "Настроить регламент с ежемесячным отчётом руководителю "
                        "подразделения о сроках осмотров залогов; уведомления — за "
                        "14 календарных дней до плановой даты осмотра.",
         "status": "в работе", "close": None, "unit": "Дивизион Альфа"},
        {"doc": "DOC-005-КМ-99-12345", "problem":
         "Не проведена сверка перечня активных кредитных лимитов по форме 1-А с "
         "данными АС ««Кредитный модуль»» по итогам проверки.",
         "assignment_": "Провести сверку перечня активных кредитных лимитов по "
                        "форме 1-А с данными АС ««Кредитный модуль»»; акт сверки "
                        "представить в службу внутреннего контроля.",
         "status": "частично исполнено", "close": None,
         "unit": "Дивизион Альфа"},
        {"doc": "DOC-006-КМ-99-12345", "problem":
         "Внутренний регламент работы с просроченной задолженностью не обновлялся с "
         "прошлого года, отсутствует контроль просрочки > 90 дней.",
         "assignment_": "Обновить регламент работы с просроченной задолженностью; "
                        "ввести контроль просрочки > 90 дней с еженедельной "
                        "эскалацией на руководителя подразделения.",
         "status": "в работе", "close": None, "unit": "Дивизион Альфа"},
        {"doc": "DOC-007-КМ-99-12345", "problem":
         "Не проведена проверка резервирования по кредитам, выданным с просрочкой "
         "обновления финансовой отчётности.",
         "assignment_": "Проверить резервирование по кредитам, выданным с просрочкой "
                        "обновления отчётности; при необходимости — доначислить "
                        "резервы согласно внутренней методике.",
         "status": "в работе", "close": None, "unit": "Финансовый департамент"},
    ],
    "КМ-99-12346": [
        # ИБ / управление доступом
        {"doc": "DOC-001-КМ-99-12346", "problem":
         "Учётные записи уволенных работников в АС «Платёжный шлюз» не "
         "заблокированы в день увольнения: 37 активных, под двумя зафиксированы "
         "входы после даты увольнения.",
         "assignment_": "Заблокировать учётные записи уволенных; настроить "
                        "автоматическую блокировку по данным кадровой системы "
                        "в день увольнения (SLA < 1 час).",
         "status": "в работе", "close": None, "unit": "ИТ-блок"},
        {"doc": "DOC-002-КМ-99-12346", "problem":
         "Пароли 11 сервисных учётных записей АС «Платёжный шлюз» не менялись "
         "более 12 месяцев.",
         "assignment_": "Назначить владельцев сервисных учётных записей; "
                        "ввести плановую смену паролей 1 раз в 90 дней; контроль "
                        "возраста пароля — на стороне ИБ-мониторинга.",
         "status": "в работе", "close": None, "unit": "ИТ-блок"},
        {"doc": "DOC-003-КМ-99-12346", "problem":
         "Периодический пересмотр прав доступа пользователей не проводился с "
         "прошлого года; матрица ролей не актуализирована.",
         "assignment_": "Ввести ежеквартальный пересмотр прав доступа по форме "
                        "Recert-1; согласование матрицы ролей — с владельцами ИС.",
         "status": "частично исполнено", "close": None, "unit": "ИТ-блок"},
        {"doc": "DOC-004-КМ-99-12346", "problem":
         "Отсутствует журналирование действий привилегированных учётных записей "
         "в АС «Платёжный шлюз».",
         "assignment_": "Включить журналирование действий привилегированных "
                        "учётных записей АС «Платёжный шлюз»; отправлять события "
                        "в SIEM.",
         "status": "в работе", "close": None, "unit": "ИТ-блок"},
        {"doc": "DOC-005-КМ-99-12346", "problem":
         "Не проводится периодический аудит ролевой модели Active Directory: "
         "группы безопасности не пересматривались.",
         "assignment_": "Провести аудит ролевой модели AD: сверка групп "
                        "безопасности с фактическими обязанностями сотрудников; "
                        "удаление неиспользуемых групп.",
         "status": "в работе", "close": None, "unit": "ИТ-блок"},
        {"doc": "DOC-006-КМ-99-12346", "problem":
         "Двухфакторная аутентификация для административных учётных записей не "
         "применяется в полном объёме.",
         "assignment_": "Внедрить двухфакторную аутентификацию для всех "
                        "административных учётных записей АС «Платёжный шлюз» и AD; "
                        "отчёт о покрытии — ежемесячно.",
         "status": "в работе", "close": None, "unit": "ИТ-блок"},
    ],
    "КМ-99-12347": [
        # обработка обращений / розница
        {"doc": "DOC-001-КМ-99-12347", "problem":
         "Нарушены сроки ответа на обращения клиентов: 115 обращений из 500 "
         "(23%) рассмотрены позже установленных 15 рабочих дней.",
         "assignment_": "Настроить в АС «Обращения» контроль сроков с эскалацией "
                        "за 3 рабочих дня до истечения срока; маршрут — на "
                        "руководителя подразделения.",
         "status": "в работе", "close": None, "unit": "Розничный бизнес"},
        {"doc": "DOC-002-КМ-99-12347", "problem":
         "Классификация обращений в АС «Обращения» не соответствует справочнику: "
         "60 обращений отнесены к неверной категории.",
         "assignment_": "Провести обучение операторов контакт-центра по "
                        "справочнику категорий; контрольная сверка — 1 раз в месяц.",
         "status": "в работе", "close": None, "unit": "Розничный бизнес"},
        {"doc": "DOC-003-КМ-99-12347", "problem":
         "Ответы клиентам по 8 обращениям не содержали решения по существу вопроса.",
         "assignment_": "Ввести выборочный контроль качества ответов (не менее "
                        "5% выборки) с обратной связью операторам.",
         "status": "исполнено", "close": "2026-10-15", "unit": "Розничный бизнес"},
        {"doc": "DOC-004-КМ-99-12347", "problem":
         "Отчётность по жалобам в АС «Обращения» искажена из-за ошибок "
         "классификации.",
         "assignment_": "Перевести в АС «Обращения» отчётность по жалобам на "
                        "основе фактической классификации после обучения; "
                        "архивная сверка — за прошлый квартал.",
         "status": "в работе", "close": None, "unit": "Розничный бизнес"},
        {"doc": "DOC-005-КМ-99-12347", "problem":
         "Не настроена автоматическая эскалация обращений с просроченным сроком.",
         "assignment_": "Настроить автоматическую эскалацию обращений с "
                        "просроченным сроком: первый уровень — руководитель "
                        "направления, второй — региональный руководитель.",
         "status": "в работе", "close": None, "unit": "Розничный бизнес"},
        {"doc": "DOC-006-КМ-99-12347", "problem":
         "Отсутствует обратная связь по итогам проверки удовлетворённости "
         "клиентов после закрытия обращений.",
         "assignment_": "Ввести периодический опрос удовлетворённости клиентов "
                        "по итогам закрытия обращений (NPS по обращениям).",
         "status": "в работе", "close": None, "unit": "Розничный бизнес"},
    ],
}


def _from_extra(km_id: str, items: list, year: str = "2026") -> list:
    rows = []
    for it in items:
        rows.append({
            "km_id": km_id,
            "doc_reg_num": it["doc"],
            "problem": it["problem"],
            "assignment_": it["assignment_"],
            "poruch_status": it["status"],
            "close_fact": it.get("close"),
            "actions": (
                f"Решение принято на совещании {year}-{it['doc'][-2:]}; "
                f"контроль исполнения — {it['unit']}."
            ),
            "block_unit": it.get("unit"),
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--truncate", action="store_true",
                    help="TRUNCATE public.t_fu_poruch_data перед загрузкой")
    ap.add_argument("--out", default=str(OUT_FIXTURE),
                    help="Куда сохранить порожденный JSON")
    args = ap.parse_args()

    corpus = json.loads(CORPUS.read_text(encoding="utf-8"))
    rows: list[dict] = []

    # 1. Из deviations testkit (9 штук)
    for doc in corpus["documents"]:
        km_id = doc["check_id"]                                # "КМ-99-12345"
        # синтетический doc_reg_num из file_id (testkit-km-99-12345 → DOC-001-КМ-99-12345)
        for i, dev in enumerate(doc.get("deviations", []), start=1):
            doc_reg_num = f"DOC-DEV-{i}-{km_id}"
            rows.append(_from_deviation(dev, km_id, doc_reg_num, i - 1))

    # 2. Дополнительные поручения, выведенные из текстов актов
    for km_id, items in EXTRA_PORUCH.items():
        rows.extend(_from_extra(km_id, items))

    # 3. Фикстуры из коробки (4шт) — для устойчивости к разным сценариям
    base = ROOT / "workspace/skills/follow_up/data/fixtures/poruch_fixture.json"
    if base.exists():
        for r in json.loads(base.read_text(encoding="utf-8")):
            r = dict(r)  # копия
            rows.append(r)

    # Дописываем poruch_key, чтобы seed-скрипт не падал на своей формуле
    out = []
    for r in rows:
        out.append({
            **r,
            "poruch_key": _poruch_key(r["km_id"], r.get("doc_reg_num"),
                                      r.get("assignment_")),
        })

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                              encoding="utf-8")
    print(f"расширенная фикстура: {args.out} ({len(out)} строк)")
    print(f"  КМ: {sorted({r['km_id'] for r in out})}")
    print(f"  статусы: {sorted({r['poruch_status'] for r in out})}")

    # Залить в Postgres
    sys.path.insert(0, str(ROOT))
    import config as botcfg
    botcfg._initialize_settings("test")
    flat = botcfg._flatten_env(botcfg._load_secrets_override())
    dsn = (flat.get("CHANNELS_POSTGRES_DSN")
           or flat.get("DATABASE_URL"))
    if not dsn:
        raise SystemExit("PG-источник не задан")
    print(f"DSN: {dsn}")

    with psycopg2.connect(dsn) as conn, conn.cursor() as cur:
        if args.truncate:
            cur.execute("TRUNCATE public.t_fu_poruch_data")
            print("TRUNCATE: OK")
        for r in out:
            cur.execute(
                "INSERT INTO public.t_fu_poruch_data "
                "(km_id, doc_reg_num, problem, assignment_, poruch_status, "
                " close_fact, actions, block_unit, poruch_key, updated_at) "
                "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s, now()) "
                "ON CONFLICT (poruch_key) DO UPDATE SET "
                "km_id=EXCLUDED.km_id, doc_reg_num=EXCLUDED.doc_reg_num, "
                "problem=EXCLUDED.problem, assignment_=EXCLUDED.assignment_, "
                "poruch_status=EXCLUDED.poruch_status, close_fact=EXCLUDED.close_fact, "
                "actions=EXCLUDED.actions, block_unit=EXCLUDED.block_unit, "
                "updated_at=now()",
                (r["km_id"], r.get("doc_reg_num"), r["problem"],
                 r["assignment_"], r["poruch_status"], r.get("close_fact"),
                 r.get("actions"), r.get("block_unit"), r["poruch_key"]))
        conn.commit()
        cur.execute("SELECT count(*) FROM public.t_fu_poruch_data")
        print(f"ВСЕГО в таблице: {cur.fetchone()[0]}")
        cur.execute(
            "SELECT km_id, count(*) FROM public.t_fu_poruch_data "
            "GROUP BY km_id ORDER BY km_id")
        print("по КМ:")
        for km, n in cur.fetchall():
            print(f"  {km}: {n}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())