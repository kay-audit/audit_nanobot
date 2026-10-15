# AppealsStructuralCache

## Назначение

Предоставляет production Appeals eligible population 2026 в shared DuckDB snapshot без массового GP prefilter на пользовательский запрос.

## Ответственность

Чтение готового GP source простым SELECT, ограниченная Arrow ingestion, пятиколоночная projection, публикация до request lifecycle, DISTINCT ID lookup и fail-fast diagnostics.

## Граница

Owns: DuckDB structural table. Does not own: source eligibility/ETL, DB pool, generic sync/registry, retrieval, reranker. May depend on: shared DB run, DuckDbCacheStore bulk API и общий snapshot path. Must not depend on: Osiris lifecycle или текстовый cache.

## Публичный контракт

`prepare_gateway_structural_cache(ctx)`, `load_structural_cache(store, db_run)`, `lookup_structural_ids(path, ...)`, `AppealsStructuralCacheError`.

## Входы

Готовая `s_grnplm_ld_audit_da_project_34.t_db_oarb_appeals_d3` (eligible 2026 population, external/manual ETL); фильтры prd/s_prd/chnl/date; существующий runtime store.

## Выходы

`main.appeals_structural_2026`: app_row_id VARCHAR, req_reg_date TIMESTAMP, prd VARCHAR, s_prd VARCHAR, chnl VARCHAR. DISTINCT string IDs для Osiris.

## Состояние

Полный structural dataset в store и snapshot до следующего gateway restart.

## Зависимости

Shared workspace DB pool, pyarrow, duckdb, DuckDbCacheStore, existing runtime mode/readiness.

## Конфигурация

`gateway.appeals_analyzer.enable` включает loader и tool; `gateway.cache.local_path` задаёт общий snapshot path. Source table registration Appeals остаётся disabled.

## Жизненный цикл

Gateway open store → full structural load → force publish → schema verification → ctx.start → channels/agent. Testing runtime пропускает production loader.

## Владение данными

GP хранит source texts. DuckDB хранит только пять structural fields, включая несколько base rows одного app_row_id.

## Поведение при ошибке

Startup failure блокирует gateway request lifecycle. Lookup failure вызывает AppealsStructuralCacheError без GP structural fallback. Readiness проверяет structural table как required dependency.

## Инварианты

Eligibility подготовлена внешним ETL и не вычисляется/проверяется gateway. Startup SELECT не содержит WHERE/DISTINCT/JOIN/EXISTS/regex и сохраняет все source rows. OR внутри values, AND между groups/date. Inclusive end date реализован exclusive next-day boundary. Base hydration повторяет constraints до ID aggregation; related tables hydrate по IDs.

## Требования

Компонент SHALL сохранять duplicate base IDs и SHALL NOT передавать source text columns в ingestion.

### Сценарий: Несколько base rows

WHEN один ID имеет A/X и B/Y, THEN оба structural rows SHALL сохраняться и каждый соответствующий lookup SHALL возвращать этот ID один раз.

## Запрещённое поведение

Полный pandas/fetchall startup result, hidden GP structural fallback, periodic Appeals sync, source text snapshot и перевод standalone на DuckDB.

## Потребители

gateway.py, production appeals-analyzer data_store/greenplum_engine.

## Реализация

`workspace/utils/appeals_structural_cache.py`; additive `lib/services/duckdb_cache_store.py::replace_arrow_batches`; `gateway.py`; Appeals backend/report modules.

## Проверка

`tests/test_appeals_structural_cache.py` и существующие Appeals/core regression tests. Фактические GP SQL plans, объёмы и channel e2e требуют закрытого контура.
