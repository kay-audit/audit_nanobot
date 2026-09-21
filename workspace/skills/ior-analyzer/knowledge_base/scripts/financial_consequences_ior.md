# Скрипт «Финансовые последствия по ИОР»

> **Контракт аналитики runtime:** Excel сохраняет все `fin_impact_sid`. Верхняя сводка считается по полной detail-выгрузке; статусная сводка — по уникальным `incdnt_sid`; разделы 1–4 — только по утверждённым ИОР. Денежный источник истины — `fin_impact_rub_amt`. Recovery и Net Loss в предметной аналитике не используются.

> **Notebook:** [`новая_схема_БЗ_v2/financial_consequences_ior_v2.ipynb`](../новая_схема_БЗ_v2/financial_consequences_ior_v2.ipynb)
> **Skill ID:** `financial_consequences_ior_v2`
> **Категория:** детализация по сущностям ИОР (финансовые последствия)

> ✅ **Схема fin_impact (сверено с реальной витриной, скан 2026-06):**
> - `d6_base_of_knowledge_incident_fin_impact` содержит **отдельные** колонки
>   `incdnt_id` (FK к `ior.incdnt_id`) и `fin_impact_id` (собственный PK строки).
>   Связь с инцидентом — по `incdnt_id` (`ior.incdnt_id = fi.incdnt_id`),
>   **НЕ** по `fin_impact_id`.
> - `fin_impact_type_name` **существует** (5 типов: «Прямая потеря», «Косвенная
>   потеря», «Нереализовавшаяся потеря», «Потеря третьих лиц», «Прибыль») —
>   используется для фильтра «прямые/косвенные потери». Детальный классификатор —
>   `fin_impact_kind_name`.
> - `fi_busn_area_id`, `fi_org_struct_id` существуют только в таблице финансовых последствий.
> - Колонки `fin_impact_subkind_name` в витрине **НЕТ** — не использовать в SELECT.

---

## 1. Краткое описание для LLM-маршрутизатора

Формирует **список всех финансовых последствий** по инцидентам операционного риска за период.
**Один ряд отчёта = одно финансовое последствие** (по `fin_impact_sid`).
У одного инцидента может быть **несколько фин. последствий разного типа** (Прямая потеря + Косвенная + Третьих лиц + Прибыль) — каждое отдельной строкой со всеми атрибутами родительского ИОР.

**Когда LLM выбирает этот скрипт:** запрос про **финансовые потери, ущерб, прямые/косвенные/нереализовавшиеся потери, прибыль, потери третьих лиц, разбивку по типам/видам** в контексте операционного риска.

---

## 2. Триггеры — когда применять

### 2.1. Прямые триггеры

| Триггер пользователя | Что распознать |
|-----------------------|-----------------|
| «финансовые последствия по ИОР» / «фин последствия» | основная тема |
| «потери по ИОР за период» | период + потери |
| «ущерб по операционному риску» | синоним потерь |
| «прямые потери» / «прямой ущерб» | фильтр `fin_impact_type_name = 'Прямая потеря'` |
| «косвенные потери» / «косвенный ущерб» | фильтр `'Косвенная потеря'` |
| «нереализовавшиеся потери» / «потенциальные потери» / «не случились» | фильтр `'Нереализовавшаяся потеря'` |
| «потери третьих лиц» / «потери клиентов» | фильтр `'Потеря третьих лиц'` |
| «прибыль по ИОР» / «положительный результат» | фильтр `'Прибыль'` |
| «виды потерь» / «структура потерь» | разбивка по `fin_impact_kind_name` |
| «потери в рублях / в валюте / в EUR / в USD» | фильтр по `fin_impact_crncy_code` |
| «потери от хищения» | по `fin_impact_kind_name LIKE '%хищение%'` |
| «потери от выплат по решению суда» | по `fin_impact_kind_name LIKE '%суд%'` |
| «детализация потерь по инциденту EVE-...» | этот скрипт + фильтр по `incdnt_sid` |

### 2.2. Контекстные триггеры

| Триггер | Действие |
|---------|----------|
| «сколько потеряли по ИОР» | Применить + агрегировать `SUM(fin_impact_rub_amt)` |
| «структура убытков ОР» | Применить + группировка по `fin_impact_type_name` |
| «ИОР с потерями > X ₽» | Применить + post-фильтр в шаблоне ответа |
| «потери с признаком мониторинга» | + `fin_impact_monitoring_flag = 'Y'` |

### 2.3. Семантические признаки

Ключевые слова в запросе: **потеря, потери, ущерб, убыток, убытки, последствия, прибыль, gain, loss, damage, financial impact**.

---

## 3. Анти-триггеры — когда НЕ применять

| Если запрос про | Использовать |
|------------------|--------------|
| **Итоговые суммы** по инцидентам (без детализации) — «сколько прямых потерь за год» в виде одного числа | [`ior_period_pao_sberbank`](ior_period_pao_sberbank.md) (там `incdnt_drct_dmg_sum` готовое поле) |
| **Возмещения** | [`vozmeshenie_ior`](vozmeshenie_ior.md) |
| **Нефинансовые/качественные потери** (репутация, регулятор) | [`ior_nonfinancial_consequences`](ior_nonfinancial_consequences.md) |
| Конкретный SID — полное досье | [`report_period_specific_ior`](report_period_specific_ior.md) |
| Кредитный/некредитный разрез (`*_cred_rub_amt`) | В этом отчёте такого поля нет — использовать `ior_period_pao_sberbank` (там есть готовые `_cred/_noncred` разбивки) |

---

## 4. Извлечение параметров

### 4.1. Контракт

| Параметр | Тип | Обязательность | Дефолт | Формат |
|----------|-----|:---------------:|--------|--------|
| `incdnt_entry_dt_begin` | DATE | да | – | `YYYY-MM-DD` |
| `incdnt_entry_dt_end` | DATE | да | – | `YYYY-MM-DD` |

### 4.2. Правила парсинга периода

См. [`vozmeshenie_ior.md` §4.2](vozmeshenie_ior.md#42-правила-парсинга-периода-из-текста-для-llm) — правила универсальные.

### 4.3. Дополнительные фильтры (опционально, в post-обработке)

Если запрос содержит конкретный тип/вид потери — добавить в `WHERE`:
- `fin_impact_type_name = '<значение>'` — для 5 типов
- `fin_impact_kind_name LIKE '%<keyword>%'` — для 33 видов
- `fin_impact_crncy_code = '<ISO>'` — для конкретной валюты
- `fin_impact_monitoring_flag = 'Y'` — для тех что на мониторинге

---

## 5. Источники данных и связки

| # | Таблица | Гранулярность |
|:-:|---|---|
| 1 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior` | 1:1 (инцидент) |
| 2 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact` | 1:N (фин. последствия) |

**Связка:** `ior.incdnt_id = incident_fin_impact.incdnt_id` (INNER JOIN — отсекает инциденты без фин. последствий).

---

## 6. Алгоритм работы

1. Фильтрация main по периоду `incdnt_entry_dt`.
2. Выгрузка `incident_fin_impact` (18 полей; `incdnt_id` — FK к инциденту, алиасится в `fi_incdnt_id` для join, чтобы не конфликтовать с `ior.incdnt_id`).
3. INNER JOIN main × fin_impact по `incdnt_id`.
4. Дедупликация по `(incdnt_sid, fin_impact_sid)`.
5. Сортировка по `incdnt_entry_dt`, `incdnt_sid`, `fin_impact_sid`.

---

## 7. SQL-эквивалент

```sql
SELECT
    -- — Идентификация инцидента —
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
    regexp_replace(ior.incdnt_summary_descr_txt, '[\x02\x08\x0b]', '') AS incdnt_summary_descr_txt,
    regexp_replace(ior.incdnt_full_descr_txt,    '[\x02\x08\x0b]', '') AS incdnt_full_descr_txt,
    -- — Оргструктура / Функ. блок / Процесс —
    ior.org_struct_id, ior.org_struct_lvl_2_name, ior.org_struct_lvl_3_name,
    ior.org_struct_lvl_4_name, ior.org_struct_lvl_5_name, ior.org_struct_lvl_6_name,
    ior.org_struct_lvl_7_name, ior.org_struct_lvl_8_name, ior.org_struct_lvl_9_name,
    ior.org_struct_lvl_10_name,
    ior.funct_block_id, ior.funct_block_lvl_2_name, ior.funct_block_lvl_3_name, ior.funct_block_lvl_4_name,
    ior.process_lvl_1_name, ior.process_lvl_2_name, ior.process_lvl_3_name, ior.process_lvl_4_name,
    ior.clntpth_lvl_4_name,
    -- — Флаги риска (Y/N) —
    ior.incdnt_security_risk_flag, ior.incdnt_infrmtn_sys_risk_flag,
    ior.incdnt_behavior_risk_flag, ior.incdnt_model_risk_flag,
    -- — Финансовое последствие (детали) —
    fi.fin_impact_id, fi.fin_impact_sid,
    fi.fin_impact_type_name,        -- тип потери (5 значений) — фильтр «прямые/косвенные»
    fi.fin_impact_kind_name,        -- детальный классификатор
    fi.fin_impact_monitoring_flag,
    fi.fin_impact_crncy_code, fi.fin_impact_local_crncy_code,
    fi.fin_impact_detection_dt, fi.fin_impact_creation_dttm, fi.fin_impact_reg_dt,
    fi.fin_impact_account_num, fi.fin_impact_docum_num,
    fi.fi_busn_area_id, fi.fi_org_struct_id,
    fi.fin_impact_ccy_amt, fi.fin_impact_local_ccy_amt, fi.fin_impact_rub_amt
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior ior
INNER JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact fi
    ON ior.incdnt_id = fi.incdnt_id  -- связь по incdnt_id (FK); fin_impact_id — PK строки
WHERE ior.incdnt_entry_dt >= TO_TIMESTAMP('{begin}', 'yyyy-MM-dd')
  AND ior.incdnt_entry_dt <  DATE_ADD(TO_TIMESTAMP('{end}', 'yyyy-MM-dd'), 1)
ORDER BY ior.incdnt_entry_dt, ior.incdnt_sid, fi.fin_impact_sid;
```

---

## 8. Структура выходного отчёта

**Гранулярность:** 1 строка = 1 фин. последствие.
**Дедупликация:** `(incdnt_sid, fin_impact_sid)`.
**Сортировка:** `incdnt_entry_dt`, `incdnt_sid`, `fin_impact_sid`.

### 8.1. Колонки (полный список с переводами)

| # | Поле | Excel-наименование | Источник |
|:-:|------|---------------------|----------|
| 1-50 | (как в [`vozmeshenie_ior.md`](vozmeshenie_ior.md#81-колонки-выходного-excel-полный-список-с-переводами), позиции 1-50 — атрибуты ИОР) | main |
| **51** | `fin_impact_id` | Идентификатор фин. последствия (ключ) | fin_impact |
| **52** | `fin_impact_sid` | Идентификатор фин. последствия (бизнес) | fin_impact |
| 53 | `fin_impact_kind_name` | Вид финансового последствия (основной классификатор после удаления `type_name`) | fin_impact |
| – | ~~`fin_impact_subkind_name`~~ | _Отсутствует в реальной витрине — не использовать_ | – |
| 55 | `fin_impact_monitoring_flag` | Требует мониторинга (Последствие) | fin_impact |
| 56 | `fin_impact_crncy_code` | Код валюты последствия | fin_impact |
| 57 | `fin_impact_local_crncy_code` | Код локальной валюты последствия | fin_impact |
| 58 | `fin_impact_detection_dt` | Дата обнаружения (Последствие) | fin_impact |
| 59 | `fin_impact_creation_dttm` | Дата создания (Последствие) | fin_impact |
| 60 | `fin_impact_reg_dt` | Дата регистрации в учёте | fin_impact |
| 61 | `fin_impact_account_num` | Аналитический счёт отражения в учёте | fin_impact |
| 62 | `fin_impact_docum_num` | Номер бухгалтерского документа | fin_impact |
| 63 | `fin_impact_ccy_amt` | Сумма последствия (в валюте) | fin_impact |
| 64 | `fin_impact_local_ccy_amt` | Сумма последствия (в локальной валюте) | fin_impact |
| 65 | **`fin_impact_rub_amt`** | **Сумма последствия (руб.)** | fin_impact |
| 17 | `fi_busn_area_id` | ID бизнес-области последствия (атрибуция) | fin_impact |
| 18 | `fi_org_struct_id` | ID оргструктуры последствия (атрибуция) | fin_impact |

---

## 9. Семантика ключевых полей результата

### 9.1. `fin_impact_type_name` — основной классификатор (5 типов)

Поле **существует** в `d6_base_of_knowledge_incident_fin_impact`, заполнено ~100%.
5 значений. Использовать для фильтра/группировки «прямые/косвенные/
нереализовавшиеся потери».

Удобный шортката для СУММ по типам (без join к fin_impact) — готовые агрегаты
в **main**-таблице ИОР, на уровне инцидента:

| `fin_impact_type_name` | Смысл | Готовый агрегат-сумма в main |
|--------------------------|-------|--------------------------------|
| Прямая потеря | Реализованный убыток | `ior.incdnt_drct_dmg_sum` |
| Косвенная потеря | Пени, штрафы, проценты | `ior.incdnt_indrct_dmg_sum` |
| Нереализовавшаяся потеря | Не случившийся убыток | `ior.incdnt_unrlzd_dmg_sum` |
| Потеря третьих лиц | Ущерб клиенту/контрагенту | `ior.incdnt_thrd_prt_sum` |
| Прибыль | Восстановление, бонус | `ior.incdnt_gain_sum` |

### 9.2. `fin_impact_kind_name` — 33 вида (что именно случилось)

Топ-категории (полный список — в БЗ через `SELECT DISTINCT`):
- **Расходы, связанные с возвратом кредитных средств и обеспечением**
- **Выплаты и компенсации по решению суда**
- **Денежные выплаты клиентам и контрагентам в целях компенсации**
- **Денежные выплаты сотрудникам в целях компенсации убытков**
- **Досрочное списание активов** (выбытие, потеря, уничтожение)
- **Начисление амортизационных расходов**
- **Начисление резервов некредитного характера** по предъявленным требованиям
- **Недополученные запланированные доходы**
- **Недополученный доход от запланированной сделки**
- **Обесценение стоимости кредита** в результате начисления резерва
- **Отрицательная переоценка стоимости торгового портфеля**
- **Повышение стоимости заимствования**
- **Потери в виде уплаченных комиссий** по проведению ошибочных операций
- **Потери в размере ошибочного платежа**
- **Потери от ошибочных платежей**
- **Потери, связанные с поиском возможности возврата** ошибочного платежа
- **Потеря активов в результате хищения**
- **Потеря наличных денежных средств** в результате хищения
- **Прочие потери, не отраженные на счетах расходов**
- **Прочие потери, отраженные на счетах расходов**
- (и ещё 13)

### 9.3. Связь `type_name` ≈ `kind_name`

`type_name` — это **категория** (5 значений), а `kind_name` — это **подкатегория/конкретный вид** (33 значения). Один вид всегда принадлежит одному типу.

Пример:
- `type_name='Прямая потеря'` + `kind_name='Потеря наличных денежных средств в результате хищения'` → реализованная прямая потеря от хищения.
- `type_name='Косвенная потеря'` + `kind_name='Повышение стоимости заимствования'` → косвенная потеря.
- `type_name='Прибыль'` + `kind_name='Прочие потери, отраженные на счетах расходов'` → восстановление.

### 9.4. Валюты — `fin_impact_crncy_code`

26 значений: `RUB`, `USD`, `EUR`, `BYN`, `BYR`, `KZT`, `UAH`, `CHF`, `GBP`, `CNY`, `HKD`, `HUF`, `INR`, `JPY`, `AED`, `AUD`, `CAD`, `DKK`, `NOK`, `PLN`, `SEK`, `SGD`, `TRY`, `ZAR`, ...

- `fin_impact_ccy_amt` — сумма в этой валюте.
- `fin_impact_rub_amt` — переведённая в рубли (для агрегатов).
- `fin_impact_local_ccy_amt` + `fin_impact_local_crncy_code` — для ДЗО, локальная валюта.

### 9.5. `fin_impact_monitoring_flag`

- `Y` — последствие на мониторинге (требует контроля исполнения возмещения / восстановления).
- `N` — закрыто.
- `NULL` — не указано.

### 9.6. Даты

- `fin_impact_detection_dt` — дата обнаружения **этого конкретного последствия** (не самого инцидента).
- `fin_impact_creation_dttm` — техническая дата создания записи в системе.
- `fin_impact_reg_dt` — дата отражения в бухгалтерском учёте (ключевая для отчётности).

### 9.7. `fi_busn_area_id` и `fi_org_struct_id`

Используются для **точной атрибуции последствия к подразделению/направлению деятельности**.
Может отличаться от `ior.org_struct_id` — последствие реализовано в **другом подразделении**, чем сам инцидент.

---

## 10. Предупреждения о данных

1. **Не все ИОР имеют фин. последствия.** Только инциденты, где есть запись в `incident_fin_impact`.
2. **Кратность.** Считать «количество ИОР с фин. потерями» = `COUNT(DISTINCT incdnt_id)`, не `COUNT(*)`. На 980 тыс. инцидентов — 4.4М фин. последствий, то есть **в среднем 4-5 последствий на инцидент**.
3. `fin_impact_kind_name` заполнено в **73%**. Может быть NULL.
4. `fin_impact_subkind_name` — колонки в реальной витрине **НЕТ**, в SELECT не использовать.
5. `fin_impact_ctgry_risk_code` ОТСУТСТВУЕТ в новой БЗ → флаги `fin_credit_risk_flag` / `fin_market_risk_flag` (которые были в v1) недоступны. Для кредитной/некредитной разбивки использовать готовые поля `incdnt_drct_dmg_cred_rub_amt`, `incdnt_drct_dmg_noncred_rub_amt` и т.п. (но они на уровне инцидента, не последствия).
6. `fin_impact_local_ccy_amt` ~18% — заполнено только для ДЗО (BYN-операции).
7. **Номера документов и счетов** (`fin_impact_account_num`, `fin_impact_docum_num`) — конф. данные. **LLM не должен выводить их в чат** без маскирования.

---

## 11. Шаблон ответа LLM пользователю

```
✅ Подготовлен отчёт «Финансовые последствия по ИОР» за период {begin} — {end}.

📊 Краткая статистика:
- Строк: {N} (фин. последствий)
- Уникальных инцидентов: {N_distinct}
- Суммарные потери: {SUM(fin_impact_rub_amt) где type IN ('Прямая потеря','Косвенная потеря','Потеря третьих лиц'):,.2f} ₽
- Прибыль (отрицательный знак к ущербу): {SUM где type='Прибыль':,.2f} ₽
- Нереализовавшиеся (потенциал): {SUM где type='Нереализовавшаяся потеря':,.2f} ₽

🏷️ Разбивка по типам последствий:
1. Прямая потеря: {amt_1:,.2f} ₽ ({pct_1}%)
2. Косвенная: {amt_2:,.2f} ₽
3. Третьих лиц: {amt_3:,.2f} ₽
4. Нереализовавшаяся: {amt_4:,.2f} ₽
5. Прибыль: {amt_5:,.2f} ₽

🏷️ Топ-3 вида потерь (kind_name) по сумме:
1. {kind_1}: {amt:,.2f} ₽
2. {kind_2}: ...

📁 Файл: Финансовые последствия ИОР {begin} — {end}.xlsx

ℹ️ Учитывать:
- Один инцидент может иметь несколько последствий (отдельной строкой каждое)
- Кредитный/некредитный разрез — в готовых полях основного отчёта (см. /ior_period_pao_sberbank)

💡 Что ещё можно посмотреть:
- Возмещения по этим же ИОР → /vozmeshenie_ior
- Нефинансовые последствия → /ior_nonfinancial_consequences
- Полное досье конкретного ИОР → /report_period_specific_ior?sid=EVE-XXX
```

---

## 12. Decision Tree

```
Запрос про деньги / суммы в контексте ИОР?
├─ Структура потерь по типам/видам?     → ✅ ЭТОТ СКРИПТ
├─ Конкретный тип (прямые/третьих лиц)? → ✅ ЭТОТ СКРИПТ + WHERE по type_name
├─ Только итоговая цифра?               → этот скрипт + агрегация в ответе (без Excel)
├─ Кредитный/некредитный разрез?        → ior_period_pao_sberbank (там готовые поля)
├─ Возмещения/компенсации?              → vozmeshenie_ior
└─ Нефинансовые (репутация/регулятор)?  → ior_nonfinancial_consequences
```

---

## 13. Пограничные случаи

| Случай | Поведение |
|--------|-----------|
| Пустой результат | «За период {begin}–{end} фин. последствий не зарегистрировано.» |
| Запрос только «прямые потери» | Применить + `WHERE fin_impact_type_name = 'Прямая потеря'` |
| Запрос «потери третьих лиц = потери клиентов» | Применить + `type_name='Потеря третьих лиц'` (нужно объяснить пользователю что это синонимы) |
| «Потери только по СЗБ» | Применить + `org_struct_lvl_3_name LIKE '%Северо-Западный%'` |
| «Потери в EUR» | + `fin_impact_crncy_code = 'EUR'` |
| «Потери > 1 млн ₽ по одному ИОР» | Post-фильтр в Excel: `HAVING SUM(fin_impact_rub_amt) > 1000000` через `groupBy('incdnt_id')` |
| Запрос «потери от хищения» | + `fin_impact_kind_name LIKE '%хищени%'` |

---

## 14. Известные проблемы и ограничения

1. **Нет `fin_impact_ctgry_risk_code`** в новой БЗ. Если нужен `creditRisk/marketRisk` на уровне отдельного последствия — данные недоступны.
2. **`fin_impact_local_crncy_code`** — раньше содержала типы потерь (баг), теперь исправлено (валюты).
3. **Дубль `incident_fin_impact_20052026`** удалён Пономаренко — остался только канонический `incident_fin_impact`.
4. **Связь с возмещениями** — нет прямого поля «возмещение по последствию». Только на уровне инцидента (`recovery_rub_amt_aggr` в main).

---

## 15. Примеры полных flow

### Пример 1: «Потери третьих лиц за 2025 по СЗБ»

**Параметры:** `_begin='2025-01-01'`, `_end='2025-12-31'`, доп. фильтры: `fin_impact_type_name='Потеря третьих лиц'`, `org_struct_lvl_3_name LIKE '%Северо-Западный%'`.
**Ответ:** общая сумма потерь третьих лиц + Excel.

### Пример 2: «Какие виды прямых потерь были в Q1 2025?»

Применить с `fin_impact_type_name='Прямая потеря'` за период `2025-01-01 – 2025-03-31`, в ответе сделать **разбивку по `fin_impact_kind_name`**.

### Пример 3: «Сколько прямых кредитных потерь за 2025?»

⚠️ Кредитный разрез на уровне последствия НЕДОСТУПЕН. LLM должен **перенаправить на `ior_period_pao_sberbank`** (там есть `incdnt_drct_dmg_cred_rub_amt` готовое) и объяснить, что разбивка cred/noncred делается на уровне инцидента, а не последствия.

---

## 16. Контракт ввода-вывода

### Input
```json
{
  "type": "object",
  "required": ["incdnt_entry_dt_begin", "incdnt_entry_dt_end"],
  "properties": {
    "incdnt_entry_dt_begin": {"type": "string", "format": "date"},
    "incdnt_entry_dt_end":   {"type": "string", "format": "date"},
    "filters": {
      "type": "object",
      "properties": {
        "_loss_type_hint": {"type": "string", "enum": ["прямая","косвенная","нереализовавшаяся","третьих_лиц","прибыль"], "description": "Тип потерь в формулировке запроса — поле type_name удалено из БЗ, используй агрегаты incdnt_*_sum в main"},
        "fin_impact_kind_name": {"type": "string", "description": "Актуальный классификатор после удаления type_name"},
        "fin_impact_crncy_code": {"type": "string"},
        "fin_impact_monitoring_flag": {"type": "string", "enum": ["Y","N"]}
      }
    }
  }
}
```

### Output
```json
{
  "format": "xlsx",
  "sheet_name": "Отчет_ОпРиски",
  "file_name_template": "Финансовые последствия ИОР {begin} – {end}.xlsx",
  "row_granularity": "1 row = 1 financial impact (fin_impact_sid)",
  "primary_key": ["incdnt_sid", "fin_impact_sid"],
  "columns_count": 67,
  "sort_order": ["incdnt_entry_dt ASC", "incdnt_sid ASC", "fin_impact_sid ASC"]
}
```
