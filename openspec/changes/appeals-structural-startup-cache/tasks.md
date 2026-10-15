## 1. Implementation

- [x] Восстановить standalone/production call graph.
- [x] Добавить bounded startup projection и Arrow bulk API.
- [x] Подключить публикацию до gateway request lifecycle.
- [x] Разделить production population и GP hydration.
- [x] Повторить filters/date и defensive validation при hydration/follow-up.

## 2. Verification

- [x] Добавить targeted prebuilt source/projection/duplicates/routing/failure tests; eligibility выполняется внешним ETL.
- [x] Выполнить targeted tests и проверки core regressions: 349 passed, 2 subtests passed; gateway smoke exit 0, новый модуль Ruff clean.
- [ ] Выполнить OpenSpec CLI validation (CLI отсутствует в локальном окружении).
- [ ] Проверить Greenplum execution plan, объём и полный gateway e2e на закрытом контуре.
