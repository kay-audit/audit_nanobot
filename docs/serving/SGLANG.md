# sglang-сервер для Nanobot

Запуск локального LLM-сервера ([sglang](https://github.com/sgl-project/sglang))
вместе с `gateway.py`. Никаких изменений в коде агента, каналах PostgreSQL,
провайдерах nanobot или таблицах БД — только поднимаем OpenAI-compatible HTTP API
и подменяем `apiBase` в `providers.vllm` (или `providers.ollama` / `providers.custom`).

> **Ветка `sglang_osiris`** добавляет этот механизм как дополнение к существующему
> vLLM-механизму. vLLM-механизм остаётся в коде и работает как раньше —
> `sglang_osiris` только предлагает альтернативу с провайдером sglang.

## Почему sglang, а не vllm

На удалённом сервере с фиксированным окружением:

| Параметр | Значение |
|----------|----------|
| GPU | 1-4 × A100-SXM4-80GB |
| Driver | 550.90.07 (CUDA 12.4) |
| torch | **2.5.1+cu124** (фиксированная версия, **НЕ ТРОГАТЬ**) |
| Модель | **Qwen3-30B-A3B-Instruct** (архитектура `qwen_3_5_moe`) |

С этой конфигурацией vLLM падает на:

1. `torchinductor` — требует более новой версии torch.
2. `qwen_3_5_moe` — архитектура модели не поддерживается установленным vLLM.

sglang поддерживает оба варианта (Qwen3-30B-A3B + torch 2.5.1+cu124), поэтому
используем его вместо vllm. Nanobot-у без разницы, кто отвечает на запросы —
он общается по OpenAI-совместимому HTTP API.

## Архитектура

```
┌─────────────────────┐
│ audit_workstation   │
│ (localhost:8000)    │
└──────────┬──────────┘
           │ INSERT INTO agent_conversation_messages
           ▼
┌─────────────────────────────────────────┐
│ PostgreSQL (audit_bridge_pg:5433)      │
│ public.agent_conversation_messages     │
└──────────┬──────────────────────────────┘
           │ polling (lib/channels/postgres_channel.py)
           ▼
┌─────────────────────────────────────────┐
│ audit_nanobot (Nanobot gateway.py)     │
│  ├─ scripts.serving.serving_bootstrap   │  <-- ЭТА ВЕТКА
│  │    ├─ pip install sglang[all]
│  │    ├─ subprocess.Popen(sglang launch_server)
│  │    └─ patch providers.vllm.apiBase
│  │
│  ├─ AgentLoop (LLM-call → OpenAI API)
│  │
│  └─ outbound dispatcher (UPDATE messages)
└──────────┬──────────────────────────────┘
           │ POST /v1/chat/completions
           ▼
┌─────────────────────────────────────────┐
│ sglang-runtime (localhost:30000)        │
│  - Qwen3-30B-A3B-Instruct              │
│  - torch 2.5.1+cu124 (НЕ ТРОГАТЬ)      │
│  - 1×A100-SXM4-80GB                    │
└─────────────────────────────────────────┘
```

Ключевой момент: **Nanobot не знает, что под ним sglang**. Он думает, что это
обычный OpenAI-compatible endpoint (как vllm-сервер). Реализация инференса —
проблема запускающего скрипта.

## Установка и запуск

### 1. Клонировать ветку

```bash
git clone https://github.com/kay-audit/audit_nanobot.git
cd audit_nanobot
git checkout sglang_osiris
```

### 2. Зависимости

На удалённом сервере уже стоит torch 2.5.1+cu124. **Не обновлять.**

```bash
# Создать venv (НЕ ставить torch — он уже есть в системе)
python3 -m venv venv
source venv/bin/activate

# Поставить только nanobot + runtime
pip install -e .
```

Bootstrap автоматически поставит `sglang[all]` при первом запуске (см.
`install_deps=true` ниже). Если установка sglang хочет обновить torch —
bootstrap упадёт с понятной ошибкой. В этом случае поставьте sglang вручную
с фиксированной версией torch:

```bash
pip install sglang[all] --no-deps
pip install torch==2.5.1+cu124 --index-url https://download.pytorch.org/whl/cu124
```

### 3. Конфигурация

Добавить в `config.json` секцию `serving`:

```json
{
  "serving": {
    "mode": "sglang",
    "provider_alias": "vllm",
    "model_name": "Qwen3-30B-A3B",
    "api_base": "http://localhost:30000/v1",
    "api_key": "EMPTY",

    "sglang": {
      "host": "0.0.0.0",
      "port": 30000,
      "model_path": "/data/models/Qwen3-30B-A3B-Instruct",
      "served_model_name": "Qwen3-30B-A3B",
      "gpu_ids": "0",
      "gpu_memory_utilization": 0.9,
      "max_model_len": 32768,
      "dtype": "bfloat16",
      "trust_remote_code": true,
      "torch_compile": false
    },

    "health_check_timeout_sec": 300,
    "install_deps": true
  }
}
```

Полный пример в [`config.serving.example.json`](../../config.serving.example.json).

### 4. Запуск

```bash
python gateway.py
```

Bootstrap автоматически:
1. Установит `sglang[all]` (если `install_deps=true`).
2. Запустит `python -m sglang.launch_server` с указанными параметрами.
3. Дождётся ответа `/v1/models` (timeout: 5 минут для загрузки модели).
4. Пропатчит `providers.vllm.apiBase` в `config.json`.
5. Создаст бэкап `config.json.bak-<ts>`.
6. Запустит Nanobot штатно.

### 5. Остановка

```bash
# Остановить sglang по PID-файлу
python -m scripts.serving.stop

# Или просто Ctrl+C в терминале — bootstrap зарегистрирует процесс в
# logs/sglang.pid и scripts.serving.stop найдёт его при следующем вызове.
```

## Параметры sglang

Полный список параметров в `scripts/serving/config.py::SglangSettings`.

| Поле | Default | Описание |
|------|---------|----------|
| `host` | `0.0.0.0` | Адрес для HTTP API. |
| `port` | `30000` | Порт для HTTP API. |
| `model_path` | `/data/models/Qwen3-30B-A3B-Instruct` | Путь к модели на диске. |
| `served_model_name` | `model_path` | Имя в `/v1/models`. |
| `gpu_ids` | `0` | Номера GPU через запятую (`0,1` для tensor parallel). |
| `gpu_memory_utilization` | `0.9` | Доля памяти GPU под KV-cache. |
| `max_model_len` | `32768` | Контекстное окно модели. |
| `dtype` | `bfloat16` | `bfloat16` для A100. |
| `trust_remote_code` | `true` | Требуется для Qwen3. |
| `torch_compile` | `false` | **Обязательно** false на torch 2.5.1+cu124. |
| `quantization` | `null` | `awq`, `gptq`, `fp8` — при необходимости. |
| `api_key` | `EMPTY` | Bearer-токен (если хотите закрыть API). |
| `extra_args` | `[]` | Любые дополнительные флаги sglang. |

## Переменные окружения

Все параметры можно переопределить через env vars (полезно для CI/CD):

```bash
SGLANG__MODE=sglang
SGLANG__API_BASE=http://localhost:30000/v1
SGLANG__SGLANG__PORT=30000
SGLANG__SGLANG__MODEL_PATH=/data/models/Qwen3-30B-A3B-Instruct
SGLANG__SGLANG__GPU_IDS=0,1,2,3
SGLANG__SGLANG__GPU_MEMORY_UTILIZATION=0.85
SGLANG__SGLANG__TORCH_COMPILE=false
SGLANG__SGLANG__MAX_MODEL_LEN=32768
SGLANG__INSTALL_DEPS=true
```

Приоритет: defaults → config.json → env vars.

## Подключение Nanobot к уже запущенному sglang

Если sglang уже поднят вручную (например, через tmux/screen на GPU-сервере),
используйте `mode=external`:

```json
{
  "serving": {
    "mode": "external",
    "provider_alias": "vllm",
    "model_name": "Qwen3-30B-A3B",
    "api_base": "http://gpu-server.local:30000/v1",
    "health_check_timeout_sec": 60
  }
}
```

В этом случае bootstrap только проверит `/v1/models` и пропатчит config.json.
Никакого `pip install`, никакого subprocess.

## Совместимость с vLLM

vLLM-механизм остаётся в коде без изменений. Если у вас уже есть рабочий
vLLM-сервер на GPU, просто уберите секцию `serving` из config.json (или
поставьте `mode=off`) — Nanobot будет ходить на сконфигурированный
`providers.vllm.apiBase` как раньше.

Сценарии:

| Секция `serving` в config.json | Поведение |
|--------------------------------|-----------|
| Отсутствует | Штатное (vLLM-механизм, как раньше) |
| `mode=off` | Штатное (vLLM-механизм, как раньше) |
| `mode=sglang` | Поднимаем sglang локально |
| `mode=external` | Health-check уже запущенного сервера |
| `mode=ollama` | Локальный запуск через ollama (для теста) |

## Troubleshooting

### sglang не устанавливается (pip хочет обновить torch)

```bash
pip install sglang[all] --no-deps
pip install -r requirements.txt  # стандартные зависимости
```

Если pip всё равно хочет обновить torch — зафиксируйте через `constraints.txt`:

```text
torch==2.5.1+cu124
torchvision==0.20.1+cu124
torchaudio==2.5.1+cu124
```

```bash
pip install -c constraints.txt sglang[all] --no-deps
```

### sglang падает с `torchinductor` error

Убедитесь, что `torch_compile: false` в секции `serving.sglang` — это
обязательный параметр для фиксированной версии torch.

### sglang падает с `qwen_3_5_moe` not supported

sglang должен поддерживать Qwen3-30B-A3B "из коробки". Если нет — проверьте
версию:

```bash
python -c "import sglang; print(sglang.__version__)"
```

Нужна `sglang>=0.4.0`. Если у вас старее — обновите только sglang (без torch):

```bash
pip install --upgrade sglang --no-deps
```

### Health-check зависает на 5 минут

Увеличьте таймаут:

```json
{
  "serving": {
    "health_check_timeout_sec": 900
  }
}
```

Большие модели (35B+) могут грузиться до 5-7 минут на медленном хранилище.

### bootstrap crashed — Nanobot не стартует

Это best-effort шаг. Если что-то пошло не так, bootstrap печатает
stacktrace и продолжает штатный старт Nanobot. Проверьте логи:

```bash
tail -f logs/sglang.log
tail -f logs/gateway.err
```

## Что НЕ меняется в этой ветке

- ❌ `lib/channels/postgres_channel.py` — без изменений (DB-шина).
- ❌ `lib/services/llm_config.py` — без изменений (резолв провайдера).
- ❌ `nanobot/providers/registry.py` — без изменений (пакетный реестр).
- ❌ `workspace/tools/*` — без изменений.
- ❌ SQL-схема — без изменений.
- ✅ `gateway.py` — добавлен **один** вызов `_bootstrap_serving()` в начале
  `main()`. Если модуль `scripts.serving` не импортируется — fallback на
  старое поведение.
- ✅ `config.json` — секция `serving` опциональна; если её нет — Nanobot
  стартует штатно (как в master).
