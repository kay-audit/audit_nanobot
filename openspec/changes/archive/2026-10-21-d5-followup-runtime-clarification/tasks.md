## 1. Source
- [x] 1.1 Сверить и перенести пять патчей, manifest и обновления source.
- [x] 1.2 Проверить идентичность, письмо, КМ и семерку tools; выполнить целевые тесты и testkit.

## 2. Интеграция
- [x] 2.1 Выборочно перенести D5 в finalize и выполнить межкомпонентные проверки с baseline.
- [x] 2.2 Подготовить локальные коммиты и отчёт, без push.

Source: целевые тесты 90 passed; runtime/startup 217 passed, 1 skipped. Stock testkit выполнен, загрузка корпуса блокируется отсутствующими весами. Отдельный degraded smoke использует явно тестовый encoder, реальная LLM/GP не вызываются; это не model end-to-end.

Итог: 93 passed D5 в каждой ветке; source runtime 217 passed/1 skipped (включает один связанный D5-тест); finalize runtime 249 passed/1 skipped; cross-component 252 passed. Stock testkit блокируется отсутствующими весами; отдельный smoke с тестовым encoder/недоступной LLM: 14 сценариев, 7 pass/4 warn/3 fail, не model end-to-end. Push не выполняется.
