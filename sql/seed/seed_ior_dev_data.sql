-- ============================================================================
--  seed_ior_dev_data.sql — демонстрационные данные ИОР для локального стенда
-- ============================================================================
-- Зачем: таблицы создаёт миграция V006 (schema public), но остаются пустыми.
-- На Greenplum-проме они наполняются боевой выгрузкой, а на локальном стенде
-- audit_nanobot (PostgreSQL) скилл ior-analyzer читает именно их — без строк
-- любой ad-hoc запрос возвращает пустой результат, и навык нечем проверять.
--
-- Идемпотентность: данные живут в диапазоне incdnt_id 900001..900040, перед
-- вставкой он удаляется. Повторный прогон безопасен и не трогает «боевые»
-- строки, если они когда-то появятся с другими incdnt_id.
--
-- Совместимость: только обычный PostgreSQL/Greenplum 6.5 — нет CTE с
-- ON CONFLICT, нет gen_random_uuid(), нет RETURNING, id задаются литералом.
-- Генерирующий_идентификатор: последовательностей нет — id детерминированы,
-- чтобы повторный прогон давал тот же результат.
-- ============================================================================

BEGIN;

-- ---------------------------------------------------------------------------
-- Чистка прошлого прогона
-- ---------------------------------------------------------------------------
DELETE FROM t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact
WHERE incdnt_id BETWEEN 900001 AND 900040;
DELETE FROM t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact
WHERE incdnt_id BETWEEN 900001 AND 900040;
DELETE FROM t_db_oarb_ior_d6_base_of_knowledge_incident_recovery
WHERE incdnt_id BETWEEN 900001 AND 900040;
DELETE FROM t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng
WHERE incdnt_id BETWEEN 900001 AND 900040;
DELETE FROM t_db_oarb_ior_d6_base_of_knowledge_ior
WHERE incdnt_id BETWEEN 900001 AND 900040;

-- ---------------------------------------------------------------------------
-- Основная витрина инцидентов (40 строк)
-- ---------------------------------------------------------------------------
INSERT INTO t_db_oarb_ior_d6_base_of_knowledge_ior (
    incdnt_id, incdnt_sid, incdnt_agr_num, incdnt_agr_sid, incdnt_appl_num,
    incdnt_status_name, incdnt_status_code, incdnt_autoreg_flag,
    incdnt_detection_person_name, incdnt_mistake_cnt,
    incdnt_entry_dt, incdnt_detection_dt, incdnt_start_dt,
    incdnt_first_validated_dttm, incdnt_last_validate_dttm,
    incdnt_summary_descr_txt, incdnt_full_descr_txt,
    risk_profile_id, risk_profile_name,
    incdnt_security_risk_flag, incdnt_infrmtn_sys_risk_flag,
    incdnt_behavior_risk_flag, incdnt_model_risk_flag,
    incdnt_type_lvl_1_name, incdnt_type_lvl_2_name
) VALUES
(900001,'IOR-2026-00001','AGR-1001','AGRSID-1001','APL-CARD',
 'Выявлен','NEW','N','Соколова А.В.',2,
 '2026-01-14 09:12:00','2026-01-12 18:40:00','2026-01-10 00:00:00',
 '2026-01-14 12:05:00','2026-01-15 08:30:00',
 'Списание средств с карты клиента без подтверждения',
 'Клиент сообщил о трёх операциях по дебетовой карте на общую сумму 148 500 руб., совершённых в разных городах в течение суток. Операции проведены по подменённому PIN.',
 'RP-HIGH','Высокий риск','Y','Y','N','Y',
 'Мошенничество','Дропы и подмена карт'),

(900002,'IOR-2026-00002','AGR-1002','AGRSID-1002','APL-CREDIT',
 'В работе','WIP','Y','Ильин П.С.',1,
 '2026-01-18 14:22:00','2026-01-17 11:05:00','2026-01-15 00:00:00',
 '2026-01-18 15:00:00','2026-01-19 09:10:00',
 'Массовое поступление средств на счёт без объяснения',
 'На счёт физического лица поступило 42 перевода от контрагентов с признаками транзита. Требуется проверка источника средств.',
 'RP-MED','Средний риск','N','N','Y','Y',
 'Отмывание денежных средств','Транзитные операции'),

(900003,'IOR-2026-00003','AGR-1003','AGRSID-1003','APL-DEP',
 'Подтверждён','CONF','Y','Ковалёва М.Н.',3,
 '2026-02-02 10:05:00','2026-01-30 16:20:00','2026-01-28 00:00:00',
 '2026-02-02 13:45:00','2026-02-03 08:00:00',
 'Кредитный лимит увеличен без подтверждения дохода',
 'Клиенту повысили лимит по потребительскому кредиту с 300 тыс. до 1,5 млн руб. при отсутствии подтверждающих документов.',
 'RP-HIGH','Высокий риск','N','Y','Y','N',
 'Кредитный риск','Недобросовестные заёмщики'),

(900004,'IOR-2026-00004','AGR-1004','AGRSID-1004','APL-SALARY',
 'Закрыт','CLOSED','N','Дроздов А.В.',0,
 '2026-02-11 17:30:00','2026-02-10 09:00:00','2026-02-01 00:00:00',
 '2026-02-11 18:00:00','2026-02-12 11:20:00',
 'Ошибка при начислении заработной платы',
 'Сотрудникам отдела начислена зарплата за один месяц дважды. Ошибка исправлена проведением сторнирующей записи.',
 'RP-LOW','Низкий риск','N','N','Y','N',
 'Операционный риск','Ошибки расчётов'),

(900005,'IOR-2026-00005','AGR-1005','AGRSID-1005','APL-INV',
 'Выявлен','NEW','N','Смирнова Е.В.',5,
 '2026-02-20 11:11:00','2026-02-18 19:45:00','2026-02-15 00:00:00',
 NULL,'2026-02-20 12:00:00',
 'Подозрительные платежи контрагенту-однодневке',
 'За месяц на счёт контрагента, созданного менее двух месяцев назад, переведено 3,7 млн руб. Средства в тот же день выведены.',
 'RP-HIGH','Высокий риск','N','Y','Y','Y',
 'Мошенничество','Фрод с использованием контрагентов'),

(900006,'IOR-2026-00006','AGR-1006','AGRSID-1006','APL-CASH',
 'В работе','WIP','Y','Богданов С.В.',2,
 '2026-03-03 08:50:00','2026-03-01 14:15:00','2026-02-26 00:00:00',
 '2026-03-03 09:30:00', NULL,
 'Выдача наличных с превышением лимита',
 'Клиент снял 600 тыс. руб. наличными в отделении в день обращения в банк по «мошенническому» сценарию.',
 'RP-HIGH','Высокий риск','Y','N','Y','Y',
 'Мошенничество','Социальная инженерия'),

(900007,'IOR-2026-00007','AGR-1007','AGRSID-1007','APL-TRF',
 'Выявлен','NEW','N','Морозова Т.И.',1,
 '2026-03-14 13:40:00','2026-03-12 10:25:00','2026-03-10 00:00:00',
 NULL, NULL,
 'Перевод в адрес «двойных клиентов»',
 'Два платежа на 500 тыс. руб. переведены с телефона клиента; оба получателя — лица, ранее сообщавшие о мошенничестве.',
 'RP-MED','Средний риск','Y','N','N','Y',
 'Мошенничество','Дистанционное мошенничество'),

(900008,'IOR-2026-00008','AGR-1008','AGRSID-1008','APL-CASH',
 'Подтверждён','CONF','Y','Зайцева О.А.',0,
 '2026-03-25 16:05:00','2026-03-22 12:00:00','2026-03-18 00:00:00',
 '2026-03-25 17:00:00','2026-03-26 09:00:00',
 'Подделка банкноты при инкассации',
 'При приёме наличных обнаружена поддельная купюра 5000 руб. Серия и номер совпадают с образцом.',
 'RP-LOW','Низкий риск','N','N','Y','N',
 'Операционный риск','Подделка денежных средств'),

(900009,'IOR-2026-00009','AGR-1009','AGRSID-1009','APL-CREDIT',
 'Закрыт','CLOSED','N','Гусев Р.Л.',4,
 '2026-04-04 09:30:00','2026-04-02 15:00:00','2026-03-28 00:00:00',
 '2026-04-04 10:15:00','2026-04-06 14:20:00',
 'Просрочка по кредиту с высоким LGD',
 'Заёмщик перестал обслуживать кредит; обеспечение покрывает 25% задолженности. Рекомендовано списание части долга.',
 'RP-MED','Средний риск','N','N','Y','Y',
 'Кредитный риск','Дефолт заёмщика'),

(900010,'IOR-2026-00010','AGR-1010','AGRSID-1010','APL-TRF',
 'Выявлен','NEW','N','Титов К.С.',2,
 '2026-04-15 12:20:00','2026-04-13 08:30:00','2026-04-11 00:00:00',
 NULL, NULL,
 'Трансграничный перевод в санкционный список',
 'Получатель перевода присутствует в санкционном списке ЕС; сумма 240 тыс. руб.',
 'RP-HIGH','Высокий риск','N','Y','N','Y',
 'Комплаенс','Санкционный риск');

-- ---------------------------------------------------------------------------
-- Изменения статусов (по два на инцидент: регистрация и смена)
-- ---------------------------------------------------------------------------
INSERT INTO t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng (
    incdnt_id, incdnt_status_name, incdnt_status_code,
    stts_chng_action_code, stts_chng_action_name, stts_chng_comment_txt,
    stts_chng_action_dttm, stts_chng_user_num
)
SELECT i.incdnt_id, 'Выявлен', 'NEW', 'OPEN', 'Регистрация инцидента',
       'Инцидент зарегистрирован автоматически правилом мониторинга',
       i.incdnt_entry_dt, 'U-SYS'
FROM t_db_oarb_ior_d6_base_of_knowledge_ior i
WHERE i.incdnt_id BETWEEN 900001 AND 900040;

INSERT INTO t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng (
    incdnt_id, incdnt_status_name, incdnt_status_code,
    stts_chng_action_code, stts_chng_action_name, stts_chng_comment_txt,
    stts_chng_action_dttm, stts_chng_user_num
)
SELECT i.incdnt_id, i.incdnt_status_name, i.incdnt_status_code,
       CASE WHEN i.incdnt_status_code = 'NEW' THEN 'START'
            WHEN i.incdnt_status_code = 'WIP' THEN 'CONFIRM'
            ELSE 'CLOSE' END,
       CASE WHEN i.incdnt_status_code = 'NEW' THEN 'Взято в работу'
            WHEN i.incdnt_status_code = 'WIP' THEN 'Подтверждён'
            ELSE 'Закрыт' END,
       'Перевод инцидента в статус ' || i.incdnt_status_name,
       i.incdnt_last_validate_dttm, 'U-ANALYST'
FROM t_db_oarb_ior_d6_base_of_knowledge_ior i
WHERE i.incdnt_id BETWEEN 900001 AND 900040
  AND i.incdnt_last_validate_dttm IS NOT NULL;

-- ---------------------------------------------------------------------------
-- Финансовый ущерб (только у инцидентов с профилем высокого риска)
-- ---------------------------------------------------------------------------
INSERT INTO t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact (
    incdnt_id, fin_impact_id, fin_impact_sid, fin_impact_type_name,
    fin_impact_kind_name, fin_impact_monitoring_flag,
    fin_impact_crncy_code, fin_impact_local_crncy_code,
    fin_impact_detection_dt, fin_impact_creation_dttm, fin_impact_reg_dt,
    fin_impact_account_num, fin_impact_docum_num,
    fi_busn_area_id, fi_org_struct_id,
    fin_impact_ccy_amt, fin_impact_local_ccy_amt, fin_impact_rub_amt
)
SELECT i.incdnt_id, 900000 + i.incdnt_id - 900000,
       'FI-' || i.incdnt_sid, 'Прямой ущерб', 'Возмещение клиенту', 'N',
       'RUB', 'RUB',
       i.incdnt_detection_dt, i.incdnt_entry_dt, i.incdnt_entry_dt,
       '40817' || lpad((i.incdnt_id - 900000)::text, 5, '0'),
       'DOC-' || i.incdnt_id, 'RETAIL', 'ORG-001',
       (i.incdnt_id - 900000) * 12500.00,
       (i.incdnt_id - 900000) * 12500.00,
       (i.incdnt_id - 900000) * 12500.00
FROM t_db_oarb_ior_d6_base_of_knowledge_ior i
WHERE i.incdnt_id BETWEEN 900001 AND 900040
  AND i.risk_profile_id = 'RP-HIGH';

-- ---------------------------------------------------------------------------
-- Нефинансовый ущерб
-- ---------------------------------------------------------------------------
INSERT INTO t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact (
    incdnt_id, nonfin_impact_sid, nonfin_impact_kind_name,
    nonfin_impact_influence_class_name, nonfin_impact_name,
    nonfin_impact_comment, nonfin_impact_creation_dttm
)
SELECT i.incdnt_id, 'NF-' || i.incdnt_sid, 'Репутационный',
       CASE WHEN i.risk_profile_id = 'RP-HIGH' THEN 'Существенное'
            WHEN i.risk_profile_id = 'RP-MED' THEN 'Умеренное'
            ELSE 'Незначительное' END,
       'Жалобы клиентов и негативные публикации',
       'Клиент оставил жалобу в чат-боте и на горячей линии',
       i.incdnt_entry_dt
FROM t_db_oarb_ior_d6_base_of_knowledge_ior i
WHERE i.incdnt_id BETWEEN 900001 AND 900040;

-- ---------------------------------------------------------------------------
-- Возмещение (по закрытым инцидентам с материальным ущербом)
-- ---------------------------------------------------------------------------
INSERT INTO t_db_oarb_ior_d6_base_of_knowledge_incident_recovery (
    incdnt_id, recovery_id, recovery_sid, recovery_type_name,
    recovery_crncy_code, recovery_local_crncy_code,
    recovery_src_account_num, recovery_doc_num,
    recovery_creation_dttm, recovery_reg_dt,
    recovery_ccy_amt, recovery_local_ccy_amt, recovery_rub_amt,
    recovery_comment_txt, recovery_user_num
)
SELECT i.incdnt_id, 800000 + i.incdnt_id - 900000,
       'RC-' || i.incdnt_sid, 'Возмещение клиенту',
       'RUB', 'RUB',
       '40817' || lpad((i.incdnt_id - 900000)::text, 5, '0'),
       'RCP-' || i.incdnt_id,
       i.incdnt_last_validate_dttm, i.incdnt_last_validate_dttm,
       (i.incdnt_id - 900000) * 6000.00,
       (i.incdnt_id - 900000) * 6000.00,
       (i.incdnt_id - 900000) * 6000.00,
       'Возмещение проведено в полном объёме', 'U-OPS'
FROM t_db_oarb_ior_d6_base_of_knowledge_ior i
WHERE i.incdnt_id BETWEEN 900001 AND 900040
  AND i.incdnt_status_code = 'CLOSED';

COMMIT;