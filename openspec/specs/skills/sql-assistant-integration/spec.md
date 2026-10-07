## Purpose

Контракт безопасной выдачи SQL Assistant и подключения к existing Osiris.

## Назначение

Grounded generation не выдаёт invalid SQL как готовый; inference вынесен в
существующий GPU worker, а admin DB configuration делегирована общему resolver.

## Ответственность

Delivery gate, проверка response IDs/shape/scores, paginated corpus read.

## Граница

Owns: SQL Assistant adapters и правила generated delivery.
Does not own: generic Nanobot, gateway, pools, sync, создание GPU jobs.
May depend on: shared DB resolver и shared Osiris request transport.
Must not depend on: env DSN по умолчанию или local GPU/model загрузка gateway.

## Публичный контракт

Public invalid result: valid=false, publishable=false, sql пуст; причины сохранены.
Osiris adapter: embed(texts) и rerank(query,texts), только existing READY worker.

## Requirements

### Requirement: безопасная выдача

Сервис SHALL запрещать publish invalid SQL, даже после исчерпания repairs.

#### Scenario: repairs не помогли

WHEN проверка остаётся invalid THEN SQL SHALL NOT присутствовать как готовый ответ.

### Requirement: existing GPU service

Адаптер SHALL использовать общую NFS correlation без запуска отдельного job.

#### Scenario: worker отсутствует

WHEN heartbeat не READY THEN адаптер SHALL вернуть unavailable без create/delete.

### Requirement: полнота корпуса

Builder SHALL проверять count и source signature до публикации.

#### Scenario: корпус больше 100000

WHEN KB содержит 105910 колонок THEN все 105910 SHALL быть прочитаны страницами.

## Зависимости

workspace.utils.db, workspace.utils.osiris_runtime, existing Appeals worker.

## Конфигурация

channels.postgres.dsn, gateway.kb_search.osiris, явный admin --profile.

## Инварианты

Нет запросов к real infrastructure в тестах; invalid gate не зависит от tool audit.

## Запрещённое поведение

Create/delete/restart GPU jobs, local model fallback и изменение generic runtime.

## Реализация

lib/services/sql_assistant_runtime.py; workspace/skills/sql_assistant/scripts/osiris_adapter.py;
workspace/skills/sql_assistant/scripts/_pg_admin.py; build_index.py.

## Проверка

Fake unit tests для delivery, DSN, Osiris correlation и corpus completeness.
Официальная OpenSpec CLI-проверка недоступна на внешней машине.
