#!/usr/bin/env bash

set -Eeuo pipefail

readonly APP_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
readonly VENV_DIR="${APP_DIR}/.venv"
readonly RUN_DIR="${APP_DIR}/.run"
readonly PID_FILE="${RUN_DIR}/dev-toolbox.pid"
readonly LOG_FILE="${RUN_DIR}/server.log"
readonly PORT_NUMBER="1700"

PYTHON_BIN="${PYTHON_BIN:-python3}"
HOST_ADDRESS="${HOST:-127.0.0.1}"
ACTION="${1:-start}"

on_error() {
    local exit_code=$?
    echo >&2
    echo "Не удалось выполнить команду '${ACTION}' (код ${exit_code})." >&2
    echo "Проверьте сообщения выше и лог ${LOG_FILE}." >&2
    exit "${exit_code}"
}

trap on_error ERR

cd "${APP_DIR}"
mkdir -p "${RUN_DIR}"

read_pid() {
    if [[ -f "${PID_FILE}" ]]; then
        tr -d '[:space:]' < "${PID_FILE}"
    fi
}

is_running() {
    local pid
    local command

    pid="$(read_pid)"
    [[ "${pid}" =~ ^[0-9]+$ ]] || return 1
    kill -0 "${pid}" 2>/dev/null || return 1

    command="$(ps -p "${pid}" -o command= 2>/dev/null || true)"
    # В некоторых контейнерах ps недоступен, хотя процесс жив. В обычной
    # системе дополнительно защищаемся от переиспользованного PID.
    [[ -z "${command}" ]] && return 0
    [[ "${command}" == *"${APP_DIR}/server.py"* ]]
}

cleanup_stale_pid() {
    if [[ -f "${PID_FILE}" ]] && ! is_running; then
        rm -f "${PID_FILE}"
    fi
}

prepare() {
    if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
        echo "Ошибка: ${PYTHON_BIN} не найден." >&2
        echo "Установите Python 3.10+ или передайте путь через PYTHON_BIN." >&2
        exit 1
    fi

    "${PYTHON_BIN}" - <<'PY'
import sys

if sys.version_info < (3, 10):
    raise SystemExit(
        f"Нужен Python 3.10 или новее, сейчас используется {sys.version.split()[0]}"
    )
PY

    mkdir -p \
        "${APP_DIR}/data/notes/drafts" \
        "${APP_DIR}/data/notes/articles" \
        "${APP_DIR}/data/notes/uploads"

    if [[ ! -x "${VENV_DIR}/bin/python" ]]; then
        echo "[1/3] Создаю виртуальное окружение…"
        "${PYTHON_BIN}" -m venv "${VENV_DIR}"
    else
        echo "[1/3] Виртуальное окружение уже готово."
    fi

    echo "[2/3] Проверяю и устанавливаю зависимости…"
    "${VENV_DIR}/bin/python" -m pip install \
        --disable-pip-version-check \
        --quiet \
        -r "${APP_DIR}/requirements.txt"
}

start_server() {
    cleanup_stale_pid
    if is_running; then
        echo "Dev Toolbox уже запущен (PID $(read_pid))."
        echo "Адрес: http://${HOST_ADDRESS}:${PORT_NUMBER}/"
        echo "Лог: ${LOG_FILE}"
        return 0
    fi

    prepare
    echo "[3/3] Запускаю Dev Toolbox в фоновом режиме…"

    nohup env \
        HOST="${HOST_ADDRESS}" \
        PORT="${PORT_NUMBER}" \
        DEBUG="false" \
        "${VENV_DIR}/bin/python" "${APP_DIR}/server.py" \
        >> "${LOG_FILE}" 2>&1 < /dev/null &

    local pid=$!
    printf '%s\n' "${pid}" > "${PID_FILE}"

    sleep 1
    if ! is_running; then
        rm -f "${PID_FILE}"
        echo "Сервер завершился сразу после запуска. Последние строки лога:" >&2
        tail -n 30 "${LOG_FILE}" >&2 || true
        return 1
    fi

    echo
    echo "Dev Toolbox запущен. Консоль можно закрыть."
    echo "Адрес: http://${HOST_ADDRESS}:${PORT_NUMBER}/"
    echo "PID: ${pid}"
    echo "Лог: ${LOG_FILE}"
}

stop_server() {
    cleanup_stale_pid
    if ! is_running; then
        echo "Dev Toolbox не запущен."
        return 0
    fi

    local pid
    pid="$(read_pid)"
    kill "${pid}"

    for _ in {1..25}; do
        if ! kill -0 "${pid}" 2>/dev/null; then
            rm -f "${PID_FILE}"
            echo "Dev Toolbox остановлен."
            return 0
        fi
        sleep 0.2
    done

    echo "Процесс ${pid} не остановился за 5 секунд." >&2
    echo "Проверьте его вручную: ps -p ${pid} -o command=" >&2
    return 1
}

show_status() {
    cleanup_stale_pid
    if is_running; then
        echo "Dev Toolbox работает (PID $(read_pid))."
        echo "Адрес: http://${HOST_ADDRESS}:${PORT_NUMBER}/"
        echo "Лог: ${LOG_FILE}"
    else
        echo "Dev Toolbox не запущен."
        return 0
    fi
}

show_logs() {
    touch "${LOG_FILE}"
    echo "Показываю лог. Для выхода нажмите Ctrl+C."
    tail -n 100 -f "${LOG_FILE}" || true
}

case "${ACTION}" in
    start)
        start_server
        ;;
    stop)
        stop_server
        ;;
    restart)
        stop_server
        start_server
        ;;
    status)
        show_status
        ;;
    logs)
        show_logs
        ;;
    *)
        echo "Использование: $0 {start|stop|restart|status|logs}" >&2
        exit 2
        ;;
esac
