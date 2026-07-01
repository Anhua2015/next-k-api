#!/usr/bin/env bash
# start_kk_vnpy.sh — King Keltner vnpy 常驻引擎（可由 start.sh 自动调用）
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="$SCRIPT_DIR/.venv"
PID_DIR="$SCRIPT_DIR/.pid"
LOG_DIR="$SCRIPT_DIR/logs"
PID_FILE="$PID_DIR/kk_vnpy.pid"
LOG_FILE="$LOG_DIR/kk_vnpy.log"
ENV_FILE="$SCRIPT_DIR/.env.oi"
REQUIREMENTS_VNPY="$SCRIPT_DIR/requirements-vnpy.txt"

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

info()  { echo -e "${GREEN}[kk-vnpy]${NC} $*"; }
warn()  { echo -e "${YELLOW}[kk-vnpy]${NC} $*"; }
error() { echo -e "${RED}[kk-vnpy]${NC} $*" >&2; }

is_running() {
    [[ -f "$1" ]] && kill -0 "$(cat "$1")" 2>/dev/null
}

load_env_file() {
    if [[ ! -f "$ENV_FILE" ]]; then
        return 0
    fi
    while IFS='=' read -r key value; do
        key=$(echo "$key" | xargs)
        [[ -z "$key" || "$key" == \#* ]] && continue
        value=$(echo "$value" | sed 's/[[:space:]]*#.*//' | xargs)
        [[ -z "${!key+x}" ]] && export "$key"="$value"
    done < <(grep -v '^\s*#' "$ENV_FILE" | grep '=')
}

should_start_kk_vnpy() {
    local engine="${KK_ENGINE:-vnpy}"
    local enabled="${KK_ENABLED:-1}"
    local auto="${KK_VNPY_AUTO_START:-1}"
    engine=$(echo "$engine" | tr '[:upper:]' '[:lower:]')
    enabled=$(echo "$enabled" | tr '[:upper:]' '[:lower:]')
    auto=$(echo "$auto" | tr '[:upper:]' '[:lower:]')
    [[ "$auto" =~ ^(1|true|yes|on)$ ]] || return 1
    [[ "$enabled" =~ ^(1|true|yes|on)$ ]] || return 1
    [[ "$engine" == "vnpy" ]] || return 1
    return 0
}

wait_for_protocol() {
    local url="${PROTOCOL_API_URL:-}"
    [[ -n "$url" ]] || { warn "PROTOCOL_API_URL 未设置，跳过 protocol 健康等待"; return 0; }
    url="${url%/}"
    local health="${url}/api/binance/health"
    local max="${KK_PROTOCOL_WAIT_SEC:-90}"
    info "等待 protocol 就绪: $health (最多 ${max}s)"
    local i=0
    while [[ $i -lt $max ]]; do
        if curl -sf "$health" >/dev/null 2>&1; then
            info "protocol 已就绪"
            return 0
        fi
        sleep 2
        i=$((i + 2))
    done
    warn "protocol 未在 ${max}s 内响应；kk_vnpy 仍将启动（请确认跳板已运行）"
}

if is_running "$PID_FILE"; then
    info "kk_vnpy 已在运行 PID=$(cat "$PID_FILE")"
    exit 0
fi

if [[ "${START_KK_SKIP_ENV:-0}" != "1" ]]; then
    load_env_file
fi

export KK_ENGINE="${KK_ENGINE:-vnpy}"

if ! should_start_kk_vnpy; then
    info "KK_VNPY 自动启动已跳过（KK_ENGINE=${KK_ENGINE} KK_ENABLED=${KK_ENABLED:-1}）"
    exit 0
fi

if [[ -n "${START_KK_VENV_PYTHON:-}" ]]; then
    PYTHON_VENV="$START_KK_VENV_PYTHON"
elif [[ -f "$VENV_DIR/bin/activate" ]]; then
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate"
    PYTHON_VENV="$VENV_DIR/bin/python"
else
    PYTHON_BIN=""
    for py in python3.11 python3 python; do
        command -v "$py" &>/dev/null && PYTHON_BIN="$py" && break
    done
    [[ -n "$PYTHON_BIN" ]] || { error "需要 Python 3.11+"; exit 1; }
    "$PYTHON_BIN" -m venv "$VENV_DIR"
    # shellcheck source=/dev/null
    source "$VENV_DIR/bin/activate"
    PYTHON_VENV="$VENV_DIR/bin/python"
fi

if [[ "${START_KK_SKIP_DEPS:-0}" != "1" ]]; then
    "$PYTHON_VENV" -m pip install --quiet --upgrade pip
    "$PYTHON_VENV" -m pip install --quiet -r "$SCRIPT_DIR/requirements.txt"
    if [[ -f "$REQUIREMENTS_VNPY" ]]; then
        "$PYTHON_VENV" -m pip install --quiet -r "$REQUIREMENTS_VNPY"
    fi
fi

mkdir -p "$PID_DIR" "$LOG_DIR"
wait_for_protocol

info "启动 kk_vnpy_runner（KK_ENGINE=$KK_ENGINE）..."
nohup "$PYTHON_VENV" "$SCRIPT_DIR/kk_vnpy_runner.py" >> "$LOG_FILE" 2>&1 &
echo $! > "$PID_FILE"
info "kk_vnpy 已启动 PID=$(cat "$PID_FILE") 日志=$LOG_FILE"
