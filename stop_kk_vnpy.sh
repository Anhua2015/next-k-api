#!/usr/bin/env bash
# stop_kk_vnpy.sh — 仅停止 KK vnpy 策略进程
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/.pid/kk_vnpy.pid"
GRACEFUL_TIMEOUT=15

if [[ ! -f "$PID_FILE" ]]; then
    echo "[kk-vnpy] 未运行"
    exit 0
fi
pid=$(cat "$PID_FILE")
if ! kill -0 "$pid" 2>/dev/null; then
    rm -f "$PID_FILE"
    echo "[kk-vnpy] 已不在运行"
    exit 0
fi
kill -TERM "$pid" 2>/dev/null || true
elapsed=0
while kill -0 "$pid" 2>/dev/null && [[ $elapsed -lt $GRACEFUL_TIMEOUT ]]; do
    sleep 1
    elapsed=$((elapsed + 1))
done
if kill -0 "$pid" 2>/dev/null; then
    kill -KILL "$pid" 2>/dev/null || true
fi
rm -f "$PID_FILE"
echo "[kk-vnpy] 已停止 PID=$pid"
