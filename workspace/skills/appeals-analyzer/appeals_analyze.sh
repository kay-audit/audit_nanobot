#!/usr/bin/env bash
set -euo pipefail

SKILL_DIR="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
cd "$SKILL_DIR"

if [[ -f /opt/app-root/bin/activate ]]; then
    # shellcheck disable=SC1091
    source /opt/app-root/bin/activate
fi

if [[ -n "${PYTHON_BIN:-}" ]]; then
    PYTHON_CMD="$PYTHON_BIN"
elif command -v python3 >/dev/null 2>&1; then
    PYTHON_CMD="python3"
elif command -v python >/dev/null 2>&1; then
    PYTHON_CMD="python"
else
    echo "Ошибка: Python не найден. Укажите путь через PYTHON_BIN." >&2
    exit 127
fi

export PYTHONUNBUFFERED=1
export PYTHONUTF8=1
export APPEALS_DISABLE_VLLM="${APPEALS_DISABLE_VLLM:-1}"
export GIGACHAT_API_URL="${GIGACHAT_API_URL:-http://liveaccess/v1/gc/chat/completions}"

PIPELINES_DIR="${PIPELINES_CACHE_DIR:-$SKILL_DIR/../../data_store/cache/caches_pipelines}"
export APPEALS_RAG_CACHE_DIR="${APPEALS_RAG_CACHE_DIR:-$PIPELINES_DIR/cache_le_finale2}"
export APPEALS_BGE_MODEL_PATH="${APPEALS_BGE_MODEL_PATH:-$PIPELINES_DIR/BAAI:bge-m3}"
export APPEALS_RERANKER_MODEL_PATH="${APPEALS_RERANKER_MODEL_PATH:-$PIPELINES_DIR/bge-reranker-v2-m3}"

mkdir -p "$SKILL_DIR/logs"
exec "$PYTHON_CMD" -u "$SKILL_DIR/scripts/cli.py" "$@"
