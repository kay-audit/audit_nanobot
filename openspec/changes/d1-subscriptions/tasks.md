## 1. Подписки и выдача

- [x] 1.1 Проверить Git HEAD и WIP, создать feature/d1_sql от d1_nanobot.
- [x] 1.2 Реализовать доверенную идентичность без chat fallback и адресный resolver.
- [x] 1.3 Добавить AST mapping, обязательную проверку подписок и metadata validation.
- [x] 1.4 Сохранить READY verbatim, read-only и максимум два repair.
- [x] 1.5 Покрыть resolver, SQL и delivery mock-регрессиями.

## 2. Интеграция

- [x] 2.1 Перенести D1 выборочно в finalize_nanobot и проверить shared OSIRIS.
- [x] 2.2 Выполнить релевантную регрессию и зафиксировать результаты.
- [ ] 2.3 Проверить DDL/catalog permissions и user_id=sudir_login в закрытом контуре до включения identity_confirmed.
