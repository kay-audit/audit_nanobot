# Команды ingest

Фаза 1 — создать Proposal и остановиться:

```bash
python3.12 -m wiki_agent ingest raw/sources/<имя-файла>
```

Можно ограничить область:

```bash
python3.12 -m wiki_agent ingest raw/sources/<имя-файла> \
  --request "Интегрировать только сведения о ..."
```

Фаза 2 — после ручной проверки Proposal:

```bash
python3.12 -m wiki_agent apply proposals/<точное-имя>.md
```

`apply` не вызывает LLM и требует ввести точный путь Proposal.
