from __future__ import annotations
import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

root=Path.cwd()
session=root/'workspace/data_store/cache/sessions/shared_osiris_20261015'
launcher=root/'workspace/skills/follow_up/scripts/follow_up_mcp'
env=dict(os.environ, PYTHONUTF8='1', PYTHONIOENCODING='utf-8', GP_ENABLED='false', HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', DATA_INDEX_DIR=str(session/'d5-index'), LOGS_DIR=str(session/'d5-logs'))
async def transport():
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    skip=dict(env,FU_SKIP_CORPUS_BOOT='1')
    with (session/'evidence/d5-mcp-stderr.log').open('w',encoding='utf-8') as log:
        params=StdioServerParameters(command=sys.executable,args=[str(launcher)],cwd=str(root),env=skip)
        async with stdio_client(params,errlog=log) as (reader,writer):
            async with ClientSession(reader,writer) as client:
                await client.initialize()
                tools=await client.list_tools()
                names={tool.name for tool in tools.tools}
                assert names=={'ask','hypotheses','deviations','status','card_start','card_status','forget'}
                for name,payload in [('ask',{'question':'','session_key':'synthetic'}),('hypotheses',{'topic':'','session_key':'synthetic'}),('card_start',{'session_key':'synthetic'})]:
                    result=await client.call_tool(name,payload)
                    value=json.loads(result.content[0].text)
                    assert value['ok'] is False and value['reason']
                print(json.dumps({'transport':'passed','tools':sorted(names),'validation_calls':3,'corpus_boot':'explicitly skipped'},ensure_ascii=False))
asyncio.run(transport())
full_env=dict(env)
full_env.pop('FU_SKIP_CORPUS_BOOT',None)
full_env['PYTHONPATH']=str(root/'workspace/skills/follow_up')
full_env['NANOBOT_HOME']=str(root)
with (session/'evidence/d5-corpus-startup-full.log').open('w',encoding='utf-8') as log:
    process=subprocess.Popen([sys.executable,'-m','backend.skill.mcp_server'],cwd=root/'workspace/skills/follow_up',stdin=subprocess.PIPE,stdout=log,stderr=subprocess.STDOUT,env=full_env)
    try:
        process.communicate(input=b'',timeout=120)
        print(json.dumps({'corpus_startup_exit_code':process.returncode,'details':'evidence/d5-corpus-startup-full.log'}))
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
        print(json.dumps({'corpus_startup':'blocked/timeout','details':'evidence/d5-corpus-startup-full.log'}))
