#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    echo "python3 не найден. Установите Python 3."
    exit 1
fi

if [ ! -x ".venv/bin/python" ]; then
    rm -rf .venv
    "$PYTHON" -m venv --without-pip .venv
fi

if ! .venv/bin/python -m pip --version >/dev/null 2>&1; then
    "$PYTHON" -m pip --python .venv install --upgrade pip
fi

.venv/bin/python -m pip install -r requirements.txt

mkdir -p snapshots
chmod +x linux/run.sh linux/setup.sh linux/install-desktop.sh

echo "Network Doctor подготовлен: $APP_DIR"
