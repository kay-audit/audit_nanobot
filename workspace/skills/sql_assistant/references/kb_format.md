# KB source format

Canonical tables: `sqlagent.kb_tables`, `sqlagent.kb_columns`,
`sqlagent.kb_examples`; полный DDL — `sql/sql_assistant/001_create_kb.sql`.
Списки (`nl_variants`, `tables`) хранятся JSON-текстом для GP6/DuckDB parity.
`kb_examples.tables` содержит только полные физические имена таблиц, например
`["prd.orders","prd.clients"]`; CTE и aliases туда не записываются.
Каждая запись имеет стабильный `id` и `updated_at` tracking column.

Реальные sources tables/columns задаются loader CLI-параметрами. Репозиторий не
содержит доказанного источника 1002/65k, поэтому имена не хардкодятся.

`load_metadata.py --kind tables|columns --input file.csv|file.jsonl` принимает
ровно поля DDL. `id` обязателен; для tables обязателен `table_name`, для columns —
`table_id` и `column_name`. Сначала используйте `--dry-run`; target table и DSN
задаются явно (`--target-table`, `--dsn-env`).
