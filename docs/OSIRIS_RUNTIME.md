# Универсальный OSIRIS Runtime

## Архитектура

`workspace/utils/osiris_runtime/` — общий инфраструктурный backend без
зависимости от Appeals, FAISS, BM25, BGE или моделей. SDK импортируется только
при lifecycle-вызове. Импорт runtime и native Tool не запускает GPU job.

- `registry.py`: доверенные ServiceProfile, проверка уникальности имени/job
  и непересекающихся NFS roots. Повторная регистрация идентичного профиля идемпотентна.
- `api.py`: OsirisRuntime.start/status/stop/invoke/invoke_legacy — общий Python API.
- `lifecycle.py`: discovery, ensure_ready, SDK create/delete/state/status,
  одна NFS-блокировка, проверка готовности и поколения heartbeat.
- `client.py`: совместимый доверенный legacy pickle v2 transport.
- `protocol.py` и `json_transport.py`: bounded JSON и файловые артефакты.
- `dispatcher.py`: явные HandlerRegistry/WorkerRequest/WorkerResult и run_worker.
- `worker.py`: heartbeat accounting, atomic claim, recovery и idle decision.

Потребитель определяет профиль и handlers. Общий runtime не интерпретирует
бизнес-содержимое payload. Новые операции регистрируются callable из доверенного
worker-кода; LLM не определяет Python imports, shell, image или worker path.

## Service Registry и Python API

```python
from pathlib import Path
from workspace.utils.osiris_runtime import ServiceProfile, ServiceRegistry, OsirisRuntime

profile = ServiceProfile(
    service_name="shared-gpu", job_name="shared-gpu-job", image="<operator CUDA image>",
    pool="common", worker_script="/shared/project/my_worker.py", num_gpus=1,
    nfs_root=Path("/shared/osiris/shared-gpu"), capabilities=("echo",),
)
registry = ServiceRegistry()
registry.register(profile)
runtime = OsirisRuntime(registry)
runtime.start("shared-gpu")
print(runtime.status("shared-gpu"))
result = runtime.invoke("shared-gpu", "echo", {"value": 7},
                        session_id="postgres:42", consumer_id="my-skill")
print(result.value, result.artifacts)
runtime.stop("shared-gpu")
```

`ServiceRegistry.load(path)` исполняет только операторский конфигурационный
файл, экспортирующий SERVICE. Его путь берётся из конфигурации приложения/CLI,
никогда из аргумента Tool. Для process-wide регистрации есть singleton `services`.
Отдельные composition scopes могут использовать собственный реестр; lifecycle
между процессами по-прежнему координируется одним NFS lock.

Dedicated worker: отдельные профиль, job, root и набор handlers на каждый сервис.
Shared worker: несколько навыков используют один зарегистрированный профиль и
разные consumer_id; второй клиент переиспользует готовую job. Разделение ответов
обеспечивают session_id, consumer_id и случайный request_id. Shared worker
объявляет все зарегистрированные capabilities в heartbeat.

## Протокол и файловые артефакты

Новый JSON-протокол сохраняет protocol_version=2 рядом с legacy pickle.
Манифест содержит request_id, service_name, operation/request_type, session_id,
consumer_id, created_at, expires_at, serialization=json, query и input/output/error paths.
Payload хранится в input JSON вместе со списком файловых артефактов; большие
бинарные данные не помещаются в JSON.

Сервисный NFS root содержит inbox/, processing/, failed/, sessions/<safe_session>/
input|output|error/, osiris_job.json, worker_heartbeat.json и launcher.lock.
JSON input/output/error имеют имя <request_id>.json. Артефакты запроса находятся
в input/<request_id>.files/, результата — в output/<request_id>.files/.

JSON содержит только конечные числа, строки, bool, null, list и dict со строковыми
ключами, без Python-объектов. Ограничения профиля: max_payload_bytes=1048576,
max_result_bytes=16777216, max_artifact_bytes=536870912; глубина JSON не выше 64.
Пути проверяются после resolve, включая symlink traversal. Файлы ограничены
namespace запроса, размером и уникальным именем. Публикация JSON атомарна.
Ответ сопоставляется по service/operation/session/consumer/request/protocol.

```python
result = runtime.invoke("shared-gpu", "echo", {}, session_id="s", consumer_id="skill-a",
                        artifacts={"input.bin": Path("/session/source.bin")},
                        file_root=Path("/session"))
```

Передаваемый файл должен находиться под file_root. Worker получает проверенные
context.artifacts и пишет файлы только под context.output_dir. WorkerResult
содержит JSON value и mapping имени к созданному файлу. InvocationResult возвращает
value и проверенные Path. Успешные output artifacts остаются на NFS для копирования;
прямой Python-потребитель после использования вызывает json_transport.cleanup.
Тайм-аут очищает input/output/error и queue manifests. Непросроченный processing
восстанавливается в inbox при рестарте; дубль изолируется в failed/*.duplicate.json.
Некорректный manifest изолируется в failed; клиент получает диагностируемый timeout,
а не ложный успешный ответ.

Legacy pickle принимается только историческим внутренним Appeals worker на
доверенном NFS. Native Tool никогда не сериализует и не читает pickle. Права NFS
должны исключать запись недоверенных пользователей в manifests и legacy payload.

## Worker и подключение нового навыка

1. Описать SERVICE в доверенном конфигурационном файле.
2. Зарегистрировать профиль в ServiceRegistry приложения/навыка.
3. Реализовать handlers и их validators. Набор handlers run_worker должен
   точно совпадать с profile.capabilities.
4. Запускать run_worker(profile, handlers) после загрузки необходимых моделей.
5. Вызывать OsirisRuntime.invoke из навыка. В async-коде использовать asyncio.to_thread.
6. Добавить trusted profile_file и allowlists в tools.osiris для доступа агента.

```python
from workspace.utils.osiris_runtime import HandlerRegistry, run_worker

def echo(payload, context):
    return {"echo": payload, "consumer": context.consumer_id}

handlers = HandlerRegistry().register("echo", echo)
run_worker(profile, handlers)
```

Минимальный полный пример: tools/osiris_echo_worker.py. Локальный запуск не
требует SDK, GPU или данных:

```powershell
python tools/osiris_echo_worker.py --nfs-root workspace/data_store/cache/sessions/echo-smoke/nfs --idle-timeout 2
```

В тестах submit/process_json_request/wait вызываются на временном NFS без start.
Реальная GPU-job создаётся только lifecycle API с настроенным SDK.

## Appeals и обратная совместимость

Первый подключённый сервис — appeals. Пользовательские изменения закрытого контура
перенесены в feature/d3_crm отдельным commit c6f18b3; feature/osiris создана от него.
Сохранены ensure_srb_d3_ready, retrieve_via_srb_d3, rerank_via_srb_d3, старые
submit_request/wait_result и CLI start/status/stop. Алгоритмы FAISS/BM25/RRF,
CUDA BGE-M3/reranker, batching, sigmoid и OOM retry не переписывались.

Appeals worker использует одни domain handlers для legacy и JSON. Legacy rerank
возвращает прежний DataFrame; JSON rerank — records с model и scores. JSON embed
принимает model=BAAI/bge-m3 и items с уникальными id/text. Retrieve принимает
allowed_ids; query идёт отдельным параметром. Загрузка глобального поискового
индекса остаётся ленивой; модели загружаются с local_files_only=True на CUDA.

Фактический default job после пользовательского переноса — srb_d3 (не srbd3),
pool=common, num_gpus=1. Переопределения: APPEALS_OSIRIS_JOB_NAME,
APPEALS_OSIRIS_POOL, APPEALS_OSIRIS_IMAGE, APPEALS_OSIRIS_WORKER_SCRIPT,
APPEALS_OSIRIS_NFS_ROOT, APPEALS_BGE_MODEL_PATH, APPEALS_RERANKER_MODEL_PATH.
Пути по умолчанию выводятся из расположения проекта. Gateway и worker должны
видеть общие файлы под одинаковыми путями.

Установка пакетов выполняется только при запуске worker, не при import.
APPEALS_OSIRIS_PIP_INDEX_URL/TRUSTED_HOST задают внутренний индекс. Если индекс
не задан, используется TOKEN_OSC из worker environment или .env навыка и
существующий внутренний репозиторий. Токен передаётся pip через environment,
не shell/argv; credential-bearing pip output не печатается. Нельзя считать,
что environment Gateway автоматически попадёт в контейнер worker.

## Native Nanobot Tool

workspace/tools/osiris.py регистрируется обычным project_tool_loader. Конфигурация
читается из ctx._settings_ref.tools.osiris; встроенный ToolsConfig её не содержит.

```json
{
  "tools": {
    "osiris": {
      "enable": true,
      "profile_files": ["skills/appeals-analyzer/utils/osiris_config.py"],
      "allowed_services": ["appeals"],
      "allowed_operations": {"appeals": ["retrieve", "rerank", "embed"]},
      "allow_stop": false,
      "admin_session_keys": [],
      "request_timeout_sec": 1800,
      "max_result_chars": 16000
    }
  }
}
```

Вызовы агента:

```json
{"action":"status","service":"appeals"}
{"action":"start","service":"appeals"}
{"action":"invoke","service":"appeals","operation":"embed","payload":{"model":"BAAI/bge-m3","items":[{"id":"test-1","text":"Синтетическое обращение"}]}}
{"action":"stop","service":"appeals"}
```

Stop запрещён по умолчанию. Оператор должен включить allow_stop и задать точные
admin_session_keys. Подтверждённого admin role в текущем RequestContext нет;
аргументы admin/role/session_id не дают права stop. При наличии RequestContext
используется исключительно реальная session_key. Без неё invoke разрешён
только прямому Python-вызову с явной сессией; stop без RequestContext запрещён.
Start/status/invoke не блокируют event loop. Файлы источника задаются относительно
текущей папки sessions/<safe_session_key> через artifacts. Крупные JSON-результаты
и бинарные файлы копируются в сессию и доставляются стандартным message Tool.
Пользователю не возвращаются traceback, credentials или произвольный SDK output.

## CLI, lifecycle и idle timeout

```bash
python osiris_job.py start
python osiris_job.py status
python osiris_job.py stop
python appeals_osiris_job.py status
python osiris_job.py --config-file /shared/other_config.py start
python osiris_job.py --config-file /shared/other_config.py invoke --operation echo --payload '{"value":7}' --session operator-test
```

OSIRIS_IDLE_TIMEOUT_SEC по умолчанию 28800 (8 часов),
OSIRIS_START_WAIT_TIMEOUT_SEC=300, OSIRIS_RETRY_AFTER_SEC=900.
Startup deadline отделён от request deadline (по умолчанию 1800).
Профиль допускает явное переопределение idle_timeout_sec. Значение передаётся
worker через osiris_job.json; worker читает его перед READY.

Idle clock начинает отсчёт на READY, не во время загрузки моделей. Пока есть
active requests, shutdown невозможен. Завершение запроса начинает новый полный
idle interval. Перед idle shutdown очередь повторно проверяется под launcher.lock.
Worker публикует stopped и завершает процесс; SDK create использует restart=False.
Внешний scheduler TTL не известен локально и не меняется: idle 8 часов НЕ
гарантирует 8 часов жизни job при внешнем ограничении платформы.

Готовность требует SDK Running плюс свежий совместимый heartbeat поколения job.
Терминальный SDK state сильнее старого heartbeat. Конкурентные старты/CLI используют
одну NFS lock и повторный discovery, не создают второй job. Сервис после idle stop
запускается следующим запросом. Во время ожидания результата доступна одна
ограниченная попытка восстановления worker, в пределах request deadline.
Administrative stop отказывается с busy при accepted queue или active requests;
метаданные удаляются только после подтверждённого terminal/not-found.

## Ошибки и диагностика

Ошибки различают unknown_service, unknown_operation, invalid_payload, startup_failed,
startup_timeout, gpu_unavailable (явный отказ ресурсов SDK), request_timeout,
worker_error и busy. Неизвестный или неподтверждённый SDK state не даёт права
создавать дубликат. Retry-after=900 — рекомендация пользователю, а не sleep.

Проверять status, freshness/generation heartbeat, capabilities, actual job name,
queue size, active requests, idle remaining; затем failed manifests и SDK state.
Не удалять чужую/неподтверждённую launcher.lock автоматически. Lock локально
умершего процесса восстанавливается только после проверки recorded PID.

## Проверка в закрытом контуре (не выполнена локально)

1. Обеспечить SDK, CUDA image, внутренний PyPI, локальные модели, общий NFS и права.
2. Перед start задать idle timeout и профильные APPEALS_* переменные в окружении,
   доступном соответствующему процессу. Проверить TOKEN_OSC внутри worker.
3. Выполнить start/status: убедиться в одном actual job и совместимом READY.
4. Выполнить invoke embed на синтетическом тексте; затем legacy retrieve/rerank
   на разрешённой тестовой выборке. Проверить сохранение результатов.
5. Два независимых Gateway одновременно вызывают start — SDK должен показать
   одну job. Два consumer_id/две сессии должны получить только свои результаты.
6. Проверить timeout, контролируемое падение worker и recovery, административный
   отказ stop при активной очереди и успешный stop после завершения.
7. Для быстрого idle smoke выставить OSIRIS_IDLE_TIMEOUT_SEC=30, убедиться в
   завершении процесса/job и освобождении GPU без restart. Затем вернуть 28800
   и проверить длительный простой; запрос перед границей должен продлить timer.
8. Отдельно узнать scheduler TTL и подтвердить atomic create/rename/lock на NFS.

Локальные mocks не подтверждают SDK API, CUDA image, реальное NFS или освобождение
GPU. Все unit/regression-проверки выполняются без обращения к закрытым данным.
