"""Talking to an agent through its web chat page.

AgentLab does not know a page's markup, so it finds the message box and the send button the way a person would (a visible
text box, a button that says "Send"), unless the owner named the selectors in the target file. A reply is whatever *new*
text appears on the page after sending, once it has stopped changing: pages stream, so the first change is not the answer.
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Any

from agentlab.core.errors import BrowserError

INPUT_SELECTORS = (
    "textarea:visible",
    "input[type=text]:visible",
    "input:not([type]):visible",
    "[contenteditable=true]:visible",
    "[role=textbox]:visible",
)
SEND_SELECTORS = (
    "button[type=submit]:visible",
    "button:has-text('Send'):visible",
    "button:has-text('Submit'):visible",
    "button:has-text('Ask'):visible",
    "[aria-label*='send' i]:visible",
    "input[type=submit]:visible",
)
QUIET_SECONDS = 0.8  # text that has not changed for this long is the finished answer
POLL_SECONDS = 0.2
MAX_TEXT_CHARS = 50_000


LABEL_CHARS = " :-\u2013\u2014>\u00b7\u2022\t"
MAX_LABEL = 12  # "You:", "User:", "Me >": what a chat page writes in front of a message it shows back


def is_echo(line: str, sent: str) -> bool:
    """Whether ``line`` is just the message that was sent, shown back (possibly after a short label such as "You:").

    A reply that merely *quotes* the question is longer than that label and is kept."""
    if not sent:
        return False
    normal = " ".join(line.split())
    if sent not in normal:
        return False
    return len(normal.replace(sent, "", 1).strip(LABEL_CHARS)) <= MAX_LABEL


def new_text(before: str, after: str, sent: str = "") -> str:
    """The lines on the page now that were not there before, in order, without the message that was just sent.

    A line that was already on the page (the heading, an earlier answer) is not new even if it appears again; a repeated
    line is new only as many times as it grew."""
    old = Counter(line.strip() for line in before.splitlines() if line.strip())
    sent_line = " ".join(sent.split())
    fresh: list[str] = []
    for raw in after.splitlines():
        line = raw.strip()
        if not line:
            continue
        if old[line] > 0:
            old[line] -= 1
            continue
        if is_echo(line, sent_line):
            continue
        fresh.append(line)
    return "\n".join(fresh)


async def visible_text(page: Any) -> str:
    try:
        return (await page.inner_text("body"))[:MAX_TEXT_CHARS]
    except Exception:
        return ""


async def first_visible(page: Any, candidates: tuple[str, ...]) -> Any | None:
    for selector in candidates:
        locator = page.locator(selector).first
        try:
            if await locator.count() and await locator.is_visible():
                return locator
        except Exception:  # noqa: S112 - an unusable candidate selector is skipped
            continue
    return None


async def find_input(page: Any, selector: str | None) -> Any:
    locator = page.locator(selector).first if selector else await first_visible(page, INPUT_SELECTORS)
    if locator is None:
        raise BrowserError("no message box found on the page (set web.input_selector in the target file)")
    return locator


async def find_send(page: Any, selector: str | None) -> Any | None:
    return page.locator(selector).first if selector else await first_visible(page, SEND_SELECTORS)


async def send_chat(
    page: Any,
    text: str,
    *,
    input_selector: str | None = None,
    send_selector: str | None = None,
    message_selector: str | None = None,
    wait_seconds: float = 20.0,
) -> tuple[str, bool]:
    """Type ``text`` into the page's message box, send it and return ``(reply, replied)``.

    ``replied`` is False when nothing new appeared in time (the reply is then empty)."""
    box = await find_input(page, input_selector)
    before = await visible_text(page)
    before_messages: list[str] = await page.locator(message_selector).all_inner_texts() if message_selector else []
    await box.fill(text)
    send = await find_send(page, send_selector)
    if send is not None:
        await send.click()
    else:
        await box.press("Enter")
    deadline = time.monotonic() + wait_seconds
    last, stable_since = "", time.monotonic()
    while time.monotonic() < deadline:
        await asyncio.sleep(POLL_SECONDS)
        now_text = await visible_text(page)
        if message_selector:
            messages = await page.locator(message_selector).all_inner_texts()
            reply = "\n".join(
                m.strip() for m in messages[len(before_messages) :] if m.strip() and m.strip() != text.strip()
            )
        else:
            reply = new_text(before, now_text, text)
        if reply != last:
            last, stable_since = reply, time.monotonic()
        elif reply and time.monotonic() - stable_since >= QUIET_SECONDS:
            return reply, True
    return last, bool(last)
