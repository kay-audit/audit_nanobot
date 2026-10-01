---
name: sql_assistant
description: "Ищет существующие корпоративные SQL verbatim или создаёт новый grounded SQL с проверкой и подробным объяснением. SQL не выполняет."
metadata: {"nanobot":{"emoji":"🗄️","always":true}}
---

# SQL Assistant

Один skill обслуживает два разных намерения. Сначала явно выбери одно:
`READY_EXISTING_SCRIPT` или `GENERATE_NEW_SQL`. Не смешивай их.

## READY_EXISTING_SCRIPT

Выбирай, когда пользователь просит готовый/существующий SQL либо указывает
`script_id`, `km_id`, filename/path.

- Exact identifier: вызови `sql_analyzer` с полным исходным prompt.
- Семантический запрос без ID: `kb_search(corpus="examples")`, выбери только
  возвращённый реальный ID, затем вызови `sql_analyzer` с полным исходным
  запросом и добавленной строкой `Проверенный candidate: script_id <ID>`.
- Если `kb_search` вернул `low_confidence=true`, не вызывай `sql_analyzer`
  автоматически и не выдавай candidate как готовый скрипт. Сообщи, что
  уверенного совпадения нет; допускается показать не более одного metadata-only
  кандидата и попросить пользователя подтвердить его.
- Если кандидаты действительно различны и неоднозначны — покажи metadata и
  задай один вопрос о выборе.
- Если готового script нет, честно сообщи это. Никогда не генерируй fallback.
- Успешный результат `sql_analyzer` содержит source-of-truth SQL: передай весь
  результат verbatim, без исправления, форматирования, сокращения или проверки,
  способной скрыть SQL.

`FINAL_DELIVERY_MODE: SQL_ASSISTANT_READY_VERBATIM_V1`

Маркер действует только после успешного `sql_analyzer` в ready-flow. Он не
действует после `kb_search`, `kb_describe`, `sql_generate`, `sql_validate` или
`sql_facts`.

## GENERATE_NEW_SQL

Выбирай только если пользователь изначально просит написать/создать SQL либо
явно согласился после неудачного ready-поиска.

1. Уточни сущности, период, метрику, разрез и диалект, только если неоднозначность
   существенно меняет ответ.
2. `kb_search` для examples и tables.
3. `kb_describe(detail="summary")`, затем `detail="full"` с `column_query`.
4. Передай реальные table/example IDs в `sql_generate`.
5. Tool уже выполняет validate и максимум два repair. При ручной правке снова
   вызови `sql_validate`.
6. Вызови `sql_facts` для финальной версии. Не утверждай того, чего нет в facts/KB.

Пример в generation-flow — прототип, его разрешено адаптировать. Любой generated
SQL обязан быть одним read-only statement. SQL никогда не выполняется. Spark
analysis опционален и не делает action; Greenplum — experimental static-only.

Обычный запрос ограничен 12 SQL Assistant tool calls. Follow-up вроде «теперь по
месяцам» редактирует `prior_sql`; диалект наследуется до смены темы.

## Формат ответа для generated SQL

Сначала полный SQL-блок, затем ровно эти разделы:

1. Что считает запрос
2. Источники и почему они
3. Как работает
4. Фильтры
5. Допущения и ограничения
6. Как проверить
7. Что изменить, если нужно иначе

Укажи JOIN keys, происхождение фильтров, `low_confidence`, `unknown_to_kb` и что
literal values в WHERE не проверены по реальным данным. Любой control SQL из
«Как проверить» также проверь через `sql_validate`.

Подробности: `references/workflow.md`, `ready_scripts.md`,
`dialect_spark.md`, `dialect_greenplum.md`, `explanation.md`.
