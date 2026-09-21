#!/bin/bash

source /opt/app-root/bin/activate 2>/dev/null || true

cd "$(dirname "$0")" || exit 1
if ! command -v python &> /dev/null && ! command -v python3 &> /dev/null; then
    echo "Ошибка: Python не установлен или не найден в PATH."
    exit 1
fi

PYTHON_CMD="python"
if command -v python3 &> /dev/null; then
    PYTHON_CMD="python3"
elif command -v python &> /dev/null; then
    PYTHON_CMD="python"
fi

chmod +x scripts/*.py 2>/dev/null
SKILL_DIR="$(pwd)"
PIPELINES_DIR="${PIPELINES_CACHE_DIR:-$SKILL_DIR/../../data_store/cache/caches_pipelines}"
export IOR_RAG_CACHE_DIR="${IOR_RAG_CACHE_DIR:-$PIPELINES_DIR/cache_final}"
export IOR_BGE_MODEL_PATH="${IOR_BGE_MODEL_PATH:-$PIPELINES_DIR/BAAI:bge-m3}"
export IOR_RERANKER_MODEL_PATH="${IOR_RERANKER_MODEL_PATH:-$PIPELINES_DIR/bge-reranker-v2-m3}"
"$PYTHON_CMD" scripts/cli.py "$@"
