## Решение

Ручной membership и Spark qualification AST используют casefold. Qualify получает
MappingSchema с вложенными уровнями schema/table/column и normalize=false.
Для PostgreSQL сохраняется исходный case KB и dialect-aware AST normalization,
поэтому quoted MixedCase не превращается в mixedcase.

Exact describe параметризован lower(table_name) IN, возвращает реальные IDs/колонки.
Сгенерированная успешная версия имеет status=valid, valid=true, publishable=true.
Failed payload рекурсивно исключает SQL/AST/plan/alternative поля и SQL statements
в сообщениях; repair diagnostics также фильтруются. Агенту запрещено самостоятельно
предлагать alternative/control SQL без отдельной успешной проверки.

## Граница

Payload gate и инструкции не являются универсальным outbound-фильтром LLM.
Реальные Spark/parser semantics проверяются отдельно на закрытом контуре;
тесты внешней машины используют fakes и явные skips отсутствующих зависимостей.
