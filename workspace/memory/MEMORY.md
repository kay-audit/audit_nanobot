# Long-term Memory

## Каналы и инфраструктура
- Канал обмена по умолчанию: postgres (таблица public.agent_conversation_messages)
- Пользователь идентифицируется абстрактно как «Коллега»

## Поведенческие правила
- Всегда писать reasoning и ответы **на русском**
- Не задавать уточняющих вопросов ради вопросов — действовать по контексту
- Паттерн ретраев: _mark_failed → status='retry'; _unstick_processing → 'pending' или 'failed'
- Имена файлов/таблиц — на латинице, комментарии — на русском

## Паттерны работы с данными
- Аудиторские данные: схема `oarb`, таблицы audit_reports/audits/report_items/violations
- Кэш: DuckDB (`workspace/skills/audit_analyzer/cache/audit_cache.duckdb`)
- Векторный поиск: FAISS (`audit_vectors`), --threshold приоритетнее --top-k
