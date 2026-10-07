## Why

Generated SQL со статусом invalid выдаётся как готовый ответ. Административные
скрипты требуют env DSN; локальный inference BGE не соответствует закрытому
контуру. Корпус колонок обрезается на 100000 строк.

## What Changes

- SQL Assistant SHALL скрывать невалидный generated SQL из публичной выдачи.
- Admin CLI SHALL использовать общий DSN resolver после инициализации профиля.
- SQL Assistant SHALL использовать существующий Osiris worker через общий NFS
  transport без create/delete/restart; существующий worker получает embed handler.
- Builder SHALL читать полный корпус и проверять полноту перед публикацией.

## Non-goals

Generic Nanobot, gateway lifecycle, DB pool, DuckDB sync не меняются.
Реальная инфраструктура, скачивание моделей и создание контейнеров вне scope.

## Tooling

OpenSpec CLI отсутствует на внешней машине. Артефакты сохранены вручную;
официальная CLI-валидация остаётся проверкой закрытого контура.
