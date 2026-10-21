#!/usr/bin/env bash
# Standalone history test: uses the existing CLI, no gateway or UI.
set -euo pipefail

SKILL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ "${1:-}" == "--help" || "${1:-}" == "-h" ]]; then
    cat <<'HELP'
Тест истории DOCX без gateway и UI.
Положите последнюю редакцию и её «Обзор изменений» в tests/manual/input
рядом с этим скриптом (папка находится внутри legal_summarizer).

Запуск: bash test_history.sh --target "Название исследуемого файла.docx"
Без --target CLI вернёт needs_input со списком документов и вопросом.
--raw                  Проверка парсера без LLM (по умолчанию 5 изменений).
--input PATH           Свой DOCX/каталог; можно повторять. Заменяет папку по умолчанию.
--session-key NAME     Отдельный прогон (по умолчанию manual-history).
Остальные параметры передаются scripts/cli_history.py без изменений.

Полный режим требует Python с зависимостями проекта и доступ к настроенной LLM.
PYTHON_BIN=/path/to/venv/bin/python выбирает интерпретатор явно.
По умолчанию используется python из PATH, затем python3.
В stdout возвращается JSON; пути к полному отчёту — result_path/result_json_path.
Повторный запуск использует кэш; для нового теста задайте другой --session-key.
HELP
    exit 0
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
    HISTORY_PYTHON="$PYTHON_BIN"
elif command -v python >/dev/null 2>&1; then
    HISTORY_PYTHON=python
else
    HISTORY_PYTHON=python3
fi
if ! command -v "$HISTORY_PYTHON" >/dev/null 2>&1; then
    echo "Не найден Python: $HISTORY_PYTHON. Укажите PYTHON_BIN." >&2
    exit 127
fi

HAS_INPUT=false
HAS_SESSION=false
for arg in "$@"; do
    case "$arg" in
        --input|--input=*) HAS_INPUT=true ;;
        --session-key|--session-key=*) HAS_SESSION=true ;;
    esac
done
if [[ "$HAS_INPUT" == false ]]; then
    set -- --input "$SKILL_DIR/tests/manual/input" "$@"
fi
if [[ "$HAS_SESSION" == false ]]; then
    set -- --session-key manual-history "$@"
fi
exec "$HISTORY_PYTHON" "$SKILL_DIR/scripts/cli_history.py" "$@"
