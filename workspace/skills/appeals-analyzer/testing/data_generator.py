from __future__ import annotations

import argparse
import json
import random
from datetime import date, timedelta
from pathlib import Path

DEFAULT_SEED = 20260921
RECORD_COUNT = 100
DATA_PATH = Path(__file__).resolve().parents[3] / "data_store" / "cache" / "testing" / "appeals" / "appeals.json"

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


def generate_records(seed: int = DEFAULT_SEED, count: int = RECORD_COUNT) -> list[dict]:
    rng = random.Random(seed)
    start = date(2024, 1, 1)
    rows = []
    for index in range(1, count + 1):
        prd, s_prd, chnl, text = TOPICS[(index - 1) % len(TOPICS)]
        suffix = rng.choice([
            "Обращение направлено на проверку профильному подразделению.",
            "Клиент ожидает письменное разъяснение и корректировку операции.",
            "Требуется проверить историю операции и сообщить результат.",
        ])
        rows.append({
            "appeal_id": f"APPEAL-TEST-{index:03d}",
            "date": (start + timedelta(days=rng.randint(0, 1095))).isoformat(),
            "prd": prd,
            "s_prd": s_prd,
            "chnl": chnl,
            "text": f"{text} {suffix} Номер синтетического кейса {index:03d}.",
        })
    return rows


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
