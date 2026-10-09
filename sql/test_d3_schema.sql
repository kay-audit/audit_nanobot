-- Тестовые данные скилла appeals-analyzer (D3 / d3_crm).
--
-- Схема test_d3 зеркалит продакшн-структуру обращений:
--   * appeals_structural — 5 колонок, которые тянет startup-загрузчик
--     (app_row_id, req_reg_date, prd, s_prd, chnl);
--   * appeal_body / appeal_dialogs / appeal_task — слои гидратации,
--     соответствующие трём таблицам из project.json.
--
-- Тестовый рантайм (NANOBOT_SKILLS_RUNTIME=testing) читает только отсюда:
-- Greenplum, FAISS, BM25, BGE, reranker и GPU не используются.

CREATE SCHEMA IF NOT EXISTS test_d3;

-- Структурный слой: ровно пять колонок, порядок фиксирован.
CREATE TABLE IF NOT EXISTS test_d3.appeals_structural (
    app_row_id   TEXT NOT NULL,
    req_reg_date TIMESTAMP NOT NULL,
    prd          TEXT NOT NULL,
    s_prd        TEXT NOT NULL,
    chnl         TEXT NOT NULL,
    CONSTRAINT appeals_structural_pkey PRIMARY KEY (app_row_id)
);

-- Текст обращения.
CREATE TABLE IF NOT EXISTS test_d3.appeal_body (
    app_row_id TEXT NOT NULL REFERENCES test_d3.appeals_structural (app_row_id) ON DELETE CASCADE,
    body       TEXT NOT NULL
);

-- Диалог по обращению.
CREATE TABLE IF NOT EXISTS test_d3.appeal_dialogs (
    app_row_id TEXT NOT NULL REFERENCES test_d3.appeals_structural (app_row_id) ON DELETE CASCADE,
    turn_no    INTEGER NOT NULL,
    speaker    TEXT NOT NULL,
    text       TEXT NOT NULL,
    CONSTRAINT appeal_dialogs_pkey PRIMARY KEY (app_row_id, turn_no)
);

-- Задача/предписание по обращению.
CREATE TABLE IF NOT EXISTS test_d3.appeal_task (
    app_row_id TEXT NOT NULL REFERENCES test_d3.appeals_structural (app_row_id) ON DELETE CASCADE,
    task_no    INTEGER NOT NULL,
    assignee   TEXT NOT NULL,
    task_text  TEXT NOT NULL,
    due_date   DATE,
    CONSTRAINT appeal_task_pkey PRIMARY KEY (app_row_id, task_no)
);

CREATE INDEX IF NOT EXISTS appeals_structural_prd_idx
    ON test_d3.appeals_structural (prd, s_prd, chnl);
CREATE INDEX IF NOT EXISTS appeals_structural_date_idx
    ON test_d3.appeals_structural (req_reg_date);