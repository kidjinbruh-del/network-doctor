#!/usr/bin/env bash
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DESKTOP_DIR=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
if [ -z "$DESKTOP_DIR" ]; then
    DESKTOP_DIR="$HOME/Desktop"
fi
mkdir -p "$DESKTOP_DIR"
mkdir -p "$HOME/.local/share/applications"

ENTRY="$DESKTOP_DIR/Network Doctor.desktop"
cat >"$ENTRY" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Network Doctor
Comment=Диагностика MikroTik Wi-Fi
Exec=$APP_DIR/linux/run.sh
Path=$APP_DIR
Icon=$APP_DIR/linux/network-doctor.svg
Terminal=false
Categories=Network;Utility;
StartupNotify=true
EOF

chmod +x "$ENTRY" "$APP_DIR/linux/run.sh" "$APP_DIR/linux/setup.sh" "$APP_DIR/linux/install-desktop.sh"
cp "$ENTRY" "$HOME/.local/share/applications/Network Doctor.desktop"
chmod +x "$HOME/.local/share/applications/Network Doctor.desktop"

if command -v gio >/dev/null 2>&1; then
    gio set "$ENTRY" metadata::trusted true >/dev/null 2>&1 || true
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$HOME/.local/share/applications" >/dev/null 2>&1 || true
fi

echo "Ярлык создан: $ENTRY"
