# Shared Osiris runtime

`workspace/utils/osiris_runtime/` owns Osiris discovery, start/stop/status, one NFS lifecycle lock, heartbeat checks and request transport. A skill defines a `ServiceProfile` and owns its worker's capability handlers. Appeals is the first consumer: `utils/srb_d3.py` validates its `retrieve`/`rerank` payloads and results, while `rerank_osiris_worker.py` retains BGE, FAISS, BM25, RRF and reranker logic. Skills call the Python API directly; they do not ask an LLM to invoke a lifecycle Tool.

## Runtime behavior

- The worker starts its idle clock when it becomes READY. Default `OSIRIS_IDLE_TIMEOUT_SEC=3600` (one hour).
- A request marks the worker busy. Idle shutdown cannot occur during a request. On completion, the full TTL starts again. Each subsequent request resets it.
- The worker scans and claims the NFS inbox before and immediately before idle shutdown. It writes `idle_stopping`, then `stopped` to heartbeat and exits its main process. Osiris create uses `restart=False`; confirm on the closed contour that this transitions the job to terminal and releases its GPU.
- A request to a stopped service uses the shared lifecycle lock, starts one job, waits for READY, then submits the original request. Default startup deadline: `OSIRIS_START_WAIT_TIMEOUT_SEC=300` (five minutes). This is separate from the retrieve/rerank request deadline (currently 1800 seconds).
- If startup cannot finish, the client raises `OsirisUnavailableError` (or `OsirisStartupTimeoutError`). Appeals logs the technical cause and tells the user to retry after `OSIRIS_RETRY_AFTER_SEC=900` (15 minutes); it does not sleep for that period. Handler failure is `OsirisRequestError`, and request execution timeout is `OsirisRequestTimeoutError`; they are not presented as startup failures.

## Operator commands

Run at the repository root:

```bash
python osiris_job.py start
python osiris_job.py status
python osiris_job.py stop
```

`python appeals_osiris_job.py start|status|stop` remains a compatibility wrapper. `start` and request auto-start call the same `ensure_ready()` implementation. `status` is read-only and displays requested/actual job name, SDK state, heartbeat state/age, GPU counts, queue size, active request count, last activity, idle TTL, idle age and remaining idle time. A terminal SDK state wins over a fresh old heartbeat. `stop` calls the confirmed `osiris.delete(actual_job_name)` and clears metadata/heartbeat only after `state/status` confirms terminal or not-found. Repeated stop is successful.

Set `OSIRIS_IDLE_TIMEOUT_SEC` before start to change TTL. The launcher writes it into job metadata so the remote worker reads the same TTL at startup. `OSIRIS_START_WAIT_TIMEOUT_SEC` changes the caller's readiness deadline. `OSIRIS_RETRY_AFTER_SEC` changes the user-facing retry interval. The worker still needs a shared NFS mount and compatible profile/job paths; exporting a path override only in a Gateway shell does not automatically change the remote container environment.

For another service, pass a Python configuration file exporting `SERVICE`:

```bash
python osiris_job.py --config-file /path/to/my_osiris_config.py start
python osiris_job.py --config-file /path/to/my_osiris_config.py status
python osiris_job.py --config-file /path/to/my_osiris_config.py stop
```

## Connect another skill

Define a stdlib-only profile in `my_osiris_config.py`. Give each independent service its own NFS root, job name and worker script. Set `service_name`, `image`, `pool`, `num_gpus`, `capabilities`, `protocol_version`, `heartbeat_max_age_sec`, and optionally `idle_timeout_sec`, `startup_wait_timeout_sec`, `retry_after_sec`:

```python
from pathlib import Path
from workspace.utils.osiris_runtime import ServiceProfile

SERVICE = ServiceProfile(
    service_name="my_skill", job_name="my_skill_gpu", image="<deployment image>",
    pool="<deployment pool>", worker_script="/shared/path/my_worker.py",
    num_gpus=1, nfs_root=Path("/shared/path/osiris/my_skill"),
    capabilities=("classify",),
)
```

The skill calls shared Python functions without duplicating SDK logic:

```python
from workspace.utils.osiris_runtime.client import submit_request, wait_result
from workspace.utils.osiris_runtime.lifecycle import ensure_ready

ensure_ready(SERVICE)  # imports SDK lazily; one shared start if needed
request_id = submit_request(SERVICE, session_id, "classify", query, payload)
result = wait_result(SERVICE, session_id, request_id, "classify", auto_recover=True)
```

Alternatively use `client.request(SERVICE, ...)` for the complete sequence. Catch `OsirisUnavailableError` to show a retry-later message. Let `OsirisRequestError` and `OsirisRequestTimeoutError` follow the skill's real handler-error path. Do not turn a handler bug into a generic startup failure.

The worker uses `WorkerActivity`, `claim_new_requests`, `recover_processing_after_restart` and `should_idle_stop` from `workspace.utils.osiris_runtime.worker`. Add a domain handler for the new `request_type` and declare that capability in the profile and heartbeat. `service_name` in a v2 manifest/heartbeat separates services; each profile also has its own NFS root. Existing Appeals v2 manifests without `service_name` remain accepted by its worker for restart recovery. Keep protocol version 2 unless an incompatible wire change is necessary.

## NFS and concurrency

Each service root contains `inbox/`, `processing/`, `failed/`, `sessions/<safe_session>/input|output|error/`, `osiris_job.json`, `worker_heartbeat.json` and `launcher.lock`. Requests are atomically published to inbox, claimed into processing, and correlated by request ID/type. A restarted worker moves non-expired processing manifests back to inbox; expired manifests are discarded, never executed. A caller timeout cleans its artifacts. If a worker dies after readiness, the client makes one bounded recovery attempt while waiting for that request.

The NFS lock protects discovery plus create/delete across Gateway processes and the CLI. Later callers wait for the lock within their startup deadline and then re-discover the job; they do not create duplicates. A lock left by a dead process on the same host can be reclaimed after checking its recorded PID. A lock whose owner cannot be verified is kept: an uncertain SDK/lock state is never permission to create another GPU job. `create_pending` with an actual job confirmed terminal/not-found can be recovered; an unconfirmed create without an actual name remains blocked until the closed-contour state is resolved.

Heartbeat contains status (`starting`, `ready`, `busy`, `idle_stopping`, `stopped`), timestamp, service name, generation, protocol, capabilities, GPU counts, queue size, active requests, last activity and idle age/TTL. Readiness requires a fresh compatible heartbeat **and** SDK `Running` state. A worker's monotonic clock decides idle shutdown; wall-clock values are for status only. A heartbeat from an old generation is not READY for a newly created job.

Do not call `osiris.create()` directly from each skill, copy lifecycle logic, bypass the shared lock, keep an idle GPU job running indefinitely, log credentials, or interpret a stale heartbeat as a live worker. The Osiris SDK is closed-contour software: only `list`, `create`, `state`, `status`, and the operator-confirmed `delete(job_name)` are used here. Verify `restart=False` terminal/GPU behavior and NFS lock semantics with the real SDK before deployment.
