from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding='utf-8')
root = Path(__file__).resolve().parent
label, *args = sys.argv[1:]
env = dict(os.environ, PYTHONIOENCODING='utf-8', PYTHONUTF8='1', NANOBOT_LIVE_E2E='0', NANOBOT_INTEGRATION='0')
temp = Path(sys.prefix) / 'test-tmp' / label
temp.mkdir(parents=True, exist_ok=True)
env.update(TEMP=str(temp), TMP=str(temp))
if args[:2] == ['-m', 'pytest']:
    args += ['--basetemp=' + str(temp / 'pytest')]
command = [sys.executable, *args]
start = time.monotonic()
with (root / 'evidence' / (label + '.log')).open('w', encoding='utf-8') as output:
    result = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, env=env)
record = {'label': label, 'command': command, 'exit_code': result.returncode, 'seconds': round(time.monotonic()-start, 2)}
(root / 'evidence' / (label + '.json')).write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(record, ensure_ascii=False))
print('\n'.join((root / 'evidence' / (label + '.log')).read_text(encoding='utf-8').splitlines()[-35:]))




raise SystemExit(result.returncode)
