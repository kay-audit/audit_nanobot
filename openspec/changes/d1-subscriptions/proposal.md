## Why

SQL Assistant должен выдавать новый SQL через действующие подписки текущего пользователя. Физические имена KB не подтверждают доступ, а отсутствие корпоративной идентификации нельзя считать отсутствием подписки.

## What Changes

- Добавить адресный SubscriptionResolver без общего кэша пользовательских данных.
- Проверять подписки перед генерацией и каждой валидацией; преобразовывать внешние источники через AST.
- Сохранить READY verbatim и delivery gate.
- Адаптировать D1 к shared OSIRIS и выборочно интегрировать в finalize_nanobot.

## Capabilities

### New Capabilities

- `skills/sql-subscriptions`: разрешение физических источников в подписные схемы текущего пользователя.

### Modified Capabilities

## Impact

Новый runtime resolver, SQL Assistant runtime/static validation, PostgresChannel identity, конфигурация, тесты и документация. DDL не применяется; корпоративная идентичность требует проверки оператором. Общие сервисы D3/D5/D6 не заменяются.
