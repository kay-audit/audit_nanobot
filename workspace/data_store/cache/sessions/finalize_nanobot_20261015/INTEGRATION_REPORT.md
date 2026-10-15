# Локальная интеграция Nanobot — 15.10.2026

**PARTIALLY READY.** Все четыре компонента объединены поверх актуальной vllm_fix. Обязательные offline-контракты проходят; **новых failures относительно baseline нет**. Общий suite остаётся красным из-за десяти исходных ошибок. Настоящие PG/GP, vLLM и GPU/NFS-контур не проверены. Push/PR не выполнялись.

Проверенный SHA исходников finalize_nanobot: **b8fac7ab920964eed8bbb1d88ded3fd28dd1fb9d**. Завершающий коммит добавляет этот отчёт и проверочные артефакты; его точный SHA и окончательный git status записаны в [FINAL_STATE.json](FINAL_STATE.json), сформированном после коммита. Runtime-код в report commit не меняется.

## 1. Исходное состояние и Git-безопасность

Выполнены git worktree list, status, branch --show-current, log -1. Уже существующий D:/audit-dev/audit_nanobot_finalize был чистым, finalize_nanobot точно совпадала с origin/vllm_fix (634afff). Повторно worktree и ветка не создавались. Основное дерево тоже было чистым, на feature/d3_crm.

Выполнен fetch origin --prune. Все пять актуальных SHA совпали с заданием. Prune удалил только локальную устаревшую tracking-ссылку origin/feature/d6_ior-clean. Удалённые ветки не изменялись. Для пяти состояний созданы backup refs codex/backup-finalize-20261015/...

Локальная D6 была позади origin на четыре коммита и обновлена fast-forward с 89da442 до 0c70948. Отсутствовавшая локальная D5 создана как tracking-ветка origin/feature/d5_followup. master/vllm_fix не менялись. Ни одна исходная ветка не получала merge finalize или другой feature. Основное дерево возвращено на D3. Worktree не удалялись.

Полные исходные и итоговые SHA, история merges и список файлов приведены в приложении ниже.

## 2. Аудит пересечений и сохранность базы

Порядок: **D6 → D5 → D3 → OSIRIS**, затем дополнительные merges исправлений исходных веток. Он сохраняет исходную Appeals-интеграцию до подключения универсального runtime.

Полная [матрица пересечений](FILE_OVERLAP_MATRIX.md) и [TSV](evidence/branch-files.tsv) построены до merge по git diff origin/vllm_fix...origin/feature. Совместное владение путём ещё не означает текстовый конфликт.

Ключевые пересечения: config.json, feature.yaml, gateway.py, llm_client/llm_config/llm_reasoning/runtime_patcher, шесть файлов общего osiris_runtime и общие D3/OSIRIS файлы.

Финальный diff относительно origin/vllm_fix **пуст** для config.json; lib/services/llm_client.py, llm_config.py, llm_reasoning.py, runtime_patcher.py; workspace/skills/audit_analyzer/scripts/cli.py, pg_provider.py; tests/test_prepare_outbound_suppressed.py и tests/test_runtime_patcher.py. В runtime_inventory сохранены все исходные feature ToolSpec и добавлен optional osiris. vllm_fix является предком HEAD.

Выбор модели в config.json сохранён: MiniMax. Проверена форма vLLM/Qwen reasoning: chat_template_kwargs.enable_thinking, для MiniMax — reasoning_split. Реальный запрос к vLLM не выполнялся.

## 3. Конфликты и решения

| Этап | Конфликт | Решение |
|---|---|---|
| D6 | config.json, llm_client.py | Сохранена база vllm_fix: конфликт отступов extraBody и комментария; функциональный LLM-код совпадал |
| D5 | те же два файла, feature.yaml | LLM-база сохранена; manifests объединены, исходные snapshots сохранены |
| D3 | те же три файла, lifecycle.py | База LLM сохранена; lifecycle из D3 содержит Windows process probe и retry атомарной замены, совместимые с IOR |
| OSIRIS | runtime_inventory.py | Объединены исходные D3/D5/D6 ToolSpec и osiris |
| OSIRIS | test_appeals_production_contracts.py | Сохранены актуальные D3-тесты и добавлен контракт idle timeout через metadata |
| Дополнительный D3 merge | COMPONENTS.md | Сохранены OSIRIS и Appeals entries, статистика пересчитана: 26 компонентов, 24 partial, 2 draft |

Остальные дополнительные merges — без конфликтов. Стратегии merge -X ours/theirs не использовались; отдельные версии выбирались после чтения diff. Интеграционные изменения без владельца: combined project.json/feature.yaml и перечисленные conflict resolutions.

## 4. Дефекты и исправления у владельцев

| Симптом / причина | Исходный коммит | Проверка и перенос |
|---|---|---|
| OSIRIS давал бы UNEXPECTED: нет canonical ToolSpec | OSIRIS 82b9bc9 | test_native_osiris_is_known_to_startup_inventory; merge 6717760 |
| D3 startup mock скрывал экспорты ApplicationContext | D3 383a29e | Подмена только create; прежние assertions порядка сохранены; ae530f5 |
| Первый call после тихой смерти MCP попадал в мёртвую сессию | D5 d3b19c7 | Preflight ping до отправки; реальные процессы, смерть/restart/concurrency; eb174e1 |
| Manifest D5 писал gateway.follow_up вместо читаемого tools.follow_up; tests использовали POSIX PID API и CRLF input | D5 d3b19c7 | Исправлены activation key и переносимость tests; eb174e1 |
| XLSX normalizer возвращал None, словари заголовков были локальными; fallback ссылался на очищаемое имя except | D6 063080d | Реальные XLSX: основной/возмещения/финансы, длинный ID, денежные значения, missing schema; aebac02 |
| OSIRIS registration test считал свой tool последним при наличии других plugins | OSIRIS e85154b | Ровно одна регистрация по имени через настоящий loader; baa4d98 |
| SQLite D5 не стартовала на Windows из-за unix-none; os.kill(pid,0) опасен как Windows probe | D5 8f0eb4b | Native VFS на Windows, POSIX/NFS сохранён; SQLite/PID/две сессии/уточнение; e5e119d |
| Full-suite терял backend path D5; seed SQL не совместим с GP 6.5 | D5 fac3da3 | Per-test syspath, UPDATE/conditional INSERT под transaction/table lock; synthetic SQLite insert/update oracle; e5e119d |
| D3 spec не соответствовала validator и отсутствовала в registry | D3 783e98e, a39c978, e4a3fda | Requirement/Scenario, WHEN/THEN, partial entry и registry guard; b7b61da |
| D3 Tool нарушал import boundary; gateway fixtures обращались к реальному structural loader | D3 e4a3fda | Обычный lazy import с isolated alias, mock внешнего loader в gateway unit tests; production tests loader сохранены; b7b61da |
| Округление monotonic deadline давало recovery budget 0.2000000000007276 вместо ≤0.2 | OSIRIS 2a0683c, 4fa10d2 | Clamp исходным timeout, frozen-clock tests legacy/JSON; 55bbacb |

Assertions не отключались и не ослаблялись. Новый XLSX-тест использует существующий заголовок FINANCIAL_RENAME «Сумма последствия (руб.)», совпадающий с ior_reports.py; доменный словарь не переписывался.

Проверенные места реализации: IOR utils/dataframe_ops.py:718,1021; workspace/tools/follow_up.py:220; D5 backend/storage/database.py:276 и writer.py:90; tools/seed_follow_up_data.py:56 и seed_follow_up_extended.py:239; workspace/tools/appeals_analyzer.py:118; osiris_runtime/client.py:94 и json_transport.py:117. Полные пути изменённых файлов — в приложении.

## 5. D3, IOR и универсальный OSIRIS

Сохранена одна общая реализация workspace/utils/osiris_runtime: registry, lifecycle, legacy и JSON transports. Контракт registry — registry.py:17.

- Appeals: service appeals, job srb_d3, NFS workspace/data_store/osiris/srb_d3, retrieve/rerank/embed. Проверены canonical text, appeals_analysis JSON, legacy CSV, NEW_SEARCH/FOLLOWUP, отсутствие ложной сохранённой выборки, structural prefilter, retrieve/rerank, XLSX и MessageTool contracts.
- IOR: отдельный service ior, job srbd6, NFS workspace/data_store/osiris/ior, собственный worker и ML-контракт embed/build_index/search/retrieve/rerank. Он сохраняет legacy transport; его не подменяли универсальным JSON worker.
- Native osiris и Python API используют общий backend. Native tool разрешает Appeals capabilities; IOR не включён в JSON allowlist, поскольку его worker сохраняет собственный legacy-контракт.
- Idle default — 28800. Native administrative stop выключен, admin_session_keys пуст. SDK/GPU/ML при обычном импорте/регистрации не запускаются.
- Mocks/synthetic filesystem подтверждают reuse одной job, locks, heartbeat, recovery, correlation, idle decision, artifacts и stop authorization. Живой GPU/NFS-кластер этим не подтверждён.

Исходная D3 не получила workspace/tools/osiris.py, универсальные api.py/JSON dispatcher. Работа компонентов не загрязняла исходные ветки чужими feature merges.

## 6. Конфигурация и manifests

project.json дополнен reviewed overlays. Его JSONC-комментарии и исходные настройки сохранены. Включены gateway.ior_analyzer, gateway.appeals_analyzer, tools.follow_up и tools.osiris. IOR регистрирует пять таблиц, Follow-up — шесть из своего manifest; это не все таблицы V007/V008.

skills.appeals_analyzer.enabled=false намеренно сохранён: generic full-text sync запрещён, production loader грузит structural fields (workspace/utils/appeals_structural_cache.py:294). Skill и Tool при этом доступны.

В config.json нет второго Follow-up MCP server: семь tools регистрирует native aggregator, один процесс обслуживает один bridge. Реальная регистрация всех десяти feature tools подтверждена.

Корневой feature.yaml описывает интеграцию: source SHA, skills/tools/overlays/tests и apply_method. Byte-exact [original manifests](manifests/original/) и [final manifests](manifests/final/), [хэши](evidence/manifest-hashes.json) сохранены. OSIRIS source manifest всё ещё описывает унаследованную D3; universal osiris добавлен отдельно в combined manifest/config, snapshot не выдан за универсальный manifest.

В checkout нет feature_bridge/audit_bridge application runner; feature_bridge не установлен. Одного merge недостаточно: overlays применены явно. Внешний apply/activation, seed и DDL не запускались. V006/V007/V008 и test_d3 SQL сохранены для оператора, их наличие не означает применения.

## 7. Окружение и результаты

Windows, отдельная integration .venv, Python 3.12.14, nanobot-ai 0.3.5, Ruff 0.16.4, root requirements + дополнительные requirements D5. [Точный freeze](evidence/final-python312-freeze.txt). Пользовательские окружения не переустанавливались.

Окружение первоначально располагалось под session workspace, поэтому AST guards сканировали зависимости. Оно перенесено в .venv, temp — в .venv/test-tmp. Первоначальные setup-прогоны не считаются authoritative baseline. Source guard в основном дереве остановлен из-за 18 тысяч исторических cache .py; targeted source tests выполнены, общий guard реально пройден в integration worktree.

| Проверка | Результат |
|---|---|
| Baseline vllm_fix offline | 4245 passed, 10 failed, 35 skipped, 11 deselected, 1 xpassed |
| Окончательный offline suite | **5366 passed, 10 failed, 36 skipped, 11 deselected, 1 xpassed; 48 subtests passed** |
| Baseline/final comparison | **0 новых failures**, точно те же десять имён |
| Обязательные файлы задания в final suite | **347 passed, 1 skipped**, 48 subtests отдельно |
| Расширенный IOR на общем runtime | 273 passed, 2 skipped, 67 subtests passed |
| Исходный D3 после последних fixes | 239 passed, 28 subtests passed |
| Исходный D5 после последних fixes | 60 passed; integration D5 + GP guard 64 passed |
| Исходный OSIRIS после budget fixes | 124 passed |
| compileall lib/workspace/tests, Python 3.12 | Passed, без исключения runtime-кода |
| compileall Python 3.11.9 | Passed, **только syntax**; зависимости/runtime не проверены |
| Python 3.14 | Не установлен, runtime не проверен; требование D6 manifest не снижалось |
| Настоящий offline ApplicationContext/AgentLoop | Passed: три skill, 15 project tools, из них 10 feature; нет failed/duplicate/drift, SDK/torch не импортированы |
| Настоящий D5 MCP transport | Passed: initialize, list_tools, три validation calls, ровно семь tools |
| Полный локальный D5 startup | **exit 0**, новая SQLite, server ready, GP=false, пустые индексы, без LLM calls |
| Gateway обычный --smoke | Blocked: отсутствует DATABASE_URL |
| Critical Ruff для lib/tools/shared runtime и IOR/Appeals, без notebooks | Passed |
| Полный Ruff | Failed: **1184 замечания**, включая feature lint debt/reference notebooks; baseline имел 6 |
| Component spec validation | Failed: **200 исходных нарушений**, новых нет |
| git diff --check / конфликты | Passed / неразрешённых конфликтов нет |

Полные локальные raw logs и JUnit: [baseline](evidence/baseline-offline.log), [final](evidence/final-suite.log), [final XML](evidence/final-suite.xml), [IOR](evidence/final-ior.log), [bootstrap](evidence/final-bootstrap.log), [D5 full startup](evidence/d5-corpus-startup-full.log). [Comparison JSON](evidence/regression-comparison.json) фиксирует имена failures. JUnit passed включает один XPASS, поэтому raw counter на единицу выше терминального passed.

D5 backend исключён штатным ruff.toml навыка; общий lint не выдаётся за отдельный аудит всего backend.

В Git сохраняются отчёт, reviewed scripts/manifests и компактные metadata. .venv, временные SQLite и raw generated output остаются локальными runtime-артефактами.

## 8. Унаследованные baseline failures

| Причина | Число | Проверки |
|---|---:|---|
| audit_analyzer/scripts/llm.py перекрывает пакет legal_summarizer/scripts/llm | 4 | Acceptance matrix, information preservation (2), resume |
| Отсутствует .secrets.env/DATABASE_URL | 3 | Два config-теста и cli_agent --smoke |
| Windows subprocess stdout encoding | 1 | audit_analyzer CLI --help, UnicodeEncodeError |
| Upstream metadata updated_at не изменяется после save | 1 | Контракт nanobot-ai 0.3.5, также падал до merge |
| Устаревший audit skill test требует удаления действующего duckdb_query_tool | 1 | Противоречит сохранённому runtime-контракту базы; assertion не менялся |

Общие базовые ошибки не назначались произвольному feature owner. Они не объявлены успешными. Один XPASS и pytest warnings не скрыты.

## 9. Blocked by environment / закрытый контур

1. tests/test_cache_ownership_claim.py требует настоящую PG и пишет claims. Первый запуск упёрся в недоступный DSN; итоговый offline suite явно использует --ignore этого файла. Assertions не менялись. Ещё 11 opt-in live/integration cases deselected штатными markers. Это не passed и не автоматически добавленный skip.
2. Настоящие ApplicationContext.start, Gateway/PostgresChannel leases, processing/finalization и media delivery через живую БД. Offline suppressed/media contracts прошли, живой канал не стартовал.
3. GP sources, structural plans/schema/permissions, реальные объёмы/hydration и IOR/D5 таблицы. Production миграции/seed/DELETE/TRUNCATE не выполнялись.
4. OSIRIS SDK/scheduler, concurrent actual job reuse, CUDA image/веса, реальный FAISS/BM25 retrieval/rerank, NFS atomic rename/locks/permissions между узлами, recovery и освобождение GPU через 28800 секунд.
5. Реальный Qwen/vLLM tool calling и reasoning/text responses. Request shape проверен offline; endpoint/ключи/веса недоступны.
6. D5 реальные поручения/акты/письма, уточнения и карточки с моделью. Локально подтверждены transport, validation, session memory/clarification persistence, seed upsert и старт пустого корпуса, а не качество ответов на закрытых данных.

Закрытый smoke: concurrent start и две сессии/одна actual job Appeals; отдельная IOR job/root; canonical+JSON+CSV NEW_SEARCH, затем FOLLOWUP и пустой mask; XLSX в реальном чате с terminal status вместо processing; D5 уточнение/ответ/карточка и изоляция аудиторов; authorized stop, idle/recovery/NFS artifact cleanup. OSIRIS runbook: docs/OSIRIS_RUNTIME.md.

## 10. Воспроизведение и локальный запуск

Все команды — из D:/audit-dev/audit_nanobot_finalize. Для нового окружения: согласованный Python, requirements.txt, workspace/skills/follow_up/requirements.txt, pytest/pytest-asyncio, Ruff 0.16.4; точные версии — freeze. Python 3.11 здесь подтверждает только синтаксис.

```powershell
Set-Location D:/audit-dev/audit_nanobot_finalize
$env:PYTHONUTF8 = '1'
$env:PYTHONIOENCODING = 'utf-8'
$py = '.venv/Scripts/python.exe'
$checks = 'workspace/data_store/cache/sessions/finalize_nanobot_20261015'
& $py "$checks/run_check.py" repeat-compile -m compileall -q lib workspace tests
& $py "$checks/run_check.py" repeat-bootstrap "$checks/verify_offline_bootstrap.py"
& $py "$checks/run_check.py" repeat-d5-mcp "$checks/verify_followup_mcp.py"
& $py "$checks/run_check.py" repeat-suite -m pytest tests workspace/skills/audit_analyzer/tests workspace/skills/audit_formulation_strengthener/tests --ignore=tests/test_cache_ownership_claim.py -m 'not live and not integration' -q --tb=short
```

run_check сохраняет command/exit_code/elapsed/log и возвращает настоящий exit code. repeat-suite ожидаемо ненулевой из-за десяти baseline failures.

Для живого запуска подготовить разрешённые **test** DATABASE_URL и LLM endpoint/key, проверить runtime/feature tables, настроить GP schema и NFS/model paths. D6 dev-overlay декларирует public tables; production schema нельзя угадывать. После операторской подготовки схемы:

```powershell
& .venv/Scripts/python.exe gateway.py --profile test --smoke
& .venv/Scripts/python.exe gateway.py --profile test
```

Это инструкция, а не утверждение о выполненном живом запуске. prod не использовать до проверки DSN/schema. Seed и migrate.py --apply не включены в automatic reproduction.

## 11. План публикации

Публикация сейчас не выполняется. После отдельного разрешения: повторный fetch и сверка origin SHA; публикация независимых source commits в свои feature-ветки; закрытый smoke и evidence; затем отдельный обычный push finalize_nanobot без force. master/vllm_fix не обновлять, finalize обратно в source не сливать, universal OSIRIS в D3 не переносить. PR — только по отдельной инструкции.

## 12. Итоговый Git audit

До report commit обе рабочие копии clean. Integration HEAD — b8fac7a, основная копия — feature/d3_crm e4a3fda. Окончательные SHA/status — FINAL_STATE.json. Пять origin refs совпадают с исходными; push не было. Важные состояния доступны через backup refs.

Интеграционные изменения без component owner: project.json/feature.yaml, смысловые конфликтные слияния и сводная статистика COMPONENTS.md. Секреты и live-конфиги не создавались.

Не связанная с задачей непоследовательность документации оставлена для отдельного сопровождения: source OSIRIS manifest называет D3; комментарии AGENTS.md местами описывают старое владение cache-файлом/patch_project_tools; Python >=3.14 отличается от фактически проверенного Python 3.12 и CI matrix. Эти тексты не использовались как доказательство живого контура.

## 13. SHA, коммиты, файлы и результаты по компонентам

### Исходные и итоговые SHA

| Ветка | Initial после fetch | Итоговый local |
|---|---|---|
| `vllm_fix` | `634afff94fd5e7154e000d62bdc3126927be54cf` | `634afff94fd5e7154e000d62bdc3126927be54cf` |
| `feature/d6_ior` | `0c709481b744043046752ff2d461819f3e65d771` | `063080d4c5e0813e4674b8f307c9ee9fbd4173ed` |
| `feature/d5_followup` | `1da5a53cda5aad00b42f753d728dec4a83588fc0` | `fac3da334f199d0e0d127b8aa2c831b4d4eef8a2` |
| `feature/d3_crm` | `ac3f5ee2486b85e0c711930f511e879234bacc88` | `e4a3fda782b1eda63ff4f5205b22a6765c850abc` |
| `feature/osiris` | `ba3702dbad6a04c189ab7fb147bdf186946e5774` | `4fa10d27da83ac9b9ac9b588dfc0cae0c5c4b24a` |

### Integration history

```text
b8fac7a chore(integration): зафиксировать итоговые manifests и статистику компонентов
55bbacb merge(integration): перенести строгий recovery budget OSIRIS
b7b61da merge(integration): перенести D3 import и gateway regression-исправления
e5e119d merge(integration): перенести платформенные и regression-исправления D5
baa4d98 merge(integration): перенести совместимый OSIRIS registration-тест
aebac02 merge(integration): перенести исправление XLSX из исходной D6
6717760 merge(integration): подключить универсальный OSIRIS и общий inventory
ae530f5 merge(integration): подключить D3 и сохранить маршрутизацию Appeals
eb174e1 merge(integration): подключить D5 и объединить feature overlays
cb475ea merge(integration): подключить D6 поверх vllm_fix
```

### feature/d6_ior

```text
063080d4c5e0813e4674b8f307c9ee9fbd4173ed fix(ior): восстановить нормализацию и заголовки атомарного XLSX-export
```

Изменённые source-файлы:

- `docs/D6.md`
- `workspace/skills/ior-analyzer/tests/test_business_contract.py`
- `workspace/skills/ior-analyzer/utils/dataframe_ops.py`

### feature/d5_followup

```text
d3b19c7f0ba3150729e1c5765f41113ccb7112e1 fix(follow-up): проверять MCP перед вызовом и согласовать activation key
8f0eb4bb581f694994207fdda014d9c592932fd0 fix(follow-up): поддержать SQLite и PID-guard на Windows
fac3da334f199d0e0d127b8aa2c831b4d4eef8a2 fix(follow-up): изолировать backend tests и сделать seed SQL переносимым
```

Изменённые source-файлы:

- `docs/D5.md`
- `feature.yaml`
- `tests/test_follow_up_llm_centralization.py`
- `tests/test_follow_up_skill.py`
- `tests/test_postgres_fixture.py`
- `tools/seed_follow_up_data.py`
- `tools/seed_follow_up_extended.py`
- `workspace/skills/follow_up/backend/storage/database.py`
- `workspace/skills/follow_up/backend/storage/writer.py`
- `workspace/tools/follow_up.py`

### feature/d3_crm

```text
383a29ebedc04e441335c5646f46e6778a8c1474 test(appeals): изолировать create без подмены модуля ApplicationContext
783e98e1f0c2032421f8944d92e842e7d1ad8a33 docs(appeals): согласовать заголовки component spec с валидатором
a39c9785523a7994fd50e7779972c737676d7c4d docs(appeals): отметить условия сценария для component validation
e4a3fda782b1eda63ff4f5205b22a6765c850abc fix(appeals): соблюдать import boundary и изолировать gateway-контракты
```

Изменённые source-файлы:

- `docs/D3.md`
- `openspec/specs/COMPONENTS.md`
- `openspec/specs/data/appeals-structural-cache/spec.md`
- `tests/test_appeals_structural_cache.py`
- `tests/test_gateway.py`
- `tests/test_gateway_startup_gate.py`
- `workspace/tools/appeals_analyzer.py`

### feature/osiris

```text
82b9bc934f0ccd770639a708f85f480ea57cae0f fix(osiris): объявить нативный инструмент в startup inventory
e85154bb57ab0539e6d0fab4483472337c1196ff test(osiris): проверять регистрацию среди всех project tools
2a0683cf949afded6f149c4cdc6f436a01348125 fix(osiris): ограничить recovery исходным request timeout
4fa10d27da83ac9b9ac9b588dfc0cae0c5c4b24a fix(osiris): сохранить исходный timeout при JSON recovery
```

Изменённые source-файлы:

- `docs/OSIRIS_RUNTIME.md`
- `lib/services/runtime_inventory.py`
- `tests/test_appeals_osiris_lifecycle.py`
- `tests/test_osiris_runtime.py`
- `tests/test_tools_osiris.py`
- `workspace/utils/osiris_runtime/client.py`
- `workspace/utils/osiris_runtime/json_transport.py`

### Обязательные test files — final JUnit

| Файл | Passed | Failed/Error | Skipped |
|---|---:|---:|---:|
| `tests/test_tools_ior_analyzer.py` | 8 | 0 | 0 |
| `tests/test_tools_appeals_analyzer.py` | 3 | 0 | 0 |
| `tests/test_appeals_routing.py` | 21 | 0 | 0 |
| `tests/test_appeals_production_contracts.py` | 63 | 0 | 1 |
| `tests/test_osiris_runtime.py` | 66 | 0 | 0 |
| `tests/test_tools_osiris.py` | 29 | 0 | 0 |
| `tests/test_follow_up_skill.py` | 32 | 0 | 0 |
| `tests/test_follow_up_llm_centralization.py` | 19 | 0 | 0 |
| `tests/test_runtime_patcher.py` | 75 | 0 | 0 |
| `tests/test_prepare_outbound_suppressed.py` | 7 | 0 | 0 |
| `tests/test_runtime_inventory.py` | 24 | 0 | 0 |

### Точные десять baseline failures

- `tests.benchmarks.test_acceptance_matrix::test_acceptance_matrix_required_modules_exist`
- `tests.contract.test_session_manager_contract.TestReadSessionMetadataReturnsLogicalUpdatedAt::test_updated_at_changes_after_save`
- `tests.test_audit_analyzer_cli.TestCLIIntegration::test_cli_help_exits_zero`
- `tests.test_config.TestSettingsModule::test_secrets_exported_to_env`
- `tests.test_config.TestSettingsModule::test_settings_loaded_via_resolver`
- `tests.test_information_preservation::test_info_preservation_e2e_mock_passes_keywords`
- `tests.test_information_preservation::test_info_preservation_e2e_partial_summary_below_threshold`
- `tests.test_profile_lifecycle::test_cli_agent_starts_without_profile_flag`
- `tests.test_resume_scenarios::test_resume_integration_run_writes_manifest`
- `workspace.skills.audit_analyzer.tests.test_audit_analyzer_behavior.TestAuditAnalyerSkillToolSet::test_no_removed_tool_modules_exist`
