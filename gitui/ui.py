"""Shared UI helpers: themes, formatting, QR rendering."""

from __future__ import annotations

from datetime import datetime, timezone

from rich.text import Text
from textual.theme import Theme

THEMES = [
    Theme(
        name="gitui",
        primary="#8B5CF6",
        secondary="#22D3EE",
        accent="#F472B6",
        foreground="#E6E8EF",
        background="#0D0E14",
        surface="#151722",
        panel="#1D2030",
        success="#34D399",
        warning="#FBBF24",
        error="#F87171",
        dark=True,
    ),
    Theme(
        name="gitui-ocean",
        primary="#38BDF8",
        secondary="#2DD4BF",
        accent="#A78BFA",
        foreground="#E2E8F0",
        background="#0B1220",
        surface="#111A2E",
        panel="#172338",
        success="#4ADE80",
        warning="#FACC15",
        error="#FB7185",
        dark=True,
    ),
    Theme(
        name="gitui-ember",
        primary="#F97316",
        secondary="#FACC15",
        accent="#EF4444",
        foreground="#F5F0EB",
        background="#120D0B",
        surface="#1C1512",
        panel="#271D18",
        success="#84CC16",
        warning="#FBBF24",
        error="#F43F5E",
        dark=True,
    ),
    Theme(
        name="gitui-matrix",
        primary="#22C55E",
        secondary="#86EFAC",
        accent="#A3E635",
        foreground="#D1FAE5",
        background="#050A06",
        surface="#0B140D",
        panel="#112015",
        success="#4ADE80",
        warning="#EAB308",
        error="#EF4444",
        dark=True,
    ),
    Theme(
        name="gitui-paper",
        primary="#6D28D9",
        secondary="#0E7490",
        accent="#DB2777",
        foreground="#1F2330",
        background="#F7F7FA",
        surface="#FFFFFF",
        panel="#ECECF3",
        success="#15803D",
        warning="#B45309",
        error="#B91C1C",
        dark=False,
    ),
]

LANG_COLORS = {
    "Python": "#3572A5", "JavaScript": "#F1E05A", "TypeScript": "#3178C6", "Go": "#00ADD8",
    "Rust": "#DEA584", "C": "#8F8F8F", "C++": "#F34B7D", "C#": "#178600", "Java": "#B07219",
    "Kotlin": "#A97BFF", "Swift": "#F05138", "Ruby": "#CC342D", "PHP": "#4F5D95",
    "Shell": "#89E051", "HTML": "#E34C26", "CSS": "#663399", "Lua": "#000080",
    "Dart": "#00B4AB", "Jupyter Notebook": "#DA5B0B", "Vue": "#41B883", "Svelte": "#FF3E00",
    "Dockerfile": "#384D54", "Makefile": "#427819", "Nix": "#7E7EFF", "Zig": "#EC915C",
}


def ago(iso: str | None) -> str:
    if not iso:
        return ""
    try:
        dt = datetime.fromisoformat(iso.replace("Z", "+00:00"))
    except ValueError:
        return iso
    secs = int((datetime.now(timezone.utc) - dt).total_seconds())
    for unit, n in (("y", 31536000), ("mo", 2592000), ("d", 86400), ("h", 3600), ("m", 60)):
        if secs >= n:
            return f"{secs // n}{unit} ago"
    return "just now"


def compact(n: int | None) -> str:
    n = n or 0
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def lang_text(lang: str | None) -> Text:
    if not lang:
        return Text("—", style="dim")
    return Text.assemble(("● ", LANG_COLORS.get(lang, "#9CA3AF")), lang)


def qr_text(data: str) -> Text | None:
    """Render a QR code with half-block characters (scannable from a phone)."""
    try:
        import qrcode  # type: ignore
    except ImportError:
        return None
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_L)
    qr.add_data(data)
    qr.make(fit=True)
    m = qr.get_matrix()
    if len(m) % 2:
        m.append([False] * len(m[0]))
    style = "#000000 on #FFFFFF"
    out = Text()
    for y in range(0, len(m), 2):
        row = ""
        for top, bot in zip(m[y], m[y + 1]):
            row += "█" if top and bot else "▀" if top else "▄" if bot else " "
        out.append(row, style=style)
        if y + 2 < len(m):
            out.append("\n")
    return out


def diff_text(diff: str) -> Text:
    out = Text()
    for line in diff.splitlines():
        if line.startswith("+++") or line.startswith("---"):
            style = "bold"
        elif line.startswith("+"):
            style = "#34D399"
        elif line.startswith("-"):
            style = "#F87171"
        elif line.startswith("@@"):
            style = "#22D3EE"
        elif line.startswith(("diff ", "index ", "commit ", "new file")):
            style = "bold #A78BFA"
        else:
            style = ""
        out.append(line + "\n", style=style)
    return out
