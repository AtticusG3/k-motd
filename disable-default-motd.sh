#!/bin/bash
# Silence stock Ubuntu/Debian MOTD fragments; keep only 01-sysinfo.
set -euo pipefail

MOTD_DIR=/etc/update-motd.d
KEEP=(01-sysinfo motd-render.py)

for script in "$MOTD_DIR"/*; do
    [[ -e "$script" ]] || continue
    base=$(basename "$script")
    keep=false
    for k in "${KEEP[@]}"; do
        [[ "$base" == "$k" ]] && keep=true && break
    done
    $keep && continue
    chmod a-x "$script" 2>/dev/null || true
done

if [[ -f /etc/default/motd-news ]]; then
    if grep -q '^ENABLED=' /etc/default/motd-news; then
        sed -i 's/^ENABLED=.*/ENABLED=0/' /etc/default/motd-news
    else
        echo 'ENABLED=0' >> /etc/default/motd-news
    fi
else
    echo 'ENABLED=0' > /etc/default/motd-news
fi

rm -f /etc/motd.d/cockpit
: > /etc/motd 2>/dev/null || true

if [[ -f /etc/fwupd/fwupd.conf ]]; then
    if grep -q '^DisableMotd=' /etc/fwupd/fwupd.conf; then
        sed -i 's/^DisableMotd=.*/DisableMotd=true/' /etc/fwupd/fwupd.conf
    elif grep -q '^\[fwupd\]' /etc/fwupd/fwupd.conf; then
        sed -i '/^\[fwupd\]/a DisableMotd=true' /etc/fwupd/fwupd.conf
    else
        printf '\n[fwupd]\nDisableMotd=true\n' >> /etc/fwupd/fwupd.conf
    fi
fi
rm -f /run/motd.d/85-fwupd 2>/dev/null || true

SSHD_DROPIN=/etc/ssh/sshd_config.d/99-motd-quiet.conf
cat > "$SSHD_DROPIN" <<'EOF'
# Custom sysinfo MOTD only; suppress OpenSSH last-login line.
PrintLastLog no
EOF

if command -v update-motd >/dev/null 2>&1; then
    update-motd
elif [[ -x /usr/lib/ubuntu-advantage/motd ]]; then
    /usr/lib/ubuntu-advantage/motd
else
    run-parts --lsbsysinit "$MOTD_DIR" > /run/motd.dynamic 2>/dev/null || true
fi

if systemctl is-active ssh >/dev/null 2>&1; then
    systemctl reload ssh
elif systemctl is-active sshd >/dev/null 2>&1; then
    systemctl reload sshd
fi

echo "motd.dynamic bytes: $(wc -c < /run/motd.dynamic 2>/dev/null || echo 0)"
echo "stock fragments disabled; only 01-sysinfo should remain in dynamic MOTD"
