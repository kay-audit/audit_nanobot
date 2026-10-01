CREATE SCHEMA IF NOT EXISTS sqlagent;

CREATE TABLE IF NOT EXISTS sqlagent.kb_tables (
    id BIGINT NOT NULL,
    table_name TEXT NOT NULL,
    group_key TEXT,
    layer TEXT,
    description TEXT,
    columns_summary TEXT,
    row_count BIGINT,
    dialect TEXT NOT NULL DEFAULT 'spark',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT kb_tables_pk PRIMARY KEY (id)
) DISTRIBUTED BY (id);

CREATE TABLE IF NOT EXISTS sqlagent.kb_columns (
    id BIGINT NOT NULL,
    table_id BIGINT NOT NULL,
    column_name TEXT NOT NULL,
    data_type TEXT,
    description TEXT,
    ordinal INTEGER,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT kb_columns_pk PRIMARY KEY (id)
) DISTRIBUTED BY (id);

CREATE TABLE IF NOT EXISTS sqlagent.kb_examples (
    id BIGINT NOT NULL,
    script_id BIGINT,
    km_id TEXT,
    file_name TEXT,
    file_path TEXT,
    nl TEXT,
    nl_variants TEXT,
    sql TEXT NOT NULL,
    script_description TEXT,
    tables TEXT,
    dialect TEXT NOT NULL DEFAULT 'spark',
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT kb_examples_pk PRIMARY KEY (id)
) DISTRIBUTED BY (id);

-- Greenplum 6 does not support CREATE INDEX IF NOT EXISTS. Apply these four
-- statements once (or guard them in the deployment migration framework).
CREATE INDEX kb_examples_script_id_idx ON sqlagent.kb_examples(script_id);
CREATE INDEX kb_examples_km_id_idx ON sqlagent.kb_examples(km_id);
CREATE INDEX kb_examples_file_name_idx ON sqlagent.kb_examples(file_name);
CREATE INDEX kb_columns_table_id_idx ON sqlagent.kb_columns(table_id);
