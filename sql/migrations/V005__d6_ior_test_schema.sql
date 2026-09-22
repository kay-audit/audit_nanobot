-- ============================================================================
-- V005 — d6_ior_test_schema (feature/d6-nanobot-with-manifest)
-- ============================================================================
-- Тестовая схема PostgreSQL для фичи ``d6_nanobot`` (IOR Analyzer).
-- Создаёт изолированную схему ``test_d6`` с таблицей ``ior_events``,
-- соответствующей структуре ``workspace/data_store/cache/testing/ior/ior.json``
-- (генерируется ``workspace/skills/ior-analyzer/testing/data_generator.py``).
--
-- Схема отдельная от ``public``, чтобы:
--   1) не вмешиваться в прод-каталог (см. sql/README.md § 5 «Граница schemas»);
--   2) drop CASCADE при rollback фичи (см. feature.yaml::isolation.removable);
--   3) поддерживать параллельные тесты фич (``test_d6`` vs ``test_legal``).
--
-- Совместимость: PostgreSQL 12+, Greenplum 6.x (без pgcrypto-зависимостей).
-- Идемпотентность: ``CREATE SCHEMA IF NOT EXISTS`` + ``DROP TABLE IF EXISTS``
-- (re-run возможен, но DROP стирает данные — для production-данных
-- использовать отдельный migration-runner, см. tools/migrate.py).
-- ============================================================================

CREATE SCHEMA IF NOT EXISTS test_d6;

COMMENT ON SCHEMA test_d6 IS
    'Изолированная тестовая схема фичи d6_nanobot (IOR Analyzer). '
    'Создаётся через apply d6_nanobot, удаляется через rollback (DROP SCHEMA CASCADE).';

DROP TABLE IF EXISTS test_d6.ior_events;

CREATE TABLE test_d6.ior_events (
    eve_id           VARCHAR(32) PRIMARY KEY,
    drp              VARCHAR(16),
    event_date       DATE,
    event_type       VARCHAR(128),
    category         VARCHAR(128),
    description      TEXT,
    status           VARCHAR(32),
    financial_loss   NUMERIC(18, 2),
    reimbursement    NUMERIC(18, 2),
    business_line    VARCHAR(64),
    product          VARCHAR(64),
    channel          VARCHAR(64),
    cause            VARCHAR(128),
    consequences     TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE test_d6.ior_events IS
    'Тестовые инциденты операционного риска. Генерируются data_generator.py '
    'и загружаются pg_loader.py. Дропается при rollback фичи d6_nanobot.';
COMMENT ON COLUMN test_d6.ior_events.eve_id IS
    'Идентификатор инцидента (EVE-TEST-NNNN для тестовых данных)';
COMMENT ON COLUMN test_d6.ior_events.drp IS
    'DRP-TEST-NNN — направление/подразделение, ведущее инцидент';
COMMENT ON COLUMN test_d6.ior_events.event_type IS
    'Тип события (краткое имя — см. data_generator.THEMES)';
COMMENT ON COLUMN test_d6.ior_events.category IS
    'Категория операционного риска (Операционный процесс, ИТ-системы и т.п.)';
COMMENT ON COLUMN test_d6.ior_events.financial_loss IS
    'Финансовый ущерб в рублях (0 для нефинансовых инцидентов)';
COMMENT ON COLUMN test_d6.ior_events.reimbursement IS
    'Возмещение ущерба (0 / 25% / 50% / 100% от финансового ущерба)';
COMMENT ON COLUMN test_d6.ior_events.created_at IS
    'Момент загрузки строки в БД (audit trail для тестов)';

-- Индексы для типовых запросов skill'а
CREATE INDEX IF NOT EXISTS ior_events_event_date_idx
    ON test_d6.ior_events (event_date);
CREATE INDEX IF NOT EXISTS ior_events_event_type_idx
    ON test_d6.ior_events (event_type);
CREATE INDEX IF NOT EXISTS ior_events_status_idx
    ON test_d6.ior_events (status);

COMMENT ON INDEX test_d6.ior_events_event_date_idx IS
    'Обслуживает фильтр по периоду (smoke_prompt «Сколько инцидентов в 2024?»).';
COMMENT ON INDEX test_d6.ior_events_event_type_idx IS
    'Обслуживает фильтр по типу (smoke: «ошибочные комиссии», «двойные списания»).';
COMMENT ON INDEX test_d6.ior_events_status_idx IS
    'Обслуживает фильтр по статусу (smoke: «возмещённые», «в работе»).';
