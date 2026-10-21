# skills/legal-document-history Specification

## Purpose
История изменений одного явно выбранного закона по приложенному обзору ConsultantPlus с сохранением фактов, фоновой обработкой и доставкой артефактов текущему пользователю.

## Назначение

Добавочный сценарий D7 для одного закона и соответствующего обзора.

## Ответственность

Выбрать и проверить пару, сохранить факты и пояснения, возобновить фон,
сохранить отчёт и передать два файла текущему пользователю.

## Граница

Owns: history selection, ZIP/DOCX guard, checkpoints, worker, native boundary.
Does not own: прежние summary/query manifests, корпоративную БД, OSIRIS lifecycle.
May depend on: общий LLM resolver/client, skill single-flight, SessionFileStore,
RequestContext и MessageTool. Must not depend on: произвольный путь как identity,
внешний обязательный сервис или новый GPU job.

## Публичный контракт

`legal_summarizer_history(inputs, target, overview, action, operation_id)`.
Действия start/status/resume. Статусы needs_input/running/completed/partial/interrupted/error.
Готовый результат содержит operation_id, счётчики, media и delivery.status.

## Требования

Нормативные требования и проверяемые сценарии находятся ниже в Requirements.

## Запрещённое поведение

Чужие файлы/операции, fallback chat_id, выбор самого большого закона,
выдуманные даты и цитаты, global reduce, полный отчёт в агентском контексте,
пагинация публичного tool, shell=True и изменение глобальных лимитов LLM.

## Зависимости

stdlib, pydantic, существующие runtime tools и общий LLM клиент.

## Реализация

- `workspace/tools/legal_summarizer_history.py`
- `workspace/skills/legal_summarizer/scripts/cli_history.py`
- `workspace/skills/legal_summarizer/scripts/application/history_pipeline.py`
- `workspace/skills/legal_summarizer/references/doc_history.md`
- `lib/core/project_settings.py::SkillHistorySettings`

## Проверка

Четыре history-теста архива, test_history_security_delivery.py, query IPC,
config/runtime inventory и регрессии D1/D3/D5/D6/OSIRIS. Синтетический native
E2E использует реальный dispatcher/worker/MessageTool и mock LLM.
Real-model качество и frontend закрытого контура ещё не подтверждены.

## Requirements

### Requirement: Trusted session boundary
Tool SHALL использовать только session_key и sender_id из RequestContext и SHALL проверять принадлежность входов, операции и результатов текущей сессии.

#### Scenario: Foreign input
- **WHEN** вход или сохранённый результат находится вне доверенной сессии
- **THEN** tool SHALL вернуть структурированную ошибку без чтения документа и доставки.

### Requirement: Explicit evidence selection
Pipeline SHALL анализировать один явно выбранный закон и соответствующий обзор; SHALL запрашивать уточнение при неоднозначности и SHALL NOT загружать документы из интернета.

#### Scenario: Missing target
- **WHEN** цель не задана или обзор не соответствует закону
- **THEN** результат SHALL иметь статус needs_input без LLM-вызова.

### Requirement: Safe document containers
Pipeline SHALL отклонять traversal, absolute paths, symlinks, дубликаты имён, зашифрованные, чрезмерные и повреждённые ZIP/DOCX до разбора XML.

#### Scenario: Unsafe ZIP member
- **WHEN** ZIP содержит небезопасную запись
- **THEN** операция SHALL завершиться структурированной ошибкой без извлечения записей.

### Requirement: Independent semantic batches
Pipeline SHALL сохранять полный before/after и явно заданные даты, сортировать события хронологически и ограничивать независимые LLM-батчи общим контекстом и резервом ответа.

#### Scenario: Invalid model response
- **WHEN** ответ содержит неверные IDs или невалидный JSON и ограниченные повторы исчерпаны
- **THEN** факты SHALL сохраниться с отметкой отсутствующего пояснения и статусом partial.

### Requirement: Durable background lifecycle
Tool SHALL быстро возвращать operation_id; worker SHALL сохранять успешные пояснения немедленно, переиспользовать их при resume и освобождать OS lock при завершении процесса.

#### Scenario: Interrupted worker
- **WHEN** worker умер и операция запущена повторно
- **THEN** успешно сохранённые события SHALL NOT объясняться повторно.

### Requirement: Session artifact delivery
Результат SHALL сохраняться через SessionFileStore как Markdown и JSON, а native tool SHALL передавать проверенные пути в MessageTool(media=...). Контекст агента SHALL получать компактные метаданные.

#### Scenario: Completed report
- **WHEN** tool получает completed или partial
- **THEN** tool SHALL доставить оба существующих файла текущему пользователю либо явно сообщить ошибку доставки.

### Requirement: Existing operations preserved
История SHALL быть добавочным сценарием; summary/question/longdoc/cache/cli_query и их публичные статусы SHALL сохранять существующее поведение.

#### Scenario: Legacy query
- **WHEN** пользователь запрашивает stats ранее сохранённого summary
- **THEN** legal_summarizer_query SHALL использовать прежний manifest и IPC-контракт.
