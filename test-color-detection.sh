#!/usr/bin/env bash
# Verify PS1 accent-colour detection in 01-sysinfo's motd_accent_sgr resolver.
# Hermetic: mocks getent/hostname on PATH and uses temp HOME dirs, so it needs
# no real /home users and never touches the system.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PASS=0
FAIL=0

assert_eq() {
    local label="$1" expected="$2" actual="$3"
    if [[ "$expected" == "$actual" ]]; then
        echo "[OK] $label"
        PASS=$((PASS + 1))
    else
        echo "[X] $label (expected '$expected', got '$actual')"
        FAIL=$((FAIL + 1))
    fi
}

# Load only the theme resolver, between its stable marker comments (no line ranges).
eval "$(awk '/# >>> motd-theme/{f=1; next} /# <<< motd-theme/{f=0} f' "$ROOT/01-sysinfo")"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
BIN="$TMP/bin"
mkdir -p "$BIN"
HOST=testhost
export PASSWD_DB="$TMP/passwd"
: > "$PASSWD_DB"

cat > "$BIN/hostname" <<EOF
#!/usr/bin/env bash
echo "$HOST"
EOF

cat > "$BIN/getent" <<'EOF'
#!/usr/bin/env bash
[[ "$1" == passwd ]] || exit 2
if [[ -n "${2:-}" ]]; then
    grep "^$2:" "$PASSWD_DB" 2>/dev/null || exit 2
else
    cat "$PASSWD_DB" 2>/dev/null || true
fi
EOF
chmod +x "$BIN/hostname" "$BIN/getent"
export PATH="$BIN:$PATH"

# Write a PS1 line with the given SGR and host marker into a shell init file.
write_ps1() {
    local file="$1" sgr="$2" host="${3:-$HOST}"
    printf 'PS1="\\[\\033[%sm\\]\\u@%s\\[\\033[0m\\]:\\w\\$ "\n' "$sgr" "$host" > "$file"
}

# Write a PS1 line with \u@\h (portable default) instead of a literal hostname.
write_ps1_h() {
    local file="$1" sgr="$2"
    printf 'PS1="\\[\\033[%sm\\]\\u@\\h\\[\\033[0m\\]:\\w\\$ "\n' "$sgr" > "$file"
}
passwd_line() {
    # Args: name uid home shell. Emits name:x:uid:gid::home:shell (passwd order).
    printf '%s:x:%s:%s::%s:%s\n' "$1" "$2" "$2" "$3" "$4" >> "$PASSWD_DB"
}

echo "=== accent-colour resolver tests ==="

# 1. MOTD_ACCENT_COLOR always wins (short-circuit before any scan).
out=$(MOTD_ACCENT_COLOR="1;31" motd_accent_sgr)
assert_eq "explicit MOTD_ACCENT_COLOR wins" "1;31" "$out"

# 2. MOTD_BASHRC override -> extract its colour.
write_ps1 "$TMP/override.bashrc" "01;33"
out=$(unset MOTD_ACCENT_COLOR MOTD_COLOR_USER; MOTD_BASHRC="$TMP/override.bashrc" motd_accent_sgr)
assert_eq "MOTD_BASHRC colour" "01;33" "$out"

# 3. MOTD_COLOR_USER -> .bash_aliases overrides .bashrc (last file wins).
mkdir -p "$TMP/alice"
write_ps1 "$TMP/alice/.bashrc" "38;5;82"
write_ps1 "$TMP/alice/.bash_aliases" "38;5;208"
passwd_line alice 1001 "$TMP/alice" /bin/bash
out=$(unset MOTD_ACCENT_COLOR MOTD_BASHRC; MOTD_COLOR_USER=alice motd_accent_sgr)
assert_eq "bash_aliases overrides bashrc" "38;5;208" "$out"

# 4. MOTD_COLOR_USER -> only .bashrc present.
mkdir -p "$TMP/bob"
write_ps1 "$TMP/bob/.bashrc" "01;32"
passwd_line bob 1002 "$TMP/bob" /bin/bash
out=$(unset MOTD_ACCENT_COLOR MOTD_BASHRC; MOTD_COLOR_USER=bob motd_accent_sgr)
assert_eq "bashrc-only colour" "01;32" "$out"

# 5. Login-env user (LOGNAME, non-root) resolves like Ubuntu SSH.
mkdir -p "$TMP/kevyn"
write_ps1 "$TMP/kevyn/.bash_aliases" "38;5;141"
passwd_line kevyn 1000 "$TMP/kevyn" /bin/bash
out=$(unset MOTD_ACCENT_COLOR MOTD_BASHRC MOTD_COLOR_USER PAM_USER SSH_ORIGINAL_USER USER; LOGNAME=kevyn motd_accent_sgr)
assert_eq "login-env user colour" "38;5;141" "$out"

# 6. PS1 without \u@ marker is ignored -> default.
mkdir -p "$TMP/nomark"
printf 'PS1="\\[\\033[38;5;9m\\]\\w\\$ "\n' > "$TMP/nomark/.bashrc"
passwd_line nomark 1003 "$TMP/nomark" /bin/bash
out=$(unset MOTD_ACCENT_COLOR MOTD_BASHRC; MOTD_COLOR_USER=nomark motd_accent_sgr)
assert_eq "PS1 without marker ignored" "38;5;43" "$out"

# 7. No colour anywhere -> teal default.
mkdir -p "$TMP/empty"
passwd_line empty 1004 "$TMP/empty" /bin/bash
out=$(unset MOTD_ACCENT_COLOR MOTD_BASHRC; MOTD_COLOR_USER=empty motd_accent_sgr)
assert_eq "default accent" "38;5;43" "$out"

# 8. File order: .profile then .bashrc -> .bashrc wins (later in order).
mkdir -p "$TMP/order"
write_ps1 "$TMP/order/.profile" "38;5;1"
write_ps1 "$TMP/order/.bashrc" "38;5;2"
passwd_line order 1005 "$TMP/order" /bin/bash
out=$(unset MOTD_ACCENT_COLOR MOTD_BASHRC; MOTD_COLOR_USER=order motd_accent_sgr)
assert_eq "later init file wins" "38;5;2" "$out"

# 9. Guess path: scan passwd for a usable user whose PS1 carries \u@<host>.
#    Requires home under /home, so only runs where a writable /home exists.
if mkdir -p "/home/.motd-test-guess" 2>/dev/null; then
    write_ps1 "/home/.motd-test-guess/.bashrc" "38;5;200"
    passwd_line guessme 1100 "/home/.motd-test-guess" /bin/bash
    out=$(unset MOTD_ACCENT_COLOR MOTD_BASHRC MOTD_COLOR_USER PAM_USER SSH_ORIGINAL_USER LOGNAME; USER=root motd_accent_sgr)
    assert_eq "guess by \\u@host" "38;5;200" "$out"

    # 10. Same guess path but with the portable \\u@\\h prompt (Debian/Ubuntu default).
    : > "$PASSWD_DB"
    mkdir -p "/home/.motd-test-guessh"
    write_ps1_h "/home/.motd-test-guessh/.bashrc" "38;5;141"
    passwd_line guessh 1101 "/home/.motd-test-guessh" /bin/bash
    out=$(unset MOTD_ACCENT_COLOR MOTD_BASHRC MOTD_COLOR_USER PAM_USER SSH_ORIGINAL_USER LOGNAME; USER=root motd_accent_sgr)
    assert_eq "guess by \\u@\\h (root SSH)" "38;5;141" "$out"

    rm -rf "/home/.motd-test-guess" "/home/.motd-test-guessh"
else
    echo "[SKIP] guess-by-host (no writable /home; covered by live-host check)"
fi

echo "---"
echo "pass: $PASS  fail: $FAIL"
[[ "$FAIL" -eq 0 ]]
