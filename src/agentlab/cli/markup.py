"""Text that comes from outside is shown as it is, never read as console markup.

The CLI styles its output with rich markup (``[red]…[/red]``). A finding's title, a tool's description, a warning that names a
file, an error that quotes a reply: any of these can come from the target under test or from a skill someone else wrote, and a
``[/]`` or a ``[bold]`` in it must neither crash a command nor restyle what follows. ``esc``, ``styled`` and ``short`` are the
ways such text goes into a markup string; ``tests/unit/test_cli_markup.py`` feeds hostile text through every renderer.
"""

from __future__ import annotations

from rich.markup import escape


def esc(value: object) -> str:
    """``value`` as text, with anything that looks like markup made literal."""
    return escape(str(value))


def styled(value: object, style: str | None) -> str:
    """``value`` in ``style``; with no style, just the (escaped) text. An empty style would leave a closing tag with nothing
    to close, which rich refuses."""
    text = esc(value)
    return f"[{style}]{text}[/{style}]" if style else text


def short(text: object, n: int = 100) -> str:
    """One line of at most ``n`` characters, safe to put in a markup string."""
    s = str(text).replace("\n", " ")
    return esc(s if len(s) <= n else s[: n - 1] + "…")
