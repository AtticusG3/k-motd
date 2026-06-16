# k-motd

A btop-style dynamic SSH login banner (MOTD) for Debian/Ubuntu-family Linux. Two drop-in files collect live stats and render rounded-corner ANSI panels on each login. Used on homelab hosts (digger, clanker, gareths-homelab, nomad) over Tailscale.

## What it shows

Each login renders independent panels:

- **Header + compute** — hostname, CPU model, clock frequency between usage % and temperature, and one row per GPU (`gpu` is an array or `null` when none are found). NVIDIA (`nvidia-smi`), AMD/ROCm (`rocm-smi`), and Intel/AMD iGPU (DRM sysfs) are supported; GPU rows show VRAM, temperature, and power where available.
- **net** — download/upload meters, LAN address, and VPN (tailscale/wireguard) address when present.
- **mem** — total, used, available, cached, and free memory.
- **disks** — per-mount usage (`/` first, then busiest mounts).
- **services** — monitored systemd units as per-unit badges (check/cross). Other failed units on the host appear as additional cross badges by name (`failed_units` is a list of unit names, not a count). Names like `motd-news` parse correctly (status glyph stripped from `systemctl list-units` output).

Meters use braille dot density for CPU, GPU, and network readings. Layout is two-column at 120+ terminal columns, stacked below that.

## Theme / accent colour

Border colour, hostname, clock, and CPU/GPU labels match the login user's shell prompt: `01-sysinfo` resolves accent SGR via `motd_accent_sgr` (reads PS1 `\u@host` colour from shell init files, with `MOTD_COLOR_USER` / `MOTD_BASHRC` overrides). Set `MOTD_ACCENT_COLOR` in config to force a colour; default fallback is teal (`38;5;43`).

## Install

From a clone of this repo (installer resolves paths from its own directory):

```bash
git clone https://github.com/AtticusG3/k-motd.git
cd k-motd
sudo ./install-motd.sh
```

Open a new SSH session to see the banner.

The installer is idempotent. It:

- Copies `01-sysinfo` and `motd-render.py` into `/etc/update-motd.d/` (CRLF normalized to LF).
- Creates `/var/cache/motd-sysinfo` and a `/etc/bash.bashrc` hook that caches terminal width on login.
- Seeds `/etc/default/motd-sysinfo` from `motd-sysinfo.example` (existing config kept, CRLF normalized).
- Runs `disable-default-motd.sh` to silence the stock distro MOTD.

## Preview without installing

```bash
./test-motd-user.sh
```

Copies the drop-ins to `~/.local/motd-sysinfo` and prints wide (120-column) and narrow (80-column) layouts. No root required.

## Configuration

Edit `/etc/default/motd-sysinfo`. Common options:

```bash
MOTD_ACCENT_COLOR="38;5;46"      # force accent (256-colour green)
MOTD_HOSTNAME="edge-01"          # display name (default: short hostname)
MOTD_SERVICES="ssh docker tailscaled"   # override auto-discovery
MOTD_MAX_DISKS=4                 # disk rows (/ always shown first)
MOTD_NET_CAP_MBPS=1000           # link capacity for network meters (default: 100)
```

When `MOTD_SERVICES` is unset, enabled units are auto-detected from: docker, nginx, ssh, tailscaled, llama-swap, llama-server, cloudflared, smartmontools, monit. See `motd-sysinfo.example` for `MOTD_COLOR_USER`, `MOTD_BASHRC`, and `MOTD_RENDERER`.

## How it works

`update-motd`/`run-parts` runs `01-sysinfo` on login:

1. Bash collects host, CPU (usage, `freq_mhz`, temp), memory, disks, GPUs, network, and systemd state.
2. Bash exports collector values and calls `motd_payload_json` — one embedded Python block that builds and `json.dumps` the full payload to stdout.
3. That JSON is piped to `motd-render.py` (Python 3.8+, stdlib only), which draws panels at the cached terminal width.

PAM/`update-motd` runs before the PTY width is known. A `/etc/bash.bashrc` hook writes the real width to `/var/cache/motd-sysinfo/last_width` on each login; the next MOTD run reads it.

### JSON contract (collector -> renderer)

Single JSON object on stdin. Top-level fields:

| Field | Type | Notes |
|-------|------|-------|
| `width` | int | Terminal columns (20–220) |
| `host` | string | Display hostname |
| `time` | string | `HH:MM:SS` |
| `theme.accent` | string | SGR params, e.g. `38;5;43` |
| `cpu` | object | `model`, `total_pct`, optional `freq_mhz`, optional `temp` |
| `mem` | object | `total_kb`, `used_kb`, `avail_kb`, `cached_kb`, `free_kb` |
| `disks` | array | `{mount, pct, avail, fstype}` |
| `gpu` | array or null | Per-GPU `{label, pct, mem_used_mb?, mem_total_mb?, temp?, power_w?}`; `null` if none |
| `net` | object | `down_kbps`, `up_kbps`, `cap_kbps`, optional LAN/VPN iface and IPv4 |
| `services` | object | `ok[]`, `fail[]` (monitored units), `failed_units[]` (other failed unit names) |

No time-series / history fields — meters reflect current readings only. The renderer accepts a legacy single-object `gpu` shape but the collector always emits an array or `null`.

## Requirements

- Debian/Ubuntu-family Linux (server-side; not Windows or macOS).
- `bash`, `python3` (stdlib only), `update-motd`/`run-parts`.

Optional:

- `nvidia-smi` — all NVIDIA GPUs.
- `rocm-smi` — AMD ROCm GPUs.
- DRM sysfs — Intel/AMD iGPU when no dedicated tool is present.
- `lm-sensors` — CPU temperature when thermal zones are unavailable.
- `systemd` — services panel.
- tailscale/wireguard interface — VPN address line.

## Tests

```bash
./test-color-detection.sh   # PS1 accent detection via motd_accent_sgr (hermetic)
./test-motd-user.sh         # render banner as current user
```

`test-color-detection.sh` sources the `motd_accent_sgr` block from `01-sysinfo` (between `# >>> motd-theme` markers), mocks `getent`/`hostname`, and uses temporary HOME dirs — no real users, no system changes.

## Uninstall

1. Remove `/etc/update-motd.d/01-sysinfo` and `/etc/update-motd.d/motd-render.py`.
2. Remove `/etc/default/motd-sysinfo`.
3. Remove `/etc/motd-sysinfo/` and the `# motd-sysinfo: cache terminal width for update-motd` block in `/etc/bash.bashrc`.
4. Remove `/var/cache/motd-sysinfo`.
5. Re-enable stock MOTD if desired: `disable-default-motd.sh` set `ENABLED=0` in `/etc/default/motd-news`, `DisableMotd=true` in `/etc/fwupd/fwupd.conf`, stripped execute bits from other `/etc/update-motd.d/*` fragments, and added `/etc/ssh/sshd_config.d/99-motd-quiet.conf` (`PrintLastLog no`). Revert those, then `systemctl reload ssh`.

## License

MIT. See [LICENSE](LICENSE).
