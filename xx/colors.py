from __future__ import annotations

import os
import sys
from dataclasses import dataclass


ANSI_COLOR_CODES = {
    "black": "30",
    "red": "31",
    "green": "32",
    "yellow": "33",
    "blue": "34",
    "magenta": "35",
    "cyan": "36",
    "white": "37",
    "bright_black": "90",
    "bright_red": "91",
    "bright_green": "92",
    "bright_yellow": "93",
    "bright_blue": "94",
    "bright_magenta": "95",
    "bright_cyan": "96",
    "bright_white": "97",
}


@dataclass(slots=True)
class ColorConfig:
    enabled: bool = True
    preview: str = "green"
    output: str = "yellow"


def should_use_color(enabled: bool) -> bool:
    if not enabled:
        return False
    if os.environ.get("NO_COLOR") is not None:
        return False
    return sys.stdout.isatty()


def colorize(text: str, color_name: str, *, enabled: bool) -> str:
    if not should_use_color(enabled):
        return text
    code = ANSI_COLOR_CODES.get(color_name.strip().lower())
    if not code:
        return text
    return f"\033[{code}m{text}\033[0m"
