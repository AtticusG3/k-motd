#!/bin/bash
# Install k-motd into /etc/update-motd.d and silence the stock distro MOTD.
# Run as root from a checkout:  sudo ./install-motd.sh
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if [[ $EUID -ne 0 ]]; then
    echo "install-motd.sh must run as root (try: sudo $0)" >&2
    exit 1
fi

# Copy the two drop-in files, normalizing CRLF -> LF on the way in.
install -d -m 0755 /etc/update-motd.d
for f in 01-sysinfo motd-render.py; do
    tr -d '\r' < "$SRC/$f" > "/etc/update-motd.d/$f"
    chmod 0755 "/etc/update-motd.d/$f"
done

# Cross-login width cache: root-owned dir (0755); last_width is world-writable
# so the user shell hook below can cache the real PTY width (PAM/update-motd runs
# before the terminal width is known). Values are digit-stripped and clamped in
# terminal_width() before use.
install -d -m 0755 -o root -g root /var/cache/motd-sysinfo
[[ -e /var/cache/motd-sysinfo/last_width ]] || : > /var/cache/motd-sysinfo/last_width
chmod 0666 /var/cache/motd-sysinfo/last_width

install -d -m 0755 /etc/motd-sysinfo
cat > /etc/motd-sysinfo/write-last-width.sh <<'EOF'
# Cache terminal width for MOTD (PAM runs before the PTY width is known).
[[ -n "${COLUMNS:-}" ]] && echo "$COLUMNS" > /var/cache/motd-sysinfo/last_width 2>/dev/null || true
EOF
chmod 0644 /etc/motd-sysinfo/write-last-width.sh

MOTD_WIDTH_MARKER='# motd-sysinfo: cache terminal width for update-motd'
if ! grep -qF "$MOTD_WIDTH_MARKER" /etc/bash.bashrc 2>/dev/null; then
    cat >> /etc/bash.bashrc <<EOF

$MOTD_WIDTH_MARKER
[[ -f /etc/motd-sysinfo/write-last-width.sh ]] && . /etc/motd-sysinfo/write-last-width.sh
EOF
fi

# Keep an existing config (CRLF-normalized); otherwise seed from the example.
if [[ -f /etc/default/motd-sysinfo ]]; then
    tmp=$(mktemp)
    tr -d '\r' < /etc/default/motd-sysinfo > "$tmp"
    mv "$tmp" /etc/default/motd-sysinfo
else
    tr -d '\r' < "$SRC/motd-sysinfo.example" > /etc/default/motd-sysinfo
fi

# Silence the stock distro MOTD fragments.
if [[ -f "$SRC/disable-default-motd.sh" ]]; then
    disable_tmp=$(mktemp)
    tr -d '\r' < "$SRC/disable-default-motd.sh" > "$disable_tmp"
    bash "$disable_tmp"
    rm -f "$disable_tmp"
fi

echo "host: $(hostname)"
if command -v nvidia-smi &>/dev/null; then echo "nvidia: yes"; else echo "nvidia: no"; fi
echo "--- motd preview ---"
TERM=xterm-256color COLUMNS=120 /etc/update-motd.d/01-sysinfo 2>&1 | head -7
echo "--- lines: $(TERM=xterm-256color COLUMNS=120 /etc/update-motd.d/01-sysinfo 2>&1 | wc -l) ---"
