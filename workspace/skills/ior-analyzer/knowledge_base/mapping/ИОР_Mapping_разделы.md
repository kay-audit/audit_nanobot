# Карта соответствия полей (Mapping) – Блок ИОР

> Справочник полей Базы знаний ИОР для использования LLM в составе AI-помощника
> (Disrupt-проект по ОР, этап «Работа с LLM», срок 31.05.2026).
> Ответственные: СРБ + СЗБ.

---

## 0. Метаинформация

| Параметр | Значение |
|----------|----------|
| Версия | 2.0 от 2026-05-22 |
| Формат | Markdown |
| Источник полей | Аудит структуры БЗ от 22.05.2026 + правки Пономаренко Н.П. |
| Схема БЗ | `arnsdpsbx_t_team_sva_oarb_4` |
| Период данных | 2025-01-01 … 2026-03-31 |
| Скрипты выгрузки | см. отдельные Markdown в [`scripts/`](scripts/) |

---

## 1. Архитектура Базы знаний ИОР

### 1.1. Состав таблиц

База знаний по Блоку ИОР состоит из **5 таблиц**:

| № | Таблица | Назначение | Полей | Строк |
|:-:|---------|-----------|:-----:|------:|
| 1 | `d6_base_of_knowledge_ior` | **Основная** – инцидент, агрегаты, флаги | 67 | 980 504 |
| 2 | `d6_base_of_knowledge_incident_stts_chng` | История изменения статусов (1:N) | 9 | 1 151 278 |
| 3 | `d6_base_of_knowledge_incident_recovery` | Возмещения детально (1:N) | 12 | 1 942 565 |
| 4 | `d6_base_of_knowledge_incident_fin_impact` | Финансовые последствия детально (1:N) | 17 | 4 421 124 |
| 5 | `d6_base_of_knowledge_incident_nonfin_impact` | Нефинансовые последствия (1:N) | 4 | 238 963 |

Для запросов «по виновным сотрудникам» и связки с продуктами используются **внешние блоки БЗ**:

| Таблица | Назначение | Полей |
|---------|-----------|:-----:|
| `d6_base_of_knowledge_employee` | Виновные сотрудники по ИОР (`incdnt_sid` → табельный) | 2 |
| `d6_base_of_knowledge_empl` | Полная база сотрудников Сбера (с ФИО) | 5 |
| `d6_base_of_knowledge_b2c_credit_fl` | Кредиты ФЛ | 13 |
| `d6_base_of_knowledge_cards` | Карты | 11 |

### 1.2. Схема связей

```
d6_base_of_knowledge_ior
(основная таблица, 67 полей)
PK: incdnt_id

        │                    │                       │
    incdnt_id                │                   incdnt_sid
        │                    │                       │   │
        ▼                    ▼                       ▼   ▼
incident_stts_chng   incident_recovery            employee
(статусы, 9 полей)   (возмещения, 12)            (виновные, 2)
                                                       │ value_
                                                       ▼
incident_fin_impact   incident_nonfin_impact|       empl
(фин. последствия, 17)  (нефин. последствия, 4)|  (с ФИО, 5 полей)
        ▲                       ▲
        │ incdnt_id             │ incdnt_id
        └───────────────────────┘
```

### 1.3. SQL-связки между таблицами

**Основная таблица ИОР ↔ дополнительные таблицы ИОР:**

```sql
-- История статусов
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior main
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng not_main
  ON main.incdnt_id = not_main.incdnt_id

-- Возмещения детально
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior main
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery not_main
  ON main.incdnt_id = not_main.incdnt_id

-- Финансовые последствия детально
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior main
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact not_main
  ON main.incdnt_id = not_main.incdnt_id

-- Нефинансовые последствия
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior main
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact not_main
  ON main.incdnt_id = not_main.incdnt_id
```

**ИОР ↔ Кредиты ФЛ:**

```sql
WITH agrmnt_nbr AS (
  SELECT *
  FROM arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_b2c_credit_fl b2c
  INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
    ON b2c.loan_agrmnt_id = ior.incdnt_agr_sid  -- по идентификатору договора
),
etsm_request_id AS (
  SELECT *
  FROM arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_b2c_credit_fl b2c
  INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
    ON b2c.etsm_request_id = ior.incdnt_appl_num  -- по номеру заявки
)
SELECT * FROM agrmnt_nbr
UNION
SELECT * FROM etsm_request_id;
```

**ИОР ↔ Виновные сотрудники:**

```sql
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
INNER JOIN arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_employee empl
  ON ior.incdnt_sid = empl.aggregateroot_id
```

**Виновный сотрудник → ФИО:**

```sql
FROM arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_employee e
INNER JOIN arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_empl ef
  ON e.value_ = ef.value_  -- по табельному номеру
```

**ИОР ↔ Карты:**

```sql
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
INNER JOIN arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_cards cred_card
  ON ior.incdnt_agr_num = cred_card.contract_number
```

---

## 2. Mapping по основной таблице `d6_base_of_knowledge_ior`

Полное имя: `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior` (67 колонок).
В колонке «Источник» далее везде указано это полное имя.

### 2.1. Идентификация и статус

Перечень полей БД, используемых для запросов вида: «найди ИОР EVE-…», «ИОР в статусе Исследование», «авторегистрационные ИОР», «ИОР, выявленные УВА».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_id` | `…d6_base_of_knowledge_ior` | bigint | Идентификационный ключ инцидента (внутренний) | id, ключ инцидента |
| `incdnt_sid` | `…d6_base_of_knowledge_ior` | string | Бизнес-идентификатор события (формат `EVE-XXXXXXX`) | SID, идентификатор, EVE-номер, номер события, номер инцидента |
| `incdnt_status_name` | `…d6_base_of_knowledge_ior` | string | Текущий статус инцидента. Значения: `Исследование`, `Утверждение`, `Утверждён`, `Удалён`, `Черновик` | статус, состояние |
| `incdnt_autoreg_flag` | `…d6_base_of_knowledge_ior` | string (Y/N) | Признак авторегистрации инцидента | автрегистрация, автоматически, autoreg |
| `incdnt_detection_person_name` | `…d6_base_of_knowledge_ior` | string | Категория лица, выявившего инцидент. Значения: `Внешние контролирующие органы`, `Вторая линия`, `Клиент`, `Первая линия`, `Сотрудник СВА`, `Сотрудник СВА в рамках оценки эффективности` | кем выявлено, выявитель, кто нашёл |
| `incdnt_mistake_cnt` | `…d6_base_of_knowledge_ior` | int | Количество ошибок, совершённых сотрудниками банка | количество ошибок |

### 2.2. Даты жизненного цикла

Перечень полей БД, используемых для запросов вида: «ИОР за 2025 год», «ИОР, обнаруженные в Q4 2025», «ИОР, утверждённые впервые в январе 2026».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_entry_dt` | `…d6_base_of_knowledge_ior` | timestamp | **Дата ввода инцидента в систему** (главное поле для фильтра по периоду) | дата ввода, дата регистрации, зарегистрирован |
| `incdnt_detection_dt` | `…d6_base_of_knowledge_ior` | timestamp | Дата обнаружения события | дата обнаружения, дата выявления, выявлен |
| `incdnt_start_dt` | `…d6_base_of_knowledge_ior` | timestamp | Дата начала инцидента | дата начала, дата события, произошёл |
| `incdnt_first_validated_dttm` | `…d6_base_of_knowledge_ior` | timestamp | Первая дата утверждения | первое утверждение, утверждён впервые |
| `incdnt_last_validate_dttm` | `…d6_base_of_knowledge_ior` | timestamp | Последняя дата утверждения | последнее утверждение, актуальная редакция |

### 2.3. Описание инцидента

Перечень полей БД, используемых для запросов вида: «ИОР про банкомат», «ИОР про хищение наличных», поиск по ключевым словам.

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_summary_descr_txt` | `…d6_base_of_knowledge_ior` | string | Предварительное (краткое) описание | краткое описание, summary, тема |
| `incdnt_full_descr_txt` | `…d6_base_of_knowledge_ior` | string | Подробное (полное) описание | полное описание, детали, обстоятельства |

### 2.4. Природа риска (ЦПР)

Перечень полей БД, используемых для запросов вида: «ИОР по мошенничеству со стороны клиента», «ИОР по фиктивному отчёту об оценке залога», «ИОР по природе риска "Хищение средств сотрудником"».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `risk_profile_id` | `…d6_base_of_knowledge_ior` | string | Идентификационный ключ ЦПР | ключ профиля риска |
| `risk_profile_name` | `…d6_base_of_knowledge_ior` | string | Наименование Цифрового профиля риска (52 значения) | ЦПР, цифровой профиль риска, природа риска, причина инцидента |

### 2.5. Классификация: тип события

Перечень полей БД, используемых для запросов вида: «ИОР по хищениям сотрудниками», «ИОР по недостачам в банкоматах», «Статистика ИОР по инцидентам, связанным с хищениями».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_type_lvl_1_name` | `…d6_base_of_knowledge_ior` | string | Тип события – уровень 1 (8 категорий: 1. Ошибки персонала; 2. Нарушение и сбои систем; 3. Нарушение прав клиентов; 4. Нарушение кадровой политики; 5. Преднамеренные действия персонала; 6. Ущерб материальным активам; 7. Преднамеренные действия третьих лиц) | тип события, категория ИОР |
| `incdnt_type_lvl_2_name` | `…d6_base_of_knowledge_ior` | string | Тип события – уровень 2 (23 значения, подкатегории) | подтип события, подкатегория |

### 2.6. Источник инцидента

Перечень полей БД, используемых для запросов вида: «ИОР по проверкам УВА», «ИОР по обращениям клиентов», «ИОР по результатам служебных расследований».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_source_name` | `…d6_base_of_knowledge_ior` | string | Название источника. Значения: `Мониторинг АС`, `Реестровое уведомление`, `Результат анализа акта УВА`, `Уведомление РК` | источник |
| `src_type_lvl_1_name` | `…d6_base_of_knowledge_ior` | string | Тип источника – уровень 1. Значения: `Внешние причины`, `Действия персонала`, `Недостатки процессов`, `Сбои систем и оборудования` | категория источника |
| `src_type_lvl_2_name` | `…d6_base_of_knowledge_ior` | string | Подтип источника – уровень 2 (17 значений: `Внешнее мошенничество`, `Действия третьих лиц`, `Умышленные действия сотрудников`, `Непреднамеренные ошибки сотрудников`, `Недоступность систем` и т.д.) | подтип источника |

### 2.7. Организационная структура (владелец)

Перечень полей БД, используемых для запросов вида: «ИОР за 2025 по СЗБ», «ИОР по ВСП №…», «ИОР по подразделению…».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `org_struct_id` | `…d6_base_of_knowledge_ior` | string | Идентификатор оргструктуры (префиксы `SBR_/EXT_/GRC_/MON_/BPS_` = ПАО Сбербанк) | оргструктура id |
| `org_struct_lvl_2_name` | `…d6_base_of_knowledge_ior` | string | Категория терр. структуры (значения: `ПАО Сбербанк (ЦА)`, `Подразделения центрального подчинения`, `Территориальные банки`…) | категория терр. структуры |
| `org_struct_lvl_3_name` | `…d6_base_of_knowledge_ior` | string | **Блок / ТБ / ПЦП** – главный фильтр «по ТБ» (59 значений) | ТБ, территориальный банк, блок, ПЦП |
| `org_struct_lvl_4_name` | `…d6_base_of_knowledge_ior` | string | Дивизион / Департамент / Центр / Категория терр. структуры | дивизион, департамент, центр |
| `org_struct_lvl_5_name` | `…d6_base_of_knowledge_ior` | string | Дивизион / Управление / Отдел / ГОСБ / ВСП | управление |
| `org_struct_lvl_6_name` | `…d6_base_of_knowledge_ior` | string | Управление / Отдел / Группа | отдел, группа |
| `org_struct_lvl_7_name` | `…d6_base_of_knowledge_ior` | string | УРМ / Группа / Управление ГОСБ / ВСП | УРМ |
| `org_struct_lvl_8_name` | `…d6_base_of_knowledge_ior` | string | Отдел ГОСБ / Сектор ГОСБ / Центр ГОСБ / ВСП | сектор ГОСБ, центр ГОСБ |
| `org_struct_lvl_9_name` | `…d6_base_of_knowledge_ior` | string | Отдел ГОСБ / ВСП – главный фильтр «по ВСП» | ВСП, отдел ГОСБ |
| `org_struct_lvl_10_name` | `…d6_base_of_knowledge_ior` | string | Группа ГОСБ и прочие подструктуры | группа ГОСБ |

### 2.8. Функциональный блок

Перечень полей БД, используемых для запросов вида: «ИОР по блоку Розничный бизнес», «ИОР по дивизиону…», «ИОР по трайбу…».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `funct_block_id` | `…d6_base_of_knowledge_ior` | string | Идентификатор функционального блока | функ. блок id |
| `funct_block_lvl_2_name` | `…d6_base_of_knowledge_ior` | string | Дивизион или трайб (6 значений: `Блок "Розничный бизнес"`, `Направление по сопровождению клиентов`, `ПАО Сбербанк (ЦА)`, `Подразделения центрального подчинения`, `120- Корпоративный лизинг`) | блок, дивизион, трайб |
| `funct_block_lvl_3_name` | `…d6_base_of_knowledge_ior` | string | Дивизион / Департамент / Центр (24 значения) | департамент, центр |
| `funct_block_lvl_4_name` | `…d6_base_of_knowledge_ior` | string | Департамент / Управление / Отдел (126 значений) | отдел, управление |

### 2.9. Процесс

Перечень полей БД, используемых для запросов вида: «ИОР по продукту Ипотека», «ИОР по процессу выдачи кредитов ФЛ».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `process_lvl_1_name` | `…d6_base_of_knowledge_ior` | string | Банк или ДЗО процесса (5 значений: `ПАО Сбербанк`, `АО Сбербанк КИБ`, `АО «Сбербанк Лизинг»`, `АО "Центр программ лояльности"`) | ДЗО |
| `process_lvl_2_name` | `…d6_base_of_knowledge_ior` | string | Функциональный блок процесса (23 значения) | блок процесса |
| `process_lvl_3_name` | `…d6_base_of_knowledge_ior` | string | Дивизион / трайб, ответственный за процесс (119 значений) | владелец процесса |
| `process_lvl_4_name` | `…d6_base_of_knowledge_ior` | string | Наименование процесса (934 значения) | процесс, бизнес-процесс, продукт |

### 2.10. Клиентский путь и направление деятельности

Перечень полей БД, используемых для запросов вида: «ИОР по клиентскому пути…», «ИОР по направлению деятельности Депозиты».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `clntpth_lvl_4_name` | `…d6_base_of_knowledge_ior` | string | Клиентский путь (79 значений). **Заполненность ~1%** – данные ограничены | клиентский путь, КП, customer journey |

### 2.11. Связи с договорами и клиентом

Перечень полей БД, используемых для запросов вида: «Найди ИОР по договору №…», «ИОР по заявке ETSM-…», «ИОР по клиентам-ФЛ».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_appl_num` | `…d6_base_of_knowledge_ior` | string | Номер заявки (сделки) по инциденту. Заполненность ~66%. Связь с `b2c_credit_fl.etsm_request_id` | номер заявки, application, ETSM |
| `incdnt_agr_num` | `…d6_base_of_knowledge_ior` | string | Номер кредитного договора. Заполненность ~15%. Связь с `cards.contract_number` | номер договора, кредитный договор |
| `incdnt_agr_sid` | `…d6_base_of_knowledge_ior` | string | Идентификатор кредитного договора. Заполненность ~14%. Связь с `b2c_credit_fl.loan_agrmnt_id` | id договора, agr sid |
| `incdnt_client_type_name` | `…d6_base_of_knowledge_ior` | string | Наименование типа клиента. Значения: `ФЛ`, `ЮЛ`, `Без клиента` | тип клиента, ФЛ, ЮЛ, физлицо, юрлицо |

### 2.12. Финансовые итоги (агрегаты)

Перечень полей БД, используемых для запросов вида: «Сколько ИОР с финансовыми потерями?», «Прямые потери за период», «ИОР с потерями > 1 млн ₽».

> ⚠️ **Заполненность ~2.26%** – агрегаты считаются только для инцидентов с утверждёнными финансовыми последствиями. Для остальных инцидентов суммы – в `incident_fin_impact` детально (см. §3.3).

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_sum` | `…d6_base_of_knowledge_ior` | decimal(28,4) | **Общая сумма всех последствий** (руб.) | сумма потерь, ущерб, total loss |
| `incdnt_drct_dmg_sum` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Прямая потеря – итого (руб.) | прямой ущерб, direct |
| `incdnt_drct_dmg_cred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Прямая потеря – с кредитным риском (руб.) | прямые кредитные потери |
| `incdnt_drct_dmg_noncred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Прямая потеря – без кредитного риска (руб.) | прямые некредитные потери |
| `incdnt_indrct_dmg_sum` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Косвенная потеря – итого (руб.) | косвенный ущерб, indirect |
| `incdnt_indrct_dmg_cred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Косвенная потеря – с кредитным риском | |
| `incdnt_indrct_dmg_noncred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Косвенная потеря – без кредитного риска | |
| `incdnt_unrlzd_dmg_sum` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Нереализовавшаяся потеря – итого (руб.) | потенциальная потеря, unrealized |
| `incdnt_unrlzd_dmg_cred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Нереализовавшаяся – с кредитным риском | |
| `incdnt_unrlzd_dmg_noncred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Нереализовавшаяся – без кредитного риска | |
| `incdnt_thrd_prt_sum` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Потеря третьих лиц – итого (руб.) | потери клиентов, third party |
| `incdnt_thrd_prt_cred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Потеря третьих лиц – с кред. риском | |
| `incdnt_thrd_prt_noncred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Потеря третьих лиц – без кред. риска | |
| `incdnt_gain_sum` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Прибыль – итого (руб.) | прибыль, gain, восстановление |
| `incdnt_gain_cred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Прибыль – с кредитным риском | |
| `incdnt_gain_noncred_rub_amt` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Прибыль – без кредитного риска | |

### 2.13. Возмещение (агрегат)

Перечень полей БД, используемых для запросов вида: «Суммарное возмещение по ИОР», «ИОР с возмещением».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `recovery_rub_amt_aggr` | `…d6_base_of_knowledge_ior` | decimal(28,4) | Возмещение – итого по инциденту (руб.). Заполненность ~39% | возмещение, возврат, компенсация |

> Для детализации по операциям возмещения – см. §3.2 (`incident_recovery`).

### 2.14. Связь с другими видами риска (флаги)

Перечень полей БД, используемых для запросов вида: «ИОР, связанные с риском поведения», «ИОР с ИБ-риском», «ИОР по модельному риску».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_security_risk_flag` | `…d6_base_of_knowledge_ior` | string (Y/N) | Связь с ИБ-риском | ИБ, информационная безопасность, security |
| `incdnt_infrmtn_sys_risk_flag` | `…d6_base_of_knowledge_ior` | string (Y/N) | Связь с риском ИС | ИС, риск ИС, информационные системы, сбой системы |
| `incdnt_behavior_risk_flag` | `…d6_base_of_knowledge_ior` | string (Y/N) | Связь с риском поведения | поведенческий риск, conduct |
| `incdnt_model_risk_flag` | `…d6_base_of_knowledge_ior` | string (Y/N) | Связь с модельным риском | модельный риск, model risk |

---

## 3. Mapping по связанным таблицам блока ИОР

### 3.1. История изменения статусов

Полное имя: `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng` (9 колонок).

Перечень полей БД, используемых для запросов вида: «ИОР, удалённые в 2025», «Кто удалил инцидент EVE-…», «История изменения статусов ИОР».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_id` | `…d6_base_of_knowledge_incident_stts_chng` | bigint | Идентификационный ключ инцидента (для джойна с main) | id инцидента |
| `incdnt_status_name` | `…d6_base_of_knowledge_incident_stts_chng` | string | Статус на момент действия (14 значений: `Анализ`, `Арбитраж изменений`, `Арбитраж удаления`, `Валидация`, `Доработка`, `Исследование`, `Исследование РМ`, `Исследование РМ ЦА`, `Подтверждение причин`, `Подтверждение удаления`, `Профильная экспертиза`, `Утвержден`, `Утверждение РМ`, `Черновик`) | статус на момент |
| `incdnt_status_code` | `…d6_base_of_knowledge_incident_stts_chng` | string | Код статуса на момент действия (17 значений) | код статуса |
| `stts_chng_action_code` | `…d6_base_of_knowledge_incident_stts_chng` | string | Код действия (25 значений: `accept`, `approve`, `delete`, `reject`, `sendForApproval` и т.д.) | код действия |
| `stts_chng_action_name` | `…d6_base_of_knowledge_incident_stts_chng` | string | Наименование действия (19 значений: `Удалить`, `Утвердить`, `На утверждение`, `Отклонить`, `Передать РМ` и т.д.) | действие, изменение |
| `stts_chng_comment_txt` | `…d6_base_of_knowledge_incident_stts_chng` | string | Комментарий / причина действия | комментарий, причина |
| `stts_chng_action_dttm` | `…d6_base_of_knowledge_incident_stts_chng` | timestamp | Дата и время совершения действия | когда, дата действия |
| `stts_chng_user_num` | `…d6_base_of_knowledge_incident_stts_chng` | string | Табельный номер пользователя | кем, табельный |
| `start_dt` | `…d6_base_of_knowledge_incident_stts_chng` | timestamp | Бизнес-дата начала действия записи | техн. поле |

### 3.2. Возмещения детально (1:N)

Полное имя: `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery` (12 колонок).

Перечень полей БД, используемых для запросов вида: «ИОР с возмещениями за 2025», «ИОР с возмещением по решению суда», «возмещения от третьих лиц».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_id` | `…d6_base_of_knowledge_incident_recovery` | bigint | Идентификационный ключ инцидента (для джойна с main) | id инцидента |
| `recovery_sid` | `…d6_base_of_knowledge_incident_recovery` | string | Идентификатор возмещения | id возмещения |
| `recovery_type_name` | `…d6_base_of_knowledge_incident_recovery` | string | Тип возмещения (14 значений: `Возмещения, полученные в судебном порядке`, `Компенсации от клиента`, `Компенсации от сотрудника`, `получение страховой выплаты`, и др.) | тип возмещения, по решению суда, страховка |
| `recovery_rub_amt` | `…d6_base_of_knowledge_incident_recovery` | decimal(18,4) | Сумма возмещения в рублях (по конкретной операции) | сумма возмещения в руб. |
| `recovery_ccy_amt` | `…d6_base_of_knowledge_incident_recovery` | decimal(18,4) | Сумма в валюте возмещения | сумма в валюте |
| `recovery_local_ccy_amt` | `…d6_base_of_knowledge_incident_recovery` | decimal(18,4) | Сумма в локальной валюте | сумма в локальной |
| `recovery_crncy_code` | `…d6_base_of_knowledge_incident_recovery` | string | Код валюты возмещения (23 значения: `RUB`, `USD`, `EUR`, `BYN`, `KZT`, …) | валюта |
| `recovery_local_crncy_code` | `…d6_base_of_knowledge_incident_recovery` | string | Код локальной валюты (`BYN`, `RUB`) | локальная валюта |
| `recovery_src_account_num` | `…d6_base_of_knowledge_incident_recovery` | string | Номер счёта – источник перевода | счёт-источник |
| `recovery_doc_num` | `…d6_base_of_knowledge_incident_recovery` | string | Номер бухгалтерского документа | номер документа |
| `recovery_creation_dttm` | `…d6_base_of_knowledge_incident_recovery` | timestamp | Дата создания возмещения | дата создания |
| `recovery_reg_dt` | `…d6_base_of_knowledge_incident_recovery` | timestamp | Дата регистрации в учёте | дата регистрации |

### 3.3. Финансовые последствия детально (1:N)

Полное имя: `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact` (17 колонок).

Перечень полей БД, используемых для запросов вида: «Детализация фин. последствий по ИОР», «Прямые потери по конкретному инциденту», «ИОР с потерями в EUR».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_id` | `…d6_base_of_knowledge_incident_fin_impact` | bigint | Идентификационный ключ инцидента (для джойна с main) | id инцидента |
| `fin_impact_id` | `…d6_base_of_knowledge_incident_fin_impact` | bigint | Идентификационный ключ фин. последствия | id последствия |
| `fin_impact_sid` | `…d6_base_of_knowledge_incident_fin_impact` | string | Идентификатор фин. последствия (бизнес) | sid последствия |
| `fin_impact_type_name` | `…d6_base_of_knowledge_incident_fin_impact` | string | Тип фин. последствия (5 значений: `Прямая потеря`, `Косвенная потеря`, `Нереализовавшаяся потеря`, `Потеря третьих лиц`, `Прибыль`) | тип последствия |
| `fin_impact_kind_name` | `…d6_base_of_knowledge_incident_fin_impact` | string | Вид фин. последствия (33 значения: `Денежные выплаты клиентам`, `Потеря наличных денежных средств в результате хищения` и др.) | вид последствия |
| `fin_impact_rub_amt` | `…d6_base_of_knowledge_incident_fin_impact` | decimal(18,4) | Сумма в рублях по конкретному последствию | сумма в руб. |
| `fin_impact_ccy_amt` | `…d6_base_of_knowledge_incident_fin_impact` | decimal(18,4) | Сумма в валюте последствия | сумма в валюте |
| `fin_impact_local_ccy_amt` | `…d6_base_of_knowledge_incident_fin_impact` | decimal(18,4) | Сумма в локальной валюте | сумма в локальной |
| `fin_impact_crncy_code` | `…d6_base_of_knowledge_incident_fin_impact` | string | Код валюты последствия (26 значений) | валюта |
| `fin_impact_local_crncy_code` | `…d6_base_of_knowledge_incident_fin_impact` | string | Код локальной валюты | локальная валюта |
| `fin_impact_monitoring_flag` | `…d6_base_of_knowledge_incident_fin_impact` | string (Y/N) | Признак мониторинга | мониторинг |
| `fin_impact_detection_dt` | `…d6_base_of_knowledge_incident_fin_impact` | timestamp | Дата обнаружения последствия | дата обнаружения |
| `fin_impact_creation_dttm` | `…d6_base_of_knowledge_incident_fin_impact` | timestamp | Дата создания записи | дата создания |
| `fin_impact_reg_dt` | `…d6_base_of_knowledge_incident_fin_impact` | timestamp | Дата отражения в учёте | дата регистрации |
| `fin_impact_account_num` | `…d6_base_of_knowledge_incident_fin_impact` | string | Номер счёта отражения в учёте | счёт |
| `fin_impact_docum_num` | `…d6_base_of_knowledge_incident_fin_impact` | string | Номер бухгалтерского документа | документ |
| `fi_busn_area_id` | `…d6_base_of_knowledge_incident_fin_impact` | string | Идентификатор направления деятельности по последствию (по Базель-II) | направление деятельности |
| `fi_org_struct_id` | `…d6_base_of_knowledge_incident_fin_impact` | string | Идентификатор оргструктуры по последствию | оргструктура последствия |

### 3.4. Нефинансовые последствия (1:N)

Полное имя: `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact` (4 колонки).

Перечень полей БД, используемых для запросов вида: «ИОР с нефинансовыми последствиями», «ИОР с репутационным влиянием», «ИОР с угрозой непрерывности».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `incdnt_id` | `…d6_base_of_knowledge_incident_nonfin_impact` | bigint | Идентификационный ключ инцидента (для джойна с main) | id инцидента |
| `nonfin_impact_sid` | `…d6_base_of_knowledge_incident_nonfin_impact` | string | Идентификатор нефинансового последствия | id последствия |
| `nonfin_impact_kind_name` | `…d6_base_of_knowledge_incident_nonfin_impact` | string | Вид качественной потери (19 значений: `Жалобы и обращения клиентов`, `Освещение в СМИ`, `Воздействие со стороны регулятора`, `Утечка, потеря или искажение защищаемой информации`, `Ущерб репутации`, `Угроза жизни и здоровью сотрудников`, `Угроза непрерывности деятельности` и др.) | качественная потеря, репутация, регулятор |
| `nonfin_impact_influence_class_name` | `…d6_base_of_knowledge_incident_nonfin_impact` | string | Классификация влияния (5 значений: `Очень высокий`, `Высокий`, `Средний`, `Низкий`, `Нет`) | класс влияния, уровень |

---

## 4. Mapping по внешним блокам (для джойнов)

### 4.1. Виновные сотрудники

Полное имя: `arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_employee` (2 колонки, 23 523 строк).

Перечень полей БД, используемых для запросов вида: «ИОР, где сотрудники признаны виновными», «Кто виноват в ИОР EVE-…».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `aggregateroot_id` | `…d6_base_of_knowledge_employee` | string | Идентификатор инцидента (= `ior.incdnt_sid`) | sid инцидента |
| `value_` | `…d6_base_of_knowledge_employee` | string | Табельный номер виновного сотрудника | табельный номер |

### 4.2. Справочник сотрудников Сбера (для ФИО)

Полное имя: `arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_empl` (5 колонок, 43 495 строк).

Перечень полей БД, используемых для запросов вида: «ФИО виновного сотрудника», «ИОР по сотруднику <ФИО>».

| Поле БД | Источник | Тип | Описание | Синоним |
|---------|----------|-----|----------|---------|
| `aggregateroot_id` | `…d6_base_of_knowledge_empl` | string | Идентификатор записи | id |
| `value_` | `…d6_base_of_knowledge_empl` | string | Табельный номер сотрудника | табельный |
| `modifier_saphr_id` | `…d6_base_of_knowledge_empl` | string | SAP HR ID | sap id |
| `fio` | `…d6_base_of_knowledge_empl` | string | ФИО сотрудника | ФИО, полное имя |
| `birth_dt` | `…d6_base_of_knowledge_empl` | date | Дата рождения | дата рождения |

### 4.3. Кредиты ФЛ (заглушка – для будущих этапов)

Полное имя: `arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_b2c_credit_fl` (13 колонок, 277 928 992 строк).

Используется для запросов «ИОР по кредитному договору №…» (через `loan_agrmnt_id`) и «по заявке ETSM…» (через `etsm_request_id`). Полная структура полей – отдельный документ при подключении блока к LLM.

### 4.4. Карты (заглушка)

Полное имя: `arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_cards` (11 колонок, 45 377 616 строк).

Связь: `cards.contract_number = ior.incdnt_agr_num`. Полная структура полей – отдельный документ.

---

## 5. Правила интерпретации запросов LLM (cheatsheet)

Эти правила выгружаются в системный промпт LLM как сжатая инструкция.

1. **Период по умолчанию** – `incdnt_entry_dt` (а не `_detection_dt` и не `_start_dt`).
2. **«ИОР с финансовыми потерями»** – JOIN с `incident_fin_impact` И фильтр `fin_impact_type_name IN ('Прямая потеря', 'Косвенная потеря', 'Потеря третьих лиц')`. **НЕ использовать `incdnt_sum > 0`** в main – заполнено только в 2.26%.
3. **«ИОР с возмещением»** – JOIN с `incident_recovery` (отбор инцидентов с возмещениями). Для агрегата по ИОР – `recovery_rub_amt_aggr` из main (заполнено в 39%).
4. **«Только ПАО Сбербанк»** – `SUBSTR(UPPER(org_struct_id), 1, 4) IN ('SBR_', 'EXT_', 'GRC_', 'MON_', 'BPS_')`.
5. **«По ТБ X»** – `org_struct_lvl_3_name LIKE '%X%'`.
6. **«По блоку X»** – `funct_block_lvl_2_name LIKE '%X%'`.
7. **«По продукту X»** – `process_lvl_4_name LIKE '%X%'`.
8. **«Удалённые ИОР»** – `incdnt_status_name = 'Удалён'` ИЛИ JOIN с `incident_stts_chng` по `stts_chng_action_name = 'Удалить'`.
9. **«С кредитным риском»** – `incdnt_drct_dmg_cred_rub_amt > 0` (или другой `*_cred_rub_amt`) из main.
10. **«ИОР сотрудника <ФИО>»** – JOIN с `employee` по `incdnt_sid = aggregateroot_id`, затем JOIN с `empl` по `value_` для получения ФИО.
11. **«ИОР по кредиту X»** – JOIN с `b2c_credit_fl` через `incdnt_agr_sid = loan_agrmnt_id` OR `incdnt_appl_num = etsm_request_id` (UNION).
12. **Период: формат границ** – `entry_dt >= dt_begin AND entry_dt < date_add(dt_end, 1)`.
13. **Дедупликация:** для базы инцидентов – один ряд на `incdnt_id`; для фин. последствий – на `(incdnt_id, fin_impact_sid)`; для возмещений – на `(incdnt_id, recovery_sid)`.
14. **Очистка описаний** от юникод-мусора: `regexp_replace(<col>, '[\u200b-\xa0\x02\x08\x0b]', '')` (применять перед экспортом).
15. **Низкая заполненность** – предупреждать пользователя при фильтрации по `clntpth_lvl_4_name` (1%), `busn_area_*` (1-2%), `org_struct_lvl_8/9/10_name` (<5%), `incdnt_agr_*` (14-15%).
16. **Префиксы оргструктуры:** `SBR_` = ПАО Сбербанк, `EXT_` = внешние, `GRC_` = ГК, `MON_` = МОН, `BPS_` = BPS.

---

## 6. Покрытие топ-запросов (из «Топ запросы_Дизрапт_СЗБ»)

| # | Запрос | Используемые таблицы / поля |
|:-:|--------|------------------------------|
| 1 | Выгрузи ИОР за 2025 год по продукту | `ior.incdnt_entry_dt` + `ior.process_lvl_4_name` |
| 2 | ИОР по проверкам УВА за 2025 с указанием видов потерь | `ior.incdnt_source_name='Результат анализа акта УВА'` + JOIN `incident_fin_impact` |
| 3 | ИОР за 2025 по подразделению | `ior.org_struct_lvl_3_name` (ТБ) или `lvl_8/9_name` (ВСП) |
| 4 | ИОР по результатам служебных расследований | `ior.incdnt_source_name` или `src_type_lvl_2_name` |
| 5 | ИОР по обращениям клиентов с ID обращения | ⚠️ Связь с B2C-обращениями – требует отдельной витрины |
| 6 | ИОР, связанные с риском поведения | `ior.incdnt_behavior_risk_flag = 'Y'` |
| 7 | Сколько ИОР с фин. потерями в Q4 2025 | `ior.incdnt_entry_dt` + JOIN `incident_fin_impact` (`fin_impact_type_name` IN потери) |
| 8 | Статистика ИОР, где сотрудники признаны виновными в потерях клиентов | JOIN `employee` + JOIN `empl` + JOIN `incident_fin_impact` (`type_name='Потеря третьих лиц'`) |
| 9 | Статистика ИОР по потерям клиентов, выявленным УВА | `ior.incdnt_source_name='Результат анализа акта УВА'` + `incident_fin_impact.fin_impact_type_name='Потеря третьих лиц'` |
| 10 | В каком ВНД описан порядок регистрации ИОР? | ❌ Вне БЗ – отдельная подсистема (SberDocs/Консультант) |

---

## 7. Скрипты выгрузки

Готовые скрипты на новой схеме БЗ – в папке `новая_схема_БЗ_v2/`. По каждому есть подробное описание в [`scripts/`](scripts/).

| Скрипт | Назначение | Markdown-описание |
|--------|-----------|---------------------|
| `deleted_ior_v2.ipynb` | Удалённые ИОР за период | [`scripts/deleted_ior.md`](scripts/deleted_ior.md) |
| `financial_consequences_ior_v2.ipynb` | Финансовые последствия ИОР за период | [`scripts/financial_consequences_ior.md`](scripts/financial_consequences_ior.md) |
| `ior_nonfinancial_consequences_v2.ipynb` | ИОР с нефинансовыми последствиями | [`scripts/ior_nonfinancial_consequences.md`](scripts/ior_nonfinancial_consequences.md) |
| `ior_period_pao_sberbank_v2.ipynb` | Полный отчёт ИОР за период по ПАО Сбербанк | [`scripts/ior_period_pao_sberbank.md`](scripts/ior_period_pao_sberbank.md) |
| `report_period_specific_ior_v2.ipynb` | Отчёт по одному `incdnt_sid` | [`scripts/report_period_specific_ior.md`](scripts/report_period_specific_ior.md) |
| `vozmeshenie_ior_v2.ipynb` | Возмещения по ИОР | [`scripts/vozmeshenie_ior.md`](scripts/vozmeshenie_ior.md) |

---

## 8. Decision Tree – карта выбора скрипта

Для LLM-маршрутизатора. Каждый запрос пользователя проходит через дерево решений:

```
[ВХОД]

  ├─ В запросе есть конкретный SID (EVE-XXXXXXX)?
  │    └─ ДА → report_period_specific_ior    (досье инцидента)
  │
  ├─ Про удалённые ИОР / журнал удалений / причины удалений?
  │    └─ ДА → deleted_ior                    (контроль удалений УВА)
  │
  ├─ Нужна детализация 1:N (каждое последствие/возмещение отдельной строкой)?
  │    ├─ По возмещениям/возвратам/компенсациям?        → vozmeshenie_ior
  │    ├─ По фин. последствиям (типы/виды потерь)?      → financial_consequences_ior
  │    └─ По нефин. последствиям (репутация/регулятор)? → ior_nonfinancial_consequences
  │
  └─ Иначе (общий запрос / агрегаты / любые фильтры по ИОР)
       → ior_period_pao_sberbank                (главный сводный отчёт, дефолт)
```

### 8.1. Расширенные правила маршрутизации (для разрешения неоднозначностей)

| Если запрос содержит | Маршрут |
|------------------------|----------|
| `EVE-\d+` (паттерн SID) | `report_period_specific_ior` |
| «удалённ*», «снесли», «удаление» | `deleted_ior` |
| «возмещени*», «возврат*», «компенсаци*», «страхов*», «восстановление резерва» | `vozmeshenie_ior` |
| «прямы* потерь*», «косвенн* потерь*», «нереализовавш*», «потери третьих лиц», «прибыль по ИОР», «структура потерь», «виды потерь», «потери в EUR/USD/RUB» | `financial_consequences_ior` |
| «репутаци*», «жалоб*», «обращени* клиент*», «регулятор*», «СМИ», «утечк*», «непрерывности», «качественные потер*», «нефинанс*» | `ior_nonfinancial_consequences` |
| «список ИОР», «выгрузи ИОР», «отчёт по ИОР», «статистика ИОР», «ИОР за период», «ИОР по ТБ/блоку/процессу/продукту/типу/источнику/ЦПР» | `ior_period_pao_sberbank` (дефолт) |
| «количество ИОР», «сколько инцидентов» | `ior_period_pao_sberbank` + агрегация `COUNT(*)` в шаблоне ответа |
| «ИОР с фин. потерями» (общая цифра) | `financial_consequences_ior` + `COUNT(DISTINCT incdnt_id)` |
| «топ ТБ/блоков/процессов» | `ior_period_pao_sberbank` + `GROUP BY ... ORDER BY COUNT(*) DESC LIMIT 10` |

### 8.2. Если запрос подходит под несколько скриптов

Приоритет (по убыванию специфичности):
1. `report_period_specific_ior` (есть SID)
2. `deleted_ior` (есть слово «удал*»)
3. `vozmeshenie_ior` / `financial_consequences_ior` / `ior_nonfinancial_consequences` (детализация 1:N)
4. `ior_period_pao_sberbank` (общий, дефолт)

---

## 9. Полные enum-значения (справочники для LLM)

### 9.1. Статусы инцидентов

#### Текущий статус (`d6_base_of_knowledge_ior.incdnt_status_name`)

`Исследование` | `Утверждение` | `Утверждён` | `Удалён` | `Черновик`

#### Статус на момент действия (`d6_base_of_knowledge_incident_stts_chng.incdnt_status_name`)

Расширенный список из 14 значений:

`Анализ` | `Арбитраж изменений` | `Арбитраж удаления` | `Валидация` | `Доработка` | `Исследование` | `Исследование РМ` | `Исследование РМ ЦА` | `Подтверждение причин` | `Подтверждение удаления` | `Профильная экспертиза` | `Утвержден` | `Утверждение РМ` | `Черновик`

#### Коды действий (`stts_chng_action_code`, 25 значений)

| Код | Действие (`stts_chng_action_name`) |
|-----|--------------------------------------|
| `accept` | В работу |
| `approve` | Утвердить |
| `approveChange` | Согласовать изменения |
| `approveDelete` | Подтвердить удаление |
| `approveDeletion` | Согласовать удаление |
| `declineRM` | Отклонить РМ |
| `delete` | Удалить |
| `inform` | Уведомить |
| `reassign` | Переназначить |
| `reassignDistribution` | Переназначить распределение |
| `reassignDistributionRM` | Переназначить распределение на РМ |
| `reassignToRM` | Передать РМ |
| `reassignToRMCA` | Переназначить на РМ ЦА |
| `reject` | Отклонить |
| `rejectChange` | Отклонить изменения |
| `rejectDeletion` | Отклонить удаление |
| `rejectDistribution` | Отклонить распределение |
| `sendForApproval` | На утверждение |
| `sendForDeletion` | Отправить на удаление |
| `sendForInvestigation` | На расследование |
| `sendToRM` | На исследование РМ |
| `toChange` | Предложить изменения |
| `toDelete` | Предложить удаление |
| `transfer` | Передать |
| `validate` | Утвердить (валидация) |

### 9.2. Тип события – `incdnt_type_lvl_1_name` (7 категорий)

1. **Ошибки персонала и недостатки процессов**
2. **Нарушение и сбои систем и оборудования**
3. **Нарушение прав клиентов и контрагентов**
4. **Нарушение кадровой политики и безопасности труда**
5. **Преднамеренные действия персонала**
6. **Ущерб материальным активам**
7. **Преднамеренные действия третьих лиц**

### 9.3. Тип события – `incdnt_type_lvl_2_name` (23 подкатегории)

| Код | Подкатегория |
|-----|--------------|
| 1.1 | Ошибки из-за нарушения внутренних процессов |
| 1.2 | Ошибки в отчётности Банка |
| 1.3 | Ошибки в договорах и информационном обмене с клиентами |
| 1.4 | Ошибки РКО и по счетам клиентов |
| 1.5 | Недостатки в выборе и работе с поставщиком |
| 1.6 | Ошибки в ВНД, связанные с несоответствием законодательству |
| 2.1 | Сбои в работе информационных систем и программ |
| 2.2 | Сбои обеспечивающей инфраструктуры (кроме информационной) |
| 3.1 | Раскрытие, утечка конфиденциальной информации |
| 3.2 | Нарушение деловых практик, законов и ВНД |
| 3.3 | Риск поведения (навязывание, неинформирование) |
| 3.5 | Недостатки в работе с контрагентами |
| 4.1 | Санкции за нарушение трудового законодательства |
| 4.2 | Выплаты за нарушение норм безопасности |
| 4.5 | Неверные расчёты Банка с сотрудниками |
| 5.1 | Преднамеренные действия персонала для выгоды банка |
| 5.2 | Преднамеренные действия персонала для выгоды посторонних |
| 6.1 | Природные факторы, включая стихийные бедствия |
| 6.2 | Техногенные факторы |
| 6.5 | Вандализм |
| (и ещё 3) | ... |

### 9.4. Источник инцидента

#### `src_type_lvl_1_name` (5 типов)

`Внешние причины` | `Действия персонала` | `Недостатки процессов` | `Сбои систем и оборудования` | (`Типы источников` – мусорное значение «корня»)

#### `src_type_lvl_2_name` (17 подтипов)

`Бездействие сотрудников` | `Внешнее мошенничество` | `Действия третьих лиц` | `ИТ сбой на стороне внешнего контрагента` | `Не полные, не точные, не актуальные данные из внешних источников` | `Нарушения функционирования оборудования` | `Не полные, не точные, не актуальные данные из внутренних источников` | `Некорректная работа систем` | `Некорректная работа систем искусственного интеллекта` | `Недостатки в требованиях к данным` | `Недоступность систем` | `Ненадежная/неэффективная организация внутренних процессов` | `Непреднамеренные ошибки сотрудников` | `Пандемии, стихийные бедствия, техногенные происшествия` | `Сбои в работе обеспечивающей инфраструктуры зданий` | `Умышленные действия сотрудников`

#### `incdnt_source_name` (4 источника)

`Мониторинг АС` | `Реестровое уведомление` | `Результат анализа акта УВА` | `Уведомление РК`

#### `incdnt_detection_person_name` (6 категорий)

`Внешние контролирующие органы` | `Вторая линия` | `Клиент` | `Первая линия` | `Сотрудник СВА` | `Сотрудник СВА в рамках оценки эффективности`

### 9.5. Клиент

#### `incdnt_client_type_name`

`ФЛ` | `ЮЛ` | `Без клиента`

### 9.6. Финансовые последствия

#### `fin_impact_type_name` (5 типов)

`Прямая потеря` | `Косвенная потеря` | `Нереализовавшаяся потеря` | `Потеря третьих лиц` | `Прибыль`

#### `fin_impact_kind_name` (33 вида, основные):

`Расходы, связанные с возвратом кредитных средств и обеспечения` |
`Выплаты и компенсации по решению суда` |
`Денежные выплаты клиентам и контрагентам в целях компенсации` |
`Денежные выплаты сотрудникам в целях компенсации убытков` |
`Досрочное списание активов (выбытие, потеря, уничтожение)` |
`Начисление амортизационных расходов по предъявленным требованиям` |
`Начисление резервов некредитного характера` |
`Недополученные запланированные доходы` |
`Недополученный доход от запланированной сделки` |
`Обесценение стоимости кредита в результате начисления резерва` |
`Отрицательная переоценка стоимости торгового портфеля` |
`Повышение стоимости заимствования` |
`Потери в виде уплаченных комиссий по проведению ошибочных операций` |
`Потери в размере ошибочного платежа` |
`Потери от ошибочных платежей` |
`Потери, связанные с поиском возможности возврата ошибочного платежа` |
`Потеря активов в результате хищения` |
`Потеря наличных денежных средств в результате хищения` |
`Прочие потери, не отраженные на счетах расходов` |
`Прочие потери, отраженные на счетах расходов` |
(и ещё 13)

#### Валюты (`fin_impact_crncy_code`, `recovery_crncy_code`)

26 значений: `RUB`, `USD`, `EUR`, `BYN`, `BYR`, `KZT`, `UAH`, `CHF`, `GBP`, `CNY`, `HKD`, `HUF`, `INR`, `JPY`, `AED`, `AUD`, `CAD`, `DKK`, `NOK`, `PLN`, `SEK`, `SGD`, `TRY`, `ZAR`, `RSD` …

#### Локальные валюты (`*_local_crncy_code`)

`RUB` | `BYN`

### 9.7. Возмещения

#### `recovery_type_name` (14 типов)

`Возмещения от участников Группы, связанных с Банком лиц, акционеров, бенефициаров` |
`Возмещения, полученные в судебном порядке` |
`Возмещения, полученные во внесудебном порядке по соглашению сторон` |
`Восстановление резерва на возможные потери по ссудам` |
`Восстановление резерва некредитного характера` |
`Компенсации от других источников` |
`Компенсации от клиента` |
`Компенсации от организации / банка-корреспондента` |
`Компенсации от сотрудника, допустившего рисковое событие` |
`возмещение от работников Банка` *(в нижнем регистре – старый ввод)* |
`возмещения, полученные от третьих лиц` |
`получение страховой выплаты от одной или нескольких внешних страховых компаний` |
`получение страховой выплаты от одной или нескольких страховых компании Группы` |
`*NULL*`

### 9.8. Нефинансовые последствия

#### `nonfin_impact_kind_name` (19 видов)

`Активное обсуждение в блогах и социальных сетях` |
`Воздействие со стороны регулятора` |
`Возникновение источников других типов риска` |
`Другие качественные потери` |
`Жалобы и обращения клиентов` |
`Использование дополнительного времени персонала` |
`Ограничения со стороны судебных и (или) административных` |
`Освещение в СМИ` |
`Отток клиентов` |
`Предписания надзорных и правоохранительных органов` |
`Приостановка деятельности в результате неблагоприятного события` |
`Снижение качества предоставления услуг / выполнения` |
`Срыв сделки и (или) неоказание банковской услуги` |
`Угроза жизни и здоровью сотрудников` |
`Угроза непрерывности деятельности` |
`Упоминание в негативном свете со стороны представителей` |
`Утечка, потеря или искажение защищаемой информации` |
`Ущерб репутации`

#### `nonfin_impact_influence_class_name` (5 уровней)

`Очень высокий` | `Высокий` | `Средний` | `Низкий` | `Нет`

### 9.9. Оргструктура – префиксы `org_struct_id`

| Префикс | Смысл |
|---------|-------|
| `SBR_` | ПАО Сбербанк (основная масса) |
| `EXT_` | Внешние / расширенные |
| `GRC_` | ГК (групповая) |
| `MON_` | МОН |
| `BPS_` | BPS |

Остальные префиксы – ДЗО (`АО Сбербанк Лизинг`, `АО Сбербанк КИБ`, `АО "Центр программ лояльности"`).

### 9.10. Функциональный блок `funct_block_lvl_2_name` (6 значений)

`Блок "Розничный бизнес"` | `Направление по сопровождению клиентов` | `ПАО Сбербанк (ЦА)` | `Подразделения центрального подчинения` | `120- Корпоративный лизинг` | *NULL*

### 9.11. Юр.лица (`process_lvl_1_name`)

`ПАО Сбербанк` | `АО Сбербанк КИБ` | `АО «Сбербанк Лизинг»` | `АО "Центр программ лояльности"` | *NULL*

### 9.12. Флаги (Y/N + NULL)

Все 5 флагов в main – `string`, значения:
- `Y` – связь подтверждена
- `N` – связи нет
- `NULL` – не определено (только у риск-флагов: `_security/_infrmtn_sys/_behavior/_model`)

Поля:
- `incdnt_autoreg_flag` (только Y/N, без NULL)
- `incdnt_security_risk_flag` (Y/N/NULL)
- `incdnt_infrmtn_sys_risk_flag` (Y/N/NULL)
- `incdnt_behavior_risk_flag` (Y/N/NULL)
- `incdnt_model_risk_flag` (Y/N/NULL)
- `fin_impact_monitoring_flag` (Y/N/NULL – в `incident_fin_impact`)

---

## 10. Шаблоны промптов LLM для типовых запросов

### 10.1. Общий шаблон взаимодействия

```
1. ПОНЯТЬ запрос
   ├ Определить целевой скрипт (Decision Tree §8)
   └ Извлечь параметры (период, фильтры)

2. УТОЧНИТЬ (если параметры неполные)
   └ AskUser: «За какой период?»  «По какому ТБ?»  «Какой тип потерь?»

3. ВЫПОЛНИТЬ
   ├ Сформировать PySpark/SQL по шаблону скрипта
   └ Запустить → получить Excel + статистику

4. ОТВЕТИТЬ
   ├ Структура: что сделано → краткая статистика → топы → файл → следующие шаги
   ├ ПРЕДУПРЕДИТЬ о низкой заполненности, если фильтр по `clntpth_*`, `busn_area_*`, `lvl_5+`
   └ НЕ ПОКАЗЫВАТЬ в чате: ФИО клиентов, табельные, договоры, счета, документы
```

### 10.2. Промпт для подсчёта количества

```
Запрос: «Сколько ИОР {фильтр} за {период}?»

→ Скрипт: ior_period_pao_sberbank (или специализированный если детализация 1:N)
→ SQL добавить: SELECT COUNT(DISTINCT incdnt_id) AS n
→ Ответ:
  «За период {begin}–{end} зарегистрировано {n} инцидентов{доп.фильтр}.»
  + НЕ показывать Excel, только число
```

### 10.3. Промпт для суммы

```
Запрос: «На какую сумму потерь / возмещений {фильтр} за {период}?»

→ Если потеря:
   • Прямые потери → JOIN incident_fin_impact + WHERE fin_impact_type_name='Прямая потеря' + SUM(fin_impact_rub_amt)
   • Все потери    → SUM по type IN ('Прямая','Косвенная','Третьих лиц')
   • Агрегат по ИОР (быстро) → SUM(incdnt_drct_dmg_sum) из main, но помнить про 2.26%
→ Если возмещений:
   • Итого    → SUM(recovery_rub_amt) из incident_recovery
   • Или      → SUM(recovery_rub_amt_aggr) из main (быстро, но 39%)
```

### 10.4. Промпт для распределения / структуры

```
Запрос: «Распределение ИОР по {типу/блоку/ТБ/виду потерь}»

→ SQL: GROUP BY <field> + COUNT(*) или SUM(amt)
→ Ответ – таблица топ-10 с долями, + Excel
```

### 10.5. Промпт для досье инцидента

```
Запрос: «{SID-pattern} → всё про инцидент EVE-…»

→ Скрипт: report_period_specific_ior
→ Ответ – структурированное досье (см. scripts/report_period_specific_ior.md §11)
→ КРИТИЧНО: не показывать ФИО клиентов из incdnt_full_descr_txt
```

---

## 11. Готовые SQL для топ-10 запросов СЗБ

> Все запросы используют схему `arnsdpsbx_t_team_sva_oarb_4`.
> Параметры в `{…}` – подставляются из запроса пользователя.

### 11.1. «Выгрузи ИОР за {год} год по продукту {X}»

```sql
SELECT *
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior
WHERE incdnt_entry_dt >= '2025-01-01'
  AND incdnt_entry_dt <  '2026-01-01'
  AND process_lvl_4_name LIKE '%{X}%'
  AND SUBSTR(UPPER(org_struct_id), 1, 4) IN ('SBR_', 'EXT_', 'GRC_', 'MON_', 'BPS_');
```

→ Скрипт: `ior_period_pao_sberbank_v2`

### 11.2. «ИОР по проверкам УВА за {год} с указанием видов потерь»

```sql
SELECT
    ior.incdnt_id, ior.incdnt_sid, ior.incdnt_entry_dt,
    ior.org_struct_lvl_3_name,
    fi.fin_impact_type_name, fi.fin_impact_kind_name, fi.fin_impact_rub_amt
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact fi
    ON ior.incdnt_id = fi.incdnt_id
WHERE ior.incdnt_entry_dt >= '2025-01-01'
  AND ior.incdnt_entry_dt <  '2026-01-01'
  AND ior.incdnt_source_name = 'Результат анализа акта УВА'
ORDER BY ior.incdnt_entry_dt, ior.incdnt_sid, fi.fin_impact_sid;
```

→ Скрипт: `financial_consequences_ior_v2` с фильтром по `incdnt_source_name`

### 11.3. «ИОР за {год} по подразделению {X}»

```sql
SELECT *
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior
WHERE incdnt_entry_dt >= '2025-01-01'
  AND incdnt_entry_dt <  '2026-01-01'
  AND (org_struct_lvl_3_name LIKE '%{X}%'   -- если ТБ/Блок
       OR org_struct_lvl_4_name LIKE '%{X}%'  -- если департамент/дивизион
       OR org_struct_lvl_8_name LIKE '%{X}%'  -- если ВСП
       OR org_struct_lvl_9_name LIKE '%{X}%')
  AND SUBSTR(UPPER(org_struct_id), 1, 4) IN ('SBR_', 'EXT_', 'GRC_', 'MON_', 'BPS_');
```

→ Скрипт: `ior_period_pao_sberbank_v2`

### 11.4. «ИОР по результатам служебных расследований за {период}»

```sql
SELECT *
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior
WHERE incdnt_entry_dt BETWEEN '{begin}' AND '{end}'
  AND (incdnt_source_name LIKE '%служебн%'   -- если есть такой источник
       OR src_type_lvl_2_name LIKE '%служебн%'
       OR incdnt_type_lvl_2_name LIKE '%служебн%');
```

→ Скрипт: `ior_period_pao_sberbank_v2`

### 11.5. «ИОР по обращениям клиентов с ID обращения»

⚠️ **Прямого поля «ID обращения B2C» нет.** Используется косвенная связка:

```sql
SELECT *
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior
WHERE incdnt_entry_dt BETWEEN '{begin}' AND '{end}'
  AND (incdnt_detection_person_name = 'Клиент'
       OR src_type_lvl_2_name LIKE '%обращени%'
       OR incdnt_source_name LIKE '%клиент%');
-- + для ID обращения – нужна доп. витрина B2C (вне БЗ ИОР)
```

→ LLM сообщает: «Прямое поле "номер обращения клиента" в БЗ ИОР отсутствует. Для связки с конкретным обращением…» *(текст в источнике обрезан кадром)*

### 11.6. «ИОР за {период}, связанные с риском поведения»

```sql
SELECT *
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior
WHERE incdnt_entry_dt BETWEEN '{begin}' AND '{end}'
  AND incdnt_behavior_risk_flag = 'Y';
```

→ Скрипт: `ior_period_pao_sberbank_v2` + `WHERE incdnt_behavior_risk_flag = 'Y'`

### 11.7. «Сколько инцидентов с фин. потерями в Q4 2025?»

```sql
SELECT COUNT(DISTINCT ior.incdnt_id) AS n_with_losses
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact fi
    ON ior.incdnt_id = fi.incdnt_id
WHERE ior.incdnt_entry_dt >= '2025-10-01'
  AND ior.incdnt_entry_dt <  '2026-01-01'
  AND fi.fin_impact_type_name IN ('Прямая потеря', 'Косвенная потеря', 'Потеря третьих лиц');
```

→ Возвращает **одно число**. Не использовать `incdnt_sum > 0` (2.26% заполненности).

### 11.8. «ИОР, где сотрудники признаны виновными в потерях клиентов»

```sql
SELECT
    ior.incdnt_id, ior.incdnt_sid, ior.incdnt_entry_dt,
    ior.org_struct_lvl_3_name,
    e.value_ AS empl_tab_num,
    empl.fio AS empl_fio,
    fi.fin_impact_rub_amt AS client_loss_amt
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
INNER JOIN arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_employee e
    ON ior.incdnt_sid = e.aggregateroot_id
LEFT JOIN arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_empl empl
    ON e.value_ = empl.value_
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact fi
    ON ior.incdnt_id = fi.incdnt_id
WHERE ior.incdnt_entry_dt BETWEEN '{begin}' AND '{end}'
  AND fi.fin_impact_type_name = 'Потеря третьих лиц'
ORDER BY fi.fin_impact_rub_amt DESC;
```

⚠️ **Конф.:** `empl_tab_num` и `empl_fio` показывать только в Excel, не в чате.

### 11.9. «Статистика ИОР по потерям клиентов, выявленным УВА»

```sql
SELECT
    ior.org_struct_lvl_3_name AS tb_name,
    COUNT(DISTINCT ior.incdnt_id) AS n_incidents,
    SUM(fi.fin_impact_rub_amt) AS sum_third_party_loss
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact fi
    ON ior.incdnt_id = fi.incdnt_id
WHERE ior.incdnt_entry_dt BETWEEN '{begin}' AND '{end}'
  AND ior.incdnt_source_name = 'Результат анализа акта УВА'
  AND fi.fin_impact_type_name = 'Потеря третьих лиц'
GROUP BY ior.org_struct_lvl_3_name
ORDER BY sum_third_party_loss DESC;
```

### 11.10. «В каком ВНД описан порядок регистрации ИОР?»

❌ **Не из БЗ ИОР.** LLM перенаправляет на SberDocs/Консультант:
> «Этот вопрос относится к ВНД. БЗ ИОР содержит данные по инцидентам, а не описание процедуры регистрации. Обратитесь к СберДокс / Консультанту, ARIS или поищите Стандарт методики № 4467.»


## 12. Типовые ошибки LLM и как их избежать

| # | Ошибка | Как избежать |
|:-:|--------|--------------|
| 1 | Использовать `incdnt_sum > 0` для поиска ИОР с фин. потерями | Заполнено только в 2.26%. Использовать JOIN с `incident_fin_impact`. |
| 2 | Считать `COUNT(*)` после JOIN с 1:N таблицами | Декартово раздувание. Использовать `COUNT(DISTINCT incdnt_id)`. |
| 3 | Сравнивать флаги через `== True` (boolean) | Все флаги — `string Y/N`. Использовать `== 'Y'`. |
| 4 | Использовать `recovery_rub_amt` из main | Поле удалено! Используется `recovery_rub_amt_aggr`. |
| 5 | Использовать `fin_impact_subkind_name` или `fin_impact_ctgry_risk_code` | Эти поля удалены/отсутствуют в новой БЗ. |
| 6 | Фильтр `incdnt_status_name = 'Удален'` (через `'е'`) | Только через `'ё'`: `'Удалён'`. Лучше `UPPER(...) = 'УДАЛЁН'`. |
| 7 | Использовать старый префикс `arnsdpsbx_t_team_sva_oarb` | Новая схема: `arnsdpsbx_t_team_sva_oarb_4`. |
| 8 | Использовать имена таблиц `40_pnp_d6_base_of_knowledge_*_test_<date>` | Новые имена: без префикса `40_pnp_` и без даты. |
| 9 | Выводить в чат ФИО клиентов из `incdnt_full_descr_txt` | Маскировать или обрезать. |
| 10 | Выводить в чат табельные номера (`stts_chng_user_num`, `value_`) | JOIN с `empl` для ФИО, или маскировать. |
| 11 | Игнорировать NULL в флагах риска | Риск-флаги могут быть `'Y'/'N'/NULL`. `WHERE flag = 'Y'` корректно (NULL не пройдёт). |
| 12 | Фильтр по `clntpth_lvl_4_name` без предупреждения о ~1% заполненности | Всегда предупреждать пользователя о низкой заполненности. |
| 13 | Использовать дату удаления `stts_chng_action_dttm` вместо даты ввода `incdnt_entry_dt` | Период по умолчанию = `incdnt_entry_dt`. |
| 14 | Не очищать описания от юникод-мусора | Применять `regexp_replace('[^\x02\x08\x0b]', '')`. |
| 15 | Считать что все ИОР относятся к ПАО Сбербанк | БЗ содержит ИОР ДЗО (Сбербанк Лизинг и др.). Фильтровать через префиксы `org_struct_id`. |
| 16 | Путать `incdnt_type_lvl_*_name` (классификация) с `risk_profile_name` (ЦПР) | Это разные вещи! Тип события — таксономия ИОР; ЦПР — природа риска. |
| 17 | Возвращать SID без префикса EVE- пользователю | Всегда показывать в полном формате `EVE-XXXXXXX`. |
| 18 | Использовать `start_dt` в `incident_stts_chng` для бизнес-фильтрации | Это техническое поле бизнес-даты записи, не для пользовательских фильтров. Использовать `stts_chng_action_dttm`. |


## 13. Расширенные правила извлечения параметров

### 13.1. Парсинг даты / периода

| Формулировка пользователя | `_begin` | `_end` |
|---|---|---|
| «за 2025 год» / «в 2025» | `2025-01-01` | `2025-12-31` |
| «за прошлый год» (текущий = 2026) | `2025-01-01` | `2025-12-31` |
| «Q1 2025» / «1 квартал 2025» / «первый квартал» | `2025-01-01` | `2025-03-31` |
| «Q2 2025» | `2025-04-01` | `2025-06-30` |
| «Q3 2025» | `2025-07-01` | `2025-09-30` |
| «Q4 2025» / «четвёртый квартал» | `2025-10-01` | `2025-12-31` |
| «за январь 2025» / «01.2025» | `2025-01-01` | `2025-01-31` |
| «за прошлый месяц» (тек. = май 2026) | `2026-04-01` | `2026-04-30` |
| «за последние 6 месяцев» (тек. = 2026-05-22) | `2025-11-22` | `2026-05-22` |
| «с 20.01.2025 по 30.01.2025» | `2025-01-20` | `2025-01-30` |
| «за всё время» / «за весь период БЗ» | `2025-01-01` | `2026-03-31` |
| «недавно» / «свежие» без явного периода | спросить или взять текущий месяц |

### 13.2. Парсинг подразделения

| Текст пользователя | Поле | Значение фильтра |
|---|---|---|
| «СЗБ» / «Северо-Западный банк» | `org_struct_lvl_3_name` | `LIKE '%Северо-Западный%'` |
| «ЮЗБ» / «Юго-Западный банк» | `org_struct_lvl_3_name` | `LIKE '%Юго-Западный%'` |
| «СИБ» / «Сибирский банк» | `org_struct_lvl_3_name` | `LIKE '%Сибирский%'` |
| «ВВБ» / «Волго-Вятский банк» | `org_struct_lvl_3_name` | `LIKE '%Волго-Вятский%'` |
| «ПВБ» / «Поволжский банк» | `org_struct_lvl_3_name` | `LIKE '%Поволжский%'` |
| «Урб» / «Уральский банк» | `org_struct_lvl_3_name` | `LIKE '%Уральский%'` |
| «ЦА» / «центральный аппарат» | `org_struct_lvl_2_name` | `= 'ПАО Сбербанк (ЦА)'` |
| «ПЦП» / «подразделение центрального подчинения» | `org_struct_lvl_2_name` | `= 'Подразделения центрального подчинения'` |
| «Розничный бизнес» / «РБ» | `funct_block_lvl_2_name` | `= 'Блок "Розничный бизнес"'` |
| «ВСП №Х» / «отделение Х» | `org_struct_lvl_8_name` / `lvl_9_name` | `LIKE '%Х%'` + ⚠️ предупредить о низкой заполненности |

### 13.3. Парсинг продукта

| Текст | Поле | Фильтр |
|---|---|---|
| «Ипотека» / «жил. кредит» | `process_lvl_4_name` | `LIKE '%ипотек%'` |
| «Кредитная карта» / «КК» | `process_lvl_4_name` | `LIKE '%кредитн%карт%'` |
| «Дебетовая карта» / «ДК» | `process_lvl_4_name` | `LIKE '%дебетов%карт%'` |
| «Потреб» / «потребительский кредит» | `process_lvl_4_name` | `LIKE '%потребительск%'` |
| «Автокредит» | `process_lvl_4_name` | `LIKE '%автокредит%'` |
| «Депозит» / «вклад» | `process_lvl_4_name` | `LIKE '%депозит%' OR LIKE '%вклад%'` |
| «РКО» / «расчётно-кассовое обслуживание» | `process_lvl_4_name` | `LIKE '%РКО%'` |

---


## 14. Глоссарий

| Термин | Расшифровка |
|---|---|
| ИОР | Инцидент операционного риска |
| ОР | Операционный риск |
| ЦПР | Цифровой профиль риска (природа инцидента) |
| БЗ | База знаний ИОР (схема `arnsdpsbx_t_team_sva_oarb_4`) |
| БВД | Бизнес-витрина данных |
| СМ 4467 | Стандарт методики расчёта последствий ИОР |
| ТБ | Территориальный банк (СЗБ, ЮЗБ, СИБ, ВВБ, ЦСКБ и др.) |
| ГОСБ | Головное отделение Сбербанка |
| ВСП | Внутреннее структурное подразделение |
| УРМ | Удалённое рабочее место |
| УВА | Управление внутреннего аудита |
| СВА | Служба внутреннего аудита |
| ПЦП | Подразделение центрального подчинения |
| ЦА | Центральный аппарат |
| ДЗО | Дочерние и зависимые общества |
| КАП | Карта аудиторских процедур |
| РК / РМ | Риск-координатор / Риск-менеджер |
| РВПС | Резерв на возможные потери по ссудам |
| РВП | Резерв на возможные потери (проценты/пени) |
| КП | Клиентский путь |
| SID | Бизнес-идентификатор (формат `EVE-XXXXXXX` для инцидента) |
| ETSM | Система регистрации заявок (Transact) |
| LLM | Large Language Model |
| RAG | Retrieval-Augmented Generation |
| AI-помощник | Чат-бот, разрабатываемый в рамках Disrupt-проекта по ОР |

---

*Документ подготовлен: 22.05.2026 • Автор: СЗБ • Версия 2.1*
