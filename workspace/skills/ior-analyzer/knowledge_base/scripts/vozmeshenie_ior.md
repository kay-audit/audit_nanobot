Вот объединенный Markdown-документ, восстановленный по представленным скриншотам. Вы можете скопировать его целиком.

***

# Скрипт «Возмещения по ИОР»

* **Notebook:** `новая_схема_БЗ_v2/vozmeshenie_ior_v2.ipynb`
* **Skill ID:** `vozmeshenie_ior_v2`
* **Категория:** детализация по сущностям ИОР (возмещения)

---

## 1. Краткое описание для LLM-маршрутизатора

Скрипт формирует **список всех возмещений** по инцидентам операционного риска за указанный период.
* **Один ряд отчёта = одно возмещение** (по полю `recovery_sid`).
* У одного инцидента может быть **несколько возмещений** (1:N) — каждое отдельной строкой со всеми атрибутами родительского ИОР.

**Контракт гранулярности аналитики:**
* Excel/CSV и общая сумма `SUM(recovery_rub_amt)` строятся по полной операционной выгрузке.
* Статусная сводка строится по одной агрегированной записи на `incdnt_sid`.
* После статусной сводки профилирование, суммы, текстовый анализ и гипотезы строятся только по утверждённым ИОР; detail-показатели фильтруются до операций этих ИОР.
* При агрегации ИОР поле `recovery_rub_amt` суммируется по всем его `recovery_sid`; поэтому общая сумма не меняется.
* Количество строк показывается только один раз — в начальном блоке отчёта. Далее любые количества означают уникальные `incdnt_sid`.
* В отчёте этого пресета запрещены показатели потерь и Net Loss: достоверным денежным показателем является только сумма возмещений.

**Когда LLM должен выбрать этот скрипт:** запрос пользователя про **возмещения, возвраты, компенсации, страховые выплаты, восстановление резерва** в контексте операционного риска.

---

## 2. Триггеры — когда применять этот скрипт

### 2.1. Прямые триггеры (любая формулировка из списка -> 100% этот скрипт)

| Триггер пользователя | Что распознать |
| :--- | :--- |
| «возмещения по ИОР» / «возмещения по операционному риску» | основная тема |
| «возмещения за период ...» / «возмещения с XX по YY» | период + возмещения |
| «по каким ИОР были возмещения» | существование возмещения |
| «возмещения по решению суда» | по типу: `recovery_type_name LIKE '%суд%'` |
| «возмещения от страховых» / «страховая выплата по ИОР» | по типу: `recovery_type_name LIKE '%страхов%'` |
| «компенсации от клиентов» / «возврат от клиента» | по типу: `recovery_type_name LIKE '%клиент%'` |
| «компенсации от сотрудников» / «возврат от работника» | по типу: `recovery_type_name LIKE '%сотрудник%'` OR LIKE `'%работник%'` |
| «восстановление резерва РВПС / на возможные потери по ссудам» | по типу: `recovery_type_name LIKE '%резерв%'` |
| «возмещения от участников Группы» | по типу |
| «возмещения, полученные во внесудебном порядке» | по типу |

### 2.2. Контекстные триггеры (вероятно этот скрипт, нужно уточнить)

| Триггер | Действие LLM |
| :--- | :--- |
| «какие деньги вернули по инциденту» | Применить, уточнить период |
| «было ли возмещение по EVE-XXX» | Применить с фильтром по `incdnt_sid`, либо предложить [report_period_specific_ior](report_period_specific_ior.md) |
| «сколько возмещено за период» | Применить + посчитать `SUM(recovery_rub_amt)` |

### 2.3. Семантические признаки в запросе

Любое из слов в запросе пользователя — **сильный сигнал** для этого скрипта:
* **возмещение**, возмещения, возврат, возвраты
* **компенсация**, компенсации
* **страховая выплата**, страховка
* **восстановление резерва**, восстановили РВПС
* `recovery`, `refund`, `compensation`
* «вернули средства», «получили обратно»

---

## 3. Анти-триггеры — когда НЕ применять (направить на другой скрипт)

| Если пользователь спрашивает | Использовать вместо |
| :--- | :--- |
| **итоговую сумму возмещения** по инциденту (без детализации операций) | `[ior_period_pao_sberbank](ior_period_pao_sberbank.md)` — там `recovery_rub_amt_aggr` |
| **только сумму потерь** без возмещений | `[financial_consequences_ior](financial_consequences_ior.md)` |
| **конкретный инцидент** по SID с полным досье (возмещения + последствия) | `[report_period_specific_ior](report_period_specific_ior.md)` |
| **Удалённые ИОР** | `[deleted_ior](deleted_ior.md)` |
| Поиск **самого договора возмещения / источника** в БЗ | Нет такой таблицы в БЗ. Только метаданные операции возмещения в `incident_recovery`. — (ограничение БЗ) |

---

## 4. Извлечение параметров из запроса пользователя

### 4.1. Параметры скрипта (контракт)

| Параметр | Тип | Обязательность | Дефолт | Формат | Источник в запросе |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `incdnt_entry_dt_begin` | DATE | да | — | `YYYY-MM-DD` | начало периода |
| `incdnt_entry_dt_end` | DATE | да | — | `YYYY-MM-DD` | конец периода (включительно) |

### 4.2. Правила парсинга периода из текста (для LLM)

| Формулировка | `_begin` | `_end` |
| :--- | :--- | :--- |
| «за 2025 год» | `2025-01-01` | `2025-12-31` |
| «за Q1 2025» / «за 1 квартал 2025» | `2025-01-01` | `2025-03-31` |
| «за Q4 2025» / «за 4 кв. 2025» | `2025-10-01` | `2025-12-31` |
| «за январь 2025» / «за 01.2025» | `2025-01-01` | `2025-01-31` |
| «с 20.01.2025 по 30.01.2025» | `2025-01-20` | `2025-01-30` |
| «последние 6 месяцев» (на дату 2026-05-22) | `2025-11-22` | `2026-05-22` |
| «за прошлый месяц» (на 2026-05) | `2026-04-01` | `2026-04-30` |
| «недавние возмещения» (без явного периода) | спросить уточнение или взять текущий месяц | |

### 4.3. Если параметры не указаны

LLM должен спросить уточнение: **«За какой период вас интересуют возмещения?»** с предложением вариантов: текущий квартал, прошлый месяц / весь 2025 год.

---

## 5. Источники данных и связи

| # | Полное имя таблицы | Кол-во полей | Гранулярность |
| :-: | :--- | :-: | :--- |
| 1 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior` | 67 | 1 ряд = 1 инцидент |
| 2 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery` | 12 | 1 ряд = 1 операция возмещения |

* **Связка:** `ior.incdnt_id = incident_recovery.incdnt_id` (INNER JOIN).
* **Эффект INNER JOIN:** инциденты без возмещений автоматически отсекаются. Это часть бизнес-логики отчёта.

---

## 6. Алгоритм работы (пошагово)

1. **Фильтрация ИОР по периоду** выполняется в Greenplum до передачи результата в Python: `incdnt_entry_dt >= begin AND incdnt_entry_dt < end_exclusive`.
   Использует `incdnt_entry_dt` (дата ввода в систему). Граница `< end+1` для полного включения дня `end`.

2. **Выгрузка возмещений** выполняется одним SQL JOIN с GP-витриной `t_db_oarb_ior_d6_base_of_knowledge_incident_recovery`; выбираются прежние поля `recovery_*`.

3. **INNER JOIN main × recovery** по `incdnt_id` — каждый инцидент дублируется столько раз, сколько у него возмещений.
4. **Дедупликация по `(incdnt_sid, recovery_sid)`** — на случай если в БЗ есть кросс-строки.
5. **Сортировка:** `incdnt_entry_dt`, `incdnt_sid`, `recovery_sid`.
6. **Экспорт в Excel** с переименованием колонок по словарю `RENAME`.

---

## 7. SQL-эквивалент (Greenplum)

```sql
SELECT
  -- Идентификация инцидента --
  ior.incdnt_id, ior.incdnt_sid,
  ior.incdnt_status_name, ior.incdnt_autoreg_flag,
  ior.incdnt_detection_person_name, ior.incdnt_source_name,
  ior.src_type_lvl_1_name, ior.src_type_lvl_2_name,
  ior.incdnt_type_lvl_1_name, ior.incdnt_type_lvl_2_name,
  ior.incdnt_detection_dt, ior.incdnt_start_dt, ior.incdnt_entry_dt,
  ior.incdnt_first_validated_dttm, ior.incdnt_last_validate_dttm,
  ior.risk_profile_id, ior.risk_profile_name,
  ior.incdnt_client_type_name, ior.incdnt_mistake_cnt,
  ior.incdnt_appl_num, ior.incdnt_agr_num, ior.incdnt_agr_sid,
  regexp_replace(ior.incdnt_summary_descr_txt, '[^\t-\x7E\x02\x08\x0b]', '') AS incdnt_summary_descr_txt,
  regexp_replace(ior.incdnt_full_descr_txt, '[^\t-\x7E\x02\x08\x0b]', '') AS incdnt_full_descr_txt,
  -- Оргструктура --
  ior.org_struct_id,
  ior.org_struct_lvl_2_name, ior.org_struct_lvl_3_name, ior.org_struct_lvl_4_name,
  ior.org_struct_lvl_5_name, ior.org_struct_lvl_6_name, ior.org_struct_lvl_7_name,
  ior.org_struct_lvl_8_name, ior.org_struct_lvl_9_name, ior.org_struct_lvl_10_name,
  -- Функ. блок --
  ior.funct_block_id,
  ior.funct_block_lvl_2_name, ior.funct_block_lvl_3_name, ior.funct_block_lvl_4_name,
  -- Процесс --
  ior.process_lvl_1_name, ior.process_lvl_2_name,
  ior.process_lvl_3_name, ior.process_lvl_4_name,
  ior.clntpth_lvl_4_name,
  -- Флаги риска (Y/N) --
  ior.incdnt_security_risk_flag, ior.incdnt_infrmtn_sys_risk_flag,
  ior.incdnt_behavior_risk_flag, ior.incdnt_model_risk_flag,
  -- Возмещение (детали) --
  rec.recovery_sid, rec.recovery_type_name,
  rec.recovery_crncy_code, rec.recovery_local_crncy_code,
  rec.recovery_src_account_num, rec.recovery_doc_num,
  rec.recovery_creation_dttm, rec.recovery_reg_dt,
  rec.recovery_ccy_amt, rec.recovery_local_ccy_amt, rec.recovery_rub_amt
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery rec
  ON ior.incdnt_id = rec.incdnt_id
WHERE ior.incdnt_entry_dt >= TIMESTAMP '{begin}'
  AND ior.incdnt_entry_dt < TIMESTAMP '{end_exclusive}'
ORDER BY ior.incdnt_entry_dt, ior.incdnt_sid, rec.recovery_sid;
```

---

## 8. Структура выходного отчёта

* **Гранулярность строки:** одна строка = одна операция возмещения по одному инциденту.
* **Сортировка:** `incdnt_entry_dt` ASC → `incdnt_sid` → `recovery_sid`.
* **Дедупликация:** по `(incdnt_sid, recovery_sid)`.

### 8.1. Колонки выходного Excel (полный список с переводами)

| # | Поле БД | Excel-наименование | Тип | Источник |
| :-: | :--- | :--- | :--- | :--- |
| 1 | `incdnt_id` | идентификационный ключ инцидента операционного риска | bigint | main |
| 2 | `incdnt_sid` | Идентификатор события | string | main |
| 3 | `incdnt_status_name` | Статус события | string | main |
| 4 | `incdnt_autoreg_flag` | Признак авторегистрации инцидента | Y/N | main |
| 5 | `incdnt_detection_person_name` | Кем выявлено событие | string | main |
| 6 | `incdnt_source_name` | Название источника | string | main |
| 7 | `src_type_lvl_1_name` | Тип источника инцидента (уровень 1) | string | main |
| 8 | `src_type_lvl_2_name` | Тип источника инцидента (уровень 2) | string | main |
| 9 | `incdnt_type_lvl_1_name` | Тип события – уровень 1 | string | main |
| 10 | `incdnt_type_lvl_2_name` | Тип события – уровень 2 | string | main |
| 11 | `incdnt_detection_dt` | Дата обнаружения (Событие) | timestamp | main |
| 12 | `incdnt_start_dt` | Дата начала инцидента | timestamp | main |
| 13 | `incdnt_entry_dt` | Дата ввода (событие) | timestamp | main |
| 14 | `incdnt_first_validated_dttm` | Первая дата утверждения инцидента | timestamp | main |
| 15 | `incdnt_last_validate_dttm` | Последняя дата утверждения инцидента | timestamp | main |
| 16 | `risk_profile_id` | Ключ Цифрового Профиля Риска | string | main |
| 17 | `risk_profile_name` | Название Цифрового профиля риска | string | main |
| 18 | `incdnt_client_type_name` | Наименование типа клиента | string | main |
| 19 | `incdnt_mistake_cnt` | Количество ошибок | int | main |
| 20 | `incdnt_appl_num` | Номер заявки (сделки) по инциденту | string | main |
| 21 | `incdnt_agr_num` | Номер кредитного договора | string | main |
| 22 | `incdnt_agr_sid` | Идентификатор кредитного договора | string | main |
| 23 | `incdnt_summary_descr_txt` | Предварительное описание | string | main |
| 24 | `incdnt_full_descr_txt` | Подробное описание | string | main |
| 25 | `org_struct_id` | Идентификационный ключ оргструктуры | string | main |
| 26 | `org_struct_lvl_2_name` | Орг.структура – уровень 2 | string | main |
| 27 | `org_struct_lvl_3_name` | Орг.структура – уровень 3 (Блок/ТБ/ПЦП) | string | main |
| 28 | `org_struct_lvl_4_name` | Орг.структура – уровень 4 (Дивизион/департамент) | string | main |
| 29 | `org_struct_lvl_5_name` | Орг.структура – уровень 5 | string | main |
| 30 | `org_struct_lvl_6_name` | Управление / отдел / группа | string | main |
| 31 | `org_struct_lvl_7_name` | УРМ / Группа / Управление ГОСБ / ВСП | string | main |
| 32 | `org_struct_lvl_8_name` | Отдел ГОСБ / Сектор ГОСБ / Центр ГОСБ / ВСП | string | main |
| 33 | `org_struct_lvl_9_name` | Отдел ГОСБ / ВСП | string | main |
| 34 | `org_struct_lvl_10_name` | Группа ГОСБ и прочие подструктуры | string | main |
| 35 | `funct_block_id` | Идентификационный ключ функционального блока | string | main |
| 36 | `funct_block_lvl_2_name` | функ. блок – уровень 2 (Дивизион/трайб) | string | main |
| 37 | `funct_block_lvl_3_name` | функ. блок – уровень 3 | string | main |
| 38 | `funct_block_lvl_4_name` | функ. блок – уровень 4 | string | main |
| 39 | `process_lvl_1_name` | Процесс — уровень 1 (Банк/ДЗО) | string | main |
| 40 | `process_lvl_2_name` | Процесс — уровень 2 | string | main |
| 41 | `process_lvl_3_name` | Процесс — уровень 3 | string | main |
| 42 | `process_lvl_4_name` | Процесс — уровень 4 (Наименование процесса) | string | main |
| 43 | `clntpth_lvl_4_name` | Клиентский путь — уровень 4 | string | main |
| 47 | `incdnt_security_risk_flag` | Связь с ИБ-риском | Y/N | main |
| 48 | `incdnt_infrmtn_sys_risk_flag` | Связь с риском информационных систем | Y/N | main |
| 49 | `incdnt_behavior_risk_flag` | Связь с поведенческим риском | Y/N | main |
| 50 | `incdnt_model_risk_flag` | Связь с модельным риском | Y/N | main |
| 51 | **`recovery_sid`** | **Идентификатор возмещения** | string | recovery |
| 52 | **`recovery_type_name`** | **Тип возмещения** | string | recovery |
| 53 | `recovery_crncy_code` | Код валюты возмещения | string | recovery |
| 54 | `recovery_local_crncy_code` | Код локальной валюты возмещения | string | recovery |
| 55 | `recovery_src_account_num` | Номер счёта – источник перевода | string | recovery |
| 56 | `recovery_doc_num` | Номер бухгалтерского документа | string | recovery |
| 57 | `recovery_creation_dttm` | Дата создания возмещения | timestamp | recovery |
| 58 | `recovery_reg_dt` | Дата регистрации в учёте | timestamp | recovery |
| 59 | `recovery_ccy_amt` | Сумма возмещения (в валюте) | decimal | recovery |
| 60 | `recovery_local_ccy_amt` | Сумма возмещения (в локальной валюте) | decimal | recovery |
| 61 | **`recovery_rub_amt`** | **Сумма возмещения (руб.)** | decimal | recovery |

---

## 9. Семантика ключевых полей результата

### 9.1. `recovery_type_name` — 14 значений

| Значение | Бизнес-смысл |
| :--- | :--- |
| Возмещения от участников Группы, связанных с Банком лиц, акционеров, бенефициаров | Внутри группы Сбера |
| Возмещения, полученные в судебном порядке | После судебного решения |
| Возмещения, полученные во внесудебном порядке по соглашению сторон | Без суда, договорённость |
| Восстановление резерва на возможные потери по ссудам | РВПС, бухгалтерское восстановление |
| Восстановление резерва некредитного характера | РВП по комиссиям/процентам |
| Компенсации от других источников | Прочие |
| Компенсации от клиента | Возврат от клиента ИОР |
| Компенсации от организации / банка-корреспондента | От контрагента-банка |
| Компенсации от сотрудника, допустившего рисковое событие | С виновного сотрудника |
| Возмещение от работников банка | (нижний регистр – старый ввод) |
| Возмещения, полученные от третьих лиц | Внешние компенсации |
| получение страховой выплаты от одной или нескольких внешних страховых компаний | Внешняя страховка |
| получение страховой выплаты от одной или нескольких страховых компаний группы | Страховка Сбера |

### 9.2. `recovery_rub_amt` vs `recovery_rub_amt_aggr`

| Поле | Где | Значение |
| :--- | :--- | :--- |
| `recovery_rub_amt` | `incident_recovery` | Сумма **одной операции** возмещения. Может быть несколько строк на инцидент. |
| `recovery_rub_amt_aggr` | `d6_base_of_knowledge_ior` (main) | **Итоговая** сумма возмещения по инциденту (агрегат, посчитанный Пономаренко). |

**Правило этого пресета:** для агрегата по инциденту суммировать **`recovery_rub_amt`** по `incdnt_sid`; для перечня операций сохранять исходные строки `recovery_sid`. Поле `recovery_rub_amt_aggr` можно использовать только как справочный контроль, но нельзя суммировать после JOIN, поскольку оно повторяется в каждой строке возмещения.

### 9.3. Валюты
* `recovery_crncy_code` — основная валюта операции (23 значения: `RUB`, `USD`, `EUR`, `BYN`, `BYR`, `KZT`, `UAH`, `CHF`, `GBP`, `CNY`, `JPY`, `INR`, `AED`, `CAD`, `DKK`, `NOK`, `PLN`, `RSD`, `SEK`, `SGD`, `TRY`, `ZAR`, `_`).
* `recovery_local_crncy_code` — локальная валюта (только `BYN` или `RUB`; заполненность ~31%).

### 9.4. Даты возмещения
* `recovery_creation_dttm` — когда **создали запись** о возмещении в системе.
* `recovery_reg_dt` — когда **зарегистрировали в бух.учёте**. Это ключевая дата для отчётности.

---

## 10. Предупреждения о данных (LLM должен учитывать)

1. **Не все ИОР имеют возмещения.** INNER JOIN отсекает ~60% инцидентов (`recovery_rub_amt_aggr` заполнено только в 39.2% main).
2. **Заполненность `recovery_local_crncy_code` ~31%** — большинство в RUB и поле NULL.
3. **Кратность записей.** Один инцидент → несколько возмещений → несколько строк отчёта. При подсчёте «количества ИОР с возмещениями» делать `COUNT(DISTINCT incdnt_id)`, не `COUNT(*)`.
4. **`recovery_doc_num` и `recovery_src_account_num`** содержат **реальные номера** счетов и документов. **LLM не должен выводить их в чат** без маскирования (например, показывать последние 4 символа: `*****1234`).
5. **`recovery_type_name = NULL`** встречается. Считать как «тип не указан».
6. **Регистр в `recovery_type_name`** непоследовательный (есть и `Компенсации от клиента` с большой буквы, и `возмещение от работников банка` с маленькой). Это исторические данные. При фильтрации использовать `LOWER()` или `LIKE` с `%`.

---

## 11. Шаблон ответа LLM пользователю

Когда скрипт отработал, LLM формирует ответ по следующей структуре:

***

✅ Подготовлен отчёт «Возмещения по ИОР» за период {begin} — {end}.

📊 Краткая статистика:
* Строк в отчёте: {N}
* Уникальных инцидентов с возмещениями: {N_distinct_incdnt}
* Суммарное возмещение: {SUM(recovery_rub_amt):,.2f} ₽
* Период по {incdnt_entry_dt}

🔸 Топ-3 типа возмещения по сумме:
1. {type_1}: {sum_1:,.2f} ₽
2. {type_2}: {sum_2:,.2f} ₽
3. {type_3}: {sum_3:,.2f} ₽

📁 Файл: `Возмещения по ИОР {begin} — {end}.xlsx`

*! Учитывать: один ИОР может иметь несколько возмещений (отдельной строкой каждое). В Excel сохраняются все операции, а для аналитики они объединяются по `incdnt_sid` с суммированием `recovery_rub_amt`.*

💡 Что ещё можно посмотреть:
* Финансовые последствия за этот же период → `/financial_consequences_ior`
* Подробное досье конкретного ИОР → `/report_period_specific_ior?sid=EVE-XXX`
* Общий отчёт по ИОР за период → `/ior_period_pao_sberbank`

***

---

## 12. Decision Tree — куда передать запрос

Пользователь спрашивает про деньги в контексте ИОР?
├── Про возмещения / возвраты / компенсации? → **ЭТОТ СКРИПТ**
├── Про потери / ущерб / убытки? → `financial_consequences_ior`
├── Про итоговые суммы по инциденту? → `ior_period_pao_sberbank`
└── Конкретный SID + всё про него? → `report_period_specific_ior`

Пользователь упомянул конкретный SID (EVE-XXXXXXX)?
└── Да → лучше `report_period_specific_ior` (там и возмещения, и фин. последствия)

Пользователь хочет ОДНУ цифру (итог возмещения за период)?
└── Этот скрипт + агрегация в шаблоне ответа: `SELECT SUM(recovery_rub_amt) FROM <отчёт>`

---

## 13. Пограничные случаи

| Случай | Поведение |
| :--- | :--- |
| **Пустой результат** (0 строк) | LLM: «За период {begin}—{end} возмещений по ИОР не зарегистрировано.» |
| Период в будущем | LLM: «Период не наступил, данных нет.» |
| Период до 2025-01-01 | LLM: «БЗ содержит данные с 01.01.2025. Период вне диапазона.» |
| Пользователь спрашивает «возмещения по EVE-XXX» | Применить, добавив `AND ior.incdnt_sid = 'EVE-XXX'` в WHERE, либо переадресовать на `report_period_specific_ior` |
| Запрос только агрегата суммы | Применить, в шаблоне ответа показать только число `SUM(recovery_rub_amt)`, не отдавать Excel |
| Запрос «возмещения по клиенту-ФЛ» | Применить + `ior.incdnt_client_type_name = 'ФЛ'` |
| Запрос «возмещения по СЗБ» | Применить + `ior.org_struct_lvl_3_name LIKE '%Северо-Западный%'` |
| Запрос «возмещения по продукту Ипотека» | Применить + `ior.process_lvl_4_name LIKE '%Ипотека%'` |

---

## 14. Известные проблемы и ограничения

1. **Нет связи с конкретным платежом.** `recovery_src_account_num` и `recovery_doc_num` — справочные, не используются для join со внешними системами учёта.
2. **Кейс «как было — как стало».** В предыдущих версиях БЗ (`40_pnp_d6_base_of_knowledge_ior_test_24042026`) возмещения брались из подписки `prx_ior_basis_*.t_incident_recovery`. Если кто-то сравнивает с историческими выгрузками — суммы могут немного отличаться из-за разной логики дедупликации.
3. **Период данных:** `2025-01-01` – `2026-03-31` (срез БЗ). За пределами — данных нет.
4. **Дубль `recovery_rub_amt` в main.** Старое поле `recovery_rub_amt` в main удалено, оставлено `recovery_rub_amt_aggr`. Если LLM встретит запрос со старым именем поля — мапить на `recovery_rub_amt_aggr`.

---

## 15. Примеры полных flow

### Пример 1: «Какие возмещения были в январе 2025?»
* **Параметры:** `_begin='2025-01-01'`, `_end='2025-01-31'`.
* **SQL** (см. §7) с заменой плейсхолдеров.
* **Ответ LLM:**
  > ✅ Отчёт «Возмещения по ИОР» за период 2025-01-01 — 2025-01-31.
  > Строк: 2 105 | Уникальных инцидентов: 1 312 | Суммарно: 158 432 100 ₽.
  > Топ типы: Восстановление резерва РВПС (62%), Компенсации от клиента (18%), Страховые выплаты (9%).
  > 📁 «Возмещения по ИОР 2025-01-01 — 2025-01-31.xlsx»

### Пример 2: «Возмещения по решению суда за Q1 2025»
* **Дополнительный фильтр:** `recovery_type_name LIKE '%судебном порядке%'`.
* **Ответ LLM** включает указание, что отфильтровали по типу возмещения.

### Пример 3: «Сколько возместили за 2025 год?»
Это запрос на агрегат. LLM запускает скрипт за 2025-01-01 – 2025-12-31, в ответе **не присылает Excel**, а сразу даёт:
  > Суммарное возмещение по ИОР за 2025: **XX,XXX,XXX,XXX ₽** (по N инцидентам).

---

## 16. Контракт ввода-вывода (JSON Schema)

### Input

```json
{
  "$schema": "http://json-schema.org/draft-07/schema#",
  "type": "object",
  "required": ["incdnt_entry_dt_begin", "incdnt_entry_dt_end"],
  "properties": {
    "incdnt_entry_dt_begin": {
      "type": "string",
      "format": "date",
      "description": "Начало периода (дата ввода ИОР), YYYY-MM-DD"
    },
    "incdnt_entry_dt_end": {
      "type": "string",
      "format": "date",
      "description": "Конец периода (включительно), YYYY-MM-DD"
    }
  }
}
```

### Output (метаданные результата)

```json
{
  "format": "xlsx",
  "sheet_name": "Отчет_ОПриски",
  "file_name_template": "Возмещения по ИОР {begin} — {end}.xlsx",
  "row_granularity": "1 row = 1 recovery operation (recovery_sid)",
  "primary_key": ["incdnt_sid", "recovery_sid"],
  "columns_count": 61,
  "sort_order": ["incdnt_entry_dt ASC", "incdnt_sid ASC", "recovery_sid ASC"]
}
```
