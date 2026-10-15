# Одна OSIRIS GPU job для D3 Appeals и D6 IOR

Дата: 15.10.2026. Статус: **PARTIALLY READY**.

Реализовано ограничение одной физической job на пользователя инфраструктуры.
В интеграции D3 и D6 привязаны к `nanobot_shared`, одна GPU; логические сервисы
`appeals` и `ior` не объединяют бизнес-алгоритмы или поисковые данные.
Физические SDK/CUDA/NFS/GP здесь отсутствуют, поэтому production-run не заявляется.

## 1. Архитектура и владение

`PhysicalDeployment` владеет physical ServiceProfile `nanobot_gpu`, job,
launcher.lock, metadata, общей очередью/admission, heartbeat и worker-owner lease.
Logical ServiceProfile содержит явную ссылку на deployment; ServiceRegistry
требует предварительной регистрации deployment. Совпадение job_name само по
себе не даёт права совместного использования; конфликты namespace отклоняются.
Lifecycle start/status/stop обоих logical profiles обращается к одной physical job.

Режим включён корневым `osiris_deployment.json`. Отсутствие этого файла в исходной
feature-ветке оставляет standalone. Присутствующий некорректный shared config
приводит к ошибке, а не fallback в `srb_d3`/`srbd6`. `OSIRIS_SHARED_CONFIG` задаёт
явный путь альтернативной конфигурации. D5 не изменена.

## 2. Worker, маршрутизация и очередь

Entrypoint: `tools/osiris_shared_worker.py`. Декларации `services` указывают
доверенные каталоги навыков. Worker загружает их как отдельные Python packages,
вызывает `create_shared_handlers(ModelPool)` и маршрутизирует по logical service
и operation. D3 и D6 используют собственные реализации dispatch, без копирования
бизнес-логики друг друга. IOR utils не загрязняет глобальный sys.path при shared load.

Операции D3: retrieve/rerank/embed. Операции D6: embed/build_index/search/retrieve/rerank.
Heavy handlers выполняются последовательно, включая потоковые вызовы step().
NFS inbox/processing — долговечная очередь. Admission под physical lifecycle lock
ограничивает суммарное число принятых задач (по умолчанию 128); переполнение
возвращает OsirisBusyError. Timeout включает ожидание в очереди и исполнение.
GPU inference нельзя насильно безопасно прервать: просроченный результат не выдаётся,
worker завершает текущую функцию. Ошибка handler не ломает обработку другого сервиса.

## 3. Идентичность и изоляция

Каждый shared envelope связывает deployment_id, service_name, trusted session_id,
consumer_id, UUID request_id, operation/request_type, protocol_version и generation.
Живой Nanobot request context имеет приоритет над переданной Python/LLM session.
Ticket принятого запроса хранится отдельно; worker сверяет ticket и имя queue-файла,
клиент — все поля ответа и текущую generation. При несовпадении результат отклоняется.

NFS: `<physical root>/services/{appeals|ior}/sessions/<safe-session>/input|output|error`.
Artifacts используют дополнительный UUID-каталог. Пути проходят confinement checks,
включая symlink escape; публикация через атомарную замену. Cleanup проверяет владение
сессией и не удаляет чужой queued request. Пустые `accepted/<UUID>.seen` не содержат
данных пользователя и запрещают reuse UUID после timeout/restart. Эти tombstones
не удаляются per-request cleanup; их можно обслуживать только после полного
останова всех старых клиентов/worker, без повторного использования UUID.

Trusted pickle оставлен только во внутреннем Python/NFS API. Native Tool работает
с JSON и allowlists, использует доверенный session context, сохраняет ограничения
на stop и доставку артефактов через MessageTool.

## 4. Lifecycle, discovery, recovery, idle

Два процесса D3/D6 берут один NFS lifecycle lock, повторно выполняют discovery и
создают job только при подтверждённом отсутствии подходящей job. Неопределённый
SDK discovery, чужая активная job или старые `srb_d3`/`srbd6` блокируют создание.
Автоматического удаления чужой/старой job нет. Подтверждённая совместимая shared
job переиспользуется. Одновременное восстановление stale lock защищено отдельным
reaper lease и повторной проверкой владельца; Windows sharing violation имеет
ограниченный retry. Неопределённого удалённого владельца lock не удаляем по возрасту.

Worker-owner lease предотвращает двух одновременно исполняющих physical worker.
После crash файлы processing восстанавливаются в inbox, но старая generation
отклоняется: операция, которая могла исполниться до crash, не replay-ится молча.
Пользователь делает явный retry с новым UUID. Exactly-once через непроверяемую
границу process crash не обещается. Pending/active задачи любого logical service
блокируют stop и idle shutdown. Общий idle TTL — 28800 секунд; после последнего
завершения отсчитывается полный интервал. Launcher TTL передаётся через metadata.

## 5. Модели, память и индексы

Изучены реальные worker loaders: оба используют BGE-M3 и BGE reranker-v2-m3,
но D3 reranker FP16, D6 — прежняя default precision. Прежний D3 load_runtime
сохранён в исходном виде; shared load — отдельная функция. Математика encode,
normalization, sigmoid, FAISS/BM25/RRF и финансовые расчёты не менялись.

ModelPool переиспользует только одинаковый canonical path + constructor/dtype
contract. Разные веса/пути/precision не подменяются: неактивные веса хранятся в CPU
RAM, перед вызовом нужный model переводится на CUDA. Одновременно резидентна одна
модель каждого вида; inference на CPU не добавлен. Точные веса/VRAM без контура
не измерены. Каталоги весов должны быть неизменяемыми в течение жизни worker.
На контуре проверить checksum/revision, CUDA/FAISS совместимость, RAM для parked
weights и VRAM вместе с GPU FAISS Appeals. Существующий CPU FAISS fallback Appeals
не менялся; нового CPU model fallback нет.

IORRuntime владеет своими extract/version/index данными по паре session/consumer.
Appeals corpus, allowed_ids, filters и индекс находятся в другом package/context.
Общего FAISS/BM25 индекса, DataFrame или текущего пользовательского запроса нет.

## 6. Изменения по веткам

- feature/osiris: physical/logical binding, registry, lifecycle, shared worker,
  модели/proxies, envelopes, UUID tombstones, cleanup ownership, discovery guards,
  concurrency/recovery tests и документация.
- feature/d3_crm: opt-in binding профиля, domain adapter, отдельный shared loader,
  неизменный standalone loader и явные standalone fixtures. Assertions результатов,
  масок, scores, XLSX и MessageTool сохранены. Byte-contract старого конфига сохранён
  за исключением явно добавленного opt-in binding; он проверяется bootstrap отдельно.
- feature/d6_ior: opt-in binding, собственные shared handlers, namespaced imports,
  сохранение extract/version контрактов и явные standalone NFS fixtures.
- finalize_nanobot: merges этих веток, разрешение конфликта двух фабрик обработчиков
  Appeals с сохранением обеих, shared deployment config и Native Tool allowlists.
- D5, config.json, lib/services/llm_client.py, llm_config.py, llm_reasoning.py,
  runtime_patcher.py не менялись относительно принятой интеграции bde7d45.

## 7. Проверки

| Проверка | Результат |
|---|---|
| Полный offline pytest, окончательный код | **5425 passed, 10 failed, 36 skipped, 11 deselected, 1 xpassed; 48 subtests passed** |
| Сравнение с прежними 5366 passed | +59 passed; те же десять baseline failures, **0 новых** |
| Обязательные contracts/guards | **467 passed, 1 skipped; 48 subtests passed** |
| Расширенный IOR regression | **275 passed, 2 skipped; 67 subtests passed** |
| ApplicationContext / skills / Native Tool / семь D5 tools | Passed; SDK/ML не импортируются |
| Настоящий D5 stdio MCP и синтетический SQLite corpus boot | Passed, семь tools, три validation calls, startup exit 0 |
| Compileall | Passed |
| Критический Ruff E9,F63,F7,F82 | Passed |
| Component validation | 200 прежних нарушений; вывод побайтово совпал с baseline |

Baseline failures сохранены по точным node IDs:

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

Полный suite выполнялся с прежним исключением tests/test_cache_ownership_claim.py
(требует настоящую PG и пишет claims), markers `not live and not integration`.
Это исключение и 11 deselected не считаются passed. Две IOR skips и общий XPASS
не скрываются. Raw logs/JUnit остаются локально: failure traces могут содержать
окружение машины. В Git попадают только безопасная сводка и воспроизводимые scripts.


Честное разделение: реальные локальные filesystem/threads/spawned processes,
реальный ApplicationContext/bootstrap, настоящий MCP stdio и SQLite; mock SDK,
synthetic domain handlers/model objects; без настоящих CUDA weights, scheduler,
межузлового NFS и PG/GP. Тестовые XLSX и сессионная доставка проверены offline,
production-корпуса и большие GPU индексы не проверены.

Покрытие обязательных сценариев: регистрация explicit deployment; оба порядка
D3/D6; одновременный start из двух процессов; existing job reuse; старые/чужие/
неопределённые job; одинаковые тексты/сессии внутри и между сервисами; все поля
корреляции; generation/timeout/recovery; независимый cleanup; чужой artifact path;
handler failure; serial GPU; backpressure; фоновый worker/lease; idle/start/stop;
legacy и JSON; Tool allowlists/context/admin stop; раздельные IOR extract indexes;
XLSX/MessageTool; отсутствие SDK/ML на gateway import.

Промежуточные новые failures расследованы и исправлены, а не объявлены baseline:
standalone fixtures нельзя было пересаживать в произвольный root с deployment;
D3 AST loader восстановлен; исправлены namespace-only IOR imports, global utils
pollution и Windows lock sharing race и MAX_PATH в длинном временном имени identity-ticket. Assertions не ослаблялись ради зелёного
результата: старые fixtures теперь явно тестируют standalone transport, а новый
shared режим проверяется отдельно на реальных потоках/процессах и файлах.

## 8. Запуск на закрытом контуре и переход

1. Остановить приём новых вопросов gateway и дождаться окончания старых задач.
2. Через штатный операторский SDK/interface проверить `srb_d3`/`srbd6` и другие
   активные jobs. После отдельного операторского подтверждения остановить старые
   job; дождаться terminal state и освобождения GPU. Автоматически их не удаляем.
   Legacy CLI можно использовать в standalone deployment до активации root shared
   config; CLI в интеграции с активным shared config управляет уже общей job.
3. Перенести finalize, подготовить один CUDA image со всеми прежними зависимостями
   D3/D6 и SDK; shared worker не делает pip install. Подготовить отдельные корпуса/
   indexes/caches D3/D6 и локальные BGE/reranker weights. Проверить доступы NFS.
4. В osiris_deployment.json настроить image, pool, job_name, абсолютные worker_script
   и nfs_root, max_queue. `${PROJECT_ROOT}` допустим только при согласованных путях
   checkout/mount. Та же конфигурация должна читаться gateway и GPU container;
   environment gateway автоматически не экспортируется в OSIRIS контейнер.
5. Настроить APPEALS_BGE_MODEL_PATH/APPEALS_RERANKER_MODEL_PATH,
   IOR_BGE_MODEL_PATH/IOR_RERANKER_MODEL_PATH/IOR_RAG_CACHE_DIR в окружении worker
   или подготовить прежние default каталоги. Сохранить раздельные dataset assets.
6. Из корня репозитория проверить оба logical view:

```bash
python osiris_job.py start
python osiris_job.py status
python osiris_job.py --config-file workspace/skills/ior-analyzer/utils/osiris_config.py status
python workspace/skills/follow_up/scripts/follow_up_mcp --check
python gateway.py --profile=prod
```

Оба status должны показывать одну actual job и heartbeat/generation, а отдельные
logical services должны сохранять свои capabilities. Первый start может быть
вызван и IOR: результат — та же job. Дополнительную GPU job для D6 не запускать.

7. Проверить реальные A→B, B→A, параллельный start с двух gateway, несколько
   пользователей и одинаковые session IDs в разных сервисах, allowed_ids/versions,
   очереди/backpressure/timeouts, crash/retry, XLSX и доставку в правильный чат.
8. Проверить idle TTL и restart=False: accepted pending/active задачи не прерываются,
   после простоя SDK показывает terminal, GPU реально освобождается. Stop busy
   должен отказать; native Tool stop по умолчанию выключен.
9. Подтвердить NFS O_EXCL/atomic replace/visibility на двух узлах, ownership/lock
   recovery, реальные модельные результаты, VRAM/RAM limits, PG/GP permissions и D5.
   До этих проверок production readiness не заявляется.

## 9. Git, публикация и SHA

Исходный принятый finalize: bde7d45c00ef7da172c0422317422ca8b749685e.
Проверенный code SHA (до report-only commit): 2bc793d60cd34e26dfff12a3de12087f4ff15528.
История сохранена, finalize не сливался в feature-ветки. master/vllm_fix/release
не изменялись. PR не создаётся; force push не используется. Публикация разрешена
пользователем в порядке OSIRIS → D3 → D6 → finalize, после final checks.
Точный SHA report commit и фактические remote SHA после push записываются в
локальный FINAL_STATE.json рядом с отчётом и сообщаются в финальном ответе
(самоссылочный SHA невозможно встроить в содержащий его Git-коммит).

### feature/osiris

```text
b6f1300748ee5eb3792a03b5032f376d08c6e6b7 fix(osiris): keep atomic temporary filenames within Windows path limits
5e650ed2b65cc52b9011adbb25d7c79e0cb07da0 fix(osiris): refuse second GPU job on foreign or ambiguous SDK discovery
b2f6fb75a1fd8e92d6bb61471ffa369c3fd1b952 fix(osiris): bind cleanup and queue dispatch to accepted session identity
5c668fb61c665e06a281230f10fb50aa73135883 fix(osiris): prevent UUID reuse after timeout and bound Windows lock recovery
984c7b2c84d4081b534252b5fdeeeac7c20c1fbd test(osiris): distinguish standalone protocol fixtures from shared deployment binding
521d944e9e2895de38437e4d0341a529f345b4a5 fix(osiris): serialize stale-lock reclamation and test worker ownership races
2ca059f48eb5c69e8277ef74a67e567a92b5aa20 fix(osiris): load skill namespace packages without generic utils collisions
a048e0dc8760c121c485afe1e60772c5f6d422e4 fix(osiris): register physical profile in legacy API and inherit worker idle policy
141210c8ad084946119637bc32cf6117795c43ae feat(osiris): share one physical GPU deployment with isolated logical requests
```

### feature/d3_crm

```text
42d6a35fd865fa33caabd33592b901251237b043 fix(appeals): preserve legacy ML loader and explicit standalone protocol fixtures
990d5176d874c473005b51f2b504550b570e2c23 feat(appeals): bind to shared GPU deployment with isolated domain handlers
```

### feature/d6_ior

```text
a4728385ee1728eee78826afd7eac8432752c2cd test(ior): keep legacy NFS fixtures explicitly standalone
73722f323dc3d659417ee4863a1326816a87629a fix(ior): keep shared worker package imports out of global utils namespace
d4b52eb75e1ed73b3ded74056dca8b09d1a95614 feat(ior): route isolated extract handlers through shared GPU deployment
```

Интеграционный configuration commit: `7ed80a4104d16db8f8e6ddfbd781281ff0740607`.

### Интеграционные merges

```text
2bc793d60cd34e26dfff12a3de12087f4ff15528 merge: keep shared identity atomic writes within Windows path limits
594bd6ac87c69d677f96d31035534a67e3c63bcc merge: fail closed on foreign GPU jobs and ambiguous scheduler discovery
012dbad3bf9ec056cbad308425e6600c90f5c9b9 merge: preserve IOR standalone NFS regression fixtures
fa9440421b573b64a5c4d7a11e35ee4d1c649f9d merge: enforce cleanup ownership and accepted manifest identity
8fd6b5c91ff9e3363b729cfcf701cab57d671070 merge: isolate IOR worker package import paths
4600f04a4535912012da61aa28749115a58f84b0 merge: prevent shared UUID reuse and bound lock recovery
8aa35ae2c2c6253c0a068d369b1dcbafbb3b6e00 merge: retain unchanged Appeals legacy ML contracts
a9075bdd68ecedc6580e4bb45c8434b923322286 merge: harden shared lock recovery and standalone compatibility guards
9899ed8dd9d1286f397288fdab247b2b218db264 merge: support namespace-only IOR worker package
a91e3c91a232d3e53941dae12379836ae3172ab1 merge: adapt IOR domain to shared GPU deployment
1462a6ff3015aaa2d2e372fa88d890d38078d30f merge: adapt Appeals domain to shared GPU deployment
c2de58397fcd936dfd1549d47869ba62a33b282f merge: preserve shared runtime API registration and idle policy
8179a5ea591efd8f8eac0d48d0b6ef1830d2d5be merge: integrate isolated shared physical OSIRIS runtime
```

Ссылки: [OSIRIS](https://github.com/kay-audit/audit_nanobot/tree/feature/osiris),
[D3](https://github.com/kay-audit/audit_nanobot/tree/feature/d3_crm),
[D6](https://github.com/kay-audit/audit_nanobot/tree/feature/d6_ior),
[finalize](https://github.com/kay-audit/audit_nanobot/tree/finalize_nanobot).
