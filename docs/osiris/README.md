# Osiris Gateway — standalone sglang + Nanobot launcher

`osiris_gateway.py` — это **единая точка входа** для запуска полного пайплайна
Nanobot + SGLang на **голой ноде с GPU** (1-4 × A100-SXM4-80GB).

Предназначен для production-задач, когда надо поднять:

```
голый сервер с NVIDIA GPU + CUDA 12.4
        │
        ▼
   python osiris_gateway.py
        │
        ├─ проверка nvidia-smi / driver / CUDA
        ├─ pip install torch==2.5.1+cu124 (ЗАФИКСИРОВАНО)
        ├─ pip install flashinfer-python, sglang[all]==0.4.3
        ├─ pip install requirements.txt (Nanobot deps)
        ├─ patch config.json (serving + providers.vllm + agents.defaults)
        ├─ start sglang serve в фоне
        ├─ health-check /v1/models
        └─ start gateway.main() (Nanobot pipeline)
              │
              └─ polls public.agent_conversation_messages
                 обрабатывает через sglang
                 пишет ответы обратно
```

## Жёсткие требования (НЕ ИЗМЕНЯЕМЫЕ)

| Параметр | Значение | Почему |
|----------|----------|--------|
| torch | **2.5.1+cu124** | qwen_3_5_moe + torchinductor падает на других версиях torch |
| sglang | **0.4.3** | последняя версия с гарантированной поддержкой torch 2.5.1 |
| CUDA | **12.4** | torch 2.5.1+cu124 собран под CUDA 12.4 |
| driver | **>= 550.90.07** | GA драйвер для CUDA 12.4 |
| GPU | **1-4 × A100-SXM4-80GB** | 35B-A3B MoE (bf16 ~70 GB) |
| Модель | **Qwen3.6 35B-A3B** | MoE: 35B общих, 3B активных |

## Быстрый старт

### На голом сервере с GPU

```bash
# 1) Подключиться к серверу
ssh user@gpu-server

# 2) Склонировать audit_nanobot (ветка sglang_osiris)
git clone https://github.com/kay-audit/audit_nanobot.git
cd audit_nanobot
git checkout sglang_osiris

# 3) Запустить (всё установится и поднимется)
python osiris_gateway.py
```

С флагами:

```bash
# Явно указать модель и количество GPU
python osiris_gateway.py \
    --model-path /data/models/Qwen3.6-35B-A3B-Instruct \
    --num-gpus 4 \
    --port 30000 \
    --max-model-len 32768

# Quantization для экономии памяти (35B в bf16 ~70 GB, awq-marlin ~20 GB)
python osiris_gateway.py --quantization awq-marlin

# Предполагается что sglang уже запущен вручную в tmux (mode=external)
python osiris_gateway.py --skip-sglang --skip-install

# Только проверить окружение и вывести план (без реальной установки)
python osiris_gateway.py --dry-run
```

### Проверка окружения

```bash
python osiris_gateway.py --dry-run
# Выведет:
#   [osiris][*] 1x GPU detected (driver 550.90.07, CUDA 12.4)
#   [osiris][+] 4x GPU: A100-SXM4-80GB 81920 MiB
#   [osiris][*] using 4 GPU(s)
#   [osiris][*] dry-run: would install torch==2.5.1+cu124, ...
```

### Управление запуском

```bash
# Просмотр логов в реальном времени
tail -f logs/sglang.log        # sglang-сервер
tail -f logs/nanobot.err       # Nanobot (ошибки)
tail -f logs/nanobot.log       # Nanobot (info)

# Остановить sglang (PID-файл)
cat logs/sglang.pid            # → 12345
kill 12345                     # или kill -TERM
# (или: rm logs/sglang.pid после kill, чтобы перезапуск понял что нет процесса)

# Перезапустить весь пайплайн
# Ctrl+C → python osiris_gateway.py
```

## Что osiris_gateway.py НЕ делает

- **Не ставит PostgreSQL** — должен быть готов отдельно (либо Docker, либо отдельный
  сервер). Нужны: `postgresql://USER:PASS@HOST:5433/act_constructor`.
- **Не качает модель** — Qwen3.6 35B-A3B должна лежать на диске
  (`/data/models/Qwen3.6-35B-A3B-Instruct` или где указано через `--model-path`).
- **Не запускает Audit Workstation** — это отдельный сервис (порт 8000).
- **Не запускает audit_bridge UI** — это Python-процесс на порту 8090.

## Зачем это отдельно от gateway.py

| Файл | Назначение | serving-секция в config.json |
|------|-----------|-----------------------------|
| `gateway.py` | обычный entrypoint Nanobot (dev/CI/локальная разработка) | опциональна; если есть — bootstrap сам поднимает LLM |
| `osiris_gateway.py` | production entrypoint для голой ноды с GPU | обязательна; создаётся из CLI args |
| `scripts/serving/` | низкоуровневые утилиты (pip install / subprocess / health-check / config patch) | переиспользуются обоими |

`osiris_gateway.py` использует `scripts.serving.*` для bootstrap, но добавляет:
1. **Жёсткие проверки окружения** (nvidia-smi, driver, CUDA, версии torch/sglang).
2. **Pre-install всех зависимостей** с правильными wheel-index URL (CUDA 12.4).
3. **Авто-конфигурацию serving** на основе CLI args (не нужно вручную править config.json).
4. **Cleanup при выходе** (SIGINT/SIGTERM → stop_serving).

## Конфигурация через ENV vars

```bash
export OSIRIS_MODEL_PATH=/data/models/Qwen3.6-35B-A3B-Instruct
export OSIRIS_SERVED_MODEL_NAME=Qwen3.6-35B-A3B
export OSIRIS_NUM_GPUS=4    # fallback если --num-gpus не передан и nvidia-smi недоступен
python osiris_gateway.py
```

## Что такое Qwen3.6 35B-A3B

```
Qwen3.6-35B-A3B-Instruct
│
├── 35B общих параметров (MoE: 64 эксперта)
├── 3B активных параметров на инференсе
├── ~70 GB в bf16 (на 1×A100-80GB впритык, лучше tp=4 или quantization)
└── Для запуска: --tp-size 4 (default при num_gpus >= 4)
```

## Диагностика

### sglang не стартует

```bash
# Проверить что GPU видны
nvidia-smi

# Проверить что torch видит CUDA
python -c "import torch; print(torch.__version__, torch.cuda.is_available())"
# Должно быть: 2.5.1+cu124 True

# Проверить sglang импортируется
python -c "import sglang; print(sglang.__version__)"
# Должно быть: 0.4.3
```

### Nanobot не подключается к Postgres

```bash
# Проверить DATABASE_URL в .secrets.env
grep DATABASE_URL .secrets.env

# Проверить что Postgres принимает соединения
docker exec -it audit_bridge_pg psql -U postgres -d act_constructor -c "SELECT 1"
```

### sglang падает на torchinductor

Если в `logs/sglang.log` есть `torch._dynamo` ошибки — это известный баг
qwen_3_5_moe + torchinductor. Решение: убедиться что
`serving.sglang.torch_compile = false` (по умолчанию `True` для sglang,
но osiris_gateway.py ставит `False` чтобы избежать inductor).

## Файлы

```
audit_nanobot/
├── osiris_gateway.py           # этот entrypoint
├── osiris_requirements.txt     # зафиксированные версии
├── gateway.py                  # стандартный entrypoint Nanobot
├── requirements.txt            # базовые зависимости Nanobot
├── config.json                 # патчится osiris_gateway.py
├── scripts/serving/            # низкоуровневые утилиты
│   ├── config.py               # схема serving-секции
│   ├── sglang_launcher.py      # subprocess + pip install
│   ├── health_check.py         # /v1/models polling
│   ├── render_config.py        # patch config.json
│   └── serving_bootstrap.py    # точка входа для gateway.py
├── logs/
│   ├── sglang.log              # sglang-сервер
│   ├── sglang.pid              # PID-файл для остановки
│   └── nanobot.{log,err}       # Nanobot pipeline
└── docs/osiris/
    ├── README.md               # этот файл
    └── TESTING_ON_LAPTOP.md    # как протестировать на ноуте без GPU
```

## См. также

- `docs/serving/SGLANG.md` — подробности по sglang (почему не vLLM, фиксы для Qwen3-MoE)
- `docs/serving/LOCAL_TEST.md` — локальное тестирование на ноуте (без GPU)
- `CHANGELOG.md` — что менялось в каждой версии
