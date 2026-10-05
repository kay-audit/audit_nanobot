---
name: appeals-analyzer
description: "Поиск релевантных клиентских обращений, аналитический отчёт и гипотезы, XLSX и уточнения по сохранённой выгрузке."
metadata: {"nanobot":{"emoji":"📩"}}
---

# Appeals Analyzer

`FINAL_DELIVERY_MODE: APPEALS_REPORT_VERBATIM_V1`

Формирует выборку обращений, математический профиль, четыре заземлённые
гипотезы и XLSX. Смысловую релевантность определяет гибридный
поиск FAISS + BM25 + RRF + BGE reranker в Osiris.

## Контракт вызова и ответа

- Передавай в параметр `prompt` **полный исходный prompt пользователя** без
  пересказа или сокращения. Каноническое сообщение, начинающееся со строки
  `Анализ обращений`, и JSON с `request_type = "appeals_analysis"` —
  однозначные маркеры этого skill; legacy CSV также разбирает сам skill.
- Успешный результат скилла является готовым ответом. Выведи **полный ответ
  LLM/скилла от первой до последней строки**, сохранив порядок, Markdown,
  таблицы, числа, все четыре гипотезы, пути и ссылки на файлы.
- Не делай после скилла summary-pass и не добавляй собственное вступление или
  заключение. Преобразование допустимо только по явному запросу пользователя.

## Режимы

| Режим | Когда использовать |
|:------|:-------------------|
| `new-search` | Prompt является canonical `Анализ обращений`, `appeals_analysis` JSON или корректной четвёркой quoted CSV-полей |
| `follow-up-id` | Вопрос содержит ID из сохранённой выгрузки |
| `follow-up-search` | Смысловой вопрос относится к сохранённой выгрузке |
| `follow-up-dialog` | Общий вопрос о ранее сформированном отчёте/гипотезе |

## Формат новой выгрузки

Предпочтительный WEB-контракт — человекочитаемый canonical envelope. Каждое
значение фильтра занимает отдельную bullet-строку; отсутствующие секции означают
пустые фильтры. После `Запрос:` передаётся полный, в том числе многострочный,
пользовательский текст:

```text
Анализ обращений

Продукт:
- Кредиты

Канал:
- СБОЛ

Период:
с 01.01.2026

Запрос:
Найди жалобы клиентов на блокировку перевода
```

JSON-контракт сохранён для обратной совместимости. Массивы фильтров независимы,
пустой массив означает «все значения», даты могут быть `null` по отдельности:

```json
{
  "request_type": "appeals_analysis",
  "filters": {
    "prd": ["Кредиты"],
    "s_prd": [],
    "chnl": ["СБОЛ"],
    "date_from": "2026-01-01",
    "date_to": null
  },
  "prompt": "Найди жалобы клиентов на блокировку перевода"
}
```

Для canonical даты берутся только из секции `Период:`, для JSON — из `filters`.
Semantic query не переопределяет период, LLM-разбор дат не вызывается.
Значения frontend-фильтров authoritative: `Офис` и `Чат` передаются как есть,
без проверки по legacy dictionary или семантической подмены.
Словарь `canonical_filters.json` используется только для legacy CSV.

Legacy-контракт сохранён без изменений. Передавай исходную строку целиком:

```text
"продукты", "субпродукты", "каналы", "смысловой запрос"
```

Внутри каждого поля действует OR/IN, непустые группы `prd`/`s_prd`/`chnl`
объединяются через AND. Пустые группы не добавляют ограничений; период — ещё один AND. Значения
`prd`/`s_prd`/`chnl` должны быть каноническими; внутренние запятые и
CSV-escaped кавычки поддерживаются. Корректная четвёрка всегда начинает новую
выгрузку, обычный текст после неё считается follow-up.

## Pipeline новой выгрузки

1. Native Nanobot Tool читает structural population из общего Gateway DuckDB
   snapshot. Только 2026 по текущей конфигурации; используется `app_row_id`.
   Population содержит непустой `req_desc` либо `msg_pprb_chat`/`msg_crm_call`.
   При недоступности snapshot возвращается ошибка, прямого GP fallback нет.
2. Osiris получает query + allowed IDs; переводит IDs в позиции глобального
   корпуса и применяет FAISS selector / BM25 shard-local weight masks до поиска.
   Индексы не перестраиваются; неизвестные vector IDs молча пропускаются.
3. Weighted RRF использует FAISS_K=2048, BM25_TOTAL_K=1372, ALPHA=0.3, K_RRF=60.
   Весь fused pool возвращается Gateway, без дополнительного top-K.
4. Gateway гидратирует кандидатов из Greenplum с повторением structural filters/date. Appeals, dialogs и tasks
   читаются раздельно, связи агрегируются до merge по `app_row_id`.
5. Canonical text: `req_desc` + непустой `msg_pprb_chat`, иначе `msg_crm_call`.
   Osiris reranker возвращает все input IDs со scores без фильтрации по score.
   Только report selection выбирает обращения со score строго > 0.5: они
   входят в итог без верхнего лимита. Если их меньше 500, итог дополняется
   лучшими оставшимися кандидатами по score до 500 уникальных обращений.
   Score ровно 0.5 не проходит порог, но участвует в доборе.
   Если доступно меньше 500 кандидатов, возвращаются все доступные.
   При пустом retrieval/hydration XLSX и гипотезы не создаются.
6. Профиль и counts считаются по полной final-выборке. LLM формирует ровно
   четыре гипотезы. Создаётся ровно один XLSX без CSV в
   `workspace/data_store/cache/sessions/<safe_session_key>/results/`.
7. Native Tool использует настоящий request session key и штатный MessageTool:
   полный отчёт + media=[путь к XLSX]. Повторно отправлять отчёт не нужно,
   если он уже доставлен инструментом. Самостоятельно формировать ссылки
   на локальные пути или искать файлы по каталогам не нужно.
8. Follow-up использует сохранённые final IDs. Тематический поиск повторяет
   global retrieval/rerank с этой маской; per-session FAISS не создаётся.

Смысловой `ILIKE` не используй. Не раскрывай внутреннюю методику sampling и не
выводи технические имена колонок.

## Семантический поиск и модели (только Osiris)

- cache: `workspace/data_store/cache/caches_pipelines/cache_le_finale2`;
- BGE-M3: `workspace/data_store/cache/caches_pipelines/BAAI:bge-m3`;
- reranker: `workspace/data_store/cache/caches_pipelines/bge-reranker-v2-m3`.

Переопределения: `APPEALS_RAG_CACHE_DIR`, `APPEALS_BGE_MODEL_PATH`,
`APPEALS_RERANKER_MODEL_PATH`. `meta.pkl` — основной источник metadata;
`meta_final.pkl` допустим только как fallback или через
`APPEALS_RAG_META_FILE`. Для online search не нужны documents, req_descs,
msg_pprb_chats, tokenized_corpus или загрузка embeddings.memmap. BGE и reranker
работают на CUDA:0; FAISS — GPU при поддержке selector, иначе CPU внутри Osiris.
Импорт модулей не запускает Osiris. Пользовательский запрос через Gateway или
standalone CLI вызывает общий `ensure_ready`: при остановленном worker сервис
стартует один раз под NFS lock и ждёт READY до 5 минут. При неудаче старта
пользователь получает просьбу повторить запрос через 15 минут. Оператор
может отдельно вызвать `python osiris_job.py start|status|stop` либо
совместимый `appeals_osiris_job.py`. Неиспользуемый worker выключается по
idle TTL (по умолчанию 1 час). Детали — [PRODUCTION.md](PRODUCTION.md).

## CLI

```bash
bash workspace/skills/appeals-analyzer/appeals_analyze.sh --profile prod \
  --prompt '"Кредиты", "", "IVR", "жалобы за 2026 год"' \
  --session-id sess_001
```

`--prompt` всегда содержит полную исходную строку пользователя.

Standalone CLI явно использует direct Greenplum через существующий общий DB pool,
без инициализации Gateway/DuckDB. Это отдельный режим, не fallback Tool.
После structural IDs используется тот же Osiris pipeline. Аргумент `filters`
у `run_appeals_report` сохранён для совместимости, источником фильтров остаётся prompt.

**Production blocker:** shared sync API пока не поддерживает initial-only для
этих таблиц без неподтверждённого tracking column. Регистрация
`skills.appeals_analyzer` оставлена `enabled: false`; включать её с текущим
polling небезопасно. Подробности и проверки — в [PRODUCTION.md](PRODUCTION.md).
