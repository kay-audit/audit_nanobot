from __future__ import annotations
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest


@pytest.mark.parametrize('name', ['ior-analyzer','ior_analyzer'])
def test_ior_cache_factory_resolves_configured_name_through_shared_api(monkeypatch,name):
    import config
    from lib.core import skill_config as shared
    path=Path(__file__).resolve().parents[1]/'workspace/skills/ior-analyzer/utils/skill_config.py'
    spec=importlib.util.spec_from_file_location('ior_config_regression',path)
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(config,'SETTINGS',{'skills':{name:{'tables':['fixture.ior']}}})
    calls=[]
    monkeypatch.setattr(shared,'build_cache_provider',lambda selected,root: calls.append((selected,root)) or 'provider')
    assert module.build_cache_provider()=='provider'
    assert calls==[(name,module._SKILL_ROOT)]


def test_missing_ior_configuration_remains_a_real_keyerror(monkeypatch):
    import config
    from lib.core.skill_config import build_cache_provider
    monkeypatch.setattr(config,'SETTINGS',{'skills':{}})
    with pytest.raises(KeyError,match='ior_analyzer'):
        build_cache_provider('ior_analyzer',Path('.'))


@pytest.mark.asyncio
async def test_native_ior_exception_retains_traceback(monkeypatch,caplog):
    from workspace.tools.ior_analyzer import IORAnalyzerTool,IORAnalyzerToolConfig
    async def broken(**kwargs):
        raise KeyError('missing_fixture_key')
    tool=IORAnalyzerTool(config=IORAnalyzerToolConfig())
    result=await tool._execute_once(prompt='ИОР',_runner=broken)
    assert result.is_error
    error=next(r for r in caplog.records if 'IOR analysis failed' in r.message)
    assert error.exc_info and error.exc_info[0] is KeyError
    assert 'missing_fixture_key' in caplog.text
