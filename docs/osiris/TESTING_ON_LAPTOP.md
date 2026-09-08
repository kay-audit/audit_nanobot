# Тестирование osiris_gateway.py на ноутбуке без NVIDIA GPU

Этот документ описывает, как **частично** протестировать `osiris_gateway.py`
на ноутбуке с Intel IRIS XE (без NVIDIA GPU). Полный запуск невозможен —
sglang требует CUDA. Но можно проверить:

1. ✅ Syntax/import всех Python-модулей
2. ✅ Установка CPU-only версии torch + базовых зависимостей Nanobot
3. ✅ `--dry-run` режим (проверка argparse, env vars, plan output)
4. ✅ Atomic JSON write для config.json
5. ✅ Сборка секции serving через `build_serving_section`
6. ❌ Запуск sglang (нужен CUDA)
7. ❌ Запуск Nanobot pipeline через реальный gateway.main (требует nanobot-ai)

## Шаг 1: Создать отдельный venv

```powershell
cd C:\Users\pasco\opencode_projects\audit_point\audit_nanobot

# Создать venv в отдельной папке (не пересекается с основным bot_venv)
python -m venv osiris_test_venv

# Активировать (Windows PowerShell)
.\osiris_test_venv\Scripts\Activate.ps1

# Обновить pip
python -m pip install --upgrade pip wheel setuptools
```

## Шаг 2: Установить CPU-only torch + базовые deps

```powershell
# CPU-only torch (без CUDA, ~250 MB вместо 2.3 GB)
pip install torch==2.5.1 torchvision==0.20.1 torchaudio==2.5.1 `
    --index-url https://download.pytorch.org/whl/cpu

# transformers + accelerate (CPU работает)
pip install transformers==4.46.3 accelerate==1.1.0

# Минимальный набор Nanobot deps (без sglang!)
# Берём из osiris_requirements.txt, удалив строки с sglang/flashinfer/index-url
$reqPath = "osiris_requirements.txt"
$content = Get-Content $reqPath
$content = $content | Where-Object {
    $_ -notmatch "^sglang" -and `
    $_ -notmatch "^flashinfer" -and `
    $_ -notmatch "^--index-url"
}
$tmpReq = "osiris_test_venv\requirements-cpu.txt"
Set-Content -Path $tmpReq -Value $content -Encoding UTF8

pip install -r osiris_test_venv\requirements-cpu.txt
```

Или одной командой (PowerShell here-string):

```powershell
@'
psycopg2-binary==2.9.12
duckdb==1.5.4
streamlit==1.56.0
httpx==0.28.1
loguru==0.7.3
faiss-cpu==1.13.2
numpy==2.4.2
pyarrow==23.0.1
PyYAML==6.0.3
redis==8.0.0
transformers==4.46.3
accelerate==1.1.0
python-docx==1.2.0
openpyxl==3.1.5
xlrd==2.0.2
pypdf==5.9.0
pdfplumber==0.11.10
python-pptx==1.0.2
Pillow==12.3.0
sqlglot==30.17.0
'@ | Set-Content osiris_test_venv\requirements-cpu.txt

pip install -r osiris_test_venv\requirements-cpu.txt
```

> ⚠️ `nanobot-ai==0.3.0` НЕ ставим на этом этапе — для тестов venv достаточно
> проверить что `scripts/serving/*.py` импортируются и `build_serving_section`
> работает. `nanobot-ai` понадобится только если хотим запустить `gateway.main()`.

## Шаг 3: Syntax + import check

```powershell
osiris_test_venv\Scripts\python.exe -m py_compile osiris_gateway.py
osiris_test_venv\Scripts\python.exe -m py_compile scripts\serving\config.py
osiris_test_venv\Scripts\python.exe -m py_compile scripts\serving\sglang_launcher.py
osiris_test_venv\Scripts\python.exe -m py_compile scripts\serving\serving_bootstrap.py
```

Если ошибок нет — AST парсится, синтаксис OK.

## Шаг 4: --dry-run с пропуском всего

```powershell
osiris_test_venv\Scripts\python.exe osiris_gateway.py `
    --dry-run `
    --skip-install `
    --skip-gpu-check `
    --model-path "C:/models/Qwen3.6-35B-A3B-Instruct" `
    --num-gpus 2
```

Ожидаемый вывод:

```
======================================================================
  Osiris Gateway v1.0
  Stack: torch 2.5.1+cu124 · sglang 0.4.3 · Qwen3.6 35B-A3B
  Model: C:/models/Qwen3.6-35B-A3B-Instruct
  sglang endpoint: http://0.0.0.0:30000/v1
======================================================================
[osiris][*] using 2 GPU(s)
[osiris][*] --skip-install set; assuming torch/sglang/flashinfer/nanobot deps present
[osiris][+] patched C:\...\config.json
  providers.vllm.apiBase = http://0.0.0.0:30000/v1
  providers.vllm.apiKey  = EMPTY
  agents.defaults.model  = Qwen3.6-35B-A3B
  serving.mode           = sglang
[osiris][*] dry-run: would start sglang on port 30000 with tp=2
[osiris][*] dry-run: would start Nanobot pipeline (gateway.main)
[osiris][+] dry-run completed
```

## Шаг 5: Проверить config.json после dry-run

```powershell
# Перед запуском сохранить оригинал
Copy-Item config.json config.json.before-osiris-test

# Запустить dry-run
osiris_test_venv\Scripts\python.exe osiris_gateway.py --dry-run --skip-install --skip-gpu-check

# Проверить что config.json содержит секцию serving
Get-Content config.json | Select-String -Pattern "serving" -Context 0,15

# Восстановить оригинал (osiris_gateway НЕ запускал sglang, config.json можно откатить)
Move-Item config.json.before-osiris-test config.json -Force
```

## Шаг 6: Проверить build_serving_section через REPL

```powershell
osiris_test_venv\Scripts\python.exe
```

```python
import sys
sys.path.insert(0, r"C:\Users\pasco\opencode_projects\audit_point\audit_nanobot")

from osiris_gateway import build_serving_section, TORCH_VERSION, SGLANG_VERSION

serving = build_serving_section(
    model_path="/data/models/Qwen3.6-35B-A3B-Instruct",
    served_model_name="Qwen3.6-35B-A3B",
    host="0.0.0.0",
    port=30000,
    num_gpus=4,
    max_model_len=32768,
    gpu_memory_utilization=0.9,
    trust_remote_code=True,
    quantization=None,
    extra_args=["--enable-metrics"],
)

import json
print(json.dumps(serving, indent=2))

# Проверить что tp-size добавился для num_gpus=4
assert "--tp-size" in serving["sglang"]["extra_args"]
assert serving["sglang"]["extra_args"][serving["sglang"]["extra_args"].index("--tp-size") + 1] == "4"

# Проверить torch.compile отключён
assert serving["sglang"]["torch_compile"] is False

print(f"\nTORCH_VERSION = {TORCH_VERSION}")
print(f"SGLANG_VERSION = {SGLANG_VERSION}")
print("OK")
```

Ожидаемый вывод: dict с секцией serving, `extra_args=["--enable-metrics", "--tp-size", "4"]`,
`torch_compile=False`, `gpu_ids="0"`.

## Шаг 7: Проверить что GPU detection корректно ругается

```powershell
# Без --skip-gpu-check (на ноуте нет NVIDIA GPU → ожидаем ошибку)
osiris_test_venv\Scripts\python.exe osiris_gateway.py --dry-run --skip-install
```

Ожидаемый вывод:

```
[osiris][*] 0 GPU(s) detected... no NVIDIA GPU detected via nvidia-smi; cannot run sglang.
```

(это OK, мы тестируем что проверка работает — на GPU-сервере пройдёт успешно)

## Что НЕ получится протестировать на ноуте

| Тест | Почему нельзя |
|------|---------------|
| `ensure_sglang()` (pip install sglang[all]) | sglang требует CUDA, на CPU-only torch не установится |
| `start_sglang()` | нужен реальный sglang + CUDA + GPU |
| torch.cuda.is_available() == True | на Intel IRIS XE всегда False |
| Загрузка Qwen3.6 35B-A3B модели | модель 70 GB в bf16, нужно 4×A100-80GB |
| `gateway.main()` полный pipeline | требует `nanobot-ai==0.3.0` пакет |

## Что РЕАЛЬНО тестируется на ноуте

| Тест | Результат |
|------|-----------|
| Syntax / AST parse | ✅ мгновенно |
| Import всех модулей (osiris_gateway + scripts.serving.*) | ✅ секунды |
| --dry-run с разными CLI args | ✅ секунды |
| build_serving_section с разными параметрами | ✅ секунды |
| patch_config: atomic write UTF-8 без BOM | ✅ секунды |
| GPU detection: правильно ругается на отсутствие nvidia-smi | ✅ мгновенно |
| CUDA detection: правильно ругается на отсутствие nvcc | ✅ мгновенно |

## Очистка после теста

```powershell
# Удалить тестовый venv
Remove-Item -Recurse -Force osiris_test_venv

# Удалить test config backup
Remove-Item config.json.before-osiris-test -ErrorAction SilentlyContinue
```

## Когда всё OK — коммит и push

```powershell
git add osiris_gateway.py osiris_requirements.txt docs/osiris/
git commit -m "feat(osiris): standalone gateway для голой ноды с GPU

- osiris_gateway.py: install+verify+launch sglang+launch Nanobot
- osiris_requirements.txt: pinned torch 2.5.1+cu124, sglang 0.4.3
- docs/osiris/README.md: production deployment guide
- docs/osiris/TESTING_ON_LAPTOP.md: dry-run tests on CPU-only venv

Stack: CUDA 12.4, torch 2.5.1+cu124 (ЗАФИКСИРОВАНО),
1-4×A100-SXM4-80GB, Qwen3.6 35B-A3B

Использует scripts.serving.* для bootstrap (sglang_launcher,
serving_bootstrap, render_config), но добавляет:
- Жёсткие проверки nvidia-smi/CUDA/driver/torch/sglang
- Pre-install всех deps с правильным wheel-index
- Auto-config serving section из CLI args
- Cleanup (stop_serving) при SIGINT/SIGTERM"
git push origin sglang_osiris
```
