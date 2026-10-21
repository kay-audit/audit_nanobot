## Purpose

Безопасно разрешать физические источники SQL Assistant в действующие подписные схемы текущего пользователя, различая отсутствие доступа и невозможность проверки.

## Requirements

### Requirement: доверенная идентичность

Система SHALL использовать только настоящий user_id текущего Postgres request после подтверждения контракта sudir_login оператором.

#### Scenario: идентичность не подтверждена
- **WHEN** ID отсутствует, некорректен или контракт ещё не подтверждён
- **THEN** возвращается identity_unavailable без пользовательских имён источников и без запроса подписок.

### Requirement: адресное разрешение схем

Система SHALL проверять только нужные схемы текущего пользователя параметризованным запросом и выбирать алиас воспроизводимо среди его действующих подписок.

#### Scenario: несколько алиасов
- **WHEN** у пользователя несколько действующих алиасов одной схемы
- **THEN** выбирается первый lowercase-алиас в лексикографическом порядке и сохраняются альтернативы.

### Requirement: различение ошибок

Система SHALL различать subscribed, not_subscribed, identity_unavailable, catalog_unavailable, subscription_check_error subscription_cache_unavailable и subscription_mapping_unknown.

#### Scenario: каталог недоступен
- **WHEN** чтение каталога подписок завершается ошибкой
- **THEN** возвращается subscription_cache_unavailable для отсутствующего/неполного снимка либо subscription_check_error для ошибки чтения; SQL не публикуется.

#### Scenario: отсутствующие подписки
- **WHEN** для нескольких схем не найдены действующие подписки, но найдены реальные варианты каталога
- **THEN** для каждой схемы возвращаются реальные sub_name и sub_alias без использования предлагаемых алиасов в SQL.

### Requirement: разрешённые источники в SQL

Система SHALL формировать lowercase prx_<alias>_<schema>.table, проверять колонки по физическим KB-метаданным и блокировать неподтверждённые источники.

#### Scenario: вложенные источники
- **WHEN** SQL содержит JOIN, CTE, UNION или вложенный SELECT
- **THEN** преобразуются только внешние источники и их квалифицированные ссылки, с сохранением содержимого литералов и комментариев.

### Requirement: безопасная выдача

Система SHALL сохранять READY verbatim и блокировать новый SQL без подписок, read-only проверки, одного statement и статуса valid с publishable=true.

#### Scenario: готовый скрипт
- **WHEN** найден исходный READY-скрипт
- **THEN** его SQL возвращается посимвольно без преобразования подписных схем.


### Requirement: только локальный снимок
SQL Assistant SHALL читать подписки из инъецированного общего DuckDB-кэша параметризованными запросами без GP fallback и без усечения результатов.

#### Scenario: новая подписка после загрузки
- **WHEN** подписка отсутствует в последнем успешном снимке
- **THEN** ответ сообщает дату снимка и возможность появления подписки после ручного обновления.

### Requirement: алиас с числовым началом
SQL Assistant SHALL принимать безопасные sub_alias, начинающиеся с цифры, как компонент полного prx_-идентификатора.

#### Scenario: реальный числовой алиас
- **WHEN** действующий алиас равен 16_dea_unit_channel
- **THEN** создаётся lowercase prx_16_dea_unit_channel_<schema> без изменения литералов SQL.

## Назначение

Разрешение доступа текущего пользователя к физическим схемам KB.

## Ответственность

Адресное чтение подписок, детерминированный выбор алиаса и AST mapping.

## Граница

Owns: SubscriptionResolution. Does not own: регистрацию tool, корпоративную идентификацию и lifecycle БД. May depend on: RequestContext, CacheProvider.read_manual_snapshot, sqlglot. Must not depend on: LLM-ответы и пользовательский текст для идентификации.

## Публичный контракт

SubscriptionResolver.resolve(table_names) асинхронно возвращает источники и mapping. SubscriptionResolution предоставляет allowed, report, public_schema и rewrite.

## Запрещённое поведение

Чужие подписки, runtime GP fallback, произвольный SQL к реестру, бизнес-SQL execution, подстановка chat_id, изменение READY.

## Зависимости

Общий DuckDB manual snapshot и SQL AST; без runtime Greenplum.

## Реализация

lib/services/subscription_resolver.py; lib/services/sql_assistant_runtime.py; lib/channels/postgres_channel.py.

## Проверка

КОГДА test_sql_assistant_subscriptions проверяет несколько пользователей и схем, ТОГДА алиасы изолированы и неверифицируемые состояния блокируют выдачу. Статус partial: корпоративные identity/schema checks остаются за оператором.

## Требования

### Требование: ссылки на нормативные сценарии

Нормативные сценарии — в разделе Requirements выше; **КОГДА** идентичность не подтверждена, **ТОГДА** выдача блокируется.

#### Сценарий: неподтверждённая идентичность

- **КОГДА** нет подтверждённого соответствия user_id=sudir_login
- **ТОГДА** проверка завершается identity_unavailable без SQL.
