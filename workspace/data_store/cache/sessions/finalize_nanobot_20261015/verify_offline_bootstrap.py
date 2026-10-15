from __future__ import annotations
import json
import os
from pathlib import Path
import sys
sys.path.insert(0,str(Path.cwd()))
sys.path.insert(0,str(Path.cwd()/'workspace'))
os.environ['FOLLOW_UP_WARM_UP']='0'
import config
config._initialize_settings(profile='test')
s=config.SETTINGS
s['channels']['postgres']['dsn']=''
s['channels']['postgres']['enabled']=False
s['logging']['db']['enabled']=False
s['gateway'].update(enable_audit=False, enable_db_logging=False, enable_cron=False)
from lib.core.application_context import ApplicationContext, _auto_register_skills
from lib.services.runtime_inventory import diff_project_tools
from lib.services.table_registry import table_registry
ctx=ApplicationContext.create(role='gateway',script_dir=Path.cwd(),workspace_dir=Path.cwd()/'workspace')
skills = ctx.agent.context.skills
loaded_skills = {entry['name'] for entry in skills.list_skills()}
expected_skills = {'ior-analyzer', 'appeals-analyzer', 'follow_up'}
assert expected_skills.issubset(loaded_skills), loaded_skills
for name in expected_skills:
    assert skills.load_skill(name)
result=ctx.project_tools_result
expected={'ior_analyzer','appeals_analyzer','osiris'}|{'mcp_follow_up_'+n for n in ('ask','hypotheses','deviations','status','card_start','card_status','forget')}
assert expected.issubset(result.registered), result
assert not result.failed and not result.duplicate, result
assert len(result.registered)==len(set(result.registered))
assert not any(diff_project_tools(result.registered,result.disabled,failed=result.failed).values())
_auto_register_skills(ctx)
tables=table_registry.table_names()
assert sum('t_db_oarb_ior_d6_' in table for table in tables)==5
assert sum('.t_fu_' in table for table in tables)==len(s['skills']['follow_up']['tables'])
assert not any('kaluginvs' in table for table in tables)
assert 'osiris' not in sys.modules
from workspace.utils.osiris_runtime.registry import ServiceRegistry
registry=ServiceRegistry()
appeals=registry.load(Path('workspace/skills/appeals-analyzer/utils/osiris_config.py'))
ior=registry.load(Path('workspace/skills/ior-analyzer/utils/osiris_config.py'))
assert appeals.job_name != ior.job_name and appeals.nfs_root != ior.nfs_root
assert appeals.idle_timeout_sec == ior.idle_timeout_sec == 28800
assert not any(name in sys.modules for name in ('torch','sentence_transformers'))
print(json.dumps({'skills':sorted(expected_skills),'registered':result.registered,'disabled':result.disabled,'tables':tables,'services':[p.service_name for p in registry.profiles()],'sdk_imported':False,'ml_imported':False},ensure_ascii=False,indent=2))
