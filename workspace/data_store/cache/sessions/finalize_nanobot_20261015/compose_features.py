from __future__ import annotations
import json
from pathlib import Path
import subprocess
import sys
import yaml
sys.path.insert(0, str(Path.cwd()))
from config import _strip_jsonc_comments

root = Path(__file__).resolve().parent
names = sys.argv[1:]
manifests = [yaml.safe_load((root / 'evidence' / ('feature-' + name + '.yaml')).read_text(encoding='utf-8-sig')) for name in names]
def git(*args):
    return subprocess.check_output(['git', *args], encoding='utf-8').strip()
def distinct(items, key):
    found = {}
    for item in items:
        found.setdefault(item[key], item)
    return list(found.values())
overlays = {}
for manifest in manifests:
    for overlay in manifest['overlays']['project_json']:
        path = overlay['path']
        if path in overlays and overlays[path]['value'] != overlay['value']:
            raise ValueError('Conflicting overlay: ' + path)
        overlays[path] = overlay
if 'osiris' in names:
    overlays['tools.osiris'] = {'op':'upsert', 'path':'tools.osiris', 'value':{'enable':True, 'allowed_services':['appeals'], 'profile_files':['skills/appeals-analyzer/utils/osiris_config.py'], 'allowed_operations':{'appeals':['retrieve','rerank','embed']}, 'allow_stop':False, 'admin_session_keys':[], 'request_timeout_sec':1800, 'max_result_chars':16000}}
combined = {
    'feature': {'id':'finalize_nanobot', 'name':'nanobot-integration', 'version':'1.0.0', 'description':'Local integration on vllm_fix; reviewed Git merges and configuration overlays'},
    'integration': {'base':git('rev-parse','origin/vllm_fix'), 'apply_method':'git_merge_and_reviewed_overlays', 'database_setup':'operator_only_not_executed', 'sources':[{'branch':'feature/' + name, 'sha':git('rev-parse','feature/' + name), 'manifest':(root / 'evidence' / ('feature-' + name + '.yaml')).relative_to(Path.cwd()).as_posix()} for name in names]},
    'requires': {'nanobot_version':'>=2.5.4', 'system_deps':['python>=3.14']},
    'skills':distinct([item for manifest in manifests for item in manifest['skills']], 'name'),
    'tools':distinct([item for manifest in manifests for item in manifest['tools']], 'name'),
    'overlays':{'project_json':list(overlays.values()), 'sql_migrations':list(dict.fromkeys(item for manifest in manifests for item in manifest['overlays']['sql_migrations']))},
    'tests':distinct([item for manifest in manifests for item in manifest['tests']], 'path'),
}
if 'osiris' in names:
    combined['tools'].append({'name':'osiris','path':'workspace/tools/osiris.py','config_key':'osiris','config_section':'tools.osiris'})
    combined['tests'].extend({'path':name} for name in ['tests/test_osiris_runtime.py','tests/test_tools_osiris.py'])
Path('feature.yaml').write_text(yaml.safe_dump(combined,allow_unicode=True,sort_keys=False),encoding='utf-8')
extra = {}
for path, overlay in overlays.items():
    current = extra
    keys = path.split('.')
    for key in keys[:-1]:
        current = current.setdefault(key,{})
    current[keys[-1]] = overlay['value']
raw = subprocess.check_output(['git','show','origin/vllm_fix:project.json'],encoding='utf-8')
base = json.loads(_strip_jsonc_comments(raw))
for section, entries in extra.items():
    if section in base:
        for key in entries:
            if key in base[section]:
                raise ValueError('Existing configuration requires manual review: '+section+'.'+key)
        encoded = json.dumps(entries,ensure_ascii=False,indent=2)[2:-2]
        insertion = '\n'.join('  '+line for line in encoded.splitlines())
        marker = '  "'+section+'": {'
        if raw.count(marker) != 1:
            raise ValueError('Ambiguous JSONC section '+section)
        raw = raw.replace(marker,marker+'\n'+insertion+',',1)
    else:
        encoded = json.dumps({section:entries},ensure_ascii=False,indent=2)[2:-2]
        index = raw.rfind('}')
        raw = raw[:index].rstrip()+',\n'+encoded+'\n}\n'
json.loads(_strip_jsonc_comments(raw))
Path('project.json').write_text(raw,encoding='utf-8')
print('Composed '+', '.join(names))
