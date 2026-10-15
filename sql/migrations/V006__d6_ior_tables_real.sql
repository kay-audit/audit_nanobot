-- ============================================================================
-- V006 — d6_ior_tables_real (feature/d6-nanobot-with-manifest)
-- ============================================================================
-- Создаёт таблицы IOR в PG-DEV под схемой ``public`` с РЕАЛЬНЫМИ именами
-- таблиц из production (``t_db_oarb_ior_d6_*``), чтобы skill мог работать
-- по тому же SQL что и на ПРОМе, без runtime-переписывания имён.
--
-- Имена таблиц — production-реальные (для совместимости SQL из
-- ior_reports.py / preset_analysis / dataframe_ops).
-- Схема — ``public`` (наша PG-DEV), а не ``s_grnplm_ld_audit_da_project_34``
-- (production Greenplum). Runtime выбирает схему через env
-- ``IOR_GP_SCHEMA`` (см. workspace/skills/ior-analyzer/utils/data_store.py).
--
-- Полный набор колонок — соответствует production GP (таблица ior содержит
-- все колонки, на которые ссылаются SELECT ior.* и JOIN'ы в
-- scripts/ior_reports.py). Минимальный набор V006 — расширен до полного
-- (см. ``PLAN.md``).
--
-- Изоляция: rollback фичи ``d6_nanobot`` дропает таблицы через
-- ``DROP TABLE IF EXISTS public.t_db_oarb_ior_d6_* CASCADE``
-- (см. feature.yaml::isolation.removable).
--
-- Совместимость: PostgreSQL 12+, Greenplum 6.x (без pgcrypto-зависимостей).
-- Идемпотентность: ``DROP TABLE IF EXISTS`` + ``CREATE TABLE``.
-- ============================================================================

-- ============================================================================
-- 1. Основная таблица инцидентов
-- ============================================================================
DROP TABLE IF EXISTS public.t_db_oarb_ior_d6_base_of_knowledge_ior CASCADE;

CREATE TABLE public.t_db_oarb_ior_d6_base_of_knowledge_ior (
    -- идентификаторы и общая информация
    incdnt_id                    BIGINT PRIMARY KEY,
    incdnt_sid                   VARCHAR(32) NOT NULL UNIQUE,
    incdnt_agr_num               VARCHAR(64),
    incdnt_agr_sid               VARCHAR(64),
    incdnt_appl_num              VARCHAR(64),

    -- статус
    incdnt_status_name           VARCHAR(64),
    incdnt_status_code           VARCHAR(16),
    incdnt_autoreg_flag          VARCHAR(2),

    -- люди и контроль
    incdnt_detection_person_name VARCHAR(256),
    incdnt_mistake_cnt           INTEGER,

    -- даты
    incdnt_entry_dt              TIMESTAMP,
    incdnt_detection_dt          TIMESTAMP,
    incdnt_start_dt              TIMESTAMP,
    incdnt_first_validated_dttm  TIMESTAMP,
    incdnt_last_validate_dttm    TIMESTAMP,

    -- описания
    incdnt_summary_descr_txt     TEXT,
    incdnt_full_descr_txt        TEXT,

    -- риск
    risk_profile_id              VARCHAR(64),
    risk_profile_name            VARCHAR(256),
    incdnt_security_risk_flag    VARCHAR(2),
    incdnt_infrmtn_sys_risk_flag VARCHAR(2),
    incdnt_behavior_risk_flag    VARCHAR(2),
    incdnt_model_risk_flag       VARCHAR(2),

    -- типы
    incdnt_type_lvl_1_name       VARCHAR(256),
    incdnt_type_lvl_2_name       VARCHAR(256),
    incdnt_source_name           VARCHAR(256),
    src_type_lvl_1_name          VARCHAR(256),
    src_type_lvl_2_name          VARCHAR(256),

    -- оргструктура
    org_struct_id                VARCHAR(64),
    org_struct_lvl_2_name        VARCHAR(256),
    org_struct_lvl_3_name        VARCHAR(256),
    org_struct_lvl_4_name        VARCHAR(256),
    org_struct_lvl_5_name        VARCHAR(256),
    org_struct_lvl_6_name        VARCHAR(256),
    org_struct_lvl_7_name        VARCHAR(256),
    org_struct_lvl_8_name        VARCHAR(256),
    org_struct_lvl_9_name        VARCHAR(256),
    org_struct_lvl_10_name       VARCHAR(256),

    -- функц. блок
    funct_block_id               VARCHAR(64),
    funct_block_lvl_2_name       VARCHAR(256),
    funct_block_lvl_3_name       VARCHAR(256),
    funct_block_lvl_4_name       VARCHAR(256),

    -- процесс
    process_lvl_1_name           VARCHAR(256),
    process_lvl_2_name           VARCHAR(256),
    process_lvl_3_name           VARCHAR(256),
    process_lvl_4_name           VARCHAR(256),

    -- клиент
    incdnt_client_type_name      VARCHAR(32),

    -- финансовые суммы
    incdnt_sum                   NUMERIC(28,4),
    incdnt_drct_dmg_sum          NUMERIC(28,4),
    incdnt_drct_dmg_cred_rub_amt NUMERIC(28,4),
    incdnt_drct_dmg_noncred_rub_amt NUMERIC(28,4),
    incdnt_indrct_dmg_sum        NUMERIC(28,4),
    incdnt_indrct_dmg_cred_rub_amt NUMERIC(28,4),
    incdnt_indrct_dmg_noncred_rub_amt NUMERIC(28,4),
    incdnt_gain_sum              NUMERIC(28,4),
    incdnt_gain_cred_rub_amt     NUMERIC(28,4),
    incdnt_gain_noncred_rub_amt  NUMERIC(28,4),
    incdnt_thrd_prt_sum          NUMERIC(28,4),
    incdnt_thrd_prt_cred_rub_amt NUMERIC(28,4),
    incdnt_thrd_prt_noncred_rub_amt NUMERIC(28,4),
    incdnt_unrlzd_dmg_sum        NUMERIC(28,4),
    incdnt_unrlzd_dmg_cred_rub_amt NUMERIC(28,4),
    incdnt_unrlzd_dmg_noncred_rub_amt NUMERIC(28,4),
    recovery_rub_amt_aggr        NUMERIC(28,4),

    created_at                   TIMESTAMPTZ NOT NULL DEFAULT now()
);

COMMENT ON TABLE public.t_db_oarb_ior_d6_base_of_knowledge_ior IS
    'Основная таблица инцидентов операционного риска. '
    'Имя совпадает с ПРОМ-GP; схема — public (наша DEV-БД). '
    'Заполняется через pg_loader.py из workspace/data_store/cache/testing/ior/ior.json.';

CREATE INDEX t_db_oarb_ior_d6_base_of_knowledge_ior_status_idx
    ON public.t_db_oarb_ior_d6_base_of_knowledge_ior (incdnt_status_name);
CREATE INDEX t_db_oarb_ior_d6_base_of_knowledge_ior_entry_dt_idx
    ON public.t_db_oarb_ior_d6_base_of_knowledge_ior (incdnt_entry_dt);
CREATE INDEX t_db_oarb_ior_d6_base_of_knowledge_ior_risk_idx
    ON public.t_db_oarb_ior_d6_base_of_knowledge_ior (risk_profile_id);

-- ============================================================================
-- 2. История изменений статусов
-- ============================================================================
DROP TABLE IF EXISTS public.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng CASCADE;

CREATE TABLE public.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng (
    incdnt_id              BIGINT NOT NULL,
    incdnt_status_name     VARCHAR(64),
    incdnt_status_code     VARCHAR(16),
    stts_chng_action_code  VARCHAR(32),
    stts_chng_action_name  VARCHAR(256),
    stts_chng_comment_txt  TEXT,
    stts_chng_action_dttm  TIMESTAMP,
    stts_chng_user_num     VARCHAR(32)
);

COMMENT ON TABLE public.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng IS
    'История смены статусов инцидентов ИОР. '
    'Имя совпадает с ПРОМ-GP; схема — public (наша DEV-БД).';

CREATE INDEX t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng_id_idx
    ON public.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng (incdnt_id);

-- ============================================================================
-- 3. Возмещение
-- ============================================================================
DROP TABLE IF EXISTS public.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery CASCADE;

CREATE TABLE public.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery (
    incdnt_id              BIGINT NOT NULL,
    recovery_id            BIGINT,
    recovery_sid           VARCHAR(64),
    recovery_type_name     VARCHAR(64),
    recovery_crncy_code    VARCHAR(16),
    recovery_local_crncy_code VARCHAR(16),
    recovery_src_account_num VARCHAR(64),
    recovery_doc_num       VARCHAR(64),
    recovery_creation_dttm TIMESTAMP,
    recovery_reg_dt        TIMESTAMP,
    recovery_ccy_amt       NUMERIC(28,4),
    recovery_local_ccy_amt NUMERIC(28,4),
    recovery_rub_amt       NUMERIC(28,4),
    recovery_comment_txt   TEXT,
    recovery_user_num      VARCHAR(32)
);

COMMENT ON TABLE public.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery IS
    'Возмещения по инцидентам ИОР. '
    'Имя совпадает с ПРОМ-GP; схема — public (наша DEV-БД).';

CREATE INDEX t_db_oarb_ior_d6_base_of_knowledge_incident_recovery_id_idx
    ON public.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery (incdnt_id);

-- ============================================================================
-- 4. Финансовые последствия
-- ============================================================================
DROP TABLE IF EXISTS public.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact CASCADE;

CREATE TABLE public.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact (
    incdnt_id                  BIGINT NOT NULL,
    fin_impact_id              BIGINT,
    fin_impact_sid             VARCHAR(64),
    fin_impact_type_name       VARCHAR(64),
    fin_impact_kind_name       VARCHAR(256),
    fin_impact_monitoring_flag VARCHAR(2),
    fin_impact_crncy_code      VARCHAR(16),
    fin_impact_local_crncy_code VARCHAR(16),
    fin_impact_detection_dt    TIMESTAMP,
    fin_impact_creation_dttm   TIMESTAMP,
    fin_impact_reg_dt          TIMESTAMP,
    fin_impact_account_num     VARCHAR(64),
    fin_impact_docum_num       VARCHAR(64),
    fi_busn_area_id            VARCHAR(64),
    fi_org_struct_id           VARCHAR(64),
    fin_impact_ccy_amt         NUMERIC(28,4),
    fin_impact_local_ccy_amt   NUMERIC(28,4),
    fin_impact_rub_amt         NUMERIC(28,4)
);

COMMENT ON TABLE public.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact IS
    'Финансовые последствия инцидентов ИОР. '
    'Имя совпадает с ПРОМ-GP; схема — public (наша DEV-БД).';

CREATE INDEX t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact_id_idx
    ON public.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact (incdnt_id);

-- ============================================================================
-- 5. Нефинансовые последствия
-- ============================================================================
DROP TABLE IF EXISTS public.t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact CASCADE;

CREATE TABLE public.t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact (
    incdnt_id                       BIGINT NOT NULL,
    nonfin_impact_sid               VARCHAR(64),
    nonfin_impact_kind_name         VARCHAR(256),
    nonfin_impact_influence_class_name VARCHAR(256),
    nonfin_impact_name              VARCHAR(256),
    nonfin_impact_comment           TEXT,
    nonfin_impact_creation_dttm     TIMESTAMP
);

COMMENT ON TABLE public.t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact IS
    'Нефинансовые последствия инцидентов ИОР. '
    'Имя совпадает с ПРОМ-GP; схема — public (наша DEV-БД).';

CREATE INDEX t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact_id_idx
    ON public.t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact (incdnt_id);
