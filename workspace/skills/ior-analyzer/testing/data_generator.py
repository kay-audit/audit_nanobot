from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

DEFAULT_SEED = 20260921
RECORD_COUNT = 1000
DATA_PATH = Path(__file__).resolve().parents[3] / "data_store" / "cache" / "testing" / "ior" / "ior.json"

THEMES = [
    ("ошибочная комиссия", "Ошибочно списана комиссия по операции клиента", "Операционный процесс"),
    ("двойное списание", "Произошло двойное списание денежных средств", "Исполнение операции"),
    ("технический сбой", "Технический сбой прервал обслуживание в цифровом канале", "ИТ-системы"),
    ("мошенничество", "Выявлена мошенническая операция с социальной инженерией", "Внешнее мошенничество"),
    ("ошибка сотрудника", "Сотрудник неверно указал реквизиты операции", "Человеческий фактор"),
    ("недоступность сервиса", "Сервис был временно недоступен для клиентов", "Доступность"),
    ("некорректное начисление", "Некорректно начислены проценты по продукту", "Расчёты"),
    ("проблема перевода", "Перевод задержан из-за ошибки маршрутизации", "Платежи"),
    ("ошибка данных", "Обнаружена ошибка обработки клиентских данных без реальной утечки", "Данные"),
    ("кредитный процесс", "Ошибка кредитного процесса привела к задержке решения", "Кредитование"),
]
PRODUCTS = ["Карты", "Переводы", "Кредиты", "Счета", "Мобильный банк"]
CHANNELS = ["Мобильный банк", "Веб", "Офис", "Контакт-центр", "API"]
STATUSES = ["Закрыт", "В работе", "Возмещён", "На проверке"]


def generate_records(seed: int = DEFAULT_SEED, count: int = RECORD_COUNT) -> list[dict]:
    rng = random.Random(seed)
    start = date(2024, 1, 1)
    records = []
    for index in range(1, count + 1):
        theme, description, category = THEMES[(index - 1) % len(THEMES)]
        loss = 0 if index % 5 == 0 else round(rng.uniform(150, 4_500_000), 2)
        reimbursement = round(loss * rng.choice([0, 0, 0.25, 0.5, 1.0]), 2)
        records.append({
            "eve_id": f"EVE-TEST-{index:04d}",
            "drp": f"DRP-TEST-{rng.randint(1, 40):03d}",
            "date": (start + timedelta(days=rng.randint(0, 1095))).isoformat(),
            "event_type": theme,
            "category": category,
            "description": f"{description}. Тестовый кейс {index:04d}.",
            "status": rng.choice(STATUSES),
            "financial_loss": loss,
            "reimbursement": reimbursement,
            "business_line": rng.choice(["Розничный бизнес", "Корпоративный бизнес", "Операции"]),
            "product": rng.choice(PRODUCTS),
            "channel": rng.choice(CHANNELS),
            "cause": theme,
            "consequences": "Финансовые и/или сервисные последствия" if loss else "Без финансовых потерь",
        })
    return records


def ensure_testing_data(*, force: bool = False, path: Path = DATA_PATH, seed: int = DEFAULT_SEED) -> Path:
    if force or not path.is_file():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(generate_records(seed), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_records(path: Path = DATA_PATH) -> list[dict]:
    return json.loads(ensure_testing_data(path=path).read_text(encoding="utf-8"))


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    args = parser.parse_args()
    print(ensure_testing_data(force=args.force, seed=args.seed))
