#!/usr/bin/env bash
# run_batch_test.sh — 7 активных пресетов + 3 Smart SQL запроса
set -e
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PYTHONPATH="${SCRIPT_DIR}:${SCRIPT_DIR}/scripts:${SCRIPT_DIR}/utils:${PYTHONPATH}" python "${SCRIPT_DIR}/scripts/run_batch_test.py"
