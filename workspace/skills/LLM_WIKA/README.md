# LLM-Wiki — внешняя версия

MiniMax отвечает на вопросы; BGE-M3 и FAISS выполняют локальный
поиск. Админ вручную добавляет выгрузки Jira/Confluence JSON. Spark и
автоматическая выгрузка в эту версию не входят.

В MiniMax передаются выбранные тексты и вопросы. Используйте только данные,
разрешённые для внешней передачи. Ключ хранится в `.env` или окружении.
В комплекте нет ключей и рабочих выгрузок.

## Первый запуск

Требуется Python 3.12. Команды — в терминале из корня проекта.

1. Установите зависимости:

```bash
python3.12 --version
python3.12 -m pip install --target .packages -r requirements.txt
```

Зависимости устанавливаются в `.packages` внутри проекта и подключаются
автоматически. Системные пакеты не изменяются; дополнительная активация не нужна.
Если `python3.12` не найден, на macOS с Homebrew:

```bash
brew install python@3.12
```

Если Homebrew не добавил команду в PATH, используйте полный путь
`$(brew --prefix python@3.12)/bin/python3.12` вместо `python3.12`.
На Windows вместо `python3.12` используйте `py -3.12`.

2. Если `.env` ещё нет, скопируйте `.env.example` в `.env`. Впишите
   `MINIMAX_API_KEY`. При необходимости измените `MINIMAX_MODEL` на доступную
   вашему тарифу модель. Установите `LLM_WIKI_ALLOW_EXTERNAL_CONTEXT=true`
   только для разрешённых к внешней передаче данных.

3. Установите embedding-модель и проверьте API:

```bash
python3.12 -m wiki_agent model install
python3.12 -m wiki_agent doctor
python3.12 -m wiki_agent doctor --ping
```

`model install` сначала ищет BGE-M3 локально: в папке проекта и стандартном
кэше Hugging Face. Если модель уже есть, использует её без скачивания и копирования.
Только при отсутствии локальной модели скачивает официальный snapshot BAAI/bge-m3
в проект и фиксирует commit в `llm_wiki_model.json`. Веса около 2,1 ГБ;
оперативной памяти нужно больше. Обычные команды модель не скачивают.
Для определённой версии: `model install --revision <commit>`.
Не редактируйте файлы установленной модели. Она работает локально;
embedding API и отдельный ключ не нужны. Для модели в другой папке задайте
`LLM_WIKI_EMBEDDING_MODEL=/полный/путь/к/bge-m3` в `.env`.
Для автоматического поиска BGE-M3 не задавайте `LLM_WIKI_EMBEDDING_MODEL`.
После смены модели перестройте существующий индекс: `python3.12 -m wiki_agent index build`.

4. Положите JSON непосредственно в `raw/sources/` (без подпапок), затем:

```bash
python3.12 -m wiki_agent jira prepare
python3.12 -m wiki_agent index status
python3.12 -m wiki_agent jira query "Расскажи о TRCORE-10047" --dry-run
python3.12 -m wiki_agent jira query "Расскажи о TRCORE-10047"
```

Используйте ключ из своей выгрузки. После добавления новых JSON повторите
`jira prepare`. Неизменившиеся summaries и эмбеддинги используются из кэша.
Перед каждым вопросом prepare не нужен. Новая версия источника — отдельный JSON;
оригиналы не перезаписываются.

## Проверка на вымышленных данных

В `examples/jira-json/` лежат три полностью вымышленных выгрузки:
`DEMO-1001` (уведомления), `DEMO-1002` (дублирование уведомлений),
`SHOP-2001` (резервирование товара). Они не являются рабочими сведениями.
Две задачи DEMO ссылаются на одну страницу: проверяется дедупликация и связи.
SHOP содержит исторический текст и страницу без текста: предупреждения ожидаемы.

После настройки `.env` и установки модели, в тестовой базе:

```bash
cp -n examples/jira-json/*.json raw/sources/
python3.12 -m wiki_agent jira prepare
python3.12 -m wiki_agent index status
python3.12 -m wiki_agent jira query "Расскажи о DEMO-1001" --dry-run
python3.12 -m wiki_agent jira query "Сколько повторных попыток доставки уведомления предусмотрено?"
python3.12 -m wiki_agent jira prepare
```

В пустой базе ожидаются 3 Jira и 4 уникальных Confluence. На повторном prepare
неизменившиеся summaries и векторы переиспользуются.
Для DEMO-1001: три повторные попытки через 1, 5 и 15 минут, срок доставки
30 секунд. Для DEMO-1002: повторный одинаковый `event_id` не должен создавать
второе уведомление. Для SHOP-2001: резерв 15 минут, но документ исторический.

Автотесты: `python3.12 -m unittest discover -s tests -t .`.
Они используют подставные ответы LLM и тестовые векторы; настоящий FAISS
проверяется локально. Это не заменяет `doctor --ping`, загрузку настоящей
BGE-модели и ответы на тестовые вопросы с вашим действующим ключом.

## Команды

| Команда `python3.12 -m wiki_agent …` | Назначение |
|---|---|
| `model install` | Найти локальную BGE-M3; скачать только если её нет |
| `doctor` / `doctor --ping` | Настройки / реальный тест MiniMax |
| `jira prepare` | Подготовить все Jira JSON и обновить поиск |
| `jira load TRCORE` | Добавить из локальных JSON проект и связанные Confluence |
| `jira load TRCORE-10047` | Добавить конкретную задачу из локальных JSON |
| `jira query "вопрос"` | Ответить по Jira/Confluence и Wiki |
| `jira query "вопрос" --dry-run` | Выбранные документы без вызова MiniMax |
| `index status` | Готовность индекса |
| `index build` | Перестроить индекс подготовленных карточек и Wiki |
| `index search "запрос"` | Локальный поиск без MiniMax |
| `query "вопрос"` | Ответить по тематической Markdown-Wiki |
| `jira ingest raw/sources/файл.json` | Предложить Wiki-страницы из Jira |
| `ingest raw/sources/файл.pdf` | Предложить Wiki-изменения из обычного документа |
| `apply proposals/точное-имя.md` | Применить явно выбранное предложение |
| `lint --technical-only` / `lint` | Отчёт проверки без LLM / с LLM |
| `watch` | Необязательный мониторинг источников, создаёт только Proposal |

Все аргументы: `python3.12 -m wiki_agent --help` и `<команда> --help`.
Для поиска по JSON ingest/apply не требуются.

## Где результаты

- `raw/extracted/jira-confluence/` — тексты.
- `.cache/llm-wiki/jira-confluence/` — карточки, summaries и manifest.
- `.cache/llm-wiki/embeddings/` — кэш векторов.
- `.cache/llm-wiki/faiss/` — `pages.faiss` и `pages.json`.
- `.cache/llm-wiki/models/bge-m3/` — скачанная модель; существующий HF-кэш тоже поддерживается.
- `wiki/` — только подтверждённые тематические статьи.

## Ошибки

Нет JSON — добавьте их в `raw/sources/`. Нет модели — выполните `model install`.
`FAISS-индекс: missing` до первого `jira prepare` — нормальное состояние:
документы ещё не подготовлены. `Ping: OK` подтверждает ответ MiniMax,
но не проверяет embedding-модель. Совместимость Python проверяется по
ограничениям установленных пакетов и требованию Python 3.12;
загрузка модели проверяется при подготовке индекса.
401/403 MiniMax — ключ и доступ к модели/тарифу; 400 — модель, параметры или
размер контекста. Ключи Coding/M Plan могут иметь ограничения применения:
проверьте условия своего тарифа для этой задачи.
Таймаут — сеть; повторы ограничены `MINIMAX_MAX_ATTEMPTS`.
Обрезан ответ — увеличьте `MINIMAX_MAX_TOKENS` или уменьшите контекст.
CPU поддерживается; для GPU установите подходящую сборку PyTorch.

[API MiniMax](https://platform.minimax.io/docs/api-reference/text-openai-api),
[BGE-M3](https://huggingface.co/BAAI/bge-m3).
