# IOR-аналитик (d6_ior) — установка и первый запуск

Эта ветка добавляет навык **ior-analyzer** в Nanobot: анализ инцидентов
операционного риска (ИОР) — стандартные выгрузки, ad-hoc запросы, отчёты,
гипотезы и уточнения по выгрузке.

## Состав ветки

```
.
├── feature.yaml                                  # манифест для audit_bridge
├── docs/D6.md                                    # описание навыка
├── sql/migrations/V006__d6_ior_tables_real.sql   # DDL 5 production-таблиц IOR
├── workspace/skills/ior-analyzer/                # навык: 89 файлов
│   ├── SKILL.md                                  # контракт навыка для LLM
│   ├── scripts/
│   │   ├── cli.py                                # entry: python cli.py
│   │   ├── ior_reports.py                        # production-flow: SQL→pandas→Excel
│   │   ├── ior_hypothesis.py                     # LLM-генерация гипотез
│   │   ├── preset_analysis/                       # 7 готовых пресетов
│   │   └── analysis_mode/                        # direct_loss, anomalies, hypotheses
│   ├── knowledge_base/                            # BGE-M3 retrieval (RAG)
│   ├── utils/                                    # data_store, dataframe_ops, bge, excel
│   ├── testing/
│   │   ├── data_generator.py                    # генерирует ior.json (1000 записей)
│   │   └── seed_test_data.py                     # seed в PG (идемпотентный)
│   └── ...
├── workspace/tools/ior_analyzer.py               # native tool (config_key=ior_analyzer)
└── tests/                                        # контрактные тесты
    ├── test_tools_ior_analyzer.py
    └── test_ior_bge_followup_contract.py
```

## Что добавляется в Nanobot

| Файл / ключ | Описание |
|---|---|
| `workspace/skills/ior-analyzer/**` | навык ior-analyzer |
| `workspace/tools/ior_analyzer.py` | native tool `ior_analyzer` |
| `gateway.ior_analyzer.enable=true` | регистрация tool |
| `skills.ior-analyzer.enabled=true` + `tables=[5]` | sync 5 таблиц в DuckDB-кэш |
| `sql/migrations/V006__d6_ior_tables_real.sql` | DDL production-схемы IOR |
| `NANOBOT_SKILLS_RUNTIME=testing` (overlay при `--env=dev`) | runtime-aware backend → `cache` (DuckDB) вместо `greenplum` |
| `IOR_TEST_DSN` (optional) | DSN для тестовой БД (если пусто — fallback в `channels.postgres.dsn`) |

Никакие файлы в `lib/`, `config.json`, корневой `project.json` навык
**не модифицирует** — все настройки идут через `feature.yaml::overlays`.

## Установка через audit_bridge

```powershell
# Один раз: применить фичу
cd C:\Users\pasco\opencode_projects\audit_point\audit_bridge
.\scripts\apply-feature.ps1 `
    -Subcommand apply `
    -RepoDir C:\Users\pasco\opencode_projects\audit_point\audit_nanobot `
    -BridgeRoot C:\Users\pasco\opencode_projects\audit_point\audit_bridge `
    -Branch feature/d6_ior `
    -Env dev          # включает NANOBOT_SKILLS_RUNTIME=testing через overlay

# Применить миграцию V006 (audit_bridge копирует файл, но НЕ выполняет DDL)
$env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5433/act_constructor"
cd C:\Users\pasco\opencode_projects\audit_point\audit_nanobot
python tools/migrate.py --apply --target V006

# Сгенерировать тестовые данные (1000 инцидентов) и залить в PG
# Скрипт ИДЕМПОТЕНТЕН — повторный запуск ничего не делает, если таблицы
# уже созданы и заполнены.
python workspace/skills/ior-analyzer/testing/seed_test_data.py `
    --json workspace/data_store/cache/testing/ior/ior.json

# Запустить Nanobot
.\scripts\run-bot.ps1
```

## Ручная проверка (если audit_bridge не используется)

```bash
# 1. Создать таблицы через V006
$env:DATABASE_URL="postgresql://postgres:postgres@127.0.0.1:5433/act_constructor"
psql -d act_constructor -f sql/migrations/V006__d6_ior_tables_real.sql

# 2. Сгенерировать тестовые данные (JSON) и залить в PG
python workspace/skills/ior-analyzer/testing/data_generator.py --force
python workspace/skills/ior-analyzer/testing/seed_test_data.py --json workspace/data_store/cache/testing/ior/ior.json

# 3. Запустить Nanobot
python gateway.py --profile=prod
```

## Тестирование через smoke-запрос

После установки отправьте в audit_workstation чат:

```
Выведи ИОР по DRP-10121 за 2026 год
```

(DRP-коды — **числовые**, см. маппинг в `seed_test_data.py::_RISK_MAP`:
DRP-10121 = ИТ-системы, DRP-10120 = Операционный процесс и т.д.)

Ожидаемый ответ:
- количество ИОР в выборке;
- сумма последствий;
- Excel-выгрузка сгенерирована;
- превью первых 5 строк.

## Контракт runtime-aware backend

`data_store.py::_configured_backend()` выбирает backend по env-vars (без
импорта из `lib/`, чтобы навык был полностью автономен):

| `IOR_DATA_BACKEND` | `NANOBOT_SKILLS_RUNTIME` | Backend |
|---|---|---|
| `cache`/`greenplum`/... | (любое) | explicit |
| (пусто) | `testing` | `cache` (DuckDB) |
| (пусто) | `production` (default) | `greenplum` |

Через `apply-feature.ps1 -Env=dev` в `.secrets.env` добавляется
`NANOBOT_SKILLS_RUNTIME=testing` (см. `runtime_env_overlay` в
`audit_bridge/feature_bridge/applier.py`).

## Тестовая схема таблиц

| Таблица | Колонки | Описание |
|---|---|---|
| `t_db_oarb_ior_d6_base_of_knowledge_ior` | 65 | основная таблица инцидентов |
| `t_db_oarb_ior_d6_base_of_knowledge_incident_stts_chng` | 8 | история смены статусов |
| `t_db_oarb_ior_d6_base_of_knowledge_incident_recovery` | 15 | возмещения |
| `t_db_oarb_ior_d6_base_of_knowledge_incident_fin_impact` | 18 | финансовые последствия |
| `t_db_oarb_ior_d6_base_of_knowledge_incident_nonfin_impact` | 7 | нефинансовые последствия |

Все таблицы — в схеме `public` (наша DEV-БД). Production-использование
предполагает миграцию в GP-схему `s_grnplm_ld_audit_da_project_34`.

## Удаление (rollback)

```bash
# Удалить фичу (DROP таблиц + restore project.json/.secrets.env)
.\scripts\apply-feature.ps1 `
    -Subcommand remove `
    -FeatureId d6_ior
```

Удаляет:
- 5 таблиц `public.t_db_oarb_ior_d6_*` (см. `feature.yaml::isolation.rollback_sql`).
- Записи `gateway.ior_analyzer.enable`, `skills.ior-analyzer.enabled`,
  `skills.ior-analyzer.tables` в `project.json` (через restore из backup).
- `NANOBOT_SKILLS_RUNTIME=testing` в `.secrets.env` (через restore из backup).
