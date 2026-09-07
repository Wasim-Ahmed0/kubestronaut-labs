"""Terminal presentation helpers (no dependencies, degrades to plain text)."""
from __future__ import annotations

import os
import shutil
import sys

_NO_COLOR = bool(os.environ.get("NO_COLOR")) or not sys.stdout.isatty()


def _c(code):
    return "" if _NO_COLOR else "\033[%sm" % code


RESET = _c("0")
BOLD = _c("1")
DIM = _c("2")
RED = _c("31")
GREEN = _c("32")
YELLOW = _c("33")
BLUE = _c("34")
MAGENTA = _c("35")
CYAN = _c("36")
GREY = _c("90")
BG_BLUE = _c("44")


def width(default=80):
    try:
        return min(shutil.get_terminal_size().columns, 100)
    except OSError:
        return default


def rule(char="─"):
    return GREY + char * width() + RESET


def banner(title, subtitle=""):
    w = width()
    print()
    print(BOLD + CYAN + "  " + title + RESET)
    if subtitle:
        print(GREY + "  " + subtitle + RESET)
    print(GREY + "  " + "─" * (w - 2) + RESET)


def head(text):
    print("\n" + BOLD + text + RESET)


def ok(text):
    print("  " + GREEN + "✓" + RESET + " " + text)


def bad(text):
    print("  " + RED + "✗" + RESET + " " + text)


def info(text):
    print("  " + CYAN + "›" + RESET + " " + text)


def warn(text):
    print("  " + YELLOW + "!" + RESET + " " + text)


def dim(text):
    print(GREY + "  " + text + RESET)


def kv(key, value, pad=14):
    print("  " + GREY + key.ljust(pad) + RESET + str(value))


def bar(frac, size=28, filled="█", empty="░"):
    frac = max(0.0, min(1.0, frac))
    n = int(round(frac * size))
    colour = GREEN if frac >= 0.999 else (YELLOW if frac >= 0.4 else RED)
    return colour + filled * n + RESET + GREY + empty * (size - n) + RESET


def score_colour(score):
    if score is None:
        return GREY
    return GREEN if score >= 80 else (YELLOW if score >= 66 else RED)


def wrap(text, indent="  "):
    import textwrap
    w = width() - len(indent) - 2
    out = []
    for para in text.split("\n"):
        if not para.strip():
            out.append("")
            continue
        out.extend(textwrap.wrap(para, w) or [""])
    return "\n".join(indent + line for line in out)


def hms(seconds):
    seconds = int(seconds or 0)
    h, rem = divmod(seconds, 3600)
    m, s = divmod(rem, 60)
    if h:
        return "%dh %02dm %02ds" % (h, m, s)
    if m:
        return "%dm %02ds" % (m, s)
    return "%ds" % s
