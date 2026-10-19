## Context

Мотивация — proposal.md. KB сохраняет физические имена; RequestContext предоставляет sender_id, но старый PostgresChannel подставлял chat_id. Корпоративная БД недоступна в локальных mock-тестах.

## Goals / Non-Goals

Разрешать подписки детерминированно без пользовательских данных в RAG. Не выполнять бизнес-SQL и не менять корпоративные объекты.

## Decisions

- `lib/services/subscription_resolver.py`: два адресных параметризованных SELECT через workspace.utils.db, без кэша. Альтернатива общего DuckDB-кэша отклонена из-за изменяемости и приватности подписок.
- `current_sudir_login`: только numeric sender_id Postgres RequestContext. `identity_confirmed=false` до подтверждения оператором; chat fallback убран в обоих inbound-путях PostgresChannel.
- `meta_code=schema_name` подтверждено пользователем как бизнес-правило. Отсутствие колонок диагностируется ошибкой чтения, неизвестная связь — subscription_mapping_unknown.
- `SubscriptionResolution.rewrite`: scoped AST sources и квалифицированные ссылки; чужие алиасы и неизвестные таблицы отклоняются. Валидатор получает копию физической схемы под public-ключами.
- Runtime проверяет подписки до LLM и при каждой проверке SQL. READY не обращается к resolver.
- Shared OSIRIS: D1 использует logical appeals embed/rerank через deployment binding; request correlation и heartbeat делегируются текущему transport. Никаких create/recover.

## Risks / Trade-offs

Неподтверждённый корпоративный ID → выдача нового SQL блокируется до проверки. Изменение подписки во время ответа → повторное чтение при валидации, без бессрочного кэша. Недоступность catalog → явный статус без вывода об отсутствии подписки.

## Migration Plan

Выборочный перенос D1-файлов в finalize_nanobot с сохранением актуальных общих runtime/OSIRIS. DDL поставляется для оператора, не применяется. Проверить закрытый контур по docs/SQL_ASSISTANT.md; затем явно включить identity_confirmed. Для отката выключить шесть gateway tools; D3/D5/D6-конфигурации сохраняются.
