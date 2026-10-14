## Purpose

Native Tool предоставляет агенту управление зарегистрированными OSIRIS-сервисами
и безопасный вызов GPU-операций через тот же Python backend, что и навыки.

## ADDED Requirements

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
