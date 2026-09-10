# SGLANG на локальной машине — отчёт о попытке запуска

## TL;DR

Запуск **sglang + Qwen3-1.7B** на локальном RTX 3050 (sm_86) **невозможен** без значительных изменений в стеке. Причины:

1. **Qwen3-поддержка в sglang требует версию ≥ 0.4.10**, а та в свою очередь требует **torch 2.13+**.
2. Наш текущий стек: **torch 2.5.1+cu124**. `sglang 0.4.10-post2` при импорте вызывает `torch._C._cpu._is_amx_tile_supported()` — функция, добавленная в torch 2.13+.
3. **torch 2.8+ (максимальная доступная на PyPI) не имеет wheels для Windows** (`triton` доступен только для `manylinux_*`). На Linux через Docker можно, но требует пересборки image и долгой компиляции.

Полный отчёт ниже.

---

## Что мы хотели

Воспроизвести серверный пайплайн локально:

```
AW (UI)  ──INSERT──▶  public.agent_conversation_messages
                              │
                              ▼ poll every 10s
                       audit_nanobot (Nanobot gateway)
                              │
                              ▼ HTTP POST /v1/chat/completions
                       sglang  ←── torch 2.5.1+cu124
                              │
                              ▼ LLM inference
                       Qwen3-6-35B-A3B-Instruct (на сервере) / Qwen3-1.7B (локально)
```

Серверный `osiris_gateway.py` зафиксирован на `sglang[all]==0.4.3` + `torch==2.5.1` (см. исходник), но реальная серверная инсталляция уже использует более новый стек (Qwen3 поддерживается).

---

## История исследования версий

### Этап 1. Установка в Windows venv

```bash
# Создано: C:\Users\Александр\opencode_projects\venv\  (Python 3.12.14)
pip install torch==2.5.1+cu124 torchvision==0.20.1 torchaudio==2.5.1 \
            --index-url https://download.pytorch.org/whl/cu124
pip install sglang==0.4.3   # ❌ ERROR: sgl-kernel requires manylinux, no win_amd64 wheels
```

**Результат:** sglang 0.4.3 **не имеет wheel'ов для Windows**. Только `manylinux_2_17_x86_64`.

### Этап 2. Поиск совместимой версии

| sglang | torch dep (extra=srt) | transformers (runtime-common) | Qwen3 support |
|---|---|---|---|
| 0.4.6   | torch==2.6.0 | transformers==4.51.1 | ❌ нет qwen3.py |
| 0.4.7   | torch==2.7.1 | transformers==4.52.3 | ❌ нет qwen3.py |
| 0.4.8   | torch==2.7.1 | transformers==4.52.3 | ❌ нет qwen3.py |
| 0.4.9   | torch==2.7.1 | transformers==4.53.0 | ❌ нет qwen3.py |
| 0.4.10.post2 | torch==2.13.0 ⚠️ | transformers==4.54.1 | ✅ есть qwen3.py + qwen3_moe.py |

**Вывод:** Qwen3 появился в `sglang==0.4.10.post2`, но эта версия требует **torch 2.13.0** (который существует только на `cu126+` wheels).

### Этап 3. Проверка доступных torch wheels

```bash
# pip index versions torch --index-url https://download.pytorch.org/whl/cu126
torch (2.8.0+cu126)         # ← max torch для cu126
# pip index versions torch --index-url https://download.pytorch.org/whl/cu128
torch (2.8.0+cu128)
# pip index versions torch --index-url https://download.pytorch.org/whl/cu129
torch (2.8.0+cu129)
# pip index versions torch --index-url https://download.pytorch.org/whl/cu124
torch (2.6.0+cu124)         # ← max torch для cu124
```

**Максимальный torch = 2.8.0** (не 2.13.0, как требует sglang). Между 2.8.0 и 2.13.0 — большой major-minor gap.

### Этап 4. sglang 0.4.10-post2 при импорте в torch 2.5.1

```
ImportError: cannot import name 'AutoProcessor' from 'transformers'
# transformers 4.57+ убрали AutoProcessor с верхнего уровня
# sglang 0.4.10 ожидает AutoProcessor там
```

**Фикс:** `transformers==4.52.4` (последняя версия с AutoProcessor на верхнем уровне).

### Этап 5. После фикса transformers

```
ImportError: cannot import name 'Int4WeightOnlyConfig' from 'torchao.quantization'
# Старая версия torchao в NGC image не имеет Int4WeightOnlyConfig (требуется Qwen3)
```

**Фикс:** обновить `torchao>=0.7` в Docker image.

### Этап 6. Попытка запуска в Docker image

Подготовили `sglang-ubuntu:0.4.9` на базе `ubuntu:24.04` с:
- `cuda-nvcc-12-8` (на 24.04 нет cuda 12.4)
- `python3.12-venv`, `pip`
- `torch==2.5.1+cu124`, `transformers==4.52.4`, `tokenizers>=0.21,<0.22`
- `sglang==0.4.10.post2` wheel + runtime deps

При запуске `python -m sglang.launch_server`:
```
AttributeError: module 'torch._C._cpu' has no attribute '_is_amx_tile_supported'
```

**Эта функция добавлена в torch 2.13**. В torch 2.5.1 её нет (или у sglang 0.4.10 не работает fallback).

### Этап 7. Поиск torch 2.13+ на Windows

```bash
pip install torch==2.13.0 --index-url https://download.pytorch.org/whl/cu126
# ERROR: No matching distribution found for torch==2.13.0
# Available versions: 2.6.0+cu126, 2.7.0+cu126, 2.7.1+cu126, 2.8.0+cu126
pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cu126  # OK
# Но triton==3.4.0 has no Windows wheels
```

**Итог:** `torch 2.13.0` физически не существует на PyPI (даже не на nightly). `torch 2.8.0` — максимальный. triton — only Linux.

---

## Что имеем

### Локально установлено и работает

| Стек | Статус |
|---|---|
| **bridge-ui** :8090 | ✅ работает |
| **Audit Workstation (uvicorn)** :8000 | ✅ работает |
| **Nanobot gateway (MiniMax API)** :8765 + Streamlit :8501 | ✅ работает |
| **Postgres 16 (Docker)** :5433 | ✅ healthy |
| **Redis 7 (Docker)** :6379 | ✅ healthy |

End-to-end тест «Ping → Pong» через `public.agent_conversation_messages` — работает.

### Локально скачано

| Файл | Размер | Назначение |
|---|---|---|
| `C:\...\models\Qwen3-1.7B` | 3.9 ГБ | Модель для локального теста |
| `pytorch wheels cp312-cu124 (torch, torchvision, torchaudio)` | ~876 МБ | Уже скачано в `models/wheels/` |

### Подготовлено в Docker image `sglang-ubuntu:0.4.9`

- `ubuntu:24.04` + `cuda-nvcc-12-8` + `python3.12-venv`
- `torch==2.5.1+cu124` + `transformers==4.52.4` + `sglang==0.4.10.post2` + runtime deps
- **Размер ~12 ГБ** (после сжатия слоёв)

### НЕ работает локально

- ❌ sglang launch_server с Qwen3 — падает на `torch._C._cpu._is_amx_tile_supported`
- ❌ torch 2.13+ не существует на PyPI
- ❌ torch 2.8+ имеет wheels, но `triton` — only Linux manylinux

---

## Рекомендации

### Запуск на сервере (A100, серверная конфигурация)

На сервере с CUDA driver 12.6+ должен работать стек:
- `torch==2.13+cu126` (использовать `--index-url https://download.pytorch.org/whl/cu126` если версия доступна, либо nightly)
- `sglang==0.4.10.post2` (`pip install --no-deps` + runtime deps)
- `triton` wheels from Linux server Python

См. `requirements-server.txt` в этой ветке.

### Альтернативы

1. **vLLM вместо sglang** — имеет wheels для Windows + Linux + cu12x. Поддерживает Qwen3. Не требует пересборки. Замена `sglang_launcher.py` → `vllm.entrypoints.openai.api_server`.
2. **Ollama** — уже работает на ПК (`gemma4:e4b` модели). Можно использовать как сервер `localhost:11434/v1/chat/completions`.
3. **Поднять torch на сервере до 2.13+** — основной путь. Локально не получится без долгой пересборки в Linux-контейнере.

### Если хочется всё-таки запустить локально (на RTX 3050)

Самый прагматичный путь:

```bash
# 1. Linux Ubuntu Docker image с torch 2.8 + sglang 0.4.10
docker build -f C:\...\models\Dockerfile.sglang-ubuntu -t sglang-rtx:0.4.10 .

# 2. Но это потребует переделать Dockerfile под torch 2.8+cu126
# 3. И скомпилировать sgl-kernel внутри (30-60 минут)

# 4. Запустить и подключить Nanobot через :30000
```

Займёт 1-2 часа с нуля на чистой машине.

---

## Артефакты в этой ветке

```
audit_nanobot/
└── docs/
    └── sglang/
        ├── SGLANG_REPORT.md          # ← этот файл
        └── requirements-server.txt   # pinned deps для серверного запуска
```

Полезные пути в рабочей директории `C:\...\models\`:
- `Qwen3-1.7B/` — скачанная модель (для локального теста)
- `wheels/` — pip wheels для torch+cu126 (можно использовать на сервере)
- `Dockerfile.sglang-ubuntu` — что мы собрали
- `Dockerfile.sglang-server` — для серверного запуска
- `sglang-ubuntu:0.4.9` (Docker image) — частично готовая среда
