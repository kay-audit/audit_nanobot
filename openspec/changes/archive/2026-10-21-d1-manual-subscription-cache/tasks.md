## 1. Реализация
- [x] 1.1 Добавить ручные ресурсы и проверить отсутствие polling тестом.
- [x] 1.2 Реализовать полную атомарную загрузку/публикацию и проверить rollback/add/delete на DuckDB.
- [x] 1.3 Перевести resolver на внутренний DuckDB API и проверить отсутствие GP, изоляцию и отсутствие усечения.
- [x] 1.4 Документировать команды GP/offline, устаревание и защиту кэша.

## 2. Интеграция
- [x] 2.1 Выборочно перенести в актуальный finalize и выполнить D1/runtime-регрессии.
- [x] 2.2 Проверить baseline failures, OpenSpec и защищённые компоненты, затем commit/push двух веток.

Проверка: исходная ветка — 457 passed, 2 skipped и 22 subtests; финальный операторский набор — 16 passed. Интеграционная ветка — 458 passed и 22 subtests D1/DuckDB; runtime — 189 passed, 1 skipped. D3/D5/D6 — шесть прежних failures подтверждены на d5d710b; дополнительный transient Windows lock failure прошёл отдельный повтор. Protected components и прежние значения конфига не изменены.

Canonical specs синхронизированы вручную и проверены strict; архивирование использует --skip-specs. Корпоративная identity/schema/GP проверка остаётся операторской, identity_confirmed=false.
