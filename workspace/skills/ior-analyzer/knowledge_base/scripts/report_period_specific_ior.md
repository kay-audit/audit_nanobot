# Скрипт «Отчёт по конкретному номеру ИОР»

> **Контракт аналитики runtime:** raw Excel сохраняет допустимое произведение `N fin impacts × M recoveries`. Аналитика считает distinct `fin_impact_sid` и distinct `recovery_sid`, поэтому суммы не умножаются cross join. Гипотезы формируются только для утверждённого ИОР.

> **Notebook:** [`новая_схема_БЗ_v2/report_period_specific_ior_v2.ipynb`](../новая_схема_БЗ_v2/report_period_specific_ior_v2.ipynb)
> **Skill ID:** `report_period_specific_ior_v2`
> **Категория:** досье инцидента (полная информация по одному ИОР)

> ✅ **Схема fin_impact (сверено с реальной витриной, скан 2026-06):**
> - `d6_base_of_knowledge_incident_fin_impact` содержит **отдельные** `incdnt_id`
>   (FK к `ior.incdnt_id`) и `fin_impact_id` (PK строки). Связь с инцидентом —
>   по `incdnt_id` (`ior.incdnt_id = fi.incdnt_id`), **НЕ** по `fin_impact_id`.
> - `fin_impact_type_name` **существует** (5 типов потерь) — можно фильтровать/
>   группировать по нему. Для быстрых СУММ по типам — готовые агрегаты в main
>   (`incdnt_drct_dmg_sum`, `incdnt_indrct_dmg_sum`, `incdnt_unrlzd_dmg_sum`,
>   `incdnt_thrd_prt_sum`, `incdnt_gain_sum`).
> - Колонки `fin_impact_subkind_name` в витрине **НЕТ** — не использовать.
> - У `recovery`, `nonfin_impact`, `stts_chng` связь по `incdnt_id` — все joins
>   работают единообразно по `incdnt_id`.

---

## 1. Краткое описание для LLM-маршрутизатора

**Полное досье одного инцидента** операционного риска по его бизнес-идентификатору (`incdnt_sid`, формат `EVE-XXXXXXX`). Объединяет атрибуты ИОР + **все фин. последствия** + **все возмещения** в одном отчёте.

**Один ряд отчёта = одна комбинация (фин. последствие × возмещение)** для этого инцидента. Если у ИОР N фин. последствий и M возмещений — в отчёте N×M строк (декартово произведение из-за двух LEFT JOIN). Это особенность отчёта-досье.

**Когда LLM выбирает этот скрипт:** когда в запросе **есть конкретный SID** (формат `EVE-XXXXXXX`) или пользователь хочет «всё про инцидент EVE-_».

---

## 2. Триггеры

### 2.1. Прямые триггеры (обязательные)

| Триггер | Применение |
|---------|------------|
| «всё про инцидент EVE-_» | основная задача |
| «досье ИОР EVE-_» | основная задача |
| «полная информация по EVE-_» | основная задача |
| «детали инцидента EVE-_» | основная задача |
| «отчёт по EVE-_» / «отчёт по конкретному ИОР» | основная задача |
| «покажи EVE-_» / «найди EVE-_» | основная задача |
| «что с инцидентом EVE-_» | основная задача |

### 2.2. Контекстные триггеры

| Триггер | Действие |
|---------|----------|
| «фин. последствия по EVE-_» | Можно этот скрипт (более полный) или `financial_consequences_ior` + фильтр по SID |
| «возмещения по EVE-_» | Этот скрипт |
| «кто работал с EVE-_» | Этот скрипт + интерпретация в шаблоне ответа |
| «нефин. последствия по EVE-_» | ⚠️ Этого нет в скрипте! Использовать `ior_nonfinancial_consequences` + фильтр |

### 2.3. Семантические признаки

**Сильный признак:** в запросе есть **`EVE-` + 7 цифр** (`EVE-5092355`, `EVE-6967014`) — почти 100% маркер этого скрипта.

---

## 3. Анти-триггеры

| Если запрос | Использовать |
|-------------|--------------|
| Период вместо SID («за январь 2025») | [`ior_period_pao_sberbank`](ior_period_pao_sberbank.md) |
| Множественные SID (список из нескольких) | Запустить скрипт N раз или использовать `ior_period_pao_sberbank` с фильтром `WHERE incdnt_sid IN (...)` |
| **Только** фин. последствия по SID (без возмещений) | `financial_consequences_ior` + фильтр по SID |
| **Только** нефин. последствия по SID | `ior_nonfinancial_consequences` + фильтр по SID |
| **Только** возмещения по SID | `vozmeshenie_ior` + фильтр по SID |
| Журнал статусов по SID | `deleted_ior` (адаптировать, сменив фильтр на нужный action) |

---

## 4. Извлечение параметров

### 4.1. Контракт

| Параметр | Тип | Обязательность | Формат |
|----------|-----|:---------------:|--------|
| `incdnt_sid` | STRING | **да** | `EVE-XXXXXXX` (например, `EVE-5092355`) |

### 4.2. Правила извлечения SID из запроса

| Образец в запросе | Извлечь |
|-------------------|---------|
| `EVE-5092355` | `EVE-5092355` |
| `eve-5092355` | `EVE-5092355` (нормализовать к верхнему регистру) |
| `5092355` без префикса | `EVE-5092355` (добавить префикс, если контекст про ИОР) |
| «инцидент номер 5092355» | `EVE-5092355` |
| «событие 5092355» | `EVE-5092355` |

### 4.3. Если SID не указан или невалиден

LLM должен **спросить:**
> «Назовите идентификатор инцидента в формате `EVE-XXXXXXX` — например, `EVE-5092355`.»

Если в запросе несколько SID — обрабатывать **по очереди** или (для краткого ответа) использовать `ior_period_pao_sberbank` с `WHERE incdnt_sid IN (...)`.

---

## 5. Источники

| # | Таблица | Гранулярность | Тип JOIN |
|:-:|---|---|---|
| 1 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior` | 1:1 (фильтр по SID) | base |
| 2 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact` | 1:N | LEFT |
| 3 | `s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery` | 1:N | LEFT |

> **LEFT JOIN** обоих — чтобы получить инцидент даже без фин. последствий / возмещений (показать всё что есть).

---

## 6. Алгоритм

1. Из main выбрать **одну строку** по `incdnt_sid = '<SID>'` (DROP DUPLICATES по `incdnt_id` на всякий случай).
2. Выгрузить **все фин. последствия** этого инцидента из `incident_fin_impact`.
3. Выгрузить **все возмещения** этого инцидента из `incident_recovery`.
4. LEFT JOIN main × fin_impact × recovery — декартово произведение.
5. Сортировка `fin_impact_sid, recovery_sid`.
6. Очистка описаний.

**Cross-join эффект:** ИОР с 3 фин. последствиями и 2 возмещениями даст 3×2 = **6 строк** в отчёте. LLM должен это понимать при подсчёте уникальных сущностей.

---

## 7. SQL-эквивалент

```sql
SELECT
    -- — Атрибуты инцидента (как в ior_period_pao_sberbank) —
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
    regexp_replace(ior.incdnt_summary_descr_txt, '[\x8C-\xEF\x02\x08\x0b]', '') AS incdnt_summary_descr_txt,
    regexp_replace(ior.incdnt_full_descr_txt,    '[\x8C-\xEF\x02\x08\x0b]', '') AS incdnt_full_descr_txt,
    ior.org_struct_id,
    ior.org_struct_lvl_2_name, ior.org_struct_lvl_3_name, ior.org_struct_lvl_4_name,
    ior.org_struct_lvl_5_name, ior.org_struct_lvl_6_name, ior.org_struct_lvl_7_name,
    ior.org_struct_lvl_8_name, ior.org_struct_lvl_9_name, ior.org_struct_lvl_10_name,
    ior.funct_block_id,
    ior.funct_block_lvl_2_name, ior.funct_block_lvl_3_name, ior.funct_block_lvl_4_name,
    ior.process_lvl_1_name, ior.process_lvl_2_name, ior.process_lvl_3_name, ior.process_lvl_4_name,
    ior.clntpth_lvl_4_name,
    ior.incdnt_security_risk_flag, ior.incdnt_infrmtn_sys_risk_flag,
    ior.incdnt_behavior_risk_flag, ior.incdnt_model_risk_flag,
    -- — Финансовые агрегаты (готовые) —
    ior.incdnt_sum,
    ior.incdnt_drct_dmg_sum, ior.incdnt_drct_dmg_cred_rub_amt, ior.incdnt_drct_dmg_noncred_rub_amt,
    ior.incdnt_indrct_dmg_sum, ior.incdnt_indrct_dmg_cred_rub_amt, ior.incdnt_indrct_dmg_noncred_rub_amt,
    ior.incdnt_unrlzd_dmg_sum, ior.incdnt_unrlzd_dmg_cred_rub_amt, ior.incdnt_unrlzd_dmg_noncred_rub_amt,
    ior.incdnt_thrd_prt_sum, ior.incdnt_thrd_prt_cred_rub_amt, ior.incdnt_thrd_prt_noncred_rub_amt,
    ior.incdnt_gain_sum, ior.incdnt_gain_cred_rub_amt, ior.incdnt_gain_noncred_rub_amt,
    ior.recovery_rub_amt_aggr,
    -- — Фин. последствие (детали) —
    fi.fin_impact_id, fi.fin_impact_sid,
    fi.fin_impact_type_name, fi.fin_impact_kind_name,
    fi.fin_impact_monitoring_flag,
    fi.fin_impact_crncy_code, fi.fin_impact_local_crncy_code,
    fi.fin_impact_detection_dt, fi.fin_impact_creation_dttm, fi.fin_impact_reg_dt,
    fi.fin_impact_account_num, fi.fin_impact_docum_num,
    fi.fi_busn_area_id, fi.fi_org_struct_id,
    fi.fin_impact_ccy_amt, fi.fin_impact_local_ccy_amt, fi.fin_impact_rub_amt,
    -- — Возмещение (детали) —
    rec.recovery_sid, rec.recovery_type_name,
    rec.recovery_crncy_code, rec.recovery_local_crncy_code,
    rec.recovery_src_account_num, rec.recovery_doc_num,
    rec.recovery_creation_dttm, rec.recovery_reg_dt,
    rec.recovery_ccy_amt, rec.recovery_local_ccy_amt, rec.recovery_rub_amt
FROM (
    SELECT * FROM s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_ior
    WHERE incdnt_sid = '{sid}'
) ior
LEFT JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact fi
    ON ior.incdnt_id = fi.incdnt_id  -- связь по incdnt_id (FK); fin_impact_id — PK строки
LEFT JOIN s_grnplm_ld_audit_da_project_34.t_db_oarb_ior_d6_base_of_knowledge_incident_recovery rec
    ON ior.incdnt_id = rec.incdnt_id
ORDER BY ior.incdnt_entry_dt, ior.incdnt_sid, fi.fin_impact_sid, rec.recovery_sid;
```

---

## 8. Структура выходного отчёта

**Гранулярность:** 1 строка = 1 комбинация (фин. последствие × возмещение). Атрибуты ИОР повторяются.
**Если у ИОР нет фин. последствий** — поля `fin_impact_*` будут NULL.
**Если нет возмещений** — `recovery_*` NULL.

### 8.1. Группы колонок

| Группа | Колонок |
|--------|:------:|
| Атрибуты ИОР (как в `ior_period_pao_sberbank`) | 50 |
| Финансовые агрегаты (готовые из main) | 16 |
| Возмещение-агрегат (`recovery_rub_amt_aggr`) | 1 |
| **Фин. последствие (детали из `incident_fin_impact`)** | **17** |
| **Возмещение (детали из `incident_recovery`)** | **11** |
| **Итого** | **95** |

---

## 9. Семантика — как читать отчёт-досье

### 9.1. Структура строк

Пример: ИОР `EVE-XXX` имеет:
- 2 фин. последствия: `Прямая потеря (500 000 ₽)` и `Косвенная потеря (50 000 ₽)`
- 1 возмещение: `Компенсация от сотрудника (200 000 ₽)`

В отчёте будет **2 × 1 = 2 строки**:

| Строка | fin_impact_kind_name | fin_impact_rub_amt | recovery_type_name | recovery_rub_amt |
|:------:|-----------------------|---------------------|----------------------|--------------------|
| 1 | Ошибка операции — двойное списание | 500 000 | Компенсация от сотрудника | 200 000 |
| 2 | Штрафные пени по ставке ЦБ | 50 000 | Компенсация от сотрудника | 200 000 |

> _В старой схеме разделение шло по `fin_impact_type_name` (Прямая/Косвенная);
> в новой это разворачивается через `fin_impact_kind_name` + готовые агрегаты
> `incdnt_drct_dmg_sum`/`incdnt_indrct_dmg_sum` в main-таблице._

**Внимание:** возмещение «дублируется» во второй строке — это **визуальное умножение**, не реальное удвоение суммы. Реальный итог по возмещению — поле `recovery_rub_amt_aggr` (в строках всех одинаковое).

### 9.2. Различие сумм возмещения

| Поле | Где | Что значит |
|------|-----|------------|
| `recovery_rub_amt_aggr` (в main) | в каждой строке отчёта одинаковое | **Итоговая** сумма возмещения по этому инциденту |
| `recovery_rub_amt` (в recovery) | в каждой строке = одна операция | Сумма **конкретной** операции возмещения |

**Правило для LLM:** при показе суммы возмещения в досье использовать **`recovery_rub_amt_aggr`** (агрегат), а **`recovery_rub_amt`** — для перечня операций.

### 9.3. Различие сумм потерь

То же самое:
- `incdnt_drct_dmg_sum`, `_cred_rub_amt`, `_noncred_rub_amt` (в main) — **агрегаты** по инциденту, дублируются в каждой строке.
- `fin_impact_rub_amt`, `fin_impact_ccy_amt` (в fin_impact) — суммы **конкретного** фин. последствия.

---

## 10. Предупреждения

1. **Cross-join эффект.** N×M строк. При подсчёте «количества фин. последствий» делать `COUNT(DISTINCT fin_impact_sid)`, не `COUNT(*)`.
2. **Нефинансовые последствия НЕ включены** в этот отчёт. Если нужны — отдельный запуск `ior_nonfinancial_consequences` с фильтром по SID.
3. **История статусов НЕ включена.** Если нужна — отдельный запрос к `incident_stts_chng`.
4. **Виновные сотрудники НЕ включены.** Если нужны — выборка из `d6_base_of_knowledge_employee` по `aggregateroot_id = incdnt_sid` (вне этого скрипта).
5. **SID без префикса** «EVE-» — нормализовать перед подстановкой в WHERE.
6. **SID не найден** в БЗ — пустой результат. LLM должен сказать «инцидент не найден» (возможно, это SID из ДЗО или из периода вне БЗ).
7. **Реальные данные в `*_account_num`, `*_docum_num`, `*_user_num`** — конф. **LLM не выводит в чат**, показывать в Excel.
8. **`incdnt_summary/full_descr_txt`** — реальный текст инцидента, может содержать **ФИО клиентов, номера договоров**. LLM показывает в чате только краткие/обезличенные фрагменты.

---

## 11. Шаблон ответа LLM

```
✅ Досье инцидента {sid}

📄 Основная информация:
  • SID: {incdnt_sid}
  • Внутренний ID: {incdnt_id}
  • Статус: {incdnt_status_name}
  • Дата ввода: {incdnt_entry_dt}
  • Дата обнаружения: {incdnt_detection_dt}
  • Дата начала: {incdnt_start_dt}
  • Авторегистрация: {Y/N → Да/Нет}

🏷️ Классификация:
  • ЦПР: {risk_profile_name}
  • Тип события: {incdnt_type_lvl_1_name} → {incdnt_type_lvl_2_name}
  • Источник: {incdnt_source_name} ({src_type_lvl_1_name}, {src_type_lvl_2_name})
  • Кем выявлено: {incdnt_detection_person_name}
  • Тип клиента: {incdnt_client_type_name}

🏢 Подразделение:
  • ТБ: {org_struct_lvl_3_name}
  • Дальше по иерархии: {lvl_4_name → lvl_5_name → ...}
  • Функциональный блок: {funct_block_lvl_2_name → lvl_3_name → lvl_4_name}
  • Процесс: {process_lvl_4_name}

📝 Описание (краткое, без конф.деталей):
  {incdnt_summary_descr_txt — обрезать до 200 символов, не показывать ФИО/договоры}

💰 Финансовые итоги (готовые агрегаты):
  • Общая сумма последствий: {incdnt_sum:,.2f} ₽
  • Прямые потери: {incdnt_drct_dmg_sum:,.2f} ₽ (кред: {_cred}, некред: {_noncred})
  • Косвенные: {incdnt_indrct_dmg_sum:,.2f} ₽
  • Нереализовавшиеся: {incdnt_unrlzd_dmg_sum:,.2f} ₽
  • Третьих лиц: {incdnt_thrd_prt_sum:,.2f} ₽
  • Прибыль: {incdnt_gain_sum:,.2f} ₽
  • ВОЗМЕЩЕНИЕ (итого): {recovery_rub_amt_aggr:,.2f} ₽
  • НЕТТО (потери - прибыль - возмещ.): {netto:,.2f} ₽

📄 Финансовые последствия ({COUNT(DISTINCT fin_impact_sid)} шт.):
  1. {fin_impact_kind_name}: {fin_impact_rub_amt:,.2f} ₽
  2. ...

💵 Возмещения ({COUNT(DISTINCT recovery_sid)} шт.):
  1. {recovery_type_name}: {recovery_rub_amt:,.2f} ₽ ({recovery_reg_dt})
  2. ...

🚩 Связи с другими видами риска:
  • ИБ: {Y/N → есть/нет}
  • ИС: {...}
  • Поведение: {...}
  • Модель: {...}

🔗 Связи:
  • Кредитный договор: {incdnt_agr_num if exists}
  • Заявка: {incdnt_appl_num if exists}

Полный Excel: «Отчёт по ИОР {sid}_{timestamp}.xlsx»

💡 Что ещё посмотреть:
  • Нефинансовые последствия по этому ИОР → /ior_nonfinancial_consequences?sid={sid}
  • Виновные сотрудники → JOIN с d6_base_of_knowledge_employee по incdnt_sid
  • История изменения статусов → запрос к d6_base_of_knowledge_incident_stts_chng по incdnt_id
```

---

## 12. Decision Tree

```
В запросе есть конкретный SID (формат EVE-XXX)?
  ├─ Да → ✅ ЭТОТ СКРИПТ
  └─ Нет:
        ├─ Просят «найди ИОР по номеру договора / заявки»? → ior_period_pao_sberbank + фильтр по incdnt_agr_num
        ├─ Просят все ИОР за период?                        → ior_period_pao_sberbank
        └─ Просят что-то конкретное (фин/возм/нефин)?        → соответствующий специализированный скрипт
```

---

## 13. Пограничные случаи

| Случай | Поведение |
|--------|-----------|
| SID не найден | «Инцидент {sid} не найден в БЗ. Возможные причины: (1) SID опечатан; (2) инцидент в ДЗО (не ПАО Сбербанк); (3) период за пределами 2025-01-01 – 2026-03-31.» |
| SID без префикса (`5092355`) | Добавить `EVE-` → `EVE-5092355`. Если LLM не уверен — спросить. |
| Запрос с несколькими SID | Запустить N раз ИЛИ переадресовать на `ior_period_pao_sberbank` с `WHERE incdnt_sid IN (...)` |
| Запрос «нефин по EVE-_» | Этот скрипт нефин-импакты не показывает! Использовать `ior_nonfinancial_consequences` + `WHERE incdnt_sid='EVE-_'` |
| Запрос «история действий по EVE-_» | Использовать `incident_stts_chng` напрямую или адаптировать `deleted_ior` (сменив фильтр) |
| Запрос про инцидент в ДЗО (SID не Сбер) | Если SID найден — показать. БЗ содержит ИОР с разными префиксами `org_struct_id` |

---

## 14. Известные проблемы

1. **Cross-join раздувает строки** при наличии многих последствий и возмещений (ИОР с 10 последствиями и 5 возмещениями = 50 строк).
2. **Не показывает нефин. последствия и журнал статусов** — для полного досье нужно дополнительно запускать другие скрипты.
3. **Виновные сотрудники не подтянуты** — нужна ручная выборка из `employee` по `aggregateroot_id`.
4. **ФИО клиента/договор** могут быть в `incdnt_full_descr_txt` — LLM должен **обрезать/маскировать** при выводе в чат.

---

## 15. Примеры flow

### Пример 1: «Что с EVE-5092355?»

LLM:
1. Извлёк `incdnt_sid='EVE-5092355'`.
2. Запустил скрипт.
3. В ответе — структурированное досье (см. §11 шаблон).

### Пример 2: «Возмещения по EVE-6967014»

LLM может запустить **этот скрипт** (полное досье, в т.ч. возмещения) ИЛИ `vozmeshenie_ior` + `WHERE incdnt_sid='EVE-6967014'` (если хочет только возмещения).
**Рекомендация:** этот скрипт = больше контекста, лучше для пользовательских запросов.

### Пример 3: «Кто виноват в EVE-_»

Этот скрипт **не покажет ФИО виновного.** LLM должен:
1. Запустить этот скрипт для контекста.
2. Дополнительно выбрать из `employee` (одна таблица, по `aggregateroot_id = incdnt_sid`):
    ```sql
    SELECT e.value_                                  -- данные/ФИО виновного хранятся в value_
    FROM d6_base_of_knowledge_employee e
    WHERE e.aggregateroot_id = '{incdnt_sid}'         -- aggregateroot_id = ior.incdnt_sid
    ```

### Пример 4: «Сравни EVE-1 и EVE-2»

Запустить **дважды** этот скрипт, в ответе LLM сделать таблицу сравнения по ключевым полям.

---

## 16. Контракт

### Input
```json
{
  "type": "object",
  "required": ["incdnt_sid"],
  "properties": {
    "incdnt_sid": {
      "type": "string",
      "pattern": "^EVE-\\d+$",
      "description": "Бизнес-идентификатор инцидента, формат EVE-XXXXXXX"
    }
  }
}
```

### Output
```json
{
  "format": "xlsx",
  "sheet_name": "Отчет_ОпРиски",
  "file_name_template": "Отчёт по ИОР {sid}_{timestamp}.xlsx",
  "row_granularity": "1 row = 1 (fin_impact × recovery) combination — Cartesian product, N×M rows",
  "joins": ["main 1:N fin_impact (LEFT)", "main 1:N recovery (LEFT)"],
  "columns_count": 95,
  "sort_order": ["incdnt_entry_dt", "incdnt_sid", "fin_impact_sid", "recovery_sid"]
}
```
