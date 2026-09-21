# Скрипт «Удалённые ИОР за период»

> **Контракт аналитики runtime:** одна raw-строка — одно действие удаления. Отчёт различает записи журнала и уникальные `incdnt_sid`, не выводит искусственные группы статусов, а причины анализирует по `stts_chng_comment_txt`. Табельные номера в narrative не показываются.

> **Notebook:** [`новая_схема_БЗ_v2/deleted_ior_v2.ipynb`](../новая_схема_БЗ_v2/deleted_ior_v2.ipynb)
> **Skill ID:** `deleted_ior_v2`
> **Категория:** аудит / контроль / контрольные процедуры

---

## 1. Краткое описание для LLM-маршрутизатора

Формирует Excel-отчёт по инцидентам со **статусом «Удалён»**, зарегистрированным в указанный период, с подробной информацией о том, **кто, когда и по какой причине удалил**.

**Один ряд отчёта = одна запись об удалении** (если удалений по одному инциденту было несколько – будут несколько строк).
Полный набор атрибутов ИОР сохраняется в отчёте – для понимания контекста удаления.

**Когда LLM выбирает этот скрипт:** запрос про **удалённые ИОР, причины удаления, кто удалял, журнал удалений, неправомерные удаления, контроль удалений** – типовой кейс УВА.

---

## 2. Триггеры

### 2.1. Прямые триггеры

| Триггер | Что распознать |
|---------|----------------|
| «удалённые ИОР» / «удалили инциденты» / «снесли ИОР» | основная тема |
| «удалённые ИОР за {период}» | период + удаление |
| «кто удалил ИОР» / «кем удалён инцидент» | по `stts_chng_user_num` |
| «причина удаления ИОР» / «почему удалили» | по `stts_chng_comment_txt` |
| «когда удалили инцидент» | по `stts_chng_action_dttm` |
| «журнал удалений» / «история удалений» | весь отчёт |
| «удалённые инциденты по {ТБ/блоку/процессу}» | + фильтры по оргструктуре |

### 2.2. Контекстные триггеры (контрольные кейсы УВА)

| Триггер | Действие |
|---------|----------|
| «контроль удалений ИОР за месяц» | Применить за конкретный месяц |
| «массовые удаления одним пользователем» | + группировка по `stts_chng_user_num`, `HAVING COUNT > N` |
| «удаления без указания причины» | + `stts_chng_comment_txt IS NULL OR LENGTH(...) < 10` |
| «удаления после утверждения» | + проверка наличия `incdnt_first_validated_dttm` (был утверждён до удаления) |
| «удаления крупных ИОР» | + `recovery_rub_amt_aggr > X` или `incdnt_drct_dmg_sum > X` |

### 2.3. Семантические признаки

Ключевые слова: **удалён, удалили, удалённые, удаление, удалить, снесли, deleted, removed, deletion**.

---

## 3. Анти-триггеры

| Если запрос про | Использовать |
|------------------|--------------|
| **Изменение других статусов** (утверждено/отклонено/отправлено на расследование) | Нужен отдельный отчёт по `stts_chng_action_name` |
| Не удалённые, а **отклонённые** ИОР | Использовать `incdnt_status_name = 'Отклонён'` в основном отчёте (`ior_period_pao_sberbank`) |
| Все ИОР за период включая не удалённые | [`ior_period_pao_sberbank`](ior_period_pao_sberbank.md) |
| Конкретный SID – детально | [`report_period_specific_ior`](report_period_specific_ior.md) (там тоже есть статусы) |

---

## 4. Извлечение параметров

### 4.1. Контракт

| Параметр | Тип | Обязательность | Дефолт | Формат |
|----------|-----|:---------------:|--------|--------|
| `incdnt_entry_dt_begin` | DATE | да | – | `YYYY-MM-DD` |
| `incdnt_entry_dt_end` | DATE | да | – | `YYYY-MM-DD` |
| `ORG_PREFIXES` | LIST | нет | `['SBR_', 'EXT_', 'GRC_', 'MON_', 'BPS_']` | Префиксы оргструктуры ПАО Сбербанк |

### 4.2. Правила парсинга периода

См. [`vozmeshenie_ior.md` §4.2](vozmeshenie_ior.md#42-правила-парсинга-периода-из-текста-для-llm).

⚠️ **Важно:** период фильтрует **дату ввода инцидента** (`incdnt_entry_dt`), а **не дату удаления** (`stts_chng_action_dttm`). Это значит:
- Если пользователь спрашивает «удалённые в январе 2025» – он, скорее всего, имеет в виду дату удаления, **но скрипт работает по дате ввода**.
- LLM должен **явно уточнить** в ответе: «Отчёт показывает ИОР, которые были **введены** в период X–Y и впоследствии удалены».
- При запросе «удалённые за январь» (дата удаления) – можно дополнительно отфильтровать в post-processing по `stts_chng_action_dttm`.

---

## 5. Источники

| # | Таблица | Гранулярность |
|:-:|---|---|
| 1 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior` (отфильтр. `status='Удалён'`) | 1:1 |
| 2 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng` (отфильтр. `action='Удалить'`) | 1:N |

**Связка:** LEFT JOIN main ↔ stts_chng по `incdnt_id`.

> LEFT JOIN – потому что в редких случаях статус «Удалён» проставлен в main, но записи в журнале статусов может не быть (исторические данные). Лучше показать инцидент с пустыми полями журнала, чем потерять его.

---

## 6. Алгоритм

1. Фильтрация main:
   - `incdnt_entry_dt ∈ [begin, end+1)`
   - `UPPER(incdnt_status_name) = 'УДАЛЁН'`
   - `SUBSTR(UPPER(org_struct_id), 1, 4) IN ORG_PREFIXES`
2. Фильтрация `incident_stts_chng`:
   - `UPPER(stts_chng_action_name) = 'УДАЛИТЬ'`
3. Переименование `stts_chng.incdnt_status_name` → `incdnt_status_name_at_action` (чтобы не конфликтовать с одноимённым полем в main).
4. LEFT JOIN main ↔ stts_chng по `incdnt_id`.
5. Сортировка: `incdnt_entry_dt`, `incdnt_sid`, `stts_chng_action_dttm`.
6. Очистка описаний от юникод-мусора.

---

## 7. SQL-эквивалент

```sql
WITH main_filtered AS (
    SELECT *
    FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior
    WHERE incdnt_entry_dt >= TO_TIMESTAMP('{begin}', 'yyyy-MM-dd')
      AND incdnt_entry_dt <  DATE_ADD(TO_TIMESTAMP('{end}', 'yyyy-MM-dd'), 1)
      AND UPPER(incdnt_status_name) = 'УДАЛЁН'
      AND SUBSTR(UPPER(org_struct_id), 1, 4) IN ('SBR_', 'EXT_', 'GRC_', 'MON_', 'BPS_')
),
status_filtered AS (
    SELECT
        incdnt_id AS st_incdnt_id,
        incdnt_status_name AS incdnt_status_name_at_action,
        incdnt_status_code,
        stts_chng_action_code, stts_chng_action_name,
        stts_chng_comment_txt,
        stts_chng_action_dttm, stts_chng_user_num
    FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng
    WHERE UPPER(stts_chng_action_name) = 'УДАЛИТЬ'
)
SELECT
    -- Поля удаления --
    s.incdnt_status_name_at_action, s.incdnt_status_code,
    s.stts_chng_action_code, s.stts_chng_action_name,
    s.stts_chng_comment_txt,
    s.stts_chng_action_dttm, s.stts_chng_user_num,
    -- Инцидент --
    m.incdnt_id, m.incdnt_sid,
    m.incdnt_status_name, m.incdnt_autoreg_flag,
    m.incdnt_detection_person_name, m.incdnt_source_name,
    m.src_type_lvl_1_name, m.src_type_lvl_2_name,
    m.incdnt_type_lvl_1_name, m.incdnt_type_lvl_2_name,
    m.incdnt_detection_dt, m.incdnt_start_dt, m.incdnt_entry_dt,
    m.incdnt_first_validated_dttm, m.incdnt_last_validate_dttm,
    m.risk_profile_id, m.risk_profile_name,
    m.incdnt_client_type_name, m.incdnt_mistake_cnt,
    m.incdnt_appl_num, m.incdnt_agr_num, m.incdnt_agr_sid,
    regexp_replace(m.incdnt_summary_descr_txt, '[\u200b-\x02\x08\x0b]', '') AS incdnt_summary_descr_txt,
    regexp_replace(m.incdnt_full_descr_txt,    '[\u200b-\x02\x08\x0b]', '') AS incdnt_full_descr_txt,
    m.org_struct_id,
    m.org_struct_lvl_2_name, m.org_struct_lvl_3_name, m.org_struct_lvl_4_name,
    m.org_struct_lvl_5_name, m.org_struct_lvl_6_name, m.org_struct_lvl_7_name,
    m.org_struct_lvl_8_name, m.org_struct_lvl_9_name, m.org_struct_lvl_10_name,
    m.funct_block_id,
    m.funct_block_lvl_2_name, m.funct_block_lvl_3_name, m.funct_block_lvl_4_name,
    m.process_lvl_1_name, m.process_lvl_2_name, m.process_lvl_3_name, m.process_lvl_4_name,
    m.clntpth_lvl_4_name,
    m.incdnt_security_risk_flag, m.incdnt_infrmtn_sys_risk_flag,
    m.incdnt_behavior_risk_flag, m.incdnt_model_risk_flag,
    -- Финансы (готовые из main) --
    m.incdnt_sum,
    m.incdnt_drct_dmg_sum, m.incdnt_drct_dmg_cred_rub_amt, m.incdnt_drct_dmg_noncred_rub_amt,
    m.incdnt_indrct_dmg_sum, m.incdnt_indrct_dmg_cred_rub_amt, m.incdnt_indrct_dmg_noncred_rub_amt,
    m.incdnt_unrlzd_dmg_sum, m.incdnt_unrlzd_dmg_cred_rub_amt, m.incdnt_unrlzd_dmg_noncred_rub_amt,
    m.incdnt_thrd_prt_sum, m.incdnt_thrd_prt_cred_rub_amt, m.incdnt_thrd_prt_noncred_rub_amt,
    m.incdnt_gain_sum, m.incdnt_gain_cred_rub_amt, m.incdnt_gain_noncred_rub_amt,
    m.recovery_rub_amt_aggr
FROM main_filtered m
LEFT JOIN status_filtered s
    ON m.incdnt_id = s.st_incdnt_id
ORDER BY m.incdnt_entry_dt, m.incdnt_sid, s.stts_chng_action_dttm;
```

---

## 8. Структура выходного отчёта

**Гранулярность:** 1 строка = 1 удаление (либо 1 инцидент без записи в журнале при LEFT JOIN).
**Сортировка:** дата ввода → SID → дата удаления.

### 8.1. Колонки (с переводом)

| # | Поле | Excel-наименование | Группа |
|:-:|------|---------------------|--------|
| **1** | `incdnt_status_name_at_action` | **Статус инцидента на момент действия** | удаление |
| 2 | `incdnt_status_code` | Код статуса на момент действия | удаление |
| 3 | `stts_chng_action_code` | Код действия (всегда `delete` / `toDelete` / `approveDeletion`) | удаление |
| 4 | `stts_chng_action_name` | Действие пользователя (`Удалить`) | удаление |
| 5 | `stts_chng_comment_txt` | **Комментарий / причина удаления** | удаление |
| 6 | `stts_chng_action_dttm` | **Дата и время удаления** | удаление |
| 7 | `stts_chng_user_num` | **Кем удалён (табельный)** | удаление |
| 8 | `incdnt_id` | Идентификационный ключ ИОР | main |
| 9 | `incdnt_sid` | Идентификатор события | main |
| 10 | `incdnt_status_name` | Текущий статус (всегда `Удалён`) | main |
| 11-67 | (полный набор атрибутов ИОР: даты, ЦПР, тип, оргструктура, процесс, флаги риска) | | main |
| 68-83 | Финансовые агрегаты (`incdnt_*_dmg_*`, `recovery_rub_amt_aggr`) | | main |

Полный список см. в [`vozmeshenie_ior.md` §8.1](vozmeshenie_ior.md#81-колонки-выходного-excel-полный-список-с-переводами) – отличие только в первых 7 колонках выше и блоке финансовых агрегатов.

---

## 9. Семантика ключевых полей

### 9.1. `incdnt_status_name_at_action`

Статус инцидента **в момент, когда его удаляли**. Например, если ИОР был утверждён, а потом удалён – здесь будет `Утвержден`, а в `incdnt_status_name` (текущий) – `Удалён`.

Возможные значения: 14 статусов из истории (`Анализ`, `Арбитраж изменений`, `Арбитраж удаления`, `Валидация`, `Доработка`, `Исследование`, `Исследование РМ`, `Исследование РМ ЦА`, `Подтверждение удаления`, `Профильная экспертиза`, `Утвержден`, `Утверждение РМ`, `Черновик`).

**Важно для УВА:** если `incdnt_status_name_at_action = 'Утвержден'` – это значит **удалили после утверждения**, что требует особого внимания.

### 9.2. `stts_chng_action_code` (в этом отчёте)

Из 25 возможных кодов в отчёт попадают связанные с удалением:
- `delete` – обычное удаление
- `toDelete` – отправка на удаление
- `approveDeletion` / `approveDelete` – подтверждение удаления
- `sendForDeletion` – отправка на удаление
- `confirmDeletion` – подтверждение
- `rejectDeletion` – отклонение удаления (не должен попасть, но возможно если статус совпадает)

### 9.3. `stts_chng_comment_txt`

Текстовое поле, заполняется пользователем при удалении.
Типичные значения: «дубль», «ошибка регистрации», «не является ОР», «отнесён к другому риску», «недостаточно данных», «по запросу <ФИО>».

**Качество поля:** часто очень кратко или пусто (50-150 символов в среднем).

### 9.4. `stts_chng_user_num`

Табельный номер пользователя, выполнившего удаление. Таблицы `d6_base_of_knowledge_empl` **НЕ существует** – реальная таблица сотрудников `d6_base_of_knowledge_employee` имеет только колонки
`aggregateroot_id` и `value_` (колонки `fio` нет). Ключ связи проверить на данных:
```sql
LEFT JOIN arnsdpsbx_t_team_sva_oarb_4.d6_base_of_knowledge_employee empl
    ON stts_chng_user_num = empl.value_  -- ⚠ сверить на данных: value_ vs aggregateroot_id
-- → empl.value_ содержит данные сотрудника (ФИО/табельный)
```

⚠️ **LLM не должен выводить табельные номера в чат** – только ФИО (если делается дополнительный JOIN) или маскированный вариант.

### 9.5. Финансовые поля в отчёте по удалениям

Зачем они здесь? – Контроль «нелегитимных» удалений. **Если удалили крупный ИОР** (с большой `incdnt_sum` или `recovery_rub_amt_aggr`) – это маркер для проверки УВА.

---

## 10. Предупреждения о данных

1. **Период фильтрует дату ввода, не дату удаления.** ИОР может быть введён в январе и удалён в марте – попадёт в отчёт за январь.
2. **Один инцидент → несколько строк** если у него несколько действий «Удалить» в журнале (редко, но возможно при «Удалить → Восстановить → Удалить»).
3. **`stts_chng_comment_txt`** может быть пустым (~0.47% записей в журнале).
4. **Финансы заполнены ~2.26%** (как обычно в main).
5. **Префиксы оргструктуры** фильтруют только ПАО Сбербанк – ДЗО (АО Сбербанк Лизинг, КИБ, Центр программы лояльности) исключаются.
6. **Регистр статуса** `'УДАЛЁН'` – проверять через `UPPER(incdnt_status_name) = 'УДАЛЁН'` (буква `ё`, не `е`!).
7. **Регистр действия** `'УДАЛИТЬ'` – тоже через UPPER.

---

## 11. Шаблон ответа LLM

```
✅ Подготовлен отчёт «Удалённые ИОР» за период {begin} – {end}.

📊 Краткая статистика:
• Удалено инцидентов: {N_distinct}
• Записей в журнале удалений: {N_total}
• Период: по дате ввода ИОР {begin}–{end}
• Период удалений: с {min(stts_chng_action_dttm)} по {max(...)}

🚩 Контрольные точки (требуют внимания УВА):
• Удалений после утверждения (status_at_action='Утвержден'): {N_after_validation}
• Удалений крупных ИОР (incdnt_sum > 1М ₽): {N_big}
• Без указания причины (comment пуст/<10 симв): {N_no_reason}

👤 Топ-5 пользователей по числу удалений:
1. {user_1} ({fio_1}): {n_1} удалений
2. ...

📁 Файл: Удалённые ИОР за период {begin} – {end}.xlsx

ℹ️ Учитывать:
• Фильтр по дате ввода (incdnt_entry_dt), а не по дате удаления
• Только ПАО Сбербанк (префиксы SBR_/EXT_/GRC_/MON_/BPS_)

💡 Связанное:
• Изменение других статусов (утверждение, отклонение) – отчёт по incident_stts_chng (адаптировать)
• Полное досье удалённого ИОР → /report_period_specific_ior?sid=EVE-XXX
```

---

## 12. Decision Tree

```
Запрос про статусы / журнал / удаления?
├─ Удаления конкретно?                          → ✅ ЭТОТ СКРИПТ
├─ Контроль / аудит удалений?                   → ✅ ЭТОТ СКРИПТ
├─ Все ИОР за период (включая удалённые)?       → ior_period_pao_sberbank
├─ Отклонённые ИОР?                             → ior_period_pao_sberbank + WHERE status='Отклонён'
├─ История утверждений?                         → нужно адаптировать deleted_ior, сменив фильтр на 'Утвердить'
└─ Конкретный SID – что с ним сейчас?           → report_period_specific_ior
```

---

## 13. Пограничные случаи

| Случай | Поведение |
|--------|-----------|
| Пустой результат | «За период {begin}–{end} удалённых ИОР не зарегистрировано.» |
| Запрос «удалённые за январь» (хочет по дате удаления) | LLM уточняет: «По дате ввода или дате удаления?». Если по дате удаления – расширить период по `incdnt_entry_dt` (например, год) и дополнительно фильтровать по `stts_chng_action_dttm`. |
| Запрос «кто чаще всех удаляет» | Применить + группировка по `stts_chng_user_num` + JOIN с `employee` для ФИО |
| «Удалённые ИОР с большими потерями» | Применить + `WHERE incdnt_sum > X` (но помнить про 2.26% заполненности!) |
| «Удалённые ИОР с возмещениями» | + `recovery_rub_amt_aggr > 0` |
| «Подозрительные удаления» | Эвристика: удаления после первой валидации (`incdnt_first_validated_dttm IS NOT NULL`) + крупные суммы + короткие комментарии |

---

## 14. Известные проблемы

1. **`incdnt_status_name = 'Удалён'`** – может проставляться вручную или автоматически после `stts_chng_action_name = 'Удалить'`. В единичных случаях статус может быть рассинхронизирован.
LEFT JOIN страхует от потери данных.
2. **Регистр «ё»/«е»** – проверять `'УДАЛЁН'` через `UPPER()`.
3. **Префиксы ORG_PREFIXES** – захардкожены в скрипте. Если нужно включить ДЗО – расширить список.

---

## 15. Примеры flow

### Пример 1: «Кто удалил ИОР EVE-6967014?»

LLM: применить с фильтром `incdnt_sid='EVE-6967014'`, в ответе:
> ИОР EVE-6967014 был удалён {date} пользователем (табельный {masked}, ФИО {ФИО}). Причина: «{comment}». Статус на момент удаления: «{status_at_action}».

### Пример 2: «Контроль удалений за квартал»

Применить за Q1 2025, в шаблоне ответа выделить **подозрительные паттерны** (удаления после утверждения, без причины, крупные суммы).

### Пример 3: «Удаления в Северо-Западном банке за месяц»

Применить + `org_struct_lvl_3_name LIKE '%Северо-Западный%'`, в ответе – список с табельными и причинами.

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
    "org_prefixes": {
      "type": "array",
      "items": {"type": "string"},
      "default": ["SBR_", "EXT_", "GRC_", "MON_", "BPS_"]
    }
  }
}
```

### Output
```json
{
  "format": "xlsx",
  "sheet_name": "Отчет_ОпРиски",
  "file_name_template": "Удалённые ИОР за период {begin} – {end}.xlsx",
  "row_granularity": "1 row = 1 deletion event (or 1 deleted incident if no journal record)",
  "primary_key": ["incdnt_sid", "stts_chng_action_dttm"],
  "join_type": "LEFT (main → status)",
  "filters_applied": [
    "incdnt_status_name = 'Удалён'",
    "stts_chng_action_name = 'Удалить'",
    "org_struct_id prefix in ORG_PREFIXES"
  ],
  "columns_count": 83,
  "sort_order": ["incdnt_entry_dt", "incdnt_sid", "stts_chng_action_dttm"]
}
```
