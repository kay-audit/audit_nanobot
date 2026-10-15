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
