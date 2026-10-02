-- V008__fu_poruch_data.sql
-- follow_up: data-store для fixture в PostgreSQL (dev-режим).
--
-- До этой миграции follow_up в dev-режиме (GP_ENABLED=false) читал
-- data из файла workspace/skills/follow_up/data/fixtures/poruch_fixture.json.
-- Изменения — через edit + restart навыка, что медленно для тестирования.
--
-- V008 вводит public.t_fu_poruch_data — таблицу данных follow_up, в которую
-- резолвер execution_control._poruch_mcp().fetch_view_rows() ходит в режиме
-- DB_ENV__MODE=postgres. Источник один и тот же (fixture.json) → можно
-- накатывать по SQL сразу и гибко менять набор строк под сценарий теста.
--
-- Схема повторяет поля файла fixture.json + poruch_key (MD5 от km|doc_reg|assignment_)
-- для совместимости с резолвером (он ожидает это поле у каждой строки).
--
-- Идемпотентно: CREATE TABLE IF NOT EXISTS + CREATE INDEX IF NOT EXISTS.
-- Совместимо: PostgreSQL 12+ и Greenplum 6.5 (без GP-специфики).

CREATE TABLE IF NOT EXISTS public.t_fu_poruch_data (
    km_id        varchar(20) NOT NULL,
    doc_reg_num  varchar(100) NULL,
    problem      text NOT NULL,
    assignment_  text NOT NULL,
    poruch_status varchar(80) NOT NULL,
    close_fact   text NULL,
    actions      text NULL,
    block_unit   varchar(255) NULL,
    poruch_key   varchar(32) NOT NULL,
    updated_at   timestamp NOT NULL DEFAULT now(),
    PRIMARY KEY (poruch_key)
);

CREATE INDEX IF NOT EXISTS t_fu_poruch_data_km_id_idx
    ON public.t_fu_poruch_data (km_id);

COMMENT ON TABLE public.t_fu_poruch_data IS
    'Данные follow_up для dev-режима (DB_ENV__MODE=postgres). '
    'Совпадает по полям с workspace/skills/follow_up/data/fixtures/poruch_fixture.json. '
    'Изменения — прямым SQL: INSERT/UPDATE/DELETE; резолвер читает на каждом ходу.';

COMMENT ON COLUMN public.t_fu_poruch_data.poruch_key IS
    'MD5(km_id|doc_reg_num|assignment_)[:32] — стабильный id строки, '
    'вычисляется в backend.storage.postgres_fixture._compute_poruch_key(). '
    'Совпадает с формулой в Greenplum-режиме (storage.gp.poruch_key).';