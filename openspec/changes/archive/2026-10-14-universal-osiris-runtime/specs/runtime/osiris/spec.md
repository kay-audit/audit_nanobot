## Purpose

Общий runtime управляет зарегистрированными GPU-сервисами и передаёт операции
между несколькими потребителями и worker через разделяемый NFS.

## ADDED Requirements

### Requirement: Registered services
Runtime SHALL разрешать только зарегистрированные профили и отклонять
конфликты имени, job и пересекающихся NFS namespace.

#### Scenario: Shared consumers
- **WHEN** два потребителя используют один профиль
- **THEN** они используют один lifecycle и получают раздельно коррелированные ответы

### Requirement: Typed transport and handlers
Runtime SHALL передавать JSON и файловые артефакты с проверкой размеров,
путей, срока и идентичности. Worker SHALL вызывать только явные handlers.
Legacy pickle SHALL оставаться доверенным внутренним интерфейсом Appeals.

#### Scenario: Unknown operation
- **WHEN** клиент вызывает операцию вне capabilities
- **THEN** запрос отклоняется до SDK и публикации файлов

#### Scenario: Artifact escape
- **WHEN** артефакт указывает за пределы сессии
- **THEN** запрос отклоняется без чтения файла

### Requirement: One lifecycle and durable queue
Runtime SHALL сохранять конкурентный запуск одной job, отдельные startup/request
timeouts и восстановление непросроченных запросов. Stop SHALL не удалять
активную очередь; idle shutdown SHALL проверять очередь под общей блокировкой.

#### Scenario: Concurrent start
- **WHEN** два клиента одновременно запускают один сервис
- **THEN** создаётся одна job и оба клиента дожидаются совместимого heartbeat

### Requirement: Eight hour idle timeout
Runtime SHALL использовать idle timeout 28800 секунд по умолчанию и
сбрасывать его при READY и завершении запроса. Активный запрос SHALL
предотвращать idle shutdown.

#### Scenario: Busy worker
- **WHEN** выполняется запрос дольше idle timeout
- **THEN** worker продолжает обработку и начинает полный idle interval после завершения

### Requirement: Appeals compatibility
Appeals SHALL сохранять публичные retrieve/rerank adapters, формат результатов,
CUDA-модели и существующие алгоритмы FAISS/BM25/RRF.

#### Scenario: Legacy reranking
- **WHEN** адаптер отправляет legacy rerank
- **THEN** он получает прежний DataFrame со всеми кандидатами и scores
