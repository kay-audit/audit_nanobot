"""Shared deployment isolation with real files, threads and spawned processes."""
from __future__ import annotations

import json
import multiprocessing
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from workspace.utils.osiris_runtime import client, json_transport, lifecycle
from workspace.utils.osiris_runtime.config import PhysicalDeployment, ServiceProfile
from workspace.utils.osiris_runtime.dispatcher import HandlerRegistry
from workspace.utils.osiris_runtime.errors import OsirisBusyError, OsirisRequestError, OsirisRequestTimeoutError, OsirisUnavailableError
from workspace.utils.osiris_runtime.registry import ServiceRegistry
from workspace.utils.osiris_runtime.shared_worker import SharedWorker


@pytest.fixture
def shared(tmp_path):
    script = tmp_path / 'worker.py'
    script.write_text('# trusted worker', encoding='utf-8')
    physical = ServiceProfile('shared', 'shared-job', 'image', 'pool', str(script), 1,
                              tmp_path / 'nfs', ('dispatch',), startup_wait_timeout_sec=3)
    deployment = PhysicalDeployment(physical, max_queue=12)
    profiles = [replace(physical, service_name=name, capabilities=('echo',),
                        nfs_root=physical.nfs_root / 'services' / name, deployment=deployment)
                for name in ('appeals', 'ior')]
    physical.prepare_nfs()
    client._atomic_json(dict(requested_name=physical.job_name, service_name='shared', generation='a'*32), physical.metadata_path)
    return deployment, profiles


class FakeSDK:
    def __init__(self, physical, state):
        self.physical, self.shared = physical, state

    def list(self):
        return ['shared-job'] if self.shared.get('created') else []

    def state(self, name):
        return 'Running' if self.shared.get('created') else 'not_found'

    status = state

    def create(self, **kwargs):
        self.shared['creates'] = self.shared.get('creates', 0) + 1
        self.shared['created'] = True
        metadata = json.loads(self.physical.metadata_path.read_text())
        client._atomic_json(dict(timestamp=time.time(), protocol_version=2,
            service_name='shared', capabilities=['dispatch'], visible_gpus=1, ready_gpus=1,
            status='ready', generation=metadata['generation'],
            logical_services={'appeals':['echo'], 'ior':['echo']}), self.physical.heartbeat_path)
        return 'shared-job'


def start_in_process(profile, state, barrier):
    sdk = FakeSDK(profile.deployment.profile, state)
    barrier.wait(timeout=10)
    lifecycle.ensure_ready(profile, sdk)


def engine(shared, handler=None):
    deployment, profiles = shared
    adapters = {}
    for p in profiles:
        def echo(payload, context):
            if payload.get('fail'):
                raise ValueError('domain error')
            if handler:
                return handler(payload, context)
            return dict(payload=payload, service=context.service_name, session=context.session_id)
        def legacy(manifest, payload):
            return dict(payload=payload, service=manifest['service_name'], session=manifest['session_id'])
        adapters[p.service_name] = (HandlerRegistry().register('echo', echo), legacy)
    result = SharedWorker(deployment, profiles, adapters, generation='a'*32)
    result.prepare()
    result.activity.ready()
    return result


def test_explicit_registration_and_namespace_conflicts(shared):
    deployment, profiles = shared
    registry = ServiceRegistry()
    with pytest.raises(ValueError):
        registry.register(profiles[0])
    registry.register_deployment(deployment)
    for profile in profiles:
        registry.register(profile)
    assert len(registry.profiles()) == 2
    with pytest.raises(ValueError):
        replace(profiles[1], nfs_root=profiles[0].nfs_root)
    with pytest.raises(ValueError):
        registry.register(replace(profiles[0], deployment=None, service_name='foreign'))


@pytest.mark.parametrize('order', [(0,1),(1,0)])
def test_single_create_both_orders_and_existing_job(shared, order):
    deployment, profiles = shared
    state = {}
    sdk = FakeSDK(deployment.profile, state)
    assert lifecycle.ensure_ready(profiles[order[0]], sdk) == 'shared-job'
    assert lifecycle.ensure_ready(profiles[order[1]], sdk) == 'shared-job'
    assert state['creates'] == 1
    assert lifecycle.ensure_ready(profiles[0], sdk) == 'shared-job'
    assert state['creates'] == 1


def test_two_processes_create_exactly_once(shared):
    _, profiles = shared
    ctx = multiprocessing.get_context('spawn')
    with ctx.Manager() as manager:
        state = manager.dict()
        barrier = ctx.Barrier(2)
        processes = [ctx.Process(target=start_in_process, args=(p,state,barrier)) for p in profiles]
        for process in processes: process.start()
        for process in processes:
            process.join(timeout=20)
            if process.is_alive(): process.terminate(); process.join()
            assert process.exitcode == 0
        assert state['creates'] == 1


@pytest.mark.parametrize('services,sessions', [((0,0),('A','B')),((1,1),('A','B')),
    ((0,1),('A','B')),((0,1),('same','same')),((0,0),('same','same'))])
@pytest.mark.parametrize('serialization', ['json','legacy'])
def test_concurrent_requests_isolated(shared, services, sessions, serialization):
    _, profiles = shared
    worker = engine(shared)
    def submit(index):
        p = profiles[services[index]]
        if serialization == 'json':
            return json_transport.submit(p, sessions[index], 'echo', {'index':index}, query='identical')
        return client.submit_request(p, sessions[index], 'echo', 'identical', {'index':index})
    with ThreadPoolExecutor(2) as pool:
        ids = list(pool.map(submit, range(2)))
        list(pool.map(lambda _: worker.step(), range(2)))
    assert len(set(ids)) == 2
    for index,rid in enumerate(ids):
        p = profiles[services[index]]
        if serialization == 'json':
            result = json_transport.wait(p,sessions[index],rid,'echo',auto_recover=False).value
        else:
            result = client.wait_result(p,sessions[index],rid,'echo')
        assert result == dict(payload={'index':index},service=p.service_name,session=sessions[index])


@pytest.mark.parametrize('field', ['request_id','service_name','session_id','consumer_id','operation','generation','deployment_id'])
def test_mismatched_response_rejected(shared, field):
    _, profiles = shared
    p = profiles[0]
    worker = engine(shared)
    rid = json_transport.submit(p,'A','echo',{})
    worker.step()
    _, dirs = client.session_dirs(p,'A')
    path = dirs['output'] / (rid+'.json')
    response = json.loads(path.read_text())
    response[field] = 'wrong'
    client._atomic_json(response,path)
    with pytest.raises(OsirisRequestError):
        json_transport.wait(p,'A',rid,'echo',auto_recover=False)


def test_queue_backpressure_and_cleanup_isolation(shared):
    deployment, profiles = shared
    a = json_transport.submit(profiles[0],'same','echo',{})
    b = json_transport.submit(profiles[1],'same','echo',{})
    for _ in range(deployment.max_queue-2): json_transport.submit(profiles[0],'A','echo',{})
    with pytest.raises(OsirisBusyError): json_transport.submit(profiles[1],'B','echo',{})
    json_transport.cleanup(profiles[0],'same',a)
    assert (profiles[1].nfs_root/'inbox'/(b+'.json')).exists()


def test_old_generation_timeout_and_recovery(shared):
    deployment, profiles = shared
    worker = engine(shared)
    rid = json_transport.submit(profiles[0],'A','echo',{})
    worker.scan()  # crash after claim
    replacement = engine(shared)
    replacement.generation = 'b'*32
    metadata = dict(requested_name=deployment.profile.job_name,service_name='shared',generation='b'*32)
    client._atomic_json(metadata,deployment.profile.metadata_path)
    replacement.step()
    with pytest.raises(OsirisRequestError): json_transport.wait(profiles[0],'A',rid,'echo',auto_recover=False)
    late = json_transport.submit(profiles[1],'A','echo',{},timeout_sec=.01)
    with pytest.raises(OsirisRequestTimeoutError):
        json_transport.wait(profiles[1],'A',late,'echo',timeout_sec=.02,auto_recover=False)
    assert not (profiles[1].nfs_root/'inbox'/(late+'.json')).exists()


def test_handler_failure_does_not_break_other_and_gpu_serial(shared):
    active = 0
    maximum = 0
    def handler(payload, context):
        nonlocal active,maximum
        active += 1; maximum=max(maximum,active)
        time.sleep(.01)
        active -= 1
        return context.service_name
    worker=engine(shared,handler)
    _,profiles=shared
    bad=json_transport.submit(profiles[0],'A','echo',{'fail':True})
    ids=[json_transport.submit(p,'B','echo',{}) for p in profiles]
    with ThreadPoolExecutor(3) as pool: list(pool.map(lambda _:worker.step(),range(3)))
    with pytest.raises(OsirisRequestError): json_transport.wait(profiles[0],'A',bad,'echo',auto_recover=False)
    for p,rid in zip(profiles,ids): assert json_transport.wait(p,'B',rid,'echo',auto_recover=False).value==p.service_name
    assert maximum==1


def test_idle_and_stop_refuse_pending_requests(shared):
    deployment,profiles=shared
    worker=engine(shared)
    worker.activity._last_mono = time.monotonic()-deployment.profile.idle_timeout_sec-1
    json_transport.submit(profiles[1],'A','echo',{})
    assert not worker.idle_stop()
    state={'created':True}
    with pytest.raises(OsirisBusyError): lifecycle.stop_service(profiles[0],FakeSDK(deployment.profile,state))


def test_legacy_job_blocks_shared_without_create_or_delete(shared):
    deployment,profiles=shared
    sdk=FakeSDK(deployment.profile,{})
    sdk.list=lambda:['prefix-srb_d3']
    sdk.state=lambda name:'Running'
    with pytest.raises(OsirisUnavailableError): lifecycle.ensure_ready(profiles[0],sdk)
    assert not sdk.shared.get('creates')


def test_tampered_artifact_path_never_executed(shared,tmp_path):
    _,profiles=shared
    p=profiles[0]
    worker=engine(shared)
    rid=json_transport.submit(p,'A','echo',{})
    path=p.nfs_root/'inbox'/(rid+'.json')
    manifest=json.loads(path.read_text()); manifest['output_path']=str(tmp_path/'foreign.json')
    client._atomic_json(manifest,path)
    worker.step()
    assert not (tmp_path/'foreign.json').exists()
    assert (p.nfs_root/'failed'/(rid+'.json')).exists()


def test_model_pool_only_reuses_exact_contract_and_runs_on_cuda(monkeypatch):
    import sys
    from types import SimpleNamespace
    from workspace.utils.osiris_runtime.shared_worker import ModelPool
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(cuda=SimpleNamespace(empty_cache=lambda:None)))
    created=[]
    class Model:
        def __init__(self,key): self.key=key; self.device='cuda:0'; created.append(self)
        def to(self,device): self.device=device
        def predict(self,*args): assert self.device=='cuda:0'; return self.key
    pool=ModelPool()
    a=pool.proxy('rerank',('path','fp16'),lambda:Model('fp16'))
    same=pool.proxy('rerank',('path','fp16'),lambda:Model('unexpected'))
    other=pool.proxy('rerank',('path','fp32'),lambda:Model('fp32'))
    assert a.predict([])==same.predict([])=='fp16'
    assert len(created)==1
    assert other.predict([])=='fp32'
    assert created[0].device=='cpu'
    assert a.predict([])=='fp16'
    assert created[1].device=='cpu'


def test_shared_session_uses_trusted_nanobot_context(shared,monkeypatch):
    import sys
    from types import SimpleNamespace
    monkeypatch.setitem(sys.modules,'nanobot.agent.tools.context',SimpleNamespace(
        current_request_context=lambda:object(),current_request_session_key=lambda:'trusted-session'))
    _,profiles=shared
    rid=client.submit_request(profiles[0],'LLM-forged','echo','',{})
    manifest=json.loads((profiles[0].nfs_root/'inbox'/(rid+'.json')).read_text())
    assert manifest['session_id']=='trusted-session'


def test_shared_json_artifacts_confined_and_cleaned_per_request(shared,tmp_path):
    from workspace.utils.osiris_runtime.dispatcher import WorkerResult
    _,profiles=shared
    def artifact(payload,context):
        context.output_dir.mkdir(parents=True)
        target=context.output_dir/'result.xlsx'
        target.write_bytes(context.session_id.encode())
        return WorkerResult(context.service_name,{'report':target})
    worker=engine(shared,artifact)
    ids=[json_transport.submit(p,session,'echo',{}) for p,session in zip(profiles,['A','B'])]
    worker.step(); worker.step()
    results=[json_transport.wait(p,s,rid,'echo',auto_recover=False)
             for p,s,rid in zip(profiles,['A','B'],ids)]
    assert [r.artifacts['report'].read_bytes() for r in results]==[b'A',b'B']
    json_transport.cleanup(profiles[0],'A',ids[0])
    assert results[1].artifacts['report'].exists()
