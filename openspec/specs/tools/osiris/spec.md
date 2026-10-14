# tools/osiris Specification

## Purpose
Native Tool предоставляет агенту управление зарегистрированными OSIRIS-сервисами
и безопасный вызов GPU-операций через тот же Python backend, что и навыки.

## Назначение

Предоставлять агенту native интерфейс управления OSIRIS и вызова разрешённых
операций через общий backend. Требования приведены ниже.

## Ответственность

Конфигурационные allowlists, действительный session context, авторизация stop,
асинхронный вызов backend, безопасные ответы и доставка больших результатов.

## Граница

- Owns: Tool schema, tools.osiris config, agent authorization и artifact delivery.
- Does not own: SDK lifecycle, NFS protocol и предметные handlers.
- May depend on: Nanobot Tool/context, pydantic, общий OsirisRuntime, session storage.
- Must not depend on: ML-модели или обход штатного project_tool_loader.

## Публичный контракт

OsirisTool.config_key/config_cls/enabled/create/execute. Действия status/start/invoke/
stop; invoke использует service, operation, payload, query, artifacts.
Session ID агента не является параметром Tool schema.

## Запрещённое поведение

Выбор image/worker/import/shell через LLM, stop без реальной административной
сессии, traceback/секреты в ответе, pickle и неограниченный inline output запрещены.

## Зависимости

Штатный project_tool_loader, Nanobot Tool/RequestContext, общий OsirisRuntime,
workspace.utils.session_key и message Tool для артефактов.

## Реализация

workspace/tools/osiris.py; конфигурация project.json::tools.osiris.

## Проверка

tests/test_tools_osiris.py проверяет реальный loader, session spoofing, права stop,
nonblocking execution, validation и файлы. Тесты прошли offline; runtime SDK smoke
не подтверждён, поэтому статус partial.

## Требования

## Requirements

### Requirement: Native registration
Tool SHALL регистрироваться штатным project_tool_loader и читать
`tools.osiris` из полного settings reference.

#### Scenario: Enabled Tool
- **WHEN** включена секция tools.osiris
- **THEN** loader регистрирует native osiris Tool без импорта OSIRIS SDK

### Requirement: Safe invocation
Tool SHALL проверять service/operation allowlist, JSON payload и реальные
session context. SDK/NFS SHALL выполняться вне event loop; крупные результаты
SHALL доставляться как сессионные артефакты.

#### Scenario: Forged session
- **WHEN** LLM передаёт session_id при наличии RequestContext
- **THEN** используется session_key текущего запроса

### Requirement: Administrative stop
Tool SHALL запрещать stop по умолчанию. Stop SHALL требовать включённый
allow_stop и реальную сессию из конфигурационного admin_session_keys.

#### Scenario: Unprivileged stop
- **WHEN** обычная сессия запрашивает stop
- **THEN** Tool возвращает отказ без вызова SDK
