# Локальный запуск Nanobot с Ollama (Qwen3.5 9b)

Сценарий для разработки: маленькая модель, плохое качество, зато стартует
за 10 секунд на ноутбуке без GPU. Используем ollama как OpenAI-compatible
HTTP API.

## Зачем это нужно

Удалённый GPU-сервер с Qwen3-30B-A3B-Instruct хорош для прода, но для
разработки и e2e-тестов (gateway ↔ postgres channel ↔ LLM ↔ outbound)
нужен быстрый и дешёвый вариант. Ollama + Qwen3.5 9b запускается за
10 секунд, потребляет ~6 GB RAM на CPU, и Nanobot общается с ней точно
так же, как с sglang на GPU-сервере.

## Шаг 1: Установить Ollama

**Windows**:
```powershell
winget install Ollama.Ollama
# Или скачать: https://ollama.com/download/windows
```

**macOS**:
```bash
brew install ollama
```

**Linux**:
```bash
curl -fsSL https://ollama.com/install.sh | sh
```

## Шаг 2: Скачать модель

```bash
ollama pull qwen3.5:9b
```

Это скачает ~6.6 GB. Другие варианты:

```bash
ollama pull qwen3:8b          # альтернатива
ollama pull gemma4:12b        # 7.6 GB
ollama pull qwen2.5-coder:7b  # для code-задач
```

## Шаг 3: Запустить Ollama

В отдельном окне:
```bash
ollama serve
```

Это поднимет OpenAI-compatible API на `http://127.0.0.1:11434/v1`.

Проверить:
```bash
curl http://127.0.0.1:11434/v1/models
```

Должно вернуть JSON со списком моделей.

## Шаг 4: Настроить Nanobot

В `config.json` добавить секцию `serving`:

```json
{
  "serving": {
    "mode": "ollama",
    "provider_alias": "ollama",
    "api_base": "http://127.0.0.1:11434/v1",
    "api_key": "ollama",
    "model_name": "qwen3.5:9b",

    "ollama": {
      "source": "external",
      "host": "127.0.0.1",
      "port": 11434,
      "model": "qwen3.5:9b"
    },

    "health_check_timeout_sec": 60,
    "install_deps": false
  }
}
```

`source=external` — bootstrap не пытается запустить ollama сам (он уже запущен
вручную). Если хотите чтобы bootstrap сам поднял ollama — поставьте
`source=spawn`.

## Шаг 5: Запустить Nanobot

```bash
python gateway.py
```

В консоли увидите:
```
[serving] mode=ollama; bootstrapping...
[serving] mode=external; assuming ollama already up at 127.0.0.1:11434
[serving] ready in 1 attempt(s); models=['qwen3.5:9b', ...]
[serving] backup -> .../config.json.bak-20260908-120000
[serving] patched .../config.json:
  providers.ollama.apiBase = http://127.0.0.1:11434/v1
  providers.ollama.apiKey  = ollama
  agents.defaults.model     = qwen3.5:9b
✓ serving ready: mode=ollama api_base=http://127.0.0.1:11434/v1 model=qwen3.5:9b
🐈 Starting nanobot gateway · project v2.4.0 (nanobot 0.3.0)...
```

## Альтернатива: bootstrap сам поднимает ollama

Если не хотите вручную запускать `ollama serve`, поставьте `source=spawn`:

```json
{
  "serving": {
    "mode": "ollama",
    "ollama": {
      "source": "spawn",
      "host": "127.0.0.1",
      "port": 11434,
      "model": "qwen3.5:9b",
      "keep_alive": "10m",
      "num_gpu": 1
    }
  }
}
```

Тогда bootstrap:
1. Запустит `ollama serve` в фоне (PID в `logs/ollama.pid`).
2. Сделает `ollama pull qwen3.5:9b` (если модель ещё не скачана).
3. Дождётся `/v1/models`.
4. Пропатчит config.json.
5. Запустит Nanobot.

При `Ctrl+C` ollama остаётся работать. Остановить:
```bash
python -m scripts.serving.stop --mode ollama
```

## Проверка после старта

В соседнем терминале:
```bash
# Проверить что Nanobot видит ollama
docker exec audit_bridge_pg psql -U postgres -d act_constructor -c "
  INSERT INTO public.agent_conversation_messages (id, chat_id, user_id, role, content, status, created_at, updated_at)
  VALUES (gen_random_uuid(), 'local-test', 'dev', 'user', 'Привет!', 'pending', NOW(), NOW());
"
```

Через 10-30 секунд:
```bash
docker exec audit_bridge_pg psql -U postgres -d act_constructor -c "
  SELECT role, LEFT(content, 200), status FROM public.agent_conversation_messages
  ORDER BY created_at DESC LIMIT 3;
"
```

Должен быть ответ от qwen3.5:9b.

## Производительность

На ноутбуке без GPU:
- Qwen3.5 9b: ~3-5 токенов/сек (CPU-only через ollama)
- Qwen3 8b: ~4-6 токенов/сек
- Gemma 4 12b: ~2-3 токенов/сек (медленнее, но качество выше)

На ноутбуке с RTX 3060/4060:
- Qwen3.5 9b: ~30-50 токенов/сек (GPU через ollama)

Для e2e-тестов этого достаточно. Для серьёзных задач — sglang на A100.
