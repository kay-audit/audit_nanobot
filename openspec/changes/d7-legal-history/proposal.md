## Why

D7 умеет суммаризировать документы, но не предоставляет историю изменений по паре закон/обзор. Архив коллеги добавляет этот сценарий без завершённой интеграции конфигурации, доверенной сессии и доставки файлов.

## What Changes

- Добавить автономный history pipeline с проверкой пары DOCX, ограниченными LLM-батчами, checkpoint и фоновым worker.
- Добавить native tool с доверенным RequestContext, защитой ZIP и передачей результатов через MessageTool.
- Сохранить исходные summary, question, manifest и query контракты без замены каталога.
- Добавить строго типизированную history policy и mock-регрессии.

## Capabilities

### New Capabilities
- `skills/legal-document-history`: безопасная история изменений, фоновые операции и доставка артефактов текущей сессии.

### Modified Capabilities

Нет. Контракт `skills/legal-summarizer-query` остаётся прежним.

## Impact

Новые модули внутри legal_summarizer, workspace/tools/legal_summarizer_history.py, точечные additions project_settings/project.json/runtime_inventory. Общий OSIRIS и LLM runtime не меняются. Глобальные contextWindowTokens/maxTokens сохраняются.
