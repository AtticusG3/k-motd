#!/bin/bash
# Preview the MOTD as a normal user, without installing to /etc (no root needed).
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DIR="${HOME}/.local/motd-sysinfo"
mkdir -p "$DIR"
for f in 01-sysinfo motd-render.py; do
    tr -d '\r' < "$SRC/$f" > "$DIR/$f"
    chmod 0755 "$DIR/$f"
done

export MOTD_RENDERER="${DIR}/motd-render.py"

echo "host: $(hostname)"
if command -v nvidia-smi &>/dev/null; then echo "nvidia: yes"; else echo "nvidia: no"; fi
echo "--- motd preview (user test, not installed to /etc) ---"
echo "wide (COLUMNS=120):"
TERM=xterm-256color COLUMNS=120 bash "$DIR/01-sysinfo" 2>&1 | head -15
echo "---"
echo "narrow (COLUMNS=80):"
TERM=xterm-256color COLUMNS=80 bash "$DIR/01-sysinfo" 2>&1 | head -20
