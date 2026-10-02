# Команда lint

Полный lint с GigaChat:

```bash
python3.12 -m wiki_agent lint
```

Только локальные технические проверки без ключа и сети:

```bash
python3.12 -m wiki_agent lint --technical-only
```

Обе команды создают новый отчёт в `reports/lint/` и ничего не исправляют.
