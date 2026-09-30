-- V007__d5_followup_tables.sql
-- Сгенерировано из backend.storage.gp::_ddl_statements("public")
-- DDL skill'а follow_up (D5): Greenplum→PostgreSQL адаптация.
-- Трансляции: DISTRIBUTED BY (col) → удалено; WITH (storage=...) → удалено.
-- Идемпотентно: все CREATE TABLE IF NOT EXISTS.
-- Применять в audit_nanobot/migrations/ для dev-БД.
-- В production (Greenplum) DDL создаётся runtime-функцией
-- ensure_schema() в skill — она выполняется при старте backend'а.
--
-- statement #1
CREATE TABLE IF NOT EXISTS public.t_fu_poruch_shadow (
            poruch_key   varchar(32) NOT NULL,
            km_id        varchar(20) NOT NULL,
            doc_reg_num  varchar(100) NULL,
            row_hash     varchar(32) NOT NULL,
            emb_status   varchar(20) NOT NULL DEFAULT 'pending',
            first_seen   timestamp NOT NULL DEFAULT now(),
            processed_at timestamp NULL,
            processed_by varchar(255) NULL,
            PRIMARY KEY (poruch_key)
        )

-- statement #2
CREATE TABLE IF NOT EXISTS public.t_fu_poruch_chunks (
            id          bigserial,
            poruch_key  varchar(32) NOT NULL,
            km_id       varchar(20) NOT NULL,
            field_src   varchar(30) NOT NULL,
            chunk_idx   int4 NOT NULL,
            chunk_text  text NOT NULL,
            emb         float4[] NOT NULL,
            created_by  varchar(255) NOT NULL,
            created_at  timestamp NOT NULL DEFAULT now()
        )

-- statement #3
CREATE TABLE IF NOT EXISTS public.t_fu_act_docs (
            file_id     varchar(64) NOT NULL,
            filename    varchar(500) NOT NULL,
            check_id    varchar(20) NOT NULL,
            topic       text NULL,
            created_by  varchar(255) NOT NULL,
            created_at  timestamp NOT NULL DEFAULT now(),
            PRIMARY KEY (file_id)
        )

-- statement #4
CREATE TABLE IF NOT EXISTS public.t_fu_act_chunks (
            id           bigserial,
            doc_file_id  varchar(64) NOT NULL,
            check_id     varchar(20) NOT NULL,
            chunk_index  int4 NOT NULL,
            header_path  varchar(1000) NULL,
            chunk_text   text NOT NULL,
            emb          float4[] NOT NULL,
            created_at   timestamp NOT NULL DEFAULT now()
        )

-- statement #5
CREATE TABLE IF NOT EXISTS public.t_fu_deviations (
            id            bigserial,
            doc_file_id   varchar(64) NOT NULL,
            check_id      varchar(20) NOT NULL,
            category      varchar(255) NULL,
            description   text NOT NULL,
            severity      varchar(50) NULL,
            financial_impact_rub float8 NULL,
            affected_systems     text NULL,
            regulation_refs      text NULL,
            affected_count       int4 NULL,
            responsible_unit     varchar(255) NULL,
            recommendation       text NULL,
            source_chunk_index   int4 NULL,
            created_at    timestamp NOT NULL DEFAULT now()
        )

-- statement #6
CREATE TABLE IF NOT EXISTS public.t_fu_repo_index (
            check_id     varchar(20) NOT NULL,
            repo_slug    varchar(100) NOT NULL,
            repo_url     varchar(500) NOT NULL,
            readme_ok    bool NOT NULL,
            quality_tier varchar(1) NOT NULL DEFAULT 'C',
            head_commit  varchar(40) NULL,
            parsed_at    timestamp NOT NULL DEFAULT now(),
            parsed_by    varchar(255) NOT NULL,
            PRIMARY KEY (check_id)
        )

-- statement #7
CREATE TABLE IF NOT EXISTS public.t_fu_repo_files (
            id           bigserial,
            repo_slug    varchar(100) NOT NULL,
            punkt_akta   varchar(150) NULL,
            file_path    varchar(1000) NOT NULL,
            file_kind    varchar(20) NOT NULL,
            authors      varchar(500) NULL,
            descr        text NULL,
            data_sources varchar(1000) NULL,
            tech         varchar(255) NULL,
            file_url     varchar(1000) NOT NULL
        )

-- statement #8
CREATE TABLE IF NOT EXISTS public.t_fu_km_method (
            check_id      varchar(20) NOT NULL,
            method_json   text NOT NULL,
            src_chunk_ids text NOT NULL,
            model_used    varchar(100) NOT NULL,
            created_by    varchar(255) NOT NULL,
            created_at    timestamp NOT NULL DEFAULT now(),
            PRIMARY KEY (check_id)
        )

-- statement #9
CREATE TABLE IF NOT EXISTS public.t_fu_verdicts (
            id          bigserial,
            poruch_key  varchar(32) NOT NULL,
            km_id       varchar(20) NULL,
            verdict     varchar(20) NOT NULL,
            comment     text NULL,
            author      varchar(255) NOT NULL,
            created_at  timestamp NOT NULL DEFAULT now()
        )

-- statement #10
CREATE TABLE IF NOT EXISTS public.t_fu_sync_lock (
            task_key     varchar(64) NOT NULL,
            locked_by    varchar(255) NULL,
            lease_until  timestamp NULL,
            PRIMARY KEY (task_key)
        )

-- statement #11
CREATE TABLE IF NOT EXISTS public.t_fu_skill_log (
            id            bigserial,
            author        varchar(255) NOT NULL,
            poruch_key    varchar(32) NULL,
            km_id         varchar(20) NULL,
            duration_ms   int4 NULL,
            blocks_filled varchar(255) NULL,
            resolved_how  varchar(30) NULL,
            created_at    timestamp NOT NULL DEFAULT now()
        )

-- statement #12
CREATE TABLE IF NOT EXISTS public.t_fu_llm_calls (
            id            bigserial,
            author        varchar(255) NOT NULL,
            host          varchar(255) NULL,
            pid           int4 NULL,
            profile       varchar(30) NULL,
            outcome       varchar(20) NOT NULL,
            waited_sec    float8 NULL,
            ran_sec       float8 NULL,
            created_at    timestamp NOT NULL DEFAULT now()
        )

-- statement #13
CREATE TABLE IF NOT EXISTS public.t_fu_check_id_audit (
            id            bigserial,
            run_id        varchar(32) NOT NULL,
            stage         varchar(10) NOT NULL,
            author        varchar(255) NOT NULL,
            value         varchar(100) NULL,
            canonical     varchar(20) NULL,
            klass         varchar(20) NOT NULL,
            sources       varchar(255) NULL,
            n_docs        int4 NOT NULL DEFAULT 0,
            n_chunks      int4 NOT NULL DEFAULT 0,
            n_devs        int4 NOT NULL DEFAULT 0,
            orphan_devs   bool NOT NULL DEFAULT false,
            created_at    timestamp NOT NULL DEFAULT now()
        )

-- statement #14
CREATE TABLE IF NOT EXISTS public.t_fu_card_baseline (
            id            bigserial,
            run_id        varchar(32) NOT NULL,
            label         varchar(100) NOT NULL,
            author        varchar(255) NOT NULL,
            km_id         varchar(20) NULL,
            resolved_how  varchar(30) NULL,
            llm_calls     int4 NULL,
            elapsed_sec   float8 NULL,
            block         varchar(30) NULL,
            block_status  varchar(10) NULL,
            block_t_sec   float8 NULL,
            shape_json    text NULL,
            sha256        varchar(32) NULL,
            size_chars    int4 NULL,
            note          text NULL,
            created_at    timestamp NOT NULL DEFAULT now()
        )

-- statement #15
CREATE TABLE IF NOT EXISTS public.t_fu_checklist (
            card_id    varchar(16) NOT NULL,
            step_idx   int4 NOT NULL,
            done       bool NOT NULL DEFAULT false,
            note       text NULL,
            author     varchar(255) NOT NULL,
            updated_at timestamp NOT NULL DEFAULT now()
        )

-- statement #16
CREATE TABLE IF NOT EXISTS public.t_fu_script_annot (
            repo_slug   varchar(64) NOT NULL,
            file_path   varchar(512) NOT NULL,
            annot_json  text NOT NULL,
            model_used  varchar(128) NULL,
            created_by  varchar(255) NOT NULL,
            created_at  timestamp NOT NULL DEFAULT now()
        )

-- statement #17
CREATE TABLE IF NOT EXISTS public.t_fu_resolve_log (
            id           bigserial,
            author       varchar(255) NOT NULL,
            event        varchar(16) NOT NULL,
            resolve_id   varchar(16) NOT NULL,
            tier         varchar(16) NULL,
            letter_hash  varchar(32) NULL,
            shown_kms    varchar(500) NULL,
            chosen_km    varchar(20) NULL,
            how          varchar(16) NULL,
            duration_ms  int4 NULL,
            created_at   timestamp NOT NULL DEFAULT now()
        )

-- statement #18
CREATE TABLE IF NOT EXISTS public.t_fu_card_analysis (
            id               bigserial,
            author           varchar(255) NOT NULL,
            card_id          varchar(16) NULL,
            km_id            varchar(20) NULL,
            poruch_key       varchar(32) NULL,
            block_unit       varchar(255) NULL,
            evidence_quality varchar(16) NULL,
            formality        varchar(16) NULL,
            created_at       timestamp NOT NULL DEFAULT now()
        )

