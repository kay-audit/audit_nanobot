"""Offline universal protocol/registry/worker contracts: no SDK, CUDA or data services."""
from __future__ import annotations

import json
import queue
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from workspace.utils.osiris_runtime import HandlerRegistry, OsirisRuntime, ServiceProfile, ServiceRegistry, WorkerResult, run_worker
from workspace.utils.osiris_runtime import client, json_transport, lifecycle, worker
from workspace.utils.osiris_runtime.config import common_timeout_settings
from workspace.utils.osiris_runtime.dispatcher import process_json_request
from workspace.utils.osiris_runtime.errors import OsirisBusyError, OsirisPayloadError, OsirisRequestError, OsirisRequestTimeoutError, OsirisResourcesUnavailableError, OsirisServiceNotFoundError, OsirisUnknownOperationError
from workspace.utils.osiris_runtime.protocol import json_bytes


@pytest.fixture
def profile(tmp_path):
    script=tmp_path/'worker.py'
    script.write_text('',encoding='utf-8')
    return ServiceProfile('test','test-job','image','pool',str(script),1,tmp_path/'nfs',('echo',),startup_wait_timeout_sec=.2)


def echo(payload,context):
    return {'payload':payload,'session':context.session_id,'consumer':context.consumer_id}


def roundtrip(profile,payload,*,session='session',consumer='first',handlers=None,**kwargs):
    rid=json_transport.submit(profile,session,'echo',payload,consumer_id=consumer,**kwargs)
    process_json_request(profile,profile.nfs_root/'inbox'/f'{rid}.json',handlers or HandlerRegistry().register('echo',echo))
    return json_transport.wait(profile,session,rid,'echo',consumer_id=consumer,auto_recover=False,timeout_sec=.05)


def test_independent_registry_and_shared_consumers(profile):
    registry=ServiceRegistry()
    registry.register(profile)
    assert registry.register(profile) is profile
    other=replace(profile,service_name='other',job_name='other-job',nfs_root=profile.nfs_root.parent/'other')
    registry.register(other)
    assert len(registry.profiles())==2
    assert OsirisRuntime(registry).profile('test') is registry.get('test')
    assert other.nfs_root != profile.nfs_root
    with pytest.raises(OsirisServiceNotFoundError): registry.get('missing')


@pytest.mark.parametrize('change',[{'job_name':'test-job'},{'nfs_root':'same'},{'nfs_root':'nested'},{'service_name':'test'}])
def test_registry_rejects_conflicts(profile,change):
    registry=ServiceRegistry(); registry.register(profile)
    values=dict(service_name='other',job_name='other',nfs_root=profile.nfs_root.parent/'other')
    if change.get('nfs_root')=='same': change=dict(nfs_root=profile.nfs_root)
    if change.get('nfs_root')=='nested': change=dict(nfs_root=profile.nfs_root/'nested')
    values.update(change)
    with pytest.raises(ValueError): registry.register(replace(profile,**values))


def test_default_idle_and_override(profile,monkeypatch):
    monkeypatch.delenv('OSIRIS_IDLE_TIMEOUT_SEC',raising=False)
    assert profile.idle_timeout_sec==28800
    assert common_timeout_settings()['idle_timeout_sec']==28800
    monkeypatch.setenv('OSIRIS_IDLE_TIMEOUT_SEC','42')
    assert common_timeout_settings()['idle_timeout_sec']==42


@pytest.mark.parametrize('payload',[b'pickle',set(),object(),float('nan'),float('inf'),{'bad':complex(1,2)},{1:'value'}])
def test_json_rejects_python_and_nonfinite_values(profile,payload):
    with pytest.raises(OsirisPayloadError): json_transport.submit(profile,'s','echo',payload)
    assert not profile.nfs_root.exists()


def test_json_depth_and_size(profile):
    with pytest.raises(OsirisPayloadError): json_bytes('x'*100,32)
    nested={}
    for _ in range(70): nested={'value':nested}
    with pytest.raises(OsirisPayloadError): json_transport.submit(profile,'s','echo',nested)


def test_unknown_operation_before_sdk(profile):
    registry=ServiceRegistry(); registry.register(profile)
    runtime=OsirisRuntime(registry,Mock())
    with pytest.raises(OsirisUnknownOperationError): runtime.invoke('test','shell',{},session_id='s')
    runtime.sdk.create.assert_not_called()
    assert not profile.nfs_root.exists()


def test_json_roundtrip_and_no_pickle(profile):
    result=roundtrip(profile,{'text':'Синтетика','values':[1,True,None]},consumer='skill-a')
    assert result.value['payload']['text']=='Синтетика'
    assert result.value['consumer']=='skill-a'
    assert not list(profile.nfs_root.rglob('*.pkl'))
    assert not list(profile.nfs_root.rglob('*.json'))


def test_parallel_consumers_and_sessions(profile):
    profile=replace(profile,startup_wait_timeout_sec=2)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda i:roundtrip(profile,{'n':i},session='s'+str(i%2),consumer='skill-'+str(i)),range(8)))
    assert len({r.request_id for r in results})==8
    assert [r.value['payload']['n'] for r in results]==list(range(8))
    assert {r.value['consumer'] for r in results}=={'skill-'+str(i) for i in range(8)}


@pytest.mark.parametrize('field,value',[('request_id','0'*32),('service_name','other'),('operation','other'),('session_id','foreign'),('consumer_id','other'),('protocol_version',99)])
def test_result_correlation(profile,field,value):
    rid=json_transport.submit(profile,'s','echo',{})
    path=profile.nfs_root/'inbox'/f'{rid}.json'
    process_json_request(profile,path,HandlerRegistry().register('echo',echo))
    _,dirs=client.session_dirs(profile,'s'); output=dirs['output']/f'{rid}.json'
    data=json.loads(output.read_text()); data[field]=value
    client._atomic_json(data,output)
    with pytest.raises(OsirisRequestError): json_transport.wait(profile,'s',rid,'echo',auto_recover=False,timeout_sec=.02)


def test_worker_error_is_safe_and_correlated(profile):
    def fail(payload,context): raise RuntimeError('private DSN/password')
    with pytest.raises(OsirisRequestError,match='Worker rejected') as error:
        roundtrip(profile,{},handlers=HandlerRegistry().register('echo',fail))
    assert 'private' not in str(error.value)
    assert not list(profile.nfs_root.rglob('*.json'))


def test_missing_handler_error(profile):
    with pytest.raises(OsirisUnknownOperationError): roundtrip(profile,{},handlers=HandlerRegistry())


def test_handler_validator(profile):
    def validate(value):
        if value.get('required') is not True: raise OsirisPayloadError('invalid')
    handler=Mock()
    with pytest.raises(OsirisRequestError): roundtrip(profile,{},handlers=HandlerRegistry().register('echo',handler,validate))
    handler.assert_not_called()


def test_request_timeout_cleans_nfs(profile):
    rid=json_transport.submit(profile,'s','echo',{})
    with pytest.raises(OsirisRequestTimeoutError): json_transport.wait(profile,'s',rid,'echo',auto_recover=False,timeout_sec=.001)
    assert not list(profile.nfs_root.rglob('*.json'))


def test_duplicate_request_id_preserves_original(profile):
    rid=json_transport.submit(profile,'s','echo',{'original':True})
    with pytest.raises(OsirisPayloadError): json_transport.submit(profile,'s','echo',{},request_id=rid)
    result_path=profile.nfs_root/'inbox'/f'{rid}.json'
    process_json_request(profile,result_path,HandlerRegistry().register('echo',echo))
    assert json_transport.wait(profile,'s',rid,'echo',auto_recover=False).value['payload']=={'original':True}


def test_artifact_input_output_roundtrip(profile,tmp_path):
    source=tmp_path/'source.bin'; source.write_bytes(b'binary fixture')
    def handle(payload,context):
        target=context.output_dir/'copy.bin'
        target.parent.mkdir(parents=True,exist_ok=True)
        target.write_bytes(context.artifacts['input.bin'].read_bytes())
        return WorkerResult({'size':target.stat().st_size},{'copy.bin':target})
    result=roundtrip(profile,{},handlers=HandlerRegistry().register('echo',handle),artifacts={'input.bin':source},file_root=tmp_path)
    assert result.artifacts['copy.bin'].read_bytes()==b'binary fixture'
    assert result.value['size']==14


def test_artifact_escape_and_size(profile,tmp_path):
    source=tmp_path/'outside.bin'; source.write_bytes(b'xx')
    with pytest.raises(OsirisPayloadError): json_transport.submit(profile,'s','echo',{},artifacts={'file':source},file_root=tmp_path/'session')
    small=replace(profile,max_artifact_bytes=1)
    with pytest.raises(OsirisPayloadError): json_transport.submit(small,'s','echo',{},artifacts={'file':source},file_root=tmp_path)
    assert not profile.nfs_root.exists()


def test_manifest_path_escape_before_read(profile,tmp_path):
    secret=tmp_path/'private'; secret.write_text('do not read')
    rid=json_transport.submit(profile,'s','echo',{})
    path=profile.nfs_root/'inbox'/f'{rid}.json'
    manifest=json.loads(path.read_text()); manifest['input_path']=str(secret)
    client._atomic_json(manifest,path)
    with pytest.raises(OsirisPayloadError): process_json_request(profile,path,HandlerRegistry().register('echo',echo))
    assert secret.read_text()=='do not read'


def test_stop_rejects_accepted_and_active_requests(profile,monkeypatch):
    sdk=Mock()
    monkeypatch.setattr(lifecycle,'discover',lambda *args:('job','running',None))
    rid=json_transport.submit(profile,'s','echo',{})
    with pytest.raises(OsirisBusyError): lifecycle.stop_service(profile,sdk)
    sdk.delete.assert_not_called()
    json_transport.cleanup(profile,'s',rid)
    heartbeat={'timestamp':time.time(),'service_name':'test','protocol_version':2,'capabilities':['echo'],'visible_gpus':1,'ready_gpus':1,'status':'busy','active_requests':1}
    client._atomic_json(heartbeat,profile.heartbeat_path)
    with pytest.raises(OsirisBusyError): lifecycle.stop_service(profile,sdk)


def test_idle_timer_handles_multiple_active_requests(profile,monkeypatch):
    profile=replace(profile,idle_timeout_sec=.01)
    profile.prepare_nfs(); activity=worker.WorkerActivity(profile); activity.ready()
    activity.begin(); activity.begin(); activity.end()
    time.sleep(.02)
    assert not worker.should_idle_stop(profile,activity,queue.Queue())
    assert activity.snapshot(0)['status']=='busy'
    activity.end()
    assert not activity.idle_expired(0)
    time.sleep(.02)
    assert worker.should_idle_stop(profile,activity,queue.Queue())


def test_generic_worker_receives_metadata_idle_and_processes_without_sdk(profile):
    profile=replace(profile,idle_timeout_sec=28800)
    profile.prepare_nfs()
    client._atomic_json({'idle_timeout_sec':.05,'generation':'fixture'},profile.metadata_path)
    rid=json_transport.submit(profile,'s','echo',{'example':True})
    thread=threading.Thread(target=run_worker,args=(profile,HandlerRegistry().register('echo',echo)),kwargs={'poll_sec':.005})
    thread.start()
    result=json_transport.wait(profile,'s',rid,'echo',auto_recover=False,timeout_sec=2)
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert result.value['payload']=={'example':True}
    heartbeat=json.loads(profile.heartbeat_path.read_text())
    assert heartbeat['idle_timeout_sec']==.05
    assert heartbeat['status']=='stopped'
    assert heartbeat['generation']=='fixture'


def test_sdk_is_lazy_and_example_imports_without_cuda():
    code="import sys; import workspace.utils.osiris_runtime; import tools.osiris_echo_worker; assert 'osiris' not in sys.modules; assert 'torch' not in sys.modules; assert 'sentence_transformers' not in sys.modules"
    result=subprocess.run([sys.executable,'-c',code],capture_output=True,text=True)
    assert result.returncode==0,result.stderr


def test_resource_error_has_separate_type(profile):
    sdk=Mock(); sdk.list.return_value=[]
    sdk.create.side_effect=RuntimeError('Insufficient GPU resources')
    with pytest.raises(OsirisResourcesUnavailableError): lifecycle.ensure_ready(profile,sdk)
    assert lifecycle._read_meta(profile)['create_pending'] is False


def test_python_api_checks_payload_before_start_and_uses_transport(profile,monkeypatch):
    registry=ServiceRegistry(); registry.register(profile)
    runtime=OsirisRuntime(registry)
    start=Mock(); monkeypatch.setattr(runtime,'start',start)
    with pytest.raises(OsirisPayloadError): runtime.invoke('test','echo',{'bad':b'x'},session_id='s')
    start.assert_not_called()
    original=json_transport.wait
    def wait(p,s,rid,op,**kwargs):
        process_json_request(p,p.nfs_root/'inbox'/f'{rid}.json',HandlerRegistry().register('echo',echo))
        kwargs['auto_recover']=False
        return original(p,s,rid,op,**kwargs)
    monkeypatch.setattr(json_transport,'wait',wait)
    assert runtime.invoke('test','echo',{'ok':True},session_id='s').value['payload']=={'ok':True}
    start.assert_called_once_with('test')


def test_generic_request_recovers_after_worker_restart(profile):
    rid=json_transport.submit(profile,'s','echo',{'recover':True})
    tasks=queue.Queue(); worker.claim_new_requests(profile,tasks)
    assert tasks.qsize()==1
    worker.recover_processing_after_restart(profile)
    process_json_request(profile,profile.nfs_root/'inbox'/f'{rid}.json',HandlerRegistry().register('echo',echo))
    assert json_transport.wait(profile,'s',rid,'echo',auto_recover=False).value['payload']['recover']


def test_claim_and_recovery_quarantine_duplicate_manifest(profile):
    rid=json_transport.submit(profile,'s','echo',{'original':True})
    path=profile.nfs_root/'inbox'/f'{rid}.json'
    original=path.read_bytes()
    tasks=queue.Queue(); worker.claim_new_requests(profile,tasks)
    path.write_bytes(original)
    worker.claim_new_requests(profile,tasks)
    assert tasks.qsize()==1
    assert (profile.nfs_root/'failed'/f'{rid}.duplicate.json').exists()
    path.write_bytes(original)
    worker.recover_processing_after_restart(profile)
    assert path.read_bytes()==original


def test_operator_cli_invoke_uses_registered_backend(profile,monkeypatch,capsys):
    import osiris_job
    monkeypatch.setattr(osiris_job,'load_profile',lambda path:profile)
    invoke=Mock(return_value=json_transport.InvocationResult('id',{'echo':True},{}))
    monkeypatch.setattr(osiris_job.OsirisRuntime,'invoke',invoke)
    assert osiris_job.main(['invoke','--operation','echo','--payload','{"x":1}','--session','operator-session'])==0
    assert json.loads(capsys.readouterr().out)['result']=={'echo':True}
    assert invoke.call_args.args==('test','echo',{'x':1})


def test_request_paths_and_cleanup_reject_untrusted_identity(profile,tmp_path):
    sentinel=tmp_path/'private'; sentinel.write_text('preserve')
    with pytest.raises(OsirisPayloadError): json_transport.cleanup(profile,'s','../../private')
    assert sentinel.read_text()=='preserve'


def test_namespace_owner_cannot_be_overwritten_by_second_process(profile):
    from workspace.utils.osiris_runtime.errors import OsirisUnavailableError
    profile.prepare_nfs()
    lifecycle._write_meta(profile,job_name='actual')
    other=replace(profile,service_name='other',job_name='other-job')
    with pytest.raises(OsirisUnavailableError):
        with lifecycle.lifecycle_lock(other,time.monotonic()+1):
            pytest.fail('foreign namespace acquired')
    assert lifecycle._read_meta(profile)['job_name']=='actual'
    assert not profile.lock_path.exists()
