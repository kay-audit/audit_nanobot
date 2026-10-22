from __future__ import annotations
import importlib.util
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest


@pytest.mark.asyncio
@pytest.mark.parametrize('failed',[True,False])
@pytest.mark.parametrize('terminal',[True,False])
async def test_real_runner_stops_after_native_ior_result(monkeypatch,failed,terminal):
    from nanobot.agent.runner import AgentRunner,AgentRunSpec
    from nanobot.agent.tools.registry import ToolRegistry
    from nanobot.agent.tools.context import RequestContext,request_context
    from nanobot.agent.tools.base import ToolResult
    from nanobot.providers.base import LLMResponse,LLMUsage,ToolCallRequest,LLMProvider
    from nanobot.utils.llm_runtime import LLMRuntime
    from workspace.tools.ior_analyzer import IORAnalyzerTool,IORAnalyzerToolConfig
    from lib.services.runtime_patcher import RuntimePatcher
    class Provider(LLMProvider):
        def __init__(self):
            super().__init__(provider_name='fixture')
            self.requests=0
        def get_default_model(self):
            return 'fixture'
        async def chat(self,messages,**kwargs):
            self.requests+=1
            if self.requests>1:
                return LLMResponse(content='recovered',usage=LLMUsage(input_tokens=10,output_tokens=2,total_tokens=12,reported_tokens=12))
            return LLMResponse(content=None, tool_calls=[ToolCallRequest(id='tc1',name='ior_analyzer',arguments={'prompt':'ИОР'})],
                finish_reason='tool_calls',usage=LLMUsage(input_tokens=10,output_tokens=2,total_tokens=12,reported_tokens=12))
    provider=Provider()
    calls=AsyncMock(wraps=provider.chat)
    monkeypatch.setattr(provider,'chat',calls)
    tool=IORAnalyzerTool(config=IORAnalyzerToolConfig())
    monkeypatch.setattr(tool,'_terminal_result',terminal,raising=False)
    execution=AsyncMock(return_value=ToolResult.error('Terminal fixture failure') if failed else 'Full native report')
    monkeypatch.setattr(tool,'execute',execution)
    tools=ToolRegistry()
    tools.register(tool)
    original=AgentRunner._run_core
    monkeypatch.setattr(AgentRunner,'_run_core',original)
    assert RuntimePatcher().patch_ior_terminal_result()[0]
    spec=AgentRunSpec(initial_messages=[{'role':'user','content':'ИОР'}],tools=tools,
        runtime=LLMRuntime.capture(provider,'fixture',context_window_tokens=100000),max_iterations=4,max_tool_result_chars=10000)
    with request_context(RequestContext(channel='postgres',chat_id='fixture',session_key='postgres:fixture',message_id='m1')):
        result=await AgentRunner().run(spec)
    assert calls.await_count==(1 if terminal else 2), repr(result)
    assert execution.await_count==1
    assert result.stop_reason==('tool_error' if failed and terminal else 'completed')
    assert result.usage.total_tokens==(12 if terminal else 24)
    assert 'try a different approach' not in result.final_content
    assert result.final_content==(('Terminal fixture failure' if failed else 'Full native report') if terminal else 'recovered')
