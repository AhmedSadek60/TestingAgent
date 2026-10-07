"""Reading what a command printed."""

from __future__ import annotations

import re
from typing import Any

ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


def said(result: Any) -> str:
    """What a command printed, with the line breaks the terminal width added taken out again, and with the colour codes
    that Typer switches on by itself when GITHUB_ACTIONS is set (so, in CI) taken out as well."""
    return " ".join(ANSI.sub("", result.output).split())
