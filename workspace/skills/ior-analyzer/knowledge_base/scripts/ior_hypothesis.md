# Скрипт «Выгрузка ИОР для анализа гипотез за период»

> **Контракт аналитики runtime:** статусная сводка считается по уникальным ИОР, после неё анализируются только статусы `Утверждён`, `Утвержден`, `Утверждение`. При нуле утверждённых аналитические разделы не формируются; при 1–19 разрешены проверочные кейс-гипотезы без заявлений о сезонности и системности.

> **Notebook:** [`notebooks/ior_hypothesis_v2.ipynb`](../notebooks/ior_hypothesis_v2.ipynb)
> **Skill ID:** `ior_hypothesis_v2`
> **Категория:** аналитика / гипотезы / тренды / выгрузка данных

---

## 1. Краткое описание для LLM-маршрутизатора

Формирует Excel-отчёт по инцидентам за указанный период со всеми 67 аналитическими колонками (включая финансовые показатели и оргструктуру) для последующего построения гипотез, поиска аномалий и анализа динамики. Поддерживает гибкую динамическую фильтрацию по статусу, территориальному банку, функциональному блоку или произвольному SQL-условию.

---

## 2. Триггеры - когда применять

### 2.1. Прямые триггеры

- «выведи гипотезу по инцидентам за {период}»
- «анализ аномалий за {период}»
- «сформулируй гипотезу по {ТБ/блоку} за {период}»
- «динамика и гипотезы по инцидентам {период}»
- «построй тренды за {период}»

---

## 3. Анти-триггеры

- «кто удалил ИОР EVE-1234567» -> отчёт по конкретному инциденту `report_period_specific_ior`
- «покажи только удалённые ИОР за период» -> отчёт `deleted_ior_v2`
- «какие возмещения по ИОР за период» -> отчёт `vozmeshenie_ior_v2`

---

## 4. Зависимости

- **Production-платформа:** Greenplum через общий DB-коннектор gateway
- **Таблица:** `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior`
- **Локальная разработка:** DuckDB; `openpyxl` и `pandas` используются для отчёта

---

## 5. Запуск

Скрипт запускается через стандартный Papermill runner в рамках `run_preset`.

Параметры:
- `incdnt_entry_dt_begin`: `YYYY-MM-DD` (дата начала ввода ИОР)
- `incdnt_entry_dt_end`: `YYYY-MM-DD` (дата окончания ввода ИОР)
- `status_filter`: строка, фильтр по `incdnt_status_name`
- `tb_filter`: строка, подстрока для фильтрации по `org_struct_lvl_3_name`
- `block_filter`: строка, подстрока для фильтрации по `funct_block_lvl_2_name` / `_3_name` / `_4_name`
- `additional_sql_filter`: дополнительное SQL-условие, совместимое с Greenplum

---

## 6. Как это работает (Алгоритм)

1. **Получение соединения Greenplum** из общего DB-пула gateway.
2. **Фильтрация в SQL до получения данных** из основной витрины по `incdnt_entry_dt ∈ [begin, end+1)`.
3. **Накладывание дополнительных фильтров** (по статусу, ТБ, функциональному блоку или кастомному SQL), если они переданы.
4. **Выборка доступных колонок** данных (без удалённых из GP `busn_area_id`, `busn_area_lvl_1_name`, `busn_area_lvl_2_name`).
5. **Очистка текстовых полей** выполняется в существующем Pandas-пайплайне отчёта.
6. **Получение Pandas DataFrame** из курсора GP и переименование колонок в соответствии с UI-маппингом.
7. **Сохранение в Excel** с помощью `openpyxl` на лист `Отчет_ОпРиски`.

---

## 7. SQL-эквивалент

```sql
SELECT
    incdnt_id,
    incdnt_sid,
    incdnt_status_name,
    incdnt_autoreg_flag,
    incdnt_detection_person_name,
    incdnt_source_name,
    src_type_lvl_1_name,
    src_type_lvl_2_name,
    incdnt_type_lvl_1_name,
    incdnt_type_lvl_2_name,
    incdnt_detection_dt,
    incdnt_start_dt,
    incdnt_entry_dt,
    incdnt_first_validated_dttm,
    incdnt_last_validate_dttm,
    risk_profile_id,
    risk_profile_name,
    incdnt_client_type_name,
    incdnt_mistake_cnt,
    incdnt_appl_num,
    incdnt_agr_num,
    incdnt_agr_sid,
    regexp_replace(incdnt_summary_descr_txt, '[\\u1128-\\uFFFF\\x02\\x08\\x0b]', '') as incdnt_summary_descr_txt,
    regexp_replace(incdnt_full_descr_txt, '[\\u1128-\\uFFFF\\x02\\x08\\x0b]', '') as incdnt_full_descr_txt,
    org_struct_id,
    org_struct_lvl_2_name, org_struct_lvl_3_name, org_struct_lvl_4_name,
    org_struct_lvl_5_name, org_struct_lvl_6_name, org_struct_lvl_7_name,
    org_struct_lvl_8_name, org_struct_lvl_9_name, org_struct_lvl_10_name,
    funct_block_id,
    funct_block_lvl_2_name, funct_block_lvl_3_name, funct_block_lvl_4_name,
    process_lvl_1_name, process_lvl_2_name, process_lvl_3_name, process_lvl_4_name,
    clntpth_lvl_4_name,
    incdnt_security_risk_flag, incdnt_infrmtn_sys_risk_flag,
    incdnt_behavior_risk_flag, incdnt_model_risk_flag,
    incdnt_sum,
    incdnt_drct_dmg_sum, incdnt_drct_dmg_cred_rub_amt, incdnt_drct_dmg_noncred_rub_amt,
    incdnt_indrct_dmg_sum, incdnt_indrct_dmg_cred_rub_amt, incdnt_indrct_dmg_noncred_rub_amt,
    incdnt_unrlzd_dmg_sum, incdnt_unrlzd_dmg_cred_rub_amt, incdnt_unrlzd_dmg_noncred_rub_amt,
    incdnt_thrd_prt_sum, incdnt_thrd_prt_cred_rub_amt, incdnt_thrd_prt_noncred_rub_amt,
    incdnt_gain_sum, incdnt_gain_cred_rub_amt, incdnt_gain_noncred_rub_amt,
    recovery_rub_amt_aggr
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior
WHERE incdnt_entry_dt >= TIMESTAMP '{begin}'
  AND incdnt_entry_dt < TIMESTAMP '{end_exclusive}'
  -- Динамические фильтры:
  AND ({status_filter_sql})
  AND ({tb_filter_sql})
  AND ({block_filter_sql})
  AND ({additional_sql_filter})
ORDER BY incdnt_entry_dt, incdnt_sid;
```

---

## 8. Структура выходного отчёта

**Гранулярность:** 1 строка = 1 инцидент операционного риска.
**Сортировка:** дата ввода (возрастание) → SID.

### 8.1. Колонки отчёта (67 полей)

Содержит полную плоскую структуру ИОР из БД, переименованную для удобства человека.
Примеры переименований:
- `incdnt_id` -> `Идентификационный ключ инцидента операционного риска`
- `incdnt_sid` -> `Идентификатор события`
- `incdnt_status_name` -> `Статус события`
- `incdnt_entry_dt` -> `Дата ввода (Событие)`
- `incdnt_sum` -> `Общая сумма всех последствий (руб.)`
- `org_struct_lvl_3_name` -> `Орг. структура – уровень 3 (Блок / ТБ / ПЦП)`

---

## 9. Семантика ключевых полей

- `incdnt_entry_dt`: Время, когда запись попала в базу данных. Именно на эту дату ориентируется построение гипотез и временной динамики.
- `incdnt_sum` и `incdnt_drct_dmg_sum`: Финансовые последствия. Используются для выявления аномально крупных потерь и построения суммарных гипотез.
- `incdnt_status_name`: Статус события. Используется для разделения на легитимные, отклонённые и удалённые инциденты.

---

## 10. Предупреждения о данных

1. Финансовые поля могут быть пустыми (~97.74% незаполненных значений). Алгоритмы гипотез должны корректно заменять `NaN` на `0` при суммировании.
2. Текстовые описания могут содержать обрывки Unicode-символов, которые ломают парсинг CSV/Excel — в ноутбуке для этого настроена регулярная очистка `_UNICODE_GARBAGE`.
3. Фильтрация по дате ввода (`incdnt_entry_dt`) может не совпадать с датой начала инцидента (`incdnt_start_dt`), так как инцидент может быть зарегистрирован позже факта его обнаружения.

---

## 11. Шаблон ответа LLM

```
✅ Сформирована аналитическая выгрузка ИОР для анализа гипотез за период {begin} – {end}.

📊 Статистика выборки:
• Всего инцидентов: {N_distinct}
• Период (по дате ввода): {begin} – {end}
• Наложенные фильтры: {filters_summary}

📁 Файл: Выгрузка ИОР для анализа гипотез {begin} - {end}.xlsx

На основе этой выгрузки далее будет построен профиль данных и сформулированы гипотезы.
```

---

## 12. Decision Tree

```
Пользователь просит гипотезу/динамику/аномалии?
├─ Нужна полная выгрузка данных?
│  ├─ Да (по умолчанию для гипотез)              → ✅ ЭТОТ СКРИПТ (ior_hypothesis_v2)
│  └─ Нет, только по удаленным                   → deleted_ior_v2
└─ Запрос на расчет конкретного ИОР?            → report_period_specific_ior
```

---

## 13. Пограничные случаи

| Случай | Поведение |
|--------|-----------|
| Выборка пуста | Сообщить пользователю: «За указанный период инцидентов не найдено. Измените параметры фильтрации.» |
| Запрос гипотезы без указания периода | Спросить у пользователя уточнение периода (по умолчанию брать текущий год). |
| Ошибка в дополнительном SQL-фильтре | Перехватить ошибку Spark, сообщить пользователю и предложить ввести фильтр в текстовом виде. |

---

## 14. Известные проблемы

1. Огромные выборки (более 100 000 строк) могут приводить к OutOfMemory при конвертации `toPandas()`. Рекомендуется использовать фильтрацию по ТБ или кварталам.
2. Неполное совпадение названий ТБ в `tb_filter` (например, «Сбер» вместо «ПАО Сбербанк») решается поиском через `contains(tb_filter.lower())`.

---

## 15. Примеры flow

### Пример 1: «Выведи гипотезу по Среднерусскому банку за Q3 2025»

LLM запускает `ior_hypothesis_v2` со следующими параметрами:
`{"incdnt_entry_dt_begin": "2025-07-01", "incdnt_entry_dt_end": "2025-09-30", "tb_filter": "Среднерусский"}`

### Пример 2: «Анализ аномалий среди утвержденных ИОР в блоке розницы за 2025 год»

LLM запускает `ior_hypothesis_v2`:
`{"incdnt_entry_dt_begin": "2025-01-01", "incdnt_entry_dt_end": "2025-12-31", "status_filter": "Утвержден", "block_filter": "Розничный"}`

---

## 16. Контракт

### Input
```json
{
  "type": "object",
  "required": ["incdnt_entry_dt_begin", "incdnt_entry_dt_end"],
  "properties": {
    "incdnt_entry_dt_begin": {"type": "string", "format": "date"},
    "incdnt_entry_dt_end":   {"type": "string", "format": "date"},
    "status_filter": {"type": "string"},
    "tb_filter": {"type": "string"},
    "block_filter": {"type": "string"},
    "additional_sql_filter": {"type": "string"}
  }
}
```

### Output
```json
{
  "format": "xlsx",
  "sheet_name": "Отчет_ОпРиски",
  "file_name_template": "Выгрузка ИОР для анализа гипотез {begin} - {end}.xlsx",
  "columns_count": 67
}
```
