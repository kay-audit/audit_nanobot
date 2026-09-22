# Feature-Bridge Authoring Guide

> Практический гайд для автора skill / tool / тестовых данных в `audit_nanobot`,
> когда они поставляются **через audit_bridge** (apply feature-branch).
>
> **Аудитория:** разработчик, который доводит код skill/tool в ветке
> `feature/<name>-with-manifest`, проходит validate/applier и устраняет
> ошибки pre-flight.
>
> **Что НЕ здесь** (см. перекрёстные ссылки):
>
> - **Нормативный контракт Skill ↔ Tool** (что запрещено между ними в
>   рантайме) — [`TARGET_ARCHITECTURE.md` §22](TARGET_ARCHITECTURE.md) и
>   [`skill-tool-architecture.md`](skill-tool-architecture.md).
> - **Пошаговый гайд создания skill** (структура SKILL.md, регистрация в
>   project.json, runtime API, DoD) — [`SKILL_AUTHORING.md`](SKILL_AUTHORING.md).
> - **Регистры таблиц/векторов skill'а** (`skills.<name>.tables[]`,
>   `vector_indexes[]`, `TableRegistry.register`, `TableResource` /
>   `VectorResource`) — [`table-registry.md`](table-registry.md).
> - **Конфигурация профилей** (`prod`/`test`, `--profile`,
>   `NANOBOT_PROFILE` устарел) — [`PROFILES.md`](PROFILES.md).
> - **Система feature-bridge со стороны audit_bridge** (state, queue,
>   applier, rollback, start-all.ps1 Step 7.5) —
>   `audit_bridge/docs/FEATURE-BRIDGES.md`.

## 0. TL;DR

**feature.yaml** в корне ветки — это **манифест-контракт** между
`audit_bridge` (apply-gate) и `audit_nanobot` (код фичи). Validator
проверяет **манифест** (структура, ссылки, AST-grep на запрещённые шаблоны).
Pytest'ы из `testing.pytest` в манифесте проверяют **runtime** (импорты,
пути, контракты API). Smoke-промпт проверяет **сквозной flow** через LLM.

```
feature.yaml ──► validator (6 стадий) ──► applier (12 шагов)
                                              │
                                              ├── pre-flight: data_generator → pytest → smoke
                                              └── overlay (project.json) + copy files + restart
```

Если **manifest корректен**, но **код сломан** — validator пропустит
(см. §6 «Частые ошибки»). Это by design: manifest-gate ≠ code-gate.

## 1. Карта зон `audit_nanobot`

Все артефакты фичи попадают в одну из зон. Часть зон жёстко запрещена,
часть требует явного объявления в манифесте.

### 1.1. Запретные зоны (`FORBIDDEN_ZONES`)

| Зона | Что лежит | Почему запрещена | Как обойти |
|---|---|---|---|
| `lib/` | кастомные сервисы поверх nanobot | меняет ядро runtime | `lib_additions[]` с явным `scope` |
| `sql/` | DDL-каталог | ломает миграции других фич | не предназначено для фич (только core) |
| `requirements.txt` | зависимости | меняет общее окружение | через OpenSpec change + релиз владельца |
| `Dockerfile` | Docker-конфиг | инфраструктура кластера | через OpenSpec change |
| `config.json` | нативный конфиг nanobot | конфликтует с другими фичами | только core-изменения через релиз |

Любой diff в эти зоны → `FORBIDDEN_ZONE_TOUCH` warning при `validate`
(не блокирует, но требует явного подтверждения оператора). Чтобы
**избежать warning**, используйте `lib_additions[]` для `lib/`, и не
трогайте остальные зоны вообще.

### 1.2. Разрешённые зоны (через манифест)

| Зона | Манифест-поле | Когда применять | Контракт |
|---|---|---|---|
| `workspace/skills/<name>/` | `skills[].path` | новый skill | см. §3.1 |
| `workspace/tools/<name>.py` | `tools[].path` | новый tool | см. §3.2 |
| `lib/services/<file>.py` | `lib_additions[].path` | cross-cutting утилита | см. §3.3 |
| `docs/<file>.md` | `isolation.files_added` | документация фичи | `docs/<file>.md` |
| `tests/test_<name>.py` | `isolation.files_added` | тесты фичи | см. §4.2 |
| `workspace/data_store/cache/<file>` | `testing.data_generator.filesystem.target_path` | тестовые данные | см. §5.3 |
| `project.json` | `overlays.project_json[]` | точечные правки конфига | см. §3.4 |

**Любые другие файлы** (включая `README.md`, `AGENTS.md`, `CHANGELOG.md`)
должны попадать в `isolation.files_added` или `files_modified` явно —
glob'ы вроде `**/*` тоже работают (резолвятся в список реальных файлов
в `applier.py::_resolve_manifest_paths`).

### 1.3. Иерархия путей в манифесте

Все пути в `feature.yaml` — **относительно корня `audit_nanobot`**
(точка отсчёта — `git rev-parse --show-toplevel`). Это важно при
diff'е с `master`: validator сравнивает файлы по relative-paths.

## 2. `feature.yaml` schema по блокам

Полная schema — в `audit_bridge/feature_bridge/schemas.py::FeatureManifest`
(pydantic v2, `extra="forbid"`). Здесь — практический разбор по блокам.

### 2.1. `feature` (метаданные)

```yaml
feature:
  id: d6_nanobot                # обязательное, уникальное; kebab-case → snake_case
  name: ior-analyzer            # обязательное; = skills[].name если есть
  version: 1.0.0                # обязательное; semver
  description: >                # обязательное, ≥1 строка
    Анализ инцидентов операционного риска...
  documentation: docs/D6.md     # опционально, путь к README фичи
  maintainer: "@kay-audit/audit-team"  # опционально
```

**`id`** используется как ключ в `.bridge_features.json::applied[]` и
для rollback (`POST /api/features/{id}/rollback`). Менять `id` после
первого apply нельзя — старые записи state станут orphan.

**`version`** сравнивается через `_satisfies_version` в `validator.py:162`
(простая semver-семантика: `1.2.3`, `>=2.5.0`, `<3.0.0`).

### 2.2. `requires` (зависимости)

```yaml
requires:
  nanobot_version: ">=2.5.0"    # обязательное, semver-требование
  system_deps:                   # опционально, информативно
    - python>=3.14
  env_vars:                      # обязательное для прод-фич (см. ниже)
    - name: NANOBOT_SKILLS_RUNTIME
      required: false
      default: production
      allowed: [production, testing]
      description: "Режим работы skill'ов"
      scope: feature            # feature | cross_cutting
```

**`env_vars[].scope`**:
- `feature` — переменная фичи, rollback очищает её (через backup `project.json`)
- `cross_cutting` — общая инфраструктура, rollback оставляет значение

Если фича переключает runtime (`NANOBOT_SKILLS_RUNTIME`) — переменная
должна быть `feature`, иначе после rollback другой фичи runtime
останется в testing.

### 2.3. `skills[]` (один или несколько skill'ов)

```yaml
skills:
  - name: ior-analyzer           # обязательное; = SKILL.md::frontmatter::name
    path: workspace/skills/ior-analyzer   # обязательное, относительный путь
    pattern: full                # full | docs-only | map_reduce
    config_section: null         # null = native tool registration (НЕ пишем skills.<name> в project.json)
```

**`pattern`**:
- `full` — skill с LLM-выводом, требует `scripts/cli.py` (Stage 3 validator).
- `docs-only` — skill без кода (только `SKILL.md` + `references/`).
- `map_reduce` — skill с map-reduce режимом.

**`config_section`**:
- `null` (по умолчанию) — skill регистрируется как native tool через
  `RuntimePatcher.patch_project_tools`. **`project.json::skills.<name>` НЕ пишем.**
  Подходит для фич с одним инструментом вроде `ior-analyzer`.
- `"skills.ior-analyzer"` — регистрация в `project.json::skills.*`
  (классический путь для `audit_analyzer`, `legal_summarizer`). Требует
  секцию в project.json (см. §5).

### 2.4. `tools[]` (native-инструменты)

```yaml
tools:
  - name: ior_analyzer           # обязательное; = Python class (snake_case)
    path: workspace/tools/ior_analyzer.py  # обязательное
    config_key: ior_analyzer     # обязательное, уникальное в bridge
    config_section: gateway.ior_analyzer  # обязательное, начинается с 'gateway.'
```

**`config_key`** — уникальный ключ в `.bridge_features.json::applied[]`
для отслеживания и rollback. Validator (Stage 6) проверяет, что
`config_keys_added` не пересекается с уже применёнными фичами.

**`config_section`** — должен начинаться с `gateway.` (warning иначе).
Tool читает свою секцию через `ctx._settings_ref.gateway.<config_key>`.

### 2.5. `overlays` (правки project.json)

```yaml
overlays:
  project_json:
    - op: upsert                  # upsert | remove
      path: gateway.ior_analyzer.enable
      value: true
      rationale: "Регистрация native tool в RuntimePatcher..."
  env_files: []                   # правки .env (обычно пусто)
  templates: []                   # Jinja-шаблоны для генерации (обычно пусто)
  sql_migrations: []              # пути к .sql-файлам (см. §5.4)
```

**`path`** — dotted path в `project.json`. Допустимы только:
- `gateway.<config_key>.*` — секция, объявленная в `tools[].config_section`
- `skills.<name>.*` — секция, объявленная в `skills[].config_section`
- Другие пути — Stage 5 validator падает с `OVERLAY_PATH_UNEXPECTED`

**`rationale`** — обязательное поле (≥1 предложение). Это объяснение
для оператора при review манифеста, **не** комментарий для кода.

### 2.6. `lib_additions` (cross-cutting утилиты)

```yaml
lib_additions:
  - path: lib/services/skill_runtime_mode.py
    justification: "Cross-skill runtime switch. Используется обоими: ..."
    scope: cross_cutting           # feature | cross_cutting
    reuse_targets:                 # обязательное для cross_cutting
      - workspace/tools/ior_analyzer.py
      - lib/services/sql_assistant_runtime.py
```

**`scope`**:
- `feature` — файл удаляется при rollback этой фичи.
- `cross_cutting` — файл **остаётся** при rollback (другие фичи могут
  зависеть). `reuse_targets` обязателен — перечисляет фичи-потребители.

### 2.7. `tests` (pre-flight pytests)

```yaml
tests:
  - path: tests/test_tools_ior_analyzer.py
    marker: optional               # required | optional
  - path: tests/test_ior_bge_followup_contract.py
    marker: required
```

**`marker`**:
- `required` — pytest **обязан** пройти (`pytest ... -q --tb=short`
  exit 0). При провале apply откатывается.
- `optional` — pytest запускается, но **провал не блокирует apply**.
  Полезно для тестов, которые требуют внешних сервисов (Greenplum, LLM).

Команда pytest формируется в `applier.py::_build_pytest_command`:
```
pytest tests/test_tools_ior_analyzer.py tests/test_ior_bge_followup_contract.py
  -q --tb=short -p no:cacheprovider
```

### 2.8. `testing` (pre-flight data + smoke)

```yaml
testing:
  runtime_env: NANOBOT_SKILLS_RUNTIME   # обязательное, имя env-var для переключения
  runtime_value: testing                # значение во время smoke (production | testing)
  runtime_data_backend: testing_pg      # бэкенд данных для тестов
  data_generator:                       # обязательное для фич с тестовыми данными
    path: workspace/skills/<name>/testing/data_generator.py
    command: "python workspace/skills/<name>/testing/data_generator.py --force"
    target_storage: filesystem         # filesystem | postgres
    filesystem:
      target_path: workspace/data_store/cache/testing/<name>/<file>.json
    idempotent: true                    # повторный запуск не ломает состояние
    deterministic: true                 # результат воспроизводимый (seed фиксирован)
    seed: 20260921                      # обязательное при deterministic: true
    record_count: 1000                  # обязательное при deterministic: true
  runner:                               # обязательное, smoke-промпт
    path: workspace/skills/<name>/testing/runner.py
    smoke_command: "python workspace/skills/<name>/testing/runner.py --smoke"
    smoke_prompt: "Сколько инцидентов в 2024 году?"
    smoke_expected:                     # обязательное, что ожидаем
      contains_section: "Сформированный отчёт"
      min_records_in_evidence: 1
  pytest:                               # ссылка на блок tests выше
    command: "pytest tests/test_tools_ior_analyzer.py ... -q --tb=short"
    marker_required_strict: true        # обязательное, при false — fail-safe вниз
    marker_optional_strict: false
```

**`testing.runner.smoke_expected`** — структурный контракт. Что должно
найтись в отчёте runner'а. `applier.py::_run_smoke` парсит stdout
runner'а и валидирует эти поля.

### 2.9. `isolation` (rollback-метаданные)

```yaml
isolation:
  removable: true                       # можно ли удалить фичу (rollback)
  strategy: files_and_config            # files_and_config | config_only | files_only
  files_added:
    - lib/services/skill_runtime_mode.py
    - docs/D6.md
    - workspace/skills/ior-analyzer/**
    - workspace/tools/ior_analyzer.py
    - tests/test_tools_ior_analyzer.py
    - tests/test_ior_bge_followup_contract.py
  files_modified: []                    # файлы, которые меняем (но не владеем)
  config_keys_added:
    - gateway.ior_analyzer.enable
  config_keys_removed: []
```

**`files_added`** — глоб'ы (`**/*`) разрешены, резолвятся в
`applier.py::_resolve_manifest_paths` против `git diff master..HEAD --name-only`.

**`config_keys_added`** — список dotted paths. Stage 6 validator
проверяет пересечение с уже применёнными фичами.

## 3. Подробные контракты

### 3.1. Skill (`workspace/skills/<name>/`)

Минимальная структура:

```
workspace/skills/ior-analyzer/
    SKILL.md                    # обязательно; frontmatter с name + description
    config.json                 # опционально, для skill-specific config
    tool.py                     # опционально, для навыков с executable entry
    scripts/
        __init__.py             # обязательно для pattern != docs-only
        cli.py                  # обязательно для pattern == full
        analysis_mode/          # произвольные подмодули
        preset_analysis/
    testing/                    # для фич с pre-flight (см. §4.3)
        data_generator.py
        runner.py
    utils/                      # skill-local утилиты (НЕ нарушают границу Skill↔Tool)
    knowledge_base/             # произвольные данные
    testing/
```

**Контракт SKILL.md** (валидируется в Stage 3):

```markdown
---
name: ior-analyzer                       # ОБЯЗАТЕЛЬНО; = manifest.skills[].name
description: "Анализ инцидентов..."      # ОБЯЗАТЕЛЬНО; непустое
metadata: {"nanobot":{"emoji":"🛡️"}}    # опционально
---

# Skill Title

(тело SKILL.md — инструкции агенту)
```

Если `name` в frontmatter ≠ `manifest.skills[].name` → ошибка
`SKILL_NAME_MISMATCH`. Это критично — agent runtime выбирает skill
по `name`.

**Граница Skill ↔ Tool** — нормативный контракт в
[`TARGET_ARCHITECTURE.md` §22](TARGET_ARCHITECTURE.md). Skill
**не вызывает** Tool программно; Tool **не импортирует** Skill.
Связь — через agent runtime.

### 3.2. Tool (`workspace/tools/<name>.py`)

Минимальная структура:

```python
"""Native nanobot tool for the ``<skill>`` skill.

The legacy ``workspace/skills/<skill>/tool.py`` is kept as a
compatibility shim for direct skill runs. AgentLoop discovers this module
through ``RuntimePatcher.patch_project_tools`` and registers the tool using
the current nanobot ``Tool.enabled`` / ``Tool.create`` contract.
"""
from __future__ import annotations

from nanobot.agent.tools.base import Tool, ToolResult, tool_parameters
from pydantic import BaseModel

from lib.services.skill_runtime_mode import (
    current_tool_session_id,
    load_testing_module,
    log_skill_runtime,
)


class IORAnalyzerToolConfig(BaseModel):
    """Configuration in ``gateway.<config_key>`` of ``project.json``."""

    enable: bool = True


@tool_parameters({
    "type": "object",
    "properties": {
        "prompt": {"type": "string", "description": "..."},
        "preset": {"type": "string", "enum": [...]},
    },
    "required": ["prompt"],
})
class IorAnalyzerTool(Tool):
    """Описание tool'а для LLM."""

    config_key = "ior_analyzer"
    config_cls = IORAnalyzerToolConfig
    enabled: ClassVar[bool] = True

    async def execute(self, **kwargs: Any) -> ToolResult:
        ...
```

**Контракт Stage 4 validator**:

- Файл существует в ветке (`TOOL_FILE_MISSING` если нет).
- Содержит `class ` (`TOOL_NO_CLASS` — warning, не error).
- `config_key` упоминается в коде (`TOOL_CONFIG_KEY_MISSING` — warning).
- `config_section` начинается с `gateway.` (`TOOL_CONFIG_SECTION_NON_GATEWAY` — warning).

Tool регистрируется через `RuntimePatcher.patch_project_tools` (см.
[`architecture/runtime-patcher-inventory.md`](architecture/runtime-patcher-inventory.md)).
**Это стандартный путь для нативных tool'ов в `workspace/tools/`** —
не нужен `register()` или `__init__.py` экспорт.

### 3.3. `lib_additions` (cross-cutting утилиты)

Когда фиче нужен файл в `lib/` (например, общий runtime-switch для
нескольких skill'ов), его объявляют в `lib_additions[]`:

```yaml
lib_additions:
  - path: lib/services/skill_runtime_mode.py
    justification: "Cross-skill runtime switch. Используется обоими: ..."
    scope: cross_cutting
    reuse_targets:
      - workspace/tools/ior_analyzer.py
      - lib/services/sql_assistant_runtime.py
```

**`scope: cross_cutting`** означает:
- Файл **остаётся** при rollback этой фичи.
- Другие фичи могут объявить тот же файл в своих `reuse_targets`.
- Если при rollback другие фичи ещё зависят от файла — rollback
  блокируется (`DEPENDENCY_BLOCKED`).

**`scope: feature`** означает:
- Файл удаляется при rollback этой фичи.
- Если другие фичи уже depend on — rollback блокируется.

### 3.4. `overlays.project_json[]`

Минимальный overlay для native tool:

```yaml
overlays:
  project_json:
    - op: upsert
      path: gateway.<config_key>.enable
      value: true
      rationale: "Регистрация native tool через RuntimePatcher..."
```

Это создаёт `project.json::gateway.<config_key>.enable = true`. Tool
при старте читает это значение через `ctx._settings_ref.gateway.<config_key>.enable`.

При rollback (Stage 5 / applier) значение восстанавливается из
`.bridge_features/<id>/project.json.bak`.

### 3.5. `sql_migrations[]`

Если фича требует создания таблиц в PostgreSQL:

```yaml
overlays:
  sql_migrations:
    - path: sql/migrations/2026-09-22-create-d6-base.sql
```

**Это НЕ применяется автоматически** — applier только копирует файл
в `sql/migrations/`. Запуск миграции — отдельная операция через
`python tools/migrate.py --apply` (см. `sql/README.md`).

Для **тестовых данных** фичи (которые применяются в pre-flight) —
используйте `testing.data_generator.target_storage: postgres` с
DSN из `testing.data_generator.postgres.<env_var_name>`.

## 4. Порядок проверки

Apply flow (12 шагов, см. `audit_bridge/feature_bridge/applier.py::apply`):

### 4.1. Validate (6 стадий, gate ДО apply)

```
python -m feature_bridge.cli validate --repo-dir <audit_nanobot> <branch>
POST /api/features/validate
```

| Стадия | Что ловит | Severity |
|---|---|---|
| **1. version_check** | `nanobot_version` не удовлетворяет `requires.nanobot_version` | error |
| **2. git_consistency** | diff содержит файлы в FORBIDDEN_ZONES; manifest объявляет несуществующие файлы | error + warning |
| **3. skill_contract** | SKILL.md отсутствует / frontmatter невалиден / `name` ≠ манифесту / scripts/ отсутствует при `pattern != docs-only` / `scripts/cli.py` отсутствует при `pattern: full` | error |
| **4. tool_contract** | tool-файл отсутствует; нет `class `; `config_key` не упоминается; `config_section` не начинается с `gateway.` | error + warning |
| **5. overlay_safety** | overlay пишет в `skills.*` когда `config_section: null`; `lib_additions[].path` не в diff | error + warning |
| **6. conflicts** | `config_keys_added` пересекается с уже применёнными фичами | error |

При `report.ok == false` — apply отказывается. CLI возвращает exit
code 0 (report содержит `ok`), HTTP — 200. **Не** HTTP 500 (см.
`audit_bridge/bridge_ui/routes_features.py::validate`).

### 4.2. Pre-flight (только если validate OK)

```
python -m feature_bridge.cli apply --repo-dir <audit_nanobot> --bridge-root <bridge> <branch>
POST /api/features/apply
```

Шаги pre-flight:

| # | Стадия | Команда | Поведение при провале |
|---|---|---|---|
| 1 | **snapshot** | `.bridge_features/<id>/feature.yaml` (manifest) | apply aborted |
| 2 | **fetch** | `git fetch <remote>` + extract `branch:SHA` | apply aborted |
| 3 | **discover** | `git ls-remote` + load manifest из SHA | apply aborted |
| 4 | **data_generator** | `python <testing.data_generator.command>` | marker_required → apply aborted |
| 5 | **pytest (required)** | `pytest <testing.pytest.command>` (только `marker: required`) | apply aborted |
| 6 | **smoke** | `python <testing.runner.smoke_command>` + проверка `smoke_expected` | apply aborted |
| 7 | **pytest (optional)** | `pytest` (только `marker: optional`) | log warning, apply продолжается |
| 8 | **copy_files** | `cp -r` для всех файлов из `isolation.files_added` | apply aborted |
| 9 | **apply_overlays** | `project.json` patch через deep-merge | apply aborted |
| 10 | **restart_check** | (опционально, если есть в manifest) | log |
| 11 | **state_write** | `.bridge_features.json::applied[<id>]` | apply aborted |
| 12 | **activation_check** | ping gateway (опционально) | log warning |

**`marker_required_strict: true`** — pytest для `marker: required`
выполняется **ДО** smoke. При провале — apply aborted **ДО** копирования
файлов. Это защита от частичного apply с битым кодом.

### 4.3. Что валидатор НЕ ловит

Validator — manifest-gate, не code-gate. **Не** ловит:

- Runtime-баги в skill-коде (неправильные пути вроде `parents[2]`
  вместо `parents[3]`).
- Отсутствующие регистрации в `project.json::skills.*` при
  `config_section: null`.
- Импорты между `workspace/skills/<a>/` и `workspace/skills/<b>/`
  (только AST-grep на запрещённые шаблоны типа `from workspace.skills`).
- Что `data_generator` реально генерит ожидаемые поля.
- Что `runner.py` запускается без ошибок.
- Что pytest проходит (это pre-flight).

Эти проверки — **только через pytest'ы**, объявленные в `feature.yaml::tests`.

## 5. Регистры БД для фичи

### 5.1. Если фиче нужны свои PG-таблицы

Два пути:

#### A. Native tool без `skills.*` секции (как `ior-analyzer`)

Если фича — обёртка над существующей таблицей (например, IOR-инциденты
уже хранятся в `oarb.audit_vectors` или в DuckDB-кэше), и tool просто
их читает:

- `project.json::skills.<name>` **не нужен**.
- `project.json::gateway.<config_key>.enable = true` достаточно.
- Tool читает данные через `lib.services.cache_provider_impl` (DuckDB-кэш)
  или `workspace.utils.db` (PG напрямую).
- `feature.yaml::sql_migrations: []`.

#### B. Skill с собственным реестром (как `audit_analyzer`)

Если фича владеет таблицами (доменная ответственность), добавьте
секцию `project.json::skills.<name>`:

```jsonc
// project.json
{
  "skills": {
    "ior-analyzer": {
      "enabled": true,
      "tables": [
        {"name": "oarb.audit_reports"},
        {"name": "oarb.audits", "label": "scripts_registry"}
      ],
      "vector_indexes": [
        {"name": "audits_index"}
      ]
    }
  }
}
```

И в feature.yaml:

```yaml
skills:
  - name: ior-analyzer
    path: workspace/skills/ior-analyzer
    pattern: full
    config_section: skills.ior-analyzer    # = имя секции в project.json
```

Это автоматическая регистрация через `lib.core.skill_registration.register`
→ `TableRegistry.register(SkillRegistration(...))`. Подробно —
[`table-registry.md`](table-registry.md).

### 5.2. Vector-индексы

Если фича использует FAISS-индексы:

```jsonc
// project.json
{
  "gateway": {
    "vector": {
      "index": {
        "storage_table": "oarb.audit_vectors",   // PG-таблица для эмбеддингов
        "default_root": "data_store/vectors",    // FAISS-индексы на диске
        "backend": "faiss",
        "indexes": {
          "audits_index": {
            "table": "audits_index",                 // DuckDB-снапшот (TableResource)
            "source_table": "oarb.audits",           // PG-источник
            "content_columns": ["description"],
            "embedding_columns": [{"column": "description_embedding"}],
            "track_column": "updated_at",
            "metric": "cosine",
            "enabled": true
          }
        }
      }
    }
  }
}
```

Это **глобальная runtime-БД** декларация (не per-skill), регистрируется
через `lib.core.infra_registration.register_vector_storage` →
`TableRegistry.register_infra("vector.storage", ...)`. Подробно —
[`VECTOR_INDEXES.md`](VECTOR_INDEXES.md).

### 5.3. Тестовые данные (data_generator)

Если фиче нужны воспроизводимые тестовые данные:

```yaml
testing:
  data_generator:
    path: workspace/skills/<name>/testing/data_generator.py
    command: "python workspace/skills/<name>/testing/data_generator.py --force"
    target_storage: filesystem          # или postgres
    filesystem:
      target_path: workspace/data_store/cache/testing/<name>/<file>.json
    idempotent: true
    deterministic: true
    seed: 20260921
    record_count: 1000
```

**Контракт data_generator**:

1. Pure stdlib (не импортирует nanobot-ai — иначе сломается на чистом Python).
2. Принимает `--force` (перезаписать) и `--seed N` (явный seed).
3. Создаёт файл по `target_path`. **Родительский каталог создаёт сам** (`Path.mkdir(parents=True, exist_ok=True)`).
4. JSON в UTF-8, `ensure_ascii=False`.
5. Один и тот же seed → один и тот же файл (byte-identical, можно сверить SHA-256).
6. Дефолтный seed захардкожен в начале файла как `DEFAULT_SEED = <N>`.

### 5.4. Smoke runner

Runner проверяет сквозной flow skill'а (вызов LLM, парсинг ответа,
структура отчёта). Контракт:

```yaml
testing:
  runner:
    path: workspace/skills/<name>/testing/runner.py
    smoke_command: "python workspace/skills/<name>/testing/runner.py --smoke"
    smoke_prompt: "<вопрос пользователя>"
    smoke_expected:
      contains_section: "<заголовок секции в отчёте>"
      min_records_in_evidence: 1
```

Runner пишет JSON-session в `data_store/cache/testing/<name>/sessions/`,
вызывает `lib.services.llm_client.call_llm_json`, валидирует структуру.

## 6. Частые ошибки (case studies из ior-analyzer)

Эти баги найдены при apply ветки `feature/d6-nanobot-with-manifest`
после прохождения validator'а. Validator их **не** поймал — поймали
только pytest'ы, объявленные в `feature.yaml::tests`.

### 6.1. `_SKILL_NAME = "ior_analyzer"` в `skill_config.py`

**Симптом:** `test_data_backend_factory_is_explicit` падает с
`KeyError: "skill 'ior_analyzer' не найден в project.json::skills"`.

**Причина:** `workspace/skills/ior-analyzer/utils/skill_config.py::_SKILL_NAME = "ior_analyzer"`
вызывает `_lib.build_cache_provider("ior_analyzer", _SKILL_ROOT)`.
Но в `project.json::skills` есть только секции для зарегистрированных
skill'ов (как `audit_analyzer`), а для native-tool-registration
(`config_section: null`) — нет.

**Что НЕ так:**

- `config_section: null` означает "не пишем `skills.<name>` в
  `project.json`" (см. §2.3).
- `skill_config.py` всё равно пытается найти `skills.<name>` через
  `_lib.build_cache_provider` — падает.

**Фиксы (один из):**

A. Использовать fallback на `gateway.<config_key>` вместо `skills.<name>`:

```python
# workspace/skills/<name>/utils/skill_config.py
def build_cache_provider() -> Any:
    """Build the standard provider for the native-tool section."""
    cfg = SETTINGS.get("gateway", {}).get(GATEWAY_KEY)
    if not isinstance(cfg, dict):
        return None  # or raise with actionable message
    return _lib._build(cfg, str(_SKILL_ROOT))
```

B. Сделать `_lib.build_cache_provider` tolerant к отсутствию секции:

```python
# lib/core/skill_config.py
def build_cache_provider(skill_name: str, skill_root: Path | str) -> Any:
    from lib.services.cache_provider_impl import build_cache_provider as _build
    cfg = _skills().get(skill_name)
    if not isinstance(cfg, dict):
        return _build({}, str(skill_root))  # empty config → default cache
    return _build(cfg, str(skill_root))
```

**Как предотвратить в следующий раз:** добавить тест, который
вызывает `build_cache_provider` с native-tool именем и проверяет,
что возвращается default provider, а не KeyError.

### 6.2. `parents[2]` вместо `parents[3]` в `bge_search_engine.py`

**Симптом:** `test_default_paths_are_shared_pipeline_paths` падает
с `assert WindowsPath('.../workspace/skills/data_store/...') == WindowsPath('.../workspace/data_store/...')`.

**Причина:**

```python
# workspace/skills/ior-analyzer/utils/bge_search_engine.py:22
_WORKSPACE_DIR = Path(__file__).resolve().parents[2]
```

Для файла `workspace/skills/ior-analyzer/utils/bge_search_engine.py`:

| `parents[N]` | Возвращает |
|---|---|
| `parents[0]` | `workspace/skills/ior-analyzer/utils/` |
| `parents[1]` | `workspace/skills/ior-analyzer/` |
| `parents[2]` | `workspace/skills/` ← **получили** |
| `parents[3]` | `workspace/` ← **хотели** |
| `parents[4]` | корень `audit_nanobot/` |

Тест ожидает путь относительно `workspace/`, не `workspace/skills/`.

**Фикс:**

```python
_WORKSPACE_DIR = Path(__file__).resolve().parents[3]
```

**Как предотвратить в следующий раз:**

- При написании `Path(__file__).resolve().parents[N]` — посчитать вручную
  по дереву каталогов.
- Тест, который проверяет финальный путь (`test_default_paths_are_shared_pipeline_paths`)
  уже есть — он поймал баг. **Не удалять такой тест при рефакторинге.**

### 6.3. `D feature.yaml` после rollback

**Симптом:** после `POST /api/features/{id}/rollback` файл `feature.yaml`
в рабочей копии `audit_nanobot` помечается как `D` (deleted) в
`git status`.

**Причина:** rollback **не восстанавливает** файлы, добавленные через
apply — он только **удаляет** их (см. `rollback.py::_remove_path`).
Но `feature.yaml` при apply добавляется как часть извлечения файлов
из ветки, а rollback не знает, что это служебный манифест bridge'а.

**Workaround:** после rollback восстановить `feature.yaml` вручную
(`git checkout HEAD -- feature.yaml` или из remote-ветки).

**Должный фикс (TODO):** добавить `feature.yaml` в список
`is_own_skill_files` или пометить как `BRIDGE_MANIFEST` в apply —
чтобы rollback не трогал его.

### 6.4. JSON5 в `project.json` ломает apply

**Симптом:** при `POST /api/features/apply` после успешного validate
получаем `JSONDecodeError: Expecting value: line 1 column 1 (char 0)`.

**Причина:** `project.json` на локальной машине содержит JSON5-комментарии
(`// ...`), но `_apply_project_json_overlay` использует `json.loads`,
который не понимает `//`.

**Фикс:** либо убрать `//` (вернуть к plain JSON), либо применить
парсер JSON5 (но это лишняя зависимость).

**Как предотвратить:** добавить lint-проверку `project.json` (только
в CI, не в runtime — CI ловит до merge в master).

## 7. Чеклист перед commit `feature.yaml`

Каждый пункт — **минимум**, без которого apply не пройдёт или pytest
упадёт. Если что-то ниже нельзя поставить галочкой — отложите commit
до устранения.

### 7.1. Манифест

- [ ] `feature.id` уникален в `.bridge_features.json::applied[]`
      (иначе Stage 6 → `CONFIG_KEY_CONFLICT`).
- [ ] `feature.version` соответствует SemVer; `requires.nanobot_version`
      совместим с текущим `nanobot`.
- [ ] Все `skills[].path` существуют в ветке (проверить через `git ls-tree`).
- [ ] Все `tools[].path` существуют в ветке.
- [ ] Все `lib_additions[].path` существуют в `git diff master..HEAD --name-only`.
- [ ] `overlays.project_json[].path` — допустимый (см. §2.5).
- [ ] `isolation.files_added` покрывает **все** новые файлы (glob'ы
      резолвятся, проверьте через `git diff master..HEAD --name-only | grep`).

### 7.2. Код

- [ ] Все пути вроде `Path(__file__).resolve().parents[N]` посчитаны
      по дереву (см. §6.2).
- [ ] Никаких `from workspace.skills.*` импортов внутри `lib/services/`
      (Skill ↔ Tool граница — см. §3.1 и `TARGET_ARCHITECTURE.md` §22).
- [ ] Никаких `from nanobot.X.Y` импортов внутри `workspace/skills/*/utils/`,
      если только это не через `lib.core.skill_config` (см. §3.3).
- [ ] `tool.config_key` упоминается в коде tool'а (`TOOL_CONFIG_KEY_MISSING`
      при validate).
- [ ] Все `env_vars` с `required: false` имеют `default` (иначе TypeError
      при runtime-resolve).

### 7.3. Тесты

- [ ] `testing.pytest.command` ссылается на все тесты из `feature.yaml::tests`.
- [ ] `marker_required_strict: true` если хотя бы один тест `marker: required`.
- [ ] `testing.runner.smoke_prompt` соответствует реальному use-case
      (тот же, что в `SKILL.md::Контракт вызова`).
- [ ] `testing.runner.smoke_expected.contains_section` — реальный
      заголовок из отчёта runner'а.

### 7.4. Локальная проверка

```bash
# 1) Validator — должен вернуть ok=true.
python -m feature_bridge.cli validate --repo-dir <path-to-audit_nanobot> <branch>

# 2) Data generator — должен создать файл с детерминизмом.
python <testing.data_generator.command>
sha256sum <target_path>
python <testing.data_generator.command>
sha256sum <target_path>     # SHA должен совпасть

# 3) Pytest — все required должны пройти.
pytest <testing.pytest.command>

# 4) Smoke — runner должен выдать секцию, указанную в smoke_expected.
python <testing.runner.smoke_command> | grep "<contains_section>"
```

Если хотя бы один пункт не прошёл — **не делайте commit манифеста**:
коммитните фикс кода, потом перегенерите `feature.yaml` (или подправьте
тот же), и перепройдите локальную проверку.

### 7.5. После merge в master

После того как ветка `feature/<name>-with-manifest` смёржена в master:

1. Удалить feature-ветку локально: `git branch -d feature/<name>-with-manifest`.
2. Удалить remote-ветку: `git push origin --delete feature/<name>-with-manifest`.
3. **НЕ** удалять `.bridge_features.json` или `.bridge_features/` —
   state живёт там, иначе теряются записи applied[].
4. Обновить [`skill-tool-inventory.md`](skill-tool-inventory.md) — добавить
   новый skill/tool в таблицу.

## 8. Перекрёстные ссылки

### Нормативные контракты (правила, не описание as-is)

- [`TARGET_ARCHITECTURE.md`](TARGET_ARCHITECTURE.md) — общий нормативный
  контракт проекта. Особенно §22 (Skill ↔ Tool).
- [`openspec/specs/architecture/skill-tool-boundary/`](../openspec/specs/architecture/skill-tool-boundary/spec.md)
  — component-level spec на границу Skill/Tool.

### Практические гайды

- [`SKILL_AUTHORING.md`](SKILL_AUTHORING.md) — **пошаговый** гайд
  создания skill (SKILL.md, project.json, runtime API, anti-patterns).
- [`skill-tool-architecture.md`](skill-tool-architecture.md) — что
  разрешено и запрещено в коде skill и tool (разделённые импорты,
  общие сервисы).
- [`table-registry.md`](table-registry.md) — как skill объявляет свои
  PG-таблицы и vector-индексы.
- [`VECTOR_INDEXES.md`](VECTOR_INDEXES.md) — FAISS, Ollama, lifecycle
  кеша.
- [`INTERNAL_API.md`](INTERNAL_API.md) — кастомные tool'ы (`workspace/tools/`),
  CLI-режимы, добавление настроек.

### Со стороны audit_bridge

- `audit_bridge/docs/FEATURE-BRIDGES.md` — обзор системы feature-bridge
  (state, queue, applier, rollback, start-all.ps1 Step 7.5).
- `audit_bridge/feature_bridge/schemas.py` — pydantic schema `feature.yaml`
  (`FeatureManifest`, `IsolationBlock`, `LibAddition`).
- `audit_bridge/feature_bridge/validator.py` — 6-стадийный validator.
- `audit_bridge/feature_bridge/applier.py` — 12-шаговый applier с pre-flight.
- `bridge_skills.md` (в `audit_point/`) — исходный план с дизайн-обоснованиями.

### Каталог

- [`docs/README.md`](README.md) — навигационный индекс проекта.
