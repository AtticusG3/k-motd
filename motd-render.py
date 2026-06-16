#!/usr/bin/env python3
"""btop-style MOTD renderer with independent rounded-corner panels."""

from __future__ import annotations

import json
import re
import sys
import unicodedata
from typing import Any, NamedTuple

R = "\033[0m"
BOLD = "\033[1m"

FG = "\033[38;5;255m"
FG_DIM = "\033[38;5;240m"
FG_GREY = "\033[38;5;245m"

BORDER = "\033[38;5;43m"
BORDER_MEM = "\033[38;5;220m"
BORDER_DSK = "\033[38;5;117m"
BORDER_NET = "\033[38;5;117m"

GREEN = "\033[38;5;84m"
YELLOW = "\033[38;5;220m"
RED = "\033[38;5;203m"
CYAN = "\033[38;5;117m"

GRAPH_BG = "\033[48;5;235m"

DEFAULT_ACCENT_SGR = "38;5;43"

STACK_BREAKPOINT = 120
COLUMN_GAP = 1
ROW_INSET = 1  # spaces inside │ cells │ on left and right

# Fixed stat column widths (mem-style: " U 100% 999.9GiB")
STAT_LABEL_W = 2
STAT_PCT_W = 5
STAT_VALUE_W = 9
STAT_CLUSTER_W = STAT_LABEL_W + STAT_PCT_W + STAT_VALUE_W

# Compute/net row labels are wider than single-char mem tags
COMPUTE_LABEL_W = 4
COMPUTE_PCT_W = 5
COMPUTE_MID_W = 9  # CPU clock / GPU VRAM column (aligns temps)

MEM_ROWS = (
    ("U", "used_kb", RED, False),
    ("A", "avail_kb", YELLOW, True),
    ("C", "cached_kb", CYAN, False),
    ("F", "free_kb", GREEN, True),
)

NET_LABEL_W = 3
NET_PCT_W = 5
NET_RATE_W = 8
NET_CLUSTER_W = NET_LABEL_W + NET_PCT_W + NET_RATE_W

DISK_MOUNT_COL = 13  # " " + mount field (12)
DISK_USE_COL = 6     # " " + "99%"
DISK_FREE_COL = 9    # " " + avail (8)
DISK_RIGHT_W = DISK_MOUNT_COL + DISK_USE_COL + DISK_FREE_COL


def normalize_sgr(raw: str) -> str:
    """Accept SGR params (01;32), 256-color (38;5;43), or a full CSI sequence."""
    text = raw.strip()
    if not text:
        return DEFAULT_ACCENT_SGR
    match = re.search(r"\[([0-9;]+)m", text)
    if match:
        return match.group(1)
    if re.fullmatch(r"[0-9;]+", text):
        return text
    return DEFAULT_ACCENT_SGR


def sgr_to_ansi(sgr: str) -> str:
    return f"\033[{normalize_sgr(sgr)}m"


def accent_text(sgr: str, text: str, *, bold: bool = False) -> str:
    """Render text with prompt-matched accent colour."""
    norm = normalize_sgr(sgr)
    has_bold = bool(re.search(r"(^|;)(1|01)(;|$)", norm))
    color = sgr_to_ansi(norm)
    if bold and not has_bold:
        return f"{BOLD}{color}{text}{R}"
    return f"{color}{text}{R}"


def clamp_width(width: int) -> int:
    return max(20, min(220, int(width)))


def column_widths(term_w: int) -> tuple[int, int]:
    left = (term_w - COLUMN_GAP) // 2
    return left, term_w - COLUMN_GAP - left


def visible_len(text: str) -> int:
    width = 0
    esc = False
    i = 0
    while i < len(text):
        ch = text[i]
        if esc:
            if ch == "m":
                esc = False
            i += 1
            continue
        if ch == "\033":
            esc = True
            i += 1
            continue
        if unicodedata.combining(ch):
            i += 1
            continue
        if unicodedata.east_asian_width(ch) in ("F", "W"):
            width += 2
        else:
            width += 1
        i += 1
    return width


def truncate(text: str, limit: int) -> str:
    out: list[str] = []
    for ch in text:
        if visible_len("".join(out) + ch) > limit:
            break
        out.append(ch)
    return "".join(out)


def flow_inline_parts(parts: list[str], width: int, sep: str = "  ") -> list[str]:
    """Pack styled inline fragments into wrapped lines within width."""
    if not parts:
        return []
    sep_vis = visible_len(sep)
    lines: list[str] = []
    current = ""

    def flush() -> None:
        nonlocal current
        if current:
            lines.append(fit_width(current, width))
            current = ""

    for part in parts:
        plen = visible_len(part)
        if plen > width:
            flush()
            lines.append(fit_width(truncate(part, width), width))
            continue
        if not current:
            current = part
            continue
        if visible_len(current) + sep_vis + plen <= width:
            current = f"{current}{sep}{part}"
        else:
            flush()
            current = part
    flush()
    return lines


def fit_width(text: str, width: int) -> str:
    if visible_len(text) > width:
        return truncate(text, width)
    gap = width - visible_len(text)
    return text + (" " * gap)


def content_width(cell_width: int) -> int:
    """Usable columns inside a cell after symmetric inset padding."""
    return max(0, cell_width - 2 * ROW_INSET)


def row_content_width(panel_w: int) -> int:
    """Columns available to row content inside borders + symmetric inset.

    A panel of width W renders as: border(1) + inset(ROW_INSET) + content +
    inset(ROW_INSET) + border(1). Builders must size content to W - 2 (borders)
    - 2*ROW_INSET (inset) so pad_cell does not truncate the rightmost value.
    """
    return content_width(panel_w - 2)


def pad_cell(content: str, cell_width: int) -> str:
    """Pad content with equal inset on both sides inside a border cell."""
    avail = content_width(cell_width)
    text = content
    if visible_len(text) > avail:
        text = truncate(text, avail)
    gap = avail - visible_len(text)
    pad = " " * ROW_INSET
    return f"{pad}{text}{' ' * gap}{pad}"


def pad_border(text: str, width: int, border: str, fill: str = "─") -> str:
    """Pad or truncate to exact width using border fill characters."""
    if visible_len(text) > width:
        return truncate(text, width)
    gap = width - visible_len(text)
    return text + f"{border}{fill * gap}{R}"


def short_cpu_model(model: str) -> str:
    for pattern in (
        r"\bi[3579]-\d+\w*\b",
        r"\bRyzen \d+ \d+\w*\b",
        r"\bEPYC \d+\w*\b",
        r"\bThreadripper \d+\w*\b",
    ):
        match = re.search(pattern, model, re.I)
        if match:
            return match.group(0)
    return model.replace("(R)", "").replace("(TM)", "").strip()


def usage_color(pct: float, kind: str = "cpu") -> str:
    p = float(pct)
    if kind == "temp":
        if p >= 80:
            return RED
        if p >= 60:
            return YELLOW
        return GREEN
    if p >= 90:
        return RED
    if p >= 75:
        return YELLOW
    return GREEN


def goodness_color(pct: float) -> str:
    """High share is good (avail/free RAM, etc.)."""
    p = float(pct)
    if p >= 75:
        return GREEN
    if p >= 50:
        return YELLOW
    return RED


def pct_color(pct: float, *, inverted: bool = False) -> str:
    if inverted:
        return goodness_color(pct)
    return usage_color(pct)


def braille_meter(pct: float, width: int, color: str | None = None) -> str:
    """Horizontal meter using braille dot density (one row tall)."""
    width = max(1, width)
    fg = color or usage_color(pct)
    dot_order = (0, 1, 2, 6, 3, 4, 5, 7)
    total = width * 8
    filled = int(float(pct) * total / 100)

    out = ""
    for i in range(width):
        cell_fill = min(8, max(0, filled - i * 8))
        value = 0x2800
        for j in range(cell_fill):
            value |= 1 << dot_order[j]
        ch = chr(value)
        if cell_fill == 0:
            out += f"{GRAPH_BG}{ch}{R}"
        else:
            out += f"{fg}{ch}{R}"
    return out


def fmt_gib(kb: int) -> str:
    return f"{kb / 1024 / 1024:.1f} GiB"


def fmt_rate_kbps(kbps: int) -> str:
    kbps = max(0, int(kbps))
    if kbps >= 1024:
        return f"{kbps / 1024:.1f}M"
    return f"{kbps}K"


def fmt_vram_mb(mb: int) -> str:
    if mb >= 1024:
        return f"{mb / 1024:.1f}G"
    return f"{int(mb)}M"


def fmt_cpu_freq_mhz(mhz: int) -> str:
    mhz = max(0, int(mhz))
    if mhz >= 1000:
        return f"{mhz / 1000:.1f}G"
    return f"{mhz}M"


def cpu_suffix(cpu: dict[str, Any]) -> str:
    parts: list[str] = []
    freq = cpu.get("freq_mhz")
    if freq is not None:
        mid = fmt_cpu_freq_mhz(int(freq))
        parts.append(f"{FG}{fit_width(mid, COMPUTE_MID_W)}{R}")
    else:
        parts.append(f"{FG_DIM}{fit_width('--', COMPUTE_MID_W)}{R}")
    temp = cpu.get("temp")
    if temp is not None:
        parts.append(f"{usage_color(float(temp), 'temp')}{int(temp)}°C{R}")
    return " ".join(parts)


def gpu_suffix(gpu: dict[str, Any]) -> str:
    parts: list[str] = []
    used = gpu.get("mem_used_mb")
    total = gpu.get("mem_total_mb")
    if used is not None and total is not None:
        mem = f"{fmt_vram_mb(int(used))}/{fmt_vram_mb(int(total))}"
        parts.append(f"{FG}{fit_width(mem, COMPUTE_MID_W)}{R}")
    else:
        parts.append(f"{FG_DIM}{fit_width('--', COMPUTE_MID_W)}{R}")
    temp = gpu.get("temp")
    if temp is not None:
        parts.append(f"{usage_color(float(temp), 'temp')}{int(temp)}°C{R}")
    power = gpu.get("power_w")
    if power is not None:
        parts.append(f"{FG_GREY}{float(power):.1f}W{R}")
    return " ".join(parts)


def fixed_meter_width(panel_w: int, stat_cluster_w: int) -> int:
    cw = row_content_width(panel_w)
    return max(4, cw - stat_cluster_w)


def disk_bar_width(panel_w: int) -> int:
    return fixed_meter_width(panel_w, DISK_RIGHT_W)


def mem_stat_columns(
    label: str,
    pct: int,
    value: str,
    label_color: str,
    *,
    pct_inverted: bool = False,
) -> str:
    pct_c = pct_color(pct, inverted=pct_inverted)
    return fit_width(
        f" {label_color}{label}{R}"
        f"{pct_c}{pct:>4}%{R}"
        f" {FG}{value:>{STAT_VALUE_W - 1}}{R}",
        STAT_CLUSTER_W,
    )


def panel_column_row(
    panel_w: int,
    meter_w: int,
    stats: str,
    pct: int,
    *,
    meter_color: str | None = None,
    pct_inverted: bool = False,
) -> str:
    cw = row_content_width(panel_w)
    color = meter_color or pct_color(pct, inverted=pct_inverted)
    meter = braille_meter(pct, meter_w, color)
    return fit_width(f"{meter}{stats}", cw)


def compute_row(
    panel_w: int,
    meter_w: int,
    label_tag: str,
    pct: int,
    suffix: str,
    cluster_w: int,
    *,
    meter_color: str | None = None,
) -> str:
    pct_c = pct_color(pct)
    body = f"{label_tag}{pct_c}{pct:>4}%{R}"
    if suffix:
        body = f"{body} {suffix}"
    stats = fit_width(body, cluster_w)
    return panel_column_row(panel_w, meter_w, stats, pct, meter_color=meter_color)


def net_pct(kbps: int, cap_kbps: int) -> int:
    if cap_kbps <= 0:
        return 0
    return min(100, int(max(0, kbps) * 100 / cap_kbps))


def render_disk_header(panel_w: int) -> str:
    cw = row_content_width(panel_w)
    bar_w = disk_bar_width(panel_w)
    return fit_width(
        f"{' ' * bar_w}{FG_DIM} {'Mount':<12}{'Use':>6}{'Free':>9}{R}",
        cw,
    )


def panel_disk_row(panel_w: int, meter_w: int, mount: str, pct: int, avail: str) -> str:
    cw = row_content_width(panel_w)
    stats = (
        f" {FG}{mount:<12}{R}"
        f" {usage_color(pct)}{pct:>4}%{R}"
        f" {FG_GREY}{avail:>8}{R}"
    )
    meter = braille_meter(pct, meter_w)
    return fit_width(f"{meter}{stats}", cw)


class Box:
    """Standalone panel; every line is exactly `width` terminal columns."""

    def __init__(self, width: int, *, accent_sgr: str = DEFAULT_ACCENT_SGR):
        self.width = max(20, int(width))
        self.inner = self.width - 2
        self.lines: list[str] = []
        self.accent_sgr = normalize_sgr(accent_sgr)
        self.border = sgr_to_ansi(self.accent_sgr)

    def accent(self, text: str, *, bold: bool = False) -> str:
        return accent_text(self.accent_sgr, text, bold=bold)

    def white(self, text: str, *, bold: bool = True) -> str:
        if bold:
            return f"{R}{BOLD}{FG}{text}{R}"
        return f"{R}{FG}{text}{R}"

    def emit(self, line: str) -> None:
        self.lines.append(line)

    def row(self, body: str, border: str | None = None) -> None:
        edge = border if border is not None else self.border
        cell = pad_cell(body, self.inner)
        self.emit(f"{edge}│{R}{cell}{edge}│{R}")

    def bottom(self, border: str | None = None) -> None:
        edge = border if border is not None else self.border
        self.emit(f"{edge}╰{edge}{'─' * self.inner}{edge}╯{R}")

    def _emit_top(self, body: str, border: str | None = None) -> None:
        edge = border if border is not None else self.border
        if visible_len(body) < self.inner:
            body = pad_border(body, self.inner, edge)
        elif visible_len(body) > self.inner:
            body = truncate(body, self.inner)
        self.emit(f"{edge}╭{body}{edge}╮{R}")

    def labeled_top(self, label: str, border: str | None = None) -> None:
        """╭─┐ label ┌──╮"""
        edge = border if border is not None else self.border
        prefix = f"{edge}─┐{FG_GREY} {label} {R}{edge}┌─"
        remain = max(0, self.inner - visible_len(prefix))
        body = f"{prefix}{edge}{'─' * remain}"
        self._emit_top(body, edge)

    def header_top(
        self,
        host: str,
        model: str,
        time: str,
        border: str | None = None,
    ) -> None:
        """╭─┐ host · model ┌── time ─╮"""
        edge = border if border is not None else self.border
        model_short = short_cpu_model(model)
        label_bookends = f"{edge}─┐ {self.white(host, bold=True)}{FG_GREY} · {model_short}{R} {edge}┌─"
        clock = f" {self.white(time, bold=True)} "
        end_dash = f"{edge}─"

        if visible_len(label_bookends) + visible_len(clock) + visible_len(end_dash) > self.inner:
            budget = self.inner - visible_len(clock) - visible_len(end_dash) - visible_len(
                f"{edge}─┐ {self.white(host, bold=True)}{FG_GREY} · {R} {edge}┌─"
            )
            model_short = truncate(model_short, max(4, budget))
            label_bookends = (
                f"{edge}─┐ {self.white(host, bold=True)}{FG_GREY} · {model_short}{R} {edge}┌─"
            )

        remain = max(
            0,
            self.inner
            - visible_len(label_bookends)
            - visible_len(clock)
            - visible_len(end_dash),
        )
        body = f"{label_bookends}{edge}{'─' * remain}{clock}{end_dash}"
        self._emit_top(body, edge)


def build_labeled_panel(
    width: int,
    label: str,
    content_lines: list[str],
    *,
    border: str | None,
    accent_sgr: str,
) -> list[str]:
    box = Box(width, accent_sgr=accent_sgr)
    box.labeled_top(label, border)
    for line in content_lines:
        box.row(line, border)
    box.bottom(border)
    return box.lines


def build_header_compute_panel(
    width: int,
    host: str,
    model: str,
    time: str,
    cpu: dict[str, Any],
    gpus: list[dict[str, Any]],
    accent_sgr: str,
) -> list[str]:
    box = Box(width, accent_sgr=accent_sgr)
    box.header_top(host, model, time)
    for line in render_compute_lines(width, cpu, gpus, accent_sgr):
        box.row(line)
    box.bottom()
    return box.lines


def flatten_panels(panels: list[list[str]]) -> list[str]:
    lines: list[str] = []
    for panel in panels:
        lines.extend(panel)
    return lines


def blank_row(width: int) -> str:
    return " " * width


def compose_columns(
    left_panels: list[list[str]],
    right_panels: list[list[str]],
    term_w: int,
) -> list[str]:
    """Place independent panel stacks side by side with a single-space gap."""
    left_w, right_w = column_widths(term_w)
    left_lines = flatten_panels(left_panels)
    right_lines = flatten_panels(right_panels)
    gap = " " * COLUMN_GAP
    out: list[str] = []
    rows = max(len(left_lines), len(right_lines))
    for i in range(rows):
        left = left_lines[i] if i < len(left_lines) else blank_row(left_w)
        right = right_lines[i] if i < len(right_lines) else blank_row(right_w)
        out.append(fit_width(left, left_w) + gap + fit_width(right, right_w))
    return out


def normalize_gpus(raw: Any) -> list[dict[str, Any]]:
    """Accept legacy single-object payload or a list of GPU rows."""
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list):
        return [g for g in raw if isinstance(g, dict)]
    return []


def render_compute_lines(
    panel_w: int,
    cpu: dict[str, Any],
    gpus: list[dict[str, Any]],
    accent_sgr: str,
) -> list[str]:
    lines: list[str] = []
    pct = int(cpu.get("total_pct", 0))
    cpu_suffix_str = cpu_suffix(cpu)

    gpu_rows: list[tuple[str, int, str]] = []
    suffixes = [cpu_suffix_str]
    for i, gpu in enumerate(gpus):
        label = str(gpu.get("label") or ("GPU" if len(gpus) == 1 else f"G{i}"))
        gpu_pct = int(gpu.get("pct", 0))
        gpu_suffix_str = gpu_suffix(gpu)
        suffixes.append(gpu_suffix_str)
        gpu_rows.append((label, gpu_pct, gpu_suffix_str))

    max_suffix = max((visible_len(s) for s in suffixes), default=0)
    cluster_w = COMPUTE_LABEL_W + COMPUTE_PCT_W + max(max_suffix + 1, 1)
    meter_w = fixed_meter_width(panel_w, cluster_w)

    cpu_tag = fit_width(f" {accent_text(accent_sgr, 'CPU', bold=True)}", COMPUTE_LABEL_W)
    lines.append(compute_row(panel_w, meter_w, cpu_tag, pct, cpu_suffix_str, cluster_w))

    for label, gpu_pct, gpu_suffix_str in gpu_rows:
        gpu_tag = fit_width(f" {accent_text(accent_sgr, label, bold=True)}", COMPUTE_LABEL_W)
        lines.append(
            compute_row(
                panel_w,
                meter_w,
                gpu_tag,
                gpu_pct,
                gpu_suffix_str,
                cluster_w,
                meter_color=RED,
            )
        )
    return lines


def render_net_lines_list(
    panel_w: int,
    net: dict[str, Any],
    accent_sgr: str,
) -> list[str]:
    cap = int(net.get("cap_kbps", 12800))
    down = int(net.get("down_kbps", 0))
    up = int(net.get("up_kbps", 0))
    meter_w = fixed_meter_width(panel_w, NET_CLUSTER_W)
    lines: list[str] = []

    for label, kbps in (("DL", down), ("UL", up)):
        tag = fit_width(f" {accent_text(accent_sgr, label, bold=True)}", NET_LABEL_W)
        rate = f"{FG}{fmt_rate_kbps(kbps)}/s{R}"
        pct = net_pct(kbps, cap)
        stats = fit_width(f"{tag}{CYAN}{pct:>4}%{R} {rate}", NET_CLUSTER_W)
        lines.append(panel_column_row(panel_w, meter_w, stats, pct, meter_color=CYAN))

    parts: list[str] = []
    ipv4 = net.get("ipv4")
    vpn = net.get("vpn_ipv4")
    iface = net.get("iface")
    vpn_iface = net.get("vpn_iface")

    if ipv4:
        label = iface or "lan"
        parts.append(f"{FG_DIM}{label}:{R} {FG}{ipv4}{R}")
    if vpn:
        label = vpn_iface or "vpn"
        parts.append(f"{FG_DIM}{label}:{R} {FG}{vpn}{R}")
    if not parts:
        parts.append(f"{FG_DIM}no global addrs{R}")

    cw = row_content_width(panel_w)
    lines.append(fit_width(" ".join(parts), cw))
    return lines


def render_mem_lines(panel_w: int, mem: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    total = int(mem.get("total_kb", 0))
    cw = row_content_width(panel_w)
    meter_w = fixed_meter_width(panel_w, STAT_CLUSTER_W)

    lines.append(fit_width(f"{FG_DIM}Total:{R} {FG}{fmt_gib(total)}{R}", cw))
    for tag, key, color, inverted in MEM_ROWS:
        kb = int(mem.get(key, 0))
        pct = int(kb * 100 / total) if total else 0
        label_color = pct_color(pct, inverted=inverted) if inverted else color
        stats = mem_stat_columns(tag, pct, fmt_gib(kb), label_color, pct_inverted=inverted)
        meter_color = pct_color(pct, inverted=inverted) if inverted else color
        lines.append(
            panel_column_row(
                panel_w,
                meter_w,
                stats,
                pct,
                meter_color=meter_color,
                pct_inverted=inverted,
            )
        )
    return lines


def render_disk_lines(panel_w: int, disks: list[dict[str, Any]]) -> list[str]:
    lines: list[str] = []
    cw = row_content_width(panel_w)
    meter_w = disk_bar_width(panel_w)
    if not disks:
        return [fit_width(f"{FG_DIM}no mounts{R}", cw)]

    lines.append(render_disk_header(panel_w))

    for disk in disks:
        mount = truncate(disk.get("mount", ""), 12)
        pct = int(disk.get("pct", 0))
        avail = disk.get("avail", "?")
        lines.append(panel_disk_row(panel_w, meter_w, mount, pct, avail))
    return lines


def normalize_failed_units(raw: Any) -> list[str]:
    """Accept legacy integer count (ignored) or a list of failed unit names."""
    if isinstance(raw, list):
        return [str(name) for name in raw if name]
    return []


def render_services_lines(panel_w: int, services: dict[str, Any]) -> list[str]:
    ok = services.get("ok", [])
    fail = services.get("fail", [])
    failed_units = normalize_failed_units(services.get("failed_units"))
    if not isinstance(ok, list):
        ok = []
    if not isinstance(fail, list):
        fail = []

    parts: list[str] = []
    shown: set[str] = set()
    for name in ok:
        shown.add(str(name))
        parts.append(f"{GREEN}\u2714 {name}{R}")
    for name in fail:
        shown.add(str(name))
        parts.append(f"{RED}\u2718 {name}{R}")
    for name in failed_units:
        if str(name) in shown:
            continue
        shown.add(str(name))
        parts.append(f"{RED}\u2718 {name}{R}")

    cw = row_content_width(panel_w)
    if not parts:
        return [fit_width(f"{FG_DIM}no services monitored{R}", cw)]

    return flow_inline_parts(parts, cw)


def theme_accent_sgr(data: dict[str, Any]) -> str:
    theme = data.get("theme")
    if isinstance(theme, dict):
        raw = theme.get("accent")
        if raw:
            return normalize_sgr(str(raw))
    return DEFAULT_ACCENT_SGR


class Payload(NamedTuple):
    """Normalized view of the JSON stdin payload."""

    width: int
    accent_sgr: str
    host: str
    model: str
    time: str
    cpu: dict[str, Any]
    gpus: list[dict[str, Any]]
    net: dict[str, Any] | None
    mem: dict[str, Any]
    disks: list[Any]
    services: dict[str, Any]


def parse_payload(data: dict[str, Any]) -> Payload:
    """Coerce raw JSON into a typed payload so render() reads as layout, not plumbing."""
    cpu = data.get("cpu")
    cpu = cpu if isinstance(cpu, dict) else {}
    gpu = data.get("gpu")
    net = data.get("net")
    mem = data.get("mem")
    disks = data.get("disks")
    services = data.get("services")
    return Payload(
        width=clamp_width(data.get("width", 100)),
        accent_sgr=theme_accent_sgr(data),
        host=data.get("host", "host"),
        model=cpu.get("model", ""),
        time=data.get("time", ""),
        cpu=cpu,
        gpus=normalize_gpus(gpu),
        net=net if isinstance(net, dict) else None,
        mem=mem if isinstance(mem, dict) else {},
        disks=disks if isinstance(disks, list) else [],
        services=services if isinstance(services, dict) else {},
    )


def build_column_panels(width: int, p: Payload) -> dict[str, list[str]]:
    """Build the four column-eligible panels at a single width (no throwaway builds)."""
    net_content = (
        render_net_lines_list(width, p.net, p.accent_sgr)
        if p.net is not None
        else [fit_width(f"{FG_DIM}no data{R}", row_content_width(width))]
    )
    return {
        "hc": build_header_compute_panel(
            width, p.host, p.model, p.time, p.cpu, p.gpus, p.accent_sgr
        ),
        "net": build_labeled_panel(
            width, "net", net_content, border=BORDER_NET, accent_sgr=p.accent_sgr
        ),
        "mem": build_labeled_panel(
            width, "mem", render_mem_lines(width, p.mem),
            border=BORDER_MEM, accent_sgr=p.accent_sgr,
        ),
        "disks": build_labeled_panel(
            width, "disks", render_disk_lines(width, p.disks),
            border=BORDER_DSK, accent_sgr=p.accent_sgr,
        ),
    }


def render(data: dict[str, Any]) -> str:
    p = parse_payload(data)

    # Services is full-width in both layouts, so build it once.
    services_panel = build_labeled_panel(
        p.width, "services", render_services_lines(p.width, p.services),
        border=None, accent_sgr=p.accent_sgr,
    )

    if p.width < STACK_BREAKPOINT:
        panels = build_column_panels(p.width, p)
        lines = flatten_panels(
            [panels["hc"], panels["net"], panels["mem"], panels["disks"], services_panel]
        )
    else:
        left = build_column_panels(column_widths(p.width)[0], p)
        right = build_column_panels(column_widths(p.width)[1], p)
        lines = compose_columns(
            [left["hc"], left["mem"]], [right["net"], right["disks"]], p.width
        )
        lines.extend(services_panel)

    return "\n".join(lines) + "\n"


def main() -> None:
    data = json.load(sys.stdin)
    sys.stdout.write(render(data))


if __name__ == "__main__":
    main()
