# runtime/osiris Specification

## Purpose
Общий runtime управляет зарегистрированными GPU-сервисами и передаёт операции
между несколькими потребителями и worker через разделяемый NFS.

## Назначение

Предоставлять общий backend выполнения зарегистрированных GPU-операций через NFS.
Нормативные требования приведены ниже; operational details — docs/OSIRIS_RUNTIME.md.

## Ответственность

Реестр профилей, lifecycle, готовность, корреляция ответов, валидация JSON/файлов,
восстановление принятых запросов и idle accounting.

## Граница

- Owns: ServiceRegistry, OSIRIS SDK lifecycle, NFS queue и envelopes.
- Does not own: модели, Greenplum и бизнес-алгоритмы навыков.
- May depend on: stdlib и лениво импортируемый OSIRIS SDK.
- Must not depend on: Appeals, FAISS, BM25, BGE или Nanobot Tool.

## Публичный контракт

ServiceProfile; ServiceRegistry.register/get/load; OsirisRuntime.start/status/stop/
invoke/invoke_legacy/cleanup; HandlerRegistry.register; WorkerRequest; WorkerResult;
run_worker; исторические lifecycle/client functions сохраняются.

## Запрещённое поведение

Динамические imports/команды по LLM-параметрам, namespace/job conflicts,
Tool pickle, смешивание сессий, удаление active job без проверки и silent success
при startup/handler failure запрещены.

## Зависимости

OSIRIS SDK нужен только при lifecycle. Модели/handlers принадлежат потребителям.

## Реализация

workspace/utils/osiris_runtime/{config,registry,api,lifecycle,client,protocol,
json_transport,dispatcher,worker,errors}.py. Пример: tools/osiris_echo_worker.py.

## Проверка

tests/test_osiris_runtime.py, tests/test_appeals_osiris_lifecycle.py и production
contracts Appeals проверены offline. Реальные SDK/NFS/CUDA/scheduler TTL требуют
отдельного smoke; статус компонента partial отражает эту границу проверки.

## Требования

## Requirements

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

#### Scenario: Malformed manifest
- **WHEN** worker получает массив вместо манифеста или некорректный deadline
- **THEN** запрос изолируется и worker продолжает обрабатывать корректные задания

#### Scenario: Retained result artifacts
- **WHEN** Python-потребитель получил файловый результат
- **THEN** файлы доступны до явного cleanup после завершения копирования

### Requirement: One lifecycle and durable queue
Runtime SHALL сохранять конкурентный запуск одной job, отдельные startup/request
timeouts и восстановление непросроченных запросов. Stop SHALL не удалять
активную очередь; idle shutdown SHALL проверять очередь под общей блокировкой.

#### Scenario: Concurrent start
- **WHEN** два клиента одновременно запускают один сервис
- **THEN** создаётся одна job и оба клиента дожидаются совместимого heartbeat

#### Scenario: Stale heartbeat with active work
- **WHEN** SDK сообщает Running, heartbeat устарел и есть processing или activity evidence текущего поколения
- **THEN** runtime не удаляет job только по возрасту heartbeat

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
