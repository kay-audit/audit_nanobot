## Why

Закрытый контур отклоняет INSERTED_DTTM и PA_ID при lowercase AST/uppercase KB.
После invalid агент предлагает непроверенный alternative SQL.

## What Changes

Вложенная MappingSchema и одинаковый casefold для Spark; exact table_names в
kb_describe; generated status=valid и запрет любого непроверенного fallback.
Регрессии без инфраструктуры и реальные parser-тесты при наличии sqlglot.

## Impact

Только SQL Assistant; generic outbound, gateway, sync и Osiris не меняются.
Ready-script verbatim остаётся отдельным контрактом.
OpenSpec CLI отсутствует; артефакты ведутся вручную без заявления об официальной
валидации. Изменяется существующая spec skills/sql-assistant-integration.
