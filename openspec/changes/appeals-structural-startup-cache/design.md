## Context

Standalone задаёт request-local greenplum scope. Native tool задаёт cache scope, который ранее управлял обеими стадиями SQL. Generic sync загружает physical tables полностью и не подходит для Appeals population с duplicate app_row_id.

## Goals / Non-Goals

Цель: build-on-start пятиколоночного snapshot готовой population 2026 и fail-fast production lookup. Standalone filtering, FAISS/BM25/RRF, reranker и Osiris lifecycle сохраняются.

## Decisions

1. Отдельный Appeals helper использует shared DB pool с серверным cursor и fetchmany(50000). Простой SELECT читает `s_grnplm_ld_audit_da_project_34.t_db_oarb_appeals_d3`; eligibility и source freshness принадлежат внешнему/manual ETL. Python получает только пять structural колонок без runtime regex/EXISTS/joins/date filtering.
2. Generic replace_arrow_batches атомарно заменяет таблицу без дедупликации, сохраняет схему пустого источника и добавляет её в publication list. TableRegistry не регистрирует DuckDB-only таблицу как GP source.
3. Gateway загружает и принудительно публикует structural dataset до ctx.start(). Ошибка блокирует запуск каналов. Readiness отдельно проверяет доступность structural snapshot.
4. Production делает SELECT DISTINCT ID с параметризованными filters/date в коротком read-only DuckDB connection. Результат собирается fetchmany в необходимый Osiris allowed-ID list без DataFrame/dict rows.
5. Hydration явно использует GP scope независимо от population backend. Base SQL повторяет prd/s_prd/chnl/date, затем defensive validation проверяет base rows до существующего агрегирования. Related relations ограничиваются candidate ID.
6. Session metadata сохраняет filters/date, чтобы follow-up hydration повторял те же ограничения.

## Risks / Trade-offs

Готовый GP source требует проверки permissions, types, времени чтения и актуальности на закрытом контуре; gateway eligibility не проверяет. Snapshot остаётся неизменным до restart. DuckDB DISTINCT и allowed-ID list всё ещё требуют памяти пропорционально eligible population; ingestion ограничивает только Python batch memory. Generic публикация повторно копирует structural table вместе с snapshot.

## Migration Plan

Оставить skills.appeals_analyzer.enabled=false, включить gateway.appeals_analyzer.enable и перезапустить gateway. При failure исправить источник/место snapshot; fallback на GP prefilter запрещён.

## Open Questions

Реальные Greenplum планы, source types и объём памяти проверяются на закрытом контуре. Локально проверяются Arrow/DuckDB projection, SQL contracts, routing и failures.
