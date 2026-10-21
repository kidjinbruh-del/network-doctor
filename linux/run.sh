#!/usr/bin/env bash
set -u
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR" || exit 1
LOG="$APP_DIR/network-doctor.log"
PY="$APP_DIR/.venv/bin/python"

message() {
    if command -v zenity >/dev/null 2>&1; then
        zenity --error --title="Network Doctor" --text="$1" >/dev/null 2>&1
    elif command -v xmessage >/dev/null 2>&1; then
        xmessage -center "$1" >/dev/null 2>&1
    elif command -v notify-send >/dev/null 2>&1; then
        notify-send "Network Doctor" "$1"
    fi
}

if [ ! -x "$PY" ] || ! "$PY" -c "import customtkinter, routeros_api, jinja2, matplotlib, reportlab, keyring" >/dev/null 2>&1; then
    "$APP_DIR/linux/setup.sh" >>"$LOG" 2>&1
    if [ ! -x "$PY" ] || ! "$PY" -c "import customtkinter, routeros_api, jinja2, matplotlib, reportlab, keyring" >/dev/null 2>&1; then
        message "Не удалось подготовить Python-окружение. Лог: $LOG"
        exit 1
    fi
fi

"$PY" "$APP_DIR/app.py" >>"$LOG" 2>&1
status=$?
if [ "$status" -ne 0 ]; then
    message "Network Doctor завершился с ошибкой. Лог: $LOG"
fi
exit "$status"
