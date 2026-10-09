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
Generated success: status=valid, valid=true, publishable=true. Финальный SQL
берётся verbatim из проверенного результата; любая новая версия требует sql_validate.
kb_describe принимает table_names для параметризованного exact full-name lookup.
Osiris adapter: embed(texts) и rerank(query,texts), только existing READY worker.

## Requirements

### Requirement: безопасная выдача

Сервис SHALL запрещать publish invalid SQL, даже после исчерпания repairs.

#### Scenario: repairs не помогли

WHEN проверка остаётся invalid THEN SQL SHALL NOT присутствовать как готовый ответ.
AND самостоятельно составленный alternative/fallback/control SQL SHALL NOT
выдаваться без отдельного status=valid, valid=true, publishable=true.

### Requirement: Spark identifier resolution

Сервис SHALL одинаково casefold-нормализовать Spark identifiers AST и KB и
передавать qualify вложенную схему, не плоские ключи schema.table.

#### Scenario: uppercase KB, lowercase parser

WHEN KB содержит INSERTED_DTTM или PA_ID AND AST использует inserted_dttm или
pa_id THEN колонка SHALL разрешаться без unknown_column.

### Requirement: explicitly selected table

Агент SHALL выполнять exact table_names -> kb_describe(full) -> выбор возвращённых
колонок -> sql_generate -> sql_validate, без угадывания колонок до describe.

#### Scenario: semantic search failed

WHEN пользователь указал UVZ_SELFSERVICE_SRC.MV_UVZ_WORK_PLANS THEN агент SHALL
получить exact карточку и её реальные колонки независимо от semantic ranking.

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
