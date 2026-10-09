# Workflow

Ready и generation — разные продуктовые контракты. Ready возвращает неизменную
запись KB. Generation получает компактные поисковые результаты, расширяет только
выбранные карточки, генерирует один SELECT/WITH, валидирует, чинит максимум дважды
и извлекает AST facts. Ни один шаг не исполняет запрос по данным.

Статусы `not_ready`, `unavailable`, `timeout`, `invalid` являются нормальными
структурированными результатами. Не маскируй их выдуманными таблицами/колонками.

Явная таблица: exact `schema.table` -> `kb_describe(table_names=[...], detail="full")`
-> только реально возвращённые колонки/table ID -> `sql_generate` -> `sql_validate`.
Semantic search не заменяет exact lookup. До describe нельзя угадывать колонки.

Generated SQL разрешено выдавать только при `status=valid`, `publishable=true` и `valid=true`.
При invalid/ошибке выдай причины проверки и нужные уточнения, без SQL-блока.
Не восстанавливай скрытый SQL из prior_sql, repair attempts или истории.
Запрещён любой самостоятельно составленный fallback/alternative SQL, включая
контрольные запросы и примеры PA_ID/COUNT(SN_ID), пока конкретная новая версия
не прошла отдельный sql_validate с тремя разрешающими полями. При exhausted repair
финал содержит только причины и рекомендации без SQL-кода.
Tool audit status=ok отражает транспорт/вызов, а не semantic validation.
Static-only не подтверждает выполнение SQL или наличие таблиц в real Spark.

