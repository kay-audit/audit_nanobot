# KB source format

Canonical tables: `s_grnplm_ld_audit_da_project_34.kb_tables`, `s_grnplm_ld_audit_da_project_34.kb_columns`,
`s_grnplm_ld_audit_da_project_34.kb_examples`; полный DDL — `sql/sql_assistant/001_create_kb.sql`.
Списки (`nl_variants`, `tables`) хранятся JSON-текстом для GP6/DuckDB parity.
`kb_examples.tables` содержит только полные физические имена таблиц, например
`["prd.orders","prd.clients"]`; это внешние читаемые источники, а не все
упоминания таблиц. CREATE/INSERT/DROP targets, CTE aliases и промежуточные
таблицы, созданные ранее в том же скрипте, туда не записываются. Statements
обрабатываются по порядку; Hive DDL поддерживает безопасный FROM/JOIN fallback.
Каждая запись имеет стабильный `id` и `updated_at` tracking column.

`bootstrap_kb.py` читает независимые источники: legacy examples через
`--source-table` и полный metadata catalog через `--metadata-table`.
Default metadata source:
`s_grnplm_ld_audit_da_sandbox_oarb.dvb_kav_repl_test`.
Ошибки разбора examples влияют только на `kb_examples.tables`,
но не исключают таблицы или колонки из metadata KB.

Одна строка `kb_tables` создаётся для нормализованной пары schema/table:
полное имя из `schema_name + "." + table_name`, описание из `table_descr`,
dialect `spark`. `schema_descr` не сохраняется. Колонки используют
`field_name`, `field_type`, `field_descr`, ordinal NULL.
Summary ограничен `--summary-columns` (default 50), сортировка по имени поля.

IDs — BLAKE2b digest_size=8, положительный 63-bit BIGINT (ноль заменяется на 1).
Table key: `lower(schema_name) + "." + lower(table_name)`.
Column key: тот же key + `"." + lower(field_name)`. Пробелы по краям удаляются.
`kb_columns.table_id` ссылается на synthetic ID родительской таблицы.
Коллизии разных logical keys завершают bootstrap до записи.
NULL/пустые schema/table/field учитываются как invalid и пропускаются целиком.
Повторные нормализованные column keys считаются в `duplicate_columns`:
идентичные metadata дедуплицируются; конфликтующие metadata вызывают ошибку.
Исходный SQL не изменяется. Dry-run только читает источники и откатывает
read-only транзакцию. Старые KB IDs автоматически не мигрируются и не удаляются:
перед apply необходим проверенный rebuild/migration в закрытом контуре.
DDL и generic runtime не меняются.

Admin CLI использует --profile prod|test и общий workspace.utils.db.resolve_dsn:
channels.postgres.dsn может быть напрямую задан в project.json; env не обязателен.
--dsn-env оставлен только для явного legacy override.
Apply сохраняет существующий bulk-upsert через temporary staging и execute_values.
Builder читает полный corpus keyset-страницами, а не первые 100000 строк.
BGE embeddings/reranker доступны через existing Osiris worker; подробности —
docs/SQL_ASSISTANT.md. Gateway не загружает веса локально.

`load_metadata.py --kind tables|columns --input file.csv|file.jsonl` принимает
ровно поля DDL. `id` обязателен; для tables обязателен `table_name`, для columns —
`table_id` и `column_name`. Сначала используйте `--dry-run`; target table и DSN
задаются явно (`--target-table`, `--dsn-env`).
