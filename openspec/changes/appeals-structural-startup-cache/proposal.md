## Why

Production Appeals требует структурную population без массового Greenplum lookup на каждый запрос. Текущий cache scope также направляет hydration в DuckDB и требует полного копирования текстовых таблиц, регистрация которых выключена.

## What Changes

- Startup loader публикует lightweight population 2026 в существующий runtime snapshot.
- Production читает allowed IDs из DuckDB; standalone сохраняет Greenplum prefilter.
- Hydration читает только candidates из Greenplum и повторяет structural constraints.
- Generic DuckDbCacheStore получает отдельный Arrow bulk API без ID upsert.

## Capabilities

### New Capabilities

- `appeals-structural-cache`: startup-only projection готовой external ETL population и split population/hydration.

### Modified Capabilities

- Нет изменения контрактов существующего sync и registry.

## Impact

Gateway startup, Appeals backend/report flow, DuckDbCacheStore additive API, targeted tests и документация. Новые зависимости, периодическая синхронизация Appeals и изменение Osiris не требуются.
