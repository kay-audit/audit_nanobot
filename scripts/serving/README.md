# scripts/serving — pre-startup LLM bootstrap

Опциональная pre-startup цепочка для `gateway.py`, поднимающая локальный
LLM-сервер перед стартом Nanobot. Никак не меняет логику агента.

## Сценарии

| Сценарий | mode | Конфигурация |
|----------|------|--------------|
| Локальная разработка (ollama) | `ollama` | см. [`docs/serving/LOCAL_TEST.md`](../../docs/serving/LOCAL_TEST.md) |
| Прод-сервер с Qwen3-30B-A3B | `sglang` | см. [`docs/serving/SGLANG.md`](../../docs/serving/SGLANG.md) |
| Подключение к уже запущенному серверу | `external` | см. [`docs/serving/SGLANG.md`](../../docs/serving/SGLANG.md) |
| Штатное поведение (vLLM через providers.vllm) | `off` или секция `serving` отсутствует | — |

## Быстрый старт

### 1. Локально с ollama

```bash
# Установить ollama (https://ollama.com/download)
ollama pull qwen3.5:9b
ollama serve &
```

В `config.json`:
```json
{
  "serving": {
    "mode": "ollama",
    "provider_alias": "ollama",
    "api_base": "http://127.0.0.1:11434/v1",
    "model_name": "qwen3.5:9b",
    "ollama": {"source": "external", "host": "127.0.0.1", "port": 11434, "model": "qwen3.5:9b"}
  }
}
```

```bash
python gateway.py
```

### 2. На GPU-сервере с sglang

```bash
git checkout sglang_osiris
```

В `config.json`:
```json
{
  "serving": {
    "mode": "sglang",
    "provider_alias": "vllm",
    "model_name": "Qwen3-30B-A3B",
    "sglang": {
      "model_path": "/data/models/Qwen3-30B-A3B-Instruct",
      "served_model_name": "Qwen3-30B-A3B",
      "gpu_ids": "0",
      "max_model_len": 32768,
      "dtype": "bfloat16",
      "torch_compile": false
    }
  }
}
```

```bash
python gateway.py
```

## CLI

```bash
# Проверить что LLM-сервер живой
python -m scripts.serving.health --api-base http://localhost:30000/v1

# Остановить sglang/ollama по PID-файлу
python -m scripts.serving.stop
python -m scripts.serving.stop --mode ollama

# Остановить всё и стартовать заново (после правок конфига)
python -m scripts.serving.stop
python gateway.py
```

## Структура пакета

```
scripts/serving/
├── __init__.py              # пакет
├── config.py                # ServingSettings, SglangSettings, OllamaSettings
├── health_check.py          # /v1/models, /health, wait_until_ready
├── sglang_launcher.py       # pip install sglang, subprocess.Popen
├── ollama_launcher.py       # subprocess.Popen "ollama serve", "ollama pull"
├── render_config.py         # patch providers.<alias>.apiBase + agents.defaults.model
├── serving_bootstrap.py     # ensure_serving() — главная точка входа
├── stop.py                  # CLI для остановки
├── health.py                # CLI health-check
└── README.md                # этот файл
```

## Безопасность

- Backup: `config.json.bak-<ts>` создаётся перед каждой правкой.
- Idempotent: если sglang/ollama уже запущен — bootstrap не запустит второй процесс.
- Graceful degradation: при любой ошибке bootstrap печатает stacktrace и
  продолжает штатный старт Nanobot (без локального LLM).
- Atomic write: `render_config` пишет через `tempfile + os.replace`
  (без половинчатых записей при крэше).

## Что НЕ меняется

- ❌ Не правит код агента (`lib/`, `workspace/`).
- ❌ Не правит SQL-схему (`sql/`).
- ❌ Не правит `nanobot/providers/registry.py` (пакетный реестр).
- ❌ Не удаляет vLLM-механизм — он остаётся как fallback.

Подробная документация:
- [`docs/serving/SGLANG.md`](../../docs/serving/SGLANG.md) — sglang на GPU-сервере.
- [`docs/serving/LOCAL_TEST.md`](../../docs/serving/LOCAL_TEST.md) — ollama локально.
