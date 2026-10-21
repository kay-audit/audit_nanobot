## Context

См. proposal.md. Дерево D7 в origin/vllm_fix совпадает с finalize_nanobot. Архив содержит 162 одинаковых, 69 изменённых, 15 новых и не содержит 2 существующих файла.

## Goals / Non-Goals

Добавить history без замены summary stack. Не переносить unrelated форматирование и откаты CLI/section_reduce. Не менять глобальное контекстное окно, OSIRIS или протокол старого query.

## Decisions

- Перенести только новые history модули; исходный skill приоритетен. Отличия каждого файла фиксируются в инвентаризации.
- Native tool получает RequestContext и проверяет все разрешённые пути после resolve, включая содержимое директорий и media. Владелец session namespace закрепляется отпечатком raw session_key/sender_id, коллизии sanitize отклоняются.
- Background worker запускается argv без shell с явными session/profile; статус и checkpoint атомарны, run.lock удерживается OS. Кэш history отделён от summary manifest.
- Независимые батчи используют общий LLM resolver/singleflight и локальную history policy. Полные факты сохраняются; сжатие применяется только к представлению LLM. Успешные IDs сохраняются до следующего батча.
- Tool вызывает существующий MessageTool для обоих результатов при status/start/resume завершённой операции; delivery error не маскируется успехом. Большой отчёт не читается агентом.

## Risks / Trade-offs

- Реальные ConsultantPlus DOCX и корпоративный vLLM недоступны → синтетические DOCX/ZIP и mock HTTP, отдельный список проверок закрытого контура.
- Длинные пути Windows и lifecycle SETTINGS влияют на legacy-тесты → baseline с явным init и коротким basetemp, затем одинаковая среда для сравнения.
- Завершение worker вне оборота не отправляет сообщение самостоятельно → агент получает результат через status, который выполняет media-доставку.

## Migration Plan

Коммиты feature/d7_finalizer от origin/vllm_fix; cherry-pick в finalize_nanobot с сохранением остальных навыков. Push обеих веток разрешён пользователем. Откат — revert D7-коммитов без изменения существующих данных summary.
