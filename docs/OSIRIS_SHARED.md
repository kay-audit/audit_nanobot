# One physical deployment, multiple logical services

`PhysicalDeployment` owns one physical profile (one GPU), lifecycle lock,
metadata, worker lease and heartbeat. Logical `ServiceProfile.deployment` binds
a skill to `<physical NFS>/services/<service_name>`. Register the physical
deployment before the logical profiles; accidental job/namespace conflicts
remain errors. All logical lifecycle calls resolve to the physical profile.

An operator-owned root `osiris_deployment.json` enables shared mode. Absence
means standalone; present but invalid configuration fails closed.
`OSIRIS_SHARED_CONFIG` selects an explicit alternative. Fields:

- `deployment`: `name`, `job_name`, `image`, `pool`, `worker_script`, `nfs_root`,
  `max_queue` (default 128). `${PROJECT_ROOT}` expands to the checkout root.
- `services`: logical names mapped to trusted `skill_root` paths.

Provision the same configuration/mounts in gateway and GPU image. Gateway shell
environment is not implicitly forwarded by the SDK. The trusted worker entry
is `tools/osiris_shared_worker.py`; domain workers export
`create_shared_handlers(ModelPool)` and load as independent Python packages.

Admission counts all accepted inbox/processing requests under the physical lock;
overflow raises `OsirisBusyError`. SharedWorker runs heavy operations serially.
A separate worker-owner lease prevents concurrent recovery/execution. Processing
is at most once per live worker. After a crash old-generation requests fail and
must be explicitly retried with new UUIDs: no exactly-once guarantee across an
unobservable crash and no silent replay of potentially executed work.

Shared JSON/pickle envelopes bind physical deployment, logical service, trusted
Nanobot session, consumer, UUID, operation, protocol and generation. Replies must
match the retained request identity and current deployment generation. Late or
mismatched replies fail closed. Files live in per-request directories inside
logical/session namespaces. Cleanup cannot traverse other requests. Pickle is
trusted internal transport only; the native Tool accepts JSON, never pickle/code.
Empty `accepted/<UUID>.seen` tombstones survive cleanup/restart and prevent UUID
reuse even after a timeout while an earlier handler could still be running.
They contain no user payload; retire them only with a fully stopped deployment
and no old clients/workers, never as part of per-request cleanup.

ModelPool shares identical canonical paths and constructor/dtype contracts only.
CUDA operations are serialized. Incompatible weights are parked in CPU memory
before another contract is moved to GPU; inference always executes on CUDA.
D3 FP16 and D6 default-precision rerankers stay distinct. Corpus, FAISS/BM25,
filters, DataFrames and IOR extract indexes stay inside their domain adapters.
The GPU image must already contain dependencies and weights: no online install.

Physical idle TTL defaults to 28800 seconds. Accepted tasks in either service
prevent stop, and completing a task resets the full timer. Stop retains admin
authorization and refuses busy deployments. Existing `srb_d3`/`srbd6` jobs block
startup and must be inspected/retired by the operator, never auto-deleted.

Offline coverage: `tests/test_osiris_shared_deployment.py` uses real files,
threads and spawned processes with synthetic SDK/domain handlers. Real SDK,
CUDA numerics/memory, cross-host NFS locks and GPU release after shutdown must
be checked on the closed contour; offline success does not establish them.

## Startup name and bounded readiness wait

The checked-in physical job name is `nanobot-shared` (one GPU for Appeals and
IOR). New create inputs must match `^[a-z0-9](?:[a-z0-9-]{0,22}[a-z0-9])?$`.
The logical deployment ID `nanobot_gpu` is not a physical job name. Historical
`srb_d3` / `srbd6` names remain discoverable and block unsafe shared startup;
no migration deletes those jobs automatically.

Startup readiness defaults to **120 seconds** in `ServiceProfile` and
`common_timeout_settings()`. `OSIRIS_START_WAIT_TIMEOUT_SEC` and
`python osiris_job.py start --startup-timeout SECONDS` still override it.
One monotonic deadline covers shared discovery, lifecycle-lock waiting,
physical startup and scheduler/heartbeat polling; recursion does not restart
the timer. An already READY job returns immediately. This does not change
request execution timeout (1800 seconds), idle TTL (28800) or retry advice (900).
The deadline bounds runtime polling; a blocking private SDK call must itself
return before Python can inspect its result (SDK/network timeout configuration
must be verified on the closed contour).

Explicit structured SDK rejection (including the observed Python-dict string
form) fails immediately, records `create_pending=False`, and never stores the
error as an actual name. Structured discovery errors are not treated as absence.
A generic transport exception or empty/malformed create response is ambiguous:
it fails immediately but retains `create_pending=True` to prevent another create
until discovery or an operator resolves the outcome. A valid accepted name can
precede scheduler visibility; this keeps waiting inside the original deadline
and does not create again. Names such as `error` are valid names, not error
heuristics. Error diagnostics expose validation codes rather than SDK payloads.

For metadata written by the old `nanobot_shared` configuration, the known alias
is accepted only in the same service namespace. An explicitly rejected SDK
payload formerly stored as `job_name` is ignored as a name, then SDK discovery
still runs before any create. Existing valid jobs are adopted, never removed
as part of this repair. Other/uncertain ownership stays fail-closed.

D6's existing degraded behavior is unchanged: SQL/XLSX generation completes
before optional Osiris index registration. Startup unavailability prevents a
usable search index; it does not remove the already generated XLSX or report.
Model/index errors remain failures, not a successful READY/search result.
Real private SDK, GPU models and cross-host NFS behavior require closed-contour
validation; the offline tests use synthetic SDK responses and clocks.
