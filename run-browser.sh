#!/usr/bin/env bash
# Local browser entry for a remote workspace. No dependencies or system changes.
set -Eeuo pipefail

readonly APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly RUN_DIR="${APP_DIR}/.run"
readonly PID_FILE="${RUN_DIR}/browser-gateway.pid"
readonly LOG_FILE="${RUN_DIR}/browser-gateway.log"
readonly PORT_NUMBER="${WORKSPACE_LOCAL_PORT:-1781}"
readonly ACTION="${1:-start}"
mkdir -p "${RUN_DIR}"

read_pid() { [[ ! -f "${PID_FILE}" ]] || tr -d '[:space:]' < "${PID_FILE}"; }

is_running() {
    local pid command
    pid="$(read_pid)"
    [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
    kill -0 "${pid}" 2>/dev/null || return 1
    command="$(ps -p "${pid}" -o command= 2>/dev/null || true)"
    [[ "${command}" == *"${APP_DIR}/workspace_gateway.py"* ]]
}

start_gateway() {
    if is_running; then
        echo "Локальный вход уже запущен (PID $(read_pid))."
        echo "http://localhost:${PORT_NUMBER}/efficiency/pomodoro"
        return
    fi
    if [[ -z "${PYTHON_BIN:-}" ]]; then
        for candidate in python3.13 python3.11 python3; do
            if command -v "${candidate}" >/dev/null 2>&1 && "${candidate}" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))' 2>/dev/null; then
                PYTHON_BIN="${candidate}"
                break
            fi
        done
    fi
    if [[ -z "${PYTHON_BIN:-}" ]] || ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(sys.version_info < (3, 11))'; then
        echo "Нужен Python 3.11 или новее. Можно задать путь через PYTHON_BIN." >&2
        return 1
    fi
    nohup "${PYTHON_BIN}" "${APP_DIR}/workspace_gateway.py" --port "${PORT_NUMBER}" >> "${LOG_FILE}" 2>&1 < /dev/null &
    local pid=$!
    printf '%s\n' "${pid}" > "${PID_FILE}"
    sleep 0.5
    if ! is_running; then
        echo "Не удалось запустить локальный вход. Проверьте ${LOG_FILE}." >&2
        return 1
    fi
    echo "Открой в Chrome: http://localhost:${PORT_NUMBER}/efficiency/pomodoro"
    echo "Терминал можно закрыть. Остановка: ./run-browser.sh stop"
}

stop_gateway() {
    if ! is_running; then
        echo "Локальный вход не запущен."
        return
    fi
    local pid
    pid="$(read_pid)"
    kill "${pid}"
    for _ in {1..25}; do
        if ! is_running; then
            : > "${PID_FILE}"
            echo "Локальный вход остановлен."
            return
        fi
        sleep 0.2
    done
    echo "Процесс ${pid} ещё работает; проверьте его вручную." >&2
    return 1
}

case "${ACTION}" in
    start) start_gateway ;;
    stop) stop_gateway ;;
    restart) stop_gateway; start_gateway ;;
    status)
        if is_running; then echo "Локальный вход работает (PID $(read_pid))."; else echo "Локальный вход не запущен."; fi
        ;;
    logs) touch "${LOG_FILE}"; tail -n 50 -f "${LOG_FILE}" ;;
    *) echo "Использование: ./run-browser.sh [start|stop|restart|status|logs]" >&2; exit 2 ;;
esac
