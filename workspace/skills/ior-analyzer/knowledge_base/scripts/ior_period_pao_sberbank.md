# Скрипт «ИОР за период по ПАО Сбербанк»

> **Контракт аналитики runtime:** верхняя сводка отражает полную выборку, статусная сводка считается по уникальным ИОР, а разделы 1–4 строятся только по утверждённым ИОР. NULL в слабо заполненных финансовых полях не трактуется как доказанный ноль.

> **Notebook:** [`новая_схема_БЗ_v2/ior_period_pao_sberbank_v2.ipynb`](../новая_схема_БЗ_v2/ior_period_pao_sberbank_v2.ipynb)
> **Skill ID:** `ior_period_pao_sberbank_v2`
> **Категория:** **главный сводный отчёт** (универсальный)

---

## 1. Краткое описание для LLM-маршрутизатора

**Это главный отчёт-агрегатор.** Один ряд = один инцидент ПАО Сбербанк за период. Содержит **все 67 полей** основной таблицы БЗ: атрибуты ИОР, классификация, оргструктура, процесс, флаги риска, **готовые финансовые агрегаты** (прямые/косвенные/нереализ./третьих лиц/прибыль с разбивкой cred/noncred) + итоговое возмещение.

**Этот скрипт — дефолтный выбор**, если запрос пользователя:
- Не требует **детализации 1:N** (последствий, возмещений, нефин-импактов)
- Не про удаления
- Не про конкретный SID
- Просит «выгрузить ИОР за период», «отчёт по ИОР», «список инцидентов», «статистику ИОР»

**Когда LLM выбирает этот скрипт:** общие запросы про ИОР, агрегированные показатели, фильтрация по любым атрибутам инцидента (статус, ТБ, блок, процесс, тип, источник, ЦПР, флаги).

---

## 2. Триггеры

### 2.1. Прямые триггеры

| Триггер | Применение |
|---------|------------|
| «выгрузи ИОР за {период}» | дефолтный |
| «отчёт по ИОР» / «сводный отчёт ИОР» | дефолтный |
| «список инцидентов за {период}» | дефолтный |
| «ИОР по продукту {X}» | + `process_lvl_4_name LIKE '%X%'` |
| «ИОР по подразделению / ТБ / блоку» | + `org_struct_lvl_3_name` или `funct_block_lvl_2_name` |
| «ИОР по типу события {X}» | + `incdnt_type_lvl_1/2_name` |
| «ИОР по источнику (УВА/обращение клиента/СР)» | + `incdnt_source_name` или `src_type_lvl_1/2_name` |
| «ИОР по природе риска (ЦПР)» | + `risk_profile_name` |
| «ИОР, связанные с ИБ-риском / поведением / ИС / моделью» | + `incdnt_*_risk_flag = 'Y'` |
| «авторегистрационные ИОР» | + `incdnt_autoreg_flag = 'Y'` |
| «ИОР клиентов-ФЛ/-ЮЛ» | + `incdnt_client_type_name` |
| «ИОР со статусом X» | + `incdnt_status_name` |
| «ИОР по продукту Ипотека с данными ОСЗ» | этот скрипт + JOIN с b2c_credit_fl (если LLM умеет) |
| «статистика ИОР за период» | этот скрипт + агрегации в шаблоне ответа |

### 2.2. Контекстные триггеры

Любой запрос про ИОР, который **НЕ требует** детализации по фин/возмещ/нефин/удалениям — идёт сюда.

### 2.3. Семантические признаки

Просто **«ИОР» + период** в запросе → этот скрипт по умолчанию, если нет более конкретного триггера.

---

## 3. Анти-триггеры (когда передать на другой скрипт)

| Если запрос требует | Используем |
|---------------------|------------|
| **Детализацию по операциям возмещения** (каждое отдельной строкой) | [`vozmeshenie_ior`](vozmeshenie_ior.md) |
| **Детализацию по каждому фин. последствию** (1:N) | [`financial_consequences_ior`](financial_consequences_ior.md) |
| **Качественные/нефинансовые потери** (репутация, регулятор) | [`ior_nonfinancial_consequences`](ior_nonfinancial_consequences.md) |
| **Только удалённые** ИОР с деталями журнала удалений | [`deleted_ior`](deleted_ior.md) |
| **Один конкретный SID** — полное досье | [`report_period_specific_ior`](report_period_specific_ior.md) |

---

## 4. Извлечение параметров

### 4.1. Контракт

| Параметр | Тип | Обязательность | Дефолт | Формат |
|----------|-----|:---------------:|--------|--------|
| `incdnt_entry_dt_begin` | DATE | да | – | `YYYY-MM-DD` |
| `incdnt_entry_dt_end` | DATE | да | – | `YYYY-MM-DD` |
| `ORG_PREFIXES` | LIST | нет | `['SBR_', 'EXT_', 'GRC_', 'MON_', 'BPS_']` | Префиксы ПАО Сбербанк |

### 4.2. Опциональные дополнительные фильтры

Все эти поля доступны в main и могут добавляться в WHERE в зависимости от запроса:

| Запрос упомянул | Добавить фильтр |
|-----------------|------------------|
| ТБ / Блок (например, СЗБ) | `org_struct_lvl_3_name LIKE '%X%'` |
| Функциональный блок | `funct_block_lvl_2_name LIKE '%X%'` |
| Продукт / процесс | `process_lvl_4_name LIKE '%X%'` |
| Тип события | `incdnt_type_lvl_1_name LIKE '%X%' OR lvl_2_name LIKE '%X%'` |
| Источник (УВА / обращение / СР) | `incdnt_source_name LIKE '%X%'` |
| ЦПР | `risk_profile_name LIKE '%X%'` |
| Тип клиента (ФЛ/ЮЛ) | `incdnt_client_type_name = '<value>'` |
| Статус | `incdnt_status_name = '<value>'` |
| Авторегистрация | `incdnt_autoreg_flag = 'Y'` |
| Связь с ИБ/ИС/поведение/модель | `incdnt_*_risk_flag = 'Y'` |
| Сумма потерь больше N | `incdnt_sum > N` (но помнить про 2.26% заполненности) |

---

## 5. Источники

| # | Таблица | Назначение |
|:-:|---|---|
| 1 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior` | **Единственный** источник (всё уже в main) |

> **Главное достоинство:** не нужно делать JOIN. Все справочные значения (`*_lvl_*_name`), флаги и финансовые агрегаты Пономаренко денормализовала в main.

---

## 6. Алгоритм

1. Фильтрация main:
   - `incdnt_entry_dt ∈ [begin, end+1)`
   - `SUBSTR(UPPER(org_struct_id), 1, 4) IN ORG_PREFIXES`
2. (Опционально) Дополнительные фильтры по запросу.
3. SELECT всех 67 полей.
4. Дедупликация по `incdnt_id`.
5. Сортировка `incdnt_entry_dt, incdnt_sid`.
6. Очистка описаний (`regexp_replace`).

---

## 7. SQL-эквивалент

```sql
SELECT
    -- — Идентификация и статус —
    incdnt_id, incdnt_sid,
    incdnt_status_name, incdnt_autoreg_flag,
    incdnt_detection_person_name, incdnt_source_name,
    src_type_lvl_1_name, src_type_lvl_2_name,
    incdnt_type_lvl_1_name, incdnt_type_lvl_2_name,
    -- — Даты —
    incdnt_detection_dt, incdnt_start_dt, incdnt_entry_dt,
    incdnt_first_validated_dttm, incdnt_last_validate_dttm,
    -- — ЦПР / клиент / связи —
    risk_profile_id, risk_profile_name,
    incdnt_client_type_name, incdnt_mistake_cnt,
    incdnt_appl_num, incdnt_agr_num, incdnt_agr_sid,
    regexp_replace(incdnt_summary_descr_txt, '[控制символы]', '') AS incdnt_summary_descr_txt,
    regexp_replace(incdnt_full_descr_txt,    '[控制символы]', '') AS incdnt_full_descr_txt,
    -- — Оргструктура —
    org_struct_id,
    org_struct_lvl_2_name, org_struct_lvl_3_name, org_struct_lvl_4_name,
    org_struct_lvl_5_name, org_struct_lvl_6_name, org_struct_lvl_7_name,
    org_struct_lvl_8_name, org_struct_lvl_9_name, org_struct_lvl_10_name,
    -- — Функ. блок —
    funct_block_id,
    funct_block_lvl_2_name, funct_block_lvl_3_name, funct_block_lvl_4_name,
    -- — Процесс / busn_area —
    process_lvl_1_name, process_lvl_2_name, process_lvl_3_name, process_lvl_4_name,
    clntpth_lvl_4_name,
    -- — Флаги риска (Y/N) —
    incdnt_security_risk_flag, incdnt_infrmtn_sys_risk_flag,
    incdnt_behavior_risk_flag, incdnt_model_risk_flag,
    -- — Финансовые агрегаты (готовые) —
    incdnt_sum,
    incdnt_drct_dmg_sum, incdnt_drct_dmg_cred_rub_amt, incdnt_drct_dmg_noncred_rub_amt,
    incdnt_indrct_dmg_sum, incdnt_indrct_dmg_cred_rub_amt, incdnt_indrct_dmg_noncred_rub_amt,
    incdnt_unrlzd_dmg_sum, incdnt_unrlzd_dmg_cred_rub_amt, incdnt_unrlzd_dmg_noncred_rub_amt,
    incdnt_thrd_prt_sum, incdnt_thrd_prt_cred_rub_amt, incdnt_thrd_prt_noncred_rub_amt,
    incdnt_gain_sum, incdnt_gain_cred_rub_amt, incdnt_gain_noncred_rub_amt,
    -- — Возмещение (агрегат) —
    recovery_rub_amt_aggr
FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior
WHERE incdnt_entry_dt >= TO_TIMESTAMP('{begin}', 'yyyy-MM-dd')
  AND incdnt_entry_dt <  DATE_ADD(TO_TIMESTAMP('{end}', 'yyyy-MM-dd'), 1)
  AND SUBSTR(UPPER(org_struct_id), 1, 4) IN ('SBR_', 'EXT_', 'GRC_', 'MON_', 'BPS_')
  -- {опциональные доп. фильтры}
ORDER BY incdnt_entry_dt, incdnt_sid;
```

> ⚠️ Примечание: в строках `regexp_replace` для `incdnt_summary_descr_txt` / `incdnt_full_descr_txt` в оригинале используется регулярное выражение для вычистки непечатаемых управляющих символов (включая коды вида `\x02`, `\x08`, `\x0b`). Точный набор символов в классе плохо читается на фото — при переносе в рабочий код рекомендую свериться с исходным файлом.

---

## 8. Структура выходного отчёта

**Гранулярность:** 1 строка = 1 инцидент.
**Дедупликация:** по `incdnt_id`.
**Колонок: 67** (полная основная таблица).

### 8.1. Группы колонок

| Группа | Поля | Колонок |
|--------|------|:------:|
| Идентификация / статус | `incdnt_id, incdnt_sid, incdnt_status_name, incdnt_autoreg_flag, incdnt_detection_person_name, incdnt_mistake_cnt` | 6 |
| Источник | `incdnt_source_name, src_type_lvl_1/2_name` | 3 |
| Тип события | `incdnt_type_lvl_1/2_name` | 2 |
| Даты | `incdnt_detection_dt, incdnt_start_dt, incdnt_entry_dt, incdnt_first_validated_dttm, incdnt_last_validate_dttm` | 5 |
| ЦПР | `risk_profile_id, risk_profile_name` | 2 |
| Клиент / договор | `incdnt_client_type_name, incdnt_appl_num, incdnt_agr_num, incdnt_agr_sid` | 4 |
| Описания | `incdnt_summary_descr_txt, incdnt_full_descr_txt` | 2 |
| Оргструктура | `org_struct_id, org_struct_lvl_2-10_name` | 10 |
| Функ. блок | `funct_block_id, funct_block_lvl_2-4_name` | 4 |
| Процесс | `process_lvl_1-4_name, clntpth_lvl_4_name` | 5 |
| Флаги риска (Y/N) | 4 флага | 4 |
| **Фин. итоги (готовые)** | `incdnt_sum + drct/indrct/unrlzd/thrd_prt/gain × (sum/cred/noncred)` = 1 + 5×3 = 16 | 16 |
| Возмещение | `recovery_rub_amt_aggr` | 1 |
| **Итого** | | **67** |

Полный список см. в [`ior_Mapping_разделы.md` §2](../ИОР_Mapping_разделы.md#2-mapping-по-основной-таблице-d6_base_of_knowledge_ior).

---

## 9. Семантика ключевых полей

### 9.1. `incdnt_status_name` — 5 текущих статусов

| Значение | Бизнес-смысл |
|----------|--------------|
| **Исследование** | Расследуется риск-координатором |
| **Утверждение** | Отправлено риск-менеджеру на утверждение |
| **Утверждён** | Финальный статус (валидирован) |
| **Удалён** | Сняли с учёта (см. [`deleted_ior`](deleted_ior.md)) |
| **Черновик** | Создан, но не отправлен в работу |

### 9.2. `incdnt_type_lvl_1_name` — 7 категорий + NULL

| Категория | Описание |
|-----------|----------|
| **1. Ошибки персонала и недостатки процессов** | Непреднамеренные ошибки сотрудников, недостатки ВНД, ошибки РКО, ошибки отчётности |
| **2. Нарушение и сбои систем и оборудования** | ИТ-сбои, недоступность АС, сбои инфраструктуры |
| **3. Нарушение прав клиентов и контрагентов** | Раскрытие/утечка конфиденциальной информации, нарушение деловых практик, риск поведения, недостатки в работе с контрагентами |
| **4. Нарушение кадровой политики и безопасности труда** | Санкции за трудовое законодательство, выплаты за нарушение норм безопасности, неверные расчёты с сотрудниками |
| **5. Преднамеренные действия персонала** | Действия для выгоды банка / для личной выгоды |
| **6. Ущерб материальным активам** | Природные / техногенные факторы, вандализм |
| **7. Преднамеренные действия третьих лиц** | Внешнее мошенничество, действия третьих лиц |

### 9.3. `src_type_lvl_1_name` — 5 типов источников

`Внешние причины`, `Действия персонала`, `Недостатки процессов`, `Сбои систем и оборудования`, `Типы источников` (последнее, видимо, мусорное значение «корня» иерархии).

### 9.4. `incdnt_source_name` — 4 источника

| Значение | Когда |
|----------|-------|
| `Мониторинг АС` | Автоматическая регистрация системой мониторинга |
| `Реестровое уведомление` | Из реестрового уведомления |
| `Результат анализа акта УВА` | **По итогам аудита УВА** (важный кейс для запросов!) |
| `Уведомление РК` | По уведомлению риск-координатора |

### 9.5. `incdnt_detection_person_name` — кто выявил (6 категорий)

`Внешние контролирующие органы`, `Вторая линия`, `Клиент`, `Первая линия`, `Сотрудник СВА`, `Сотрудник СВА в рамках оценки эффективности`.

### 9.6. `incdnt_client_type_name`

`ФЛ`, `ЮЛ`, `Без клиента`.

### 9.7. Финансовые поля — что значит «sum / cred / noncred»

Для каждого типа потерь (`drct_dmg`, `indrct_dmg`, `unrlzd_dmg`, `thrd_prt`, `gain`) есть 3 поля:

| Суффикс | Смысл |
|---------|-------|
| `_sum` | **Итого** по этому типу (сумма cred + noncred) |
| `_cred_rub_amt` | Часть, **связанная с кредитным риском** |
| `_noncred_rub_amt` | Часть, **не связанная с кредитным риском** |

Пример: `incdnt_drct_dmg_sum = 1 000 000 ₽`, `incdnt_drct_dmg_cred_rub_amt = 800 000`, `incdnt_drct_dmg_noncred_rub_amt = 200 000`.

**`incdnt_sum`** — общая сумма всех типов (drct + indrct + unrlzd + thrd_prt + gain).

### 9.8. `recovery_rub_amt_aggr`

**Итоговая** сумма возмещения по инциденту (агрегат). Заполнено в ~39% инцидентов. Для детализации операций → [`vozmeshenie_ior`](vozmeshenie_ior.md).

### 9.9. Флаги риска

Все 4 + `incdnt_autoreg_flag` — **string Y/N**, у риск-флагов может быть NULL (если связь не определена).

---

## 10. Предупреждения о данных

1. **`incdnt_sum` и фин. агрегаты — заполнены только в 2.26%.** Для остальных 97.74% — будет NULL. Это норма: агрегаты считаются только для инцидентов с утверждёнными фин. последствиями. Не интерпретировать NULL как «нет потерь».
2. **Заполненность оргструктуры:**
   - `lvl_3_name` (ТБ): 97% — основной фильтр «по подразделению».
   - `lvl_4_name`: 75%
   - `lvl_5+`: < 10% (мало данных)
3. **`clntpth_lvl_4_name`**: 0.92% — для запросов «по клиентскому пути» предупреждать пользователя.
4. **`busn_area_*`**: 0.01-2.26% — то же.
5. **`incdnt_agr_*`**: 14-15% — связь с кредитом редкая.
6. **`incdnt_appl_num`**: 66% — связь с заявкой почаще.
7. **Только ПАО Сбербанк** — фильтр по префиксам. ДЗО исключены (но `process_lvl_1_name` может показывать ДЗО, если ИОР техподразделения инцидентов произошёл в процессе ДЗО).

---

## 11. Шаблон ответа LLM

```
✅ Подготовлен отчёт «ИОР за период по ПАО Сбербанк» за {begin} — {end}.

📊 Краткая статистика:
  • Инцидентов: {N}
  • С финансовыми последствиями (incdnt_sum > 0): {N_fin}
  • С возмещениями: {N_recovery}
  • Авторегистраций: {N_autoreg}
  • Статусы: Утверждён {n_a}, Исследование {n_i}, Утверждение {n_u}, Удалён {n_d}, Черновик {n_ch}

💰 Финансы (по 2.26% инцидентов с заполненными агрегатами):
  • Прямые потери: {SUM(incdnt_drct_dmg_sum):,.2f} ₽
    ├ с кредитным риском: {SUM(_cred):,.2f} ₽
    └ без кред. риска: {SUM(_noncred):,.2f} ₽
  • Косвенные потери: {SUM(incdnt_indrct_dmg_sum):,.2f} ₽
  • Третьих лиц: {SUM(incdnt_thrd_prt_sum):,.2f} ₽
  • Возмещения: {SUM(recovery_rub_amt_aggr):,.2f} ₽
  • Нетто (потери - возмещение): {NETTO:,.2f} ₽

🏆 Топ-5 ТБ по количеству ИОР:
  1. {tb_1}: {n_1}
  2. ...

📁 Файл: ИОР за период по ПАО Сбербанк {begin} — {end}.xlsx

ℹ️ Учитывать:
  • Только ПАО Сбербанк (исключены ДЗО)
  • Период по incdnt_entry_dt (дата ввода)
  • Финансовые поля заполнены только у ~2% инцидентов

💡 Для детализации:
  • Каждое фин. последствие отдельной строкой → /financial_consequences_ior
  • Каждое возмещение отдельной строкой → /vozmeshenie_ior
  • Качественные последствия → /ior_nonfinancial_consequences
  • Удалённые ИОР детально → /deleted_ior
  • Полное досье одного ИОР → /report_period_specific_ior?sid=EVE-XXX
```

---

## 12. Decision Tree (входная точка для всех запросов про ИОР)

```
Запрос про ИОР?
  ├─ Конкретный SID указан? → report_period_specific_ior
  ├─ Только удалённые?       → deleted_ior
  ├─ Детализация 1:N нужна?
  │    ├─ По возмещениям?    → vozmeshenie_ior
  │    ├─ По фин. послед?    → financial_consequences_ior
  │    └─ По нефин. послед?  → ior_nonfinancial_consequences
  └─ Иначе                   → ✅ ЭТОТ СКРИПТ (дефолт)
```

---

## 13. Пограничные случаи

| Случай | Поведение |
|--------|-----------|
| Пустой результат | «За период {begin}—{end} по ПАО Сбербанк инцидентов не зарегистрировано.» |
| Запрос «ИОР с потерями» | Применить + предупредить: «Поле `incdnt_sum` заполнено только в 2.26% — реальное число ИОР с фин. потерями определяется через JOIN с `incident_fin_impact`. Рекомендую /financial_consequences_ior для точности.» |
| Запрос «ИОР по ВСП №...» | Применить + `org_struct_lvl_8_name LIKE '%N%' OR lvl_9_name LIKE '%N%'` + **предупредить о низкой заполненности** lvl_8/9 |
| «Топ ТБ по числу ИОР» | Этот скрипт + группировка `GROUP BY org_struct_lvl_3_name ORDER BY COUNT(*) DESC LIMIT 10` |
| «Распределение по типам событий» | Группировка по `incdnt_type_lvl_1_name` |
| «ИОР, связанные с риском поведения» | + `incdnt_behavior_risk_flag = 'Y'` |
| Запрос про ДЗО (Сбербанк лизинг и т.п.) | Снять `ORG_PREFIXES` фильтр или сменить префиксы; предупредить, что это уже **не «ПАО Сбербанк»** |

---

## 14. Известные проблемы

1. **2.26% заполненности фин.** — главный нюанс. Нужно объяснять пользователю.
2. **Низкая заполненность нижних уровней оргструктуры** (lvl_5+) — фильтрация по ним вернёт мало данных.
3. **Префиксы оргструктуры захардкожены.** Для ДЗО нужно явно расширить.
4. **`incdnt_source_name = 'Типы источников'`** — выглядит как мусор/корень иерархии. Может встретиться, не пугаться.

---

## 15. Примеры flow

### Пример 1: «Выгрузи ИОР за 2025 год по продукту Ипотека»

Параметры: период `2025-01-01 — 2025-12-31`, фильтр `process_lvl_4_name LIKE '%ипотек%'`.
В ответе: количество ИОР, топ-ТБ, разбивка по типам событий.

### Пример 2: «Сколько ИОР по поведенческому риску в Q4 2025»

Применить + `incdnt_behavior_risk_flag = 'Y'` за Q4. В ответе — число и Excel.

### Пример 3: «Статистика ИОР по ТБ за полугодие»

Применить за полугодие, в шаблоне ответа сделать **группировку по `org_struct_lvl_3_name`** с числом ИОР и итоговыми потерями.

### Пример 4: «ИОР по СЗБ за 2025 в статусе Утверждён»

`org_struct_lvl_3_name LIKE '%Северо-Западный%'` + `incdnt_status_name = 'Утверждён'`.

### Пример 5: «ИОР, выявленные УВА»

`incdnt_source_name = 'Результат анализа акта УВА'` ИЛИ `incdnt_detection_person_name LIKE '%СВА%'`.

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
    },
    "filters": {
      "type": "object",
      "description": "Опциональные доп. фильтры",
      "properties": {
        "org_struct_lvl_3_name_like":    {"type": "string"},
        "funct_block_lvl_2_name_like":   {"type": "string"},
        "process_lvl_4_name_like":       {"type": "string"},
        "incdnt_type_lvl_1_name":        {"type": "string"},
        "incdnt_source_name":            {"type": "string"},
        "incdnt_status_name":            {"type": "string"},
        "incdnt_client_type_name":       {"type": "string", "enum": ["ФЛ","ЮЛ","Без клиента"]},
        "incdnt_autoreg_flag":           {"type": "string", "enum": ["Y","N"]},
        "incdnt_security_risk_flag":     {"type": "string", "enum": ["Y","N"]},
        "incdnt_infrmtn_sys_risk_flag":  {"type": "string", "enum": ["Y","N"]},
        "incdnt_behavior_risk_flag":     {"type": "string", "enum": ["Y","N"]},
        "incdnt_model_risk_flag":        {"type": "string", "enum": ["Y","N"]}
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
  "file_name_template": "ИОР за период по ПАО Сбербанк {begin} — {end}.xlsx",
  "row_granularity": "1 row = 1 incident",
  "primary_key": ["incdnt_id"],
  "columns_count": 67,
  "sort_order": ["incdnt_entry_dt ASC", "incdnt_sid ASC"]
}
```
