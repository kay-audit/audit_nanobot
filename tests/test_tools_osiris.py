"""Native tool authorization, session, delivery and actual loader registration."""
from __future__ import annotations

import asyncio
import json
import shutil
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, Mock

import pytest
from nanobot.agent.tools.context import RequestContext, request_context

from workspace.tools.osiris import OsirisTool, OsirisToolConfig
from workspace.utils.osiris_runtime import ServiceProfile
from workspace.utils.osiris_runtime.errors import OsirisRequestTimeoutError, OsirisStartupTimeoutError
from workspace.utils.osiris_runtime.json_transport import InvocationResult


@pytest.fixture
def tool(tmp_path):
    profile=ServiceProfile('test','job','image','pool','worker.py',1,tmp_path/'nfs',('echo',))
    runtime=Mock()
    runtime.profile.return_value=profile
    runtime.status.return_value={'service_name':'test','Osiris state':'running'}
    runtime.start.return_value='job'
    runtime.stop.return_value='job'
    runtime.invoke.return_value=InvocationResult('r',{'ok':True},{})
    return OsirisTool(config=OsirisToolConfig(enable=True,allowed_services=['test']),runtime=runtime,workspace=tmp_path)


def context(session='postgres:real'):
    return request_context(RequestContext(channel='postgres',chat_id='real',session_key=session))


def test_metadata_and_settings_reference():
    ctx=SimpleNamespace(_settings_ref=SimpleNamespace(tools=SimpleNamespace(osiris={'enable':True})))
    assert OsirisTool.enabled(ctx)
    assert not OsirisTool.enabled(SimpleNamespace())
    instance=OsirisTool(config=OsirisToolConfig())
    assert instance.name=='osiris'
    assert instance.parameters['required']==['action','service']
    assert 'session_id' not in instance.parameters['properties']
    assert 'image' not in instance.parameters['properties']


@pytest.mark.asyncio
async def test_invoke_uses_real_session_and_thread(tool):
    loop_thread=threading.get_ident()
    def invoke(*args,**kwargs):
        assert threading.get_ident()!=loop_thread
        assert kwargs['session_id']=='postgres:real'
        return InvocationResult('r',{'ok':True},{})
    tool.runtime.invoke.side_effect=invoke
    with context():
        result=await tool.execute(action='invoke',service='test',operation='echo',payload={},session_id='forged')
    assert json.loads(result)['result']=={'ok':True}
    assert tool.runtime.invoke.call_args.kwargs['consumer_id']=='nanobot'


@pytest.mark.parametrize('arguments',[
    dict(action='invoke',service='other',operation='echo',payload={}),
    dict(action='invoke',service='test',operation='shell',payload={}),
    dict(action='invoke',service='test',operation='echo',payload={'bad':b'pickle'}),
    dict(action='invoke',service='test',operation='echo',payload='text'),
    dict(action='shell',service='test'),
])
@pytest.mark.asyncio
async def test_invalid_inputs_never_call_backend(tool,arguments):
    with context(): result=await tool.execute(**arguments)
    assert result.is_error
    tool.runtime.invoke.assert_not_called()
    tool.runtime.start.assert_not_called()
    tool.runtime.stop.assert_not_called()


@pytest.mark.asyncio
async def test_missing_session_and_explicit_python_session(tool):
    result=await tool.execute(action='invoke',service='test',operation='echo',payload={})
    assert result.is_error
    result=await tool.execute(action='invoke',service='test',operation='echo',payload={},session_id='python-session')
    assert json.loads(result)['status']=='ok'
    assert tool.runtime.invoke.call_args.kwargs['session_id']=='python-session'


@pytest.mark.parametrize('allow_stop,admins,session',[(False,['postgres:admin'],'postgres:admin'),(True,[],'postgres:admin'),(True,['postgres:admin'],'postgres:user')])
@pytest.mark.asyncio
async def test_stop_requires_configuration_and_real_admin_context(tool,allow_stop,admins,session):
    tool.config.allow_stop=allow_stop
    tool.config.admin_session_keys=admins
    with context(session):
        result=await tool.execute(action='stop',service='test',session_id='postgres:admin',admin=True)
    assert result.is_error
    tool.runtime.stop.assert_not_called()


@pytest.mark.asyncio
async def test_admin_stop_and_no_context_forgery(tool):
    tool.config.allow_stop=True; tool.config.admin_session_keys=['postgres:admin']
    result=await tool.execute(action='stop',service='test',session_id='postgres:admin')
    assert result.is_error
    with context('postgres:admin'):
        result=await tool.execute(action='stop',service='test')
    assert json.loads(result)['status']=='stopped'
    tool.runtime.stop.assert_called_once_with('test')


@pytest.mark.asyncio
async def test_operation_allowlist(tool):
    tool.config.allowed_operations={'test':[]}
    with context(): result=await tool.execute(action='invoke',service='test',operation='echo',payload={})
    assert result.is_error
    tool.runtime.invoke.assert_not_called()


@pytest.mark.parametrize('mask', [None, [], ['1', '3']])
@pytest.mark.asyncio
async def test_native_tool_does_not_require_retrieve_filters(tool, mask):
    from dataclasses import replace
    tool.runtime.profile.return_value = replace(tool.runtime.profile('test'), capabilities=('retrieve',))
    with context():
        result = await tool.execute(action='invoke', service='test', operation='retrieve',
                                    payload={'allowed_ids': mask}, query='q')
    assert json.loads(result)['status'] == 'ok'
    assert tool.runtime.invoke.call_args.args[2] == {'allowed_ids': mask}


@pytest.mark.asyncio
async def test_status_and_start_delegate_to_common_backend(tool):
    assert json.loads(await tool.execute(action='status',service='test'))['Osiris state']=='running'
    assert json.loads(await tool.execute(action='start',service='test'))['service_name']=='test'
    tool.runtime.start.assert_called_once_with('test')


@pytest.mark.asyncio
async def test_stop_and_sdk_do_not_block_event_loop(tool):
    entered=threading.Event(); release=threading.Event()
    def status(service):
        entered.set(); release.wait(timeout=2); return {'ready':True}
    tool.runtime.status.side_effect=status
    task=asyncio.create_task(tool.execute(action='status',service='test'))
    await asyncio.to_thread(entered.wait,1)
    assert not task.done()
    release.set()
    assert json.loads(await task)['ready']


@pytest.mark.parametrize('error,code',[(OsirisStartupTimeoutError('secret'),'startup_timeout'),(OsirisRequestTimeoutError('secret'),'request_timeout'),(RuntimeError('private DSN/password'),'worker_error')])
@pytest.mark.asyncio
async def test_safe_typed_errors(tool,error,code):
    tool.runtime.invoke.side_effect=error
    with context(): result=await tool.execute(action='invoke',service='test',operation='echo',payload={})
    assert result.is_error
    assert json.loads(str(result))['error']==code
    assert 'secret' not in str(result) and 'private' not in str(result)


@pytest.mark.asyncio
async def test_artifact_sources_are_session_confined(tool,tmp_path):
    source=tmp_path/'secret'; source.write_text('outside')
    with context():
        result=await tool.execute(action='invoke',service='test',operation='echo',payload={},artifacts={'file':'../../secret'})
    assert result.is_error
    tool.runtime.invoke.assert_not_called()


@pytest.mark.asyncio
async def test_large_result_delivered_to_real_session(tool,tmp_path):
    tool.config.max_result_chars=256
    tool.runtime.invoke.return_value=InvocationResult('r',{'text':'x'*3000},{})
    tool.message_tool=SimpleNamespace(execute=AsyncMock(return_value='sent'))
    with context():
        result=await tool.execute(action='invoke',service='test',operation='echo',payload={})
    assert len(result)<256
    media=tool.message_tool.execute.call_args.kwargs['media']
    assert len(media)==1
    path=Path(media[0])
    assert path.is_relative_to(tmp_path/'data_store/cache/sessions/postgres_real')
    assert json.loads(path.read_text())['result']['text']=='x'*3000


def test_real_project_tool_loader_registers_native_tool(tmp_path,monkeypatch):
    from lib.services.project_tool_loader import register_project_tools
    tools=tmp_path/'tools'; tools.mkdir()
    shutil.copyfile(Path(__file__).resolve().parents[1]/'workspace/tools/osiris.py',tools/'osiris.py')
    profile_file=tmp_path/'profile.py'
    profile_file.write_text("from pathlib import Path\nfrom workspace.utils.osiris_runtime import ServiceProfile\nSERVICE=ServiceProfile('test','job','image','pool',__file__,1,Path(__file__).parent/'nfs',('echo',))\n",encoding='utf-8')
    agent=MagicMock(); agent.workspace=str(tmp_path)
    agent.tools.get.return_value=None; agent.tools.has.return_value=False
    settings=SimpleNamespace(tools=SimpleNamespace(osiris={'enable':True,'allowed_services':['test'],'profile_files':['profile.py']}))
    result=register_project_tools(agent,tmp_path,settings=settings)
    assert 'osiris' in result.registered
    registered=agent.tools.register.call_args.args[0]
    assert registered.name=='osiris'
    assert registered.runtime.profile('test').capabilities==('echo',)


@pytest.mark.parametrize('failure', [None, 'copy', 'delivery'])
@pytest.mark.asyncio
async def test_result_artifact_cleanup_only_after_successful_delivery(tool, tmp_path, monkeypatch, failure):
    from workspace.utils.osiris_runtime import client, json_transport
    profile = tool.runtime.profile('test')
    rid = 'a' * 32
    _, dirs = client.session_dirs(profile, 'postgres:real')
    source = dirs['output'] / f'{rid}.files' / 'result.bin'
    source.parent.mkdir(parents=True)
    source.write_bytes(b'fixture')
    tool.runtime.invoke.return_value = InvocationResult(rid, {'ok': True}, {'result.bin': source})
    tool.runtime.cleanup.side_effect = lambda service, **kwargs: json_transport.cleanup(profile, kwargs['session_id'], kwargs['request_id'])
    async def deliver(**kwargs):
        assert source.exists()
        assert Path(kwargs['media'][0]).read_bytes() == b'fixture'
        if failure == 'delivery':
            raise RuntimeError('delivery failed')
        return 'sent'
    tool.message_tool = SimpleNamespace(execute=AsyncMock(side_effect=deliver))
    if failure == 'copy':
        monkeypatch.setattr(shutil, 'copyfile', Mock(side_effect=OSError('copy failed')))
    with context():
        result = await tool.execute(action='invoke', service='test', operation='echo', payload={})
    if failure:
        assert result.is_error
        assert json.loads(str(result))['request_id'] == rid
        assert source.exists()
        tool.runtime.cleanup.assert_not_called()
    else:
        assert json.loads(result)['status'] == 'ok'
        assert not source.exists()
        assert Path(tool.message_tool.execute.call_args.kwargs['media'][0]).exists()


@pytest.mark.asyncio
async def test_large_result_does_not_overwrite_named_binary_artifact(tool,tmp_path):
    tool.config.max_result_chars=256
    source=tmp_path/'binary'; source.write_bytes(b'binary fixture')
    tool.runtime.invoke.return_value=InvocationResult('r',{'text':'x'*1000},{'result.json':source})
    tool.message_tool=SimpleNamespace(execute=AsyncMock(return_value='sent'))
    with context():
        result=await tool.execute(action='invoke',service='test',operation='echo',payload={})
    media=[Path(p) for p in tool.message_tool.execute.call_args.kwargs['media']]
    assert len(media)==2 and len(result)<256
    assert next(path for path in media if path.name=='result.json').read_bytes()==b'binary fixture'
    report=next(path for path in media if path.name!='result.json')
    assert json.loads(report.read_text())['result']['text']=='x'*1000
