"""Talking to an agent through its web chat page.

AgentLab does not know a page's markup, so it finds the message box and the send button the way a person would (a visible
text box, a button that says "Send"), unless the owner named the selectors in the target file. A reply is whatever *new*
text appears on the page after sending, once it has stopped changing: pages stream, so the first change is not the answer.
"""

from __future__ import annotations

import asyncio
import re
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


# What a page shows while it is still working on an answer. That text is not the answer, however long it stays on screen.
PENDING_WORDS = re.compile(
    r"^\W*(processing|thinking|generating|loading|typing|searching|analy[sz]ing|working|please wait|one moment)\W*$", re.I
)
PENDING_MAX_CHARS = 60  # a line this short that ends in an ellipsis ("Choosing the right AI for you\u2026") is a status
STOP_BUTTON = re.compile(r"^\s*stop( generating| response)?\s*$", re.I)  # shown while an answer is being written
MAX_CONTROL = 40  # a button or link label is short; a reply that long is never mistaken for one


def is_pending(reply: str) -> bool:
    """Whether ``reply`` is only a status line ("Processing", "Thinking\u2026", "Choosing the right AI for you\u2026").

    A reply that has real text in it, or that is long, is not pending."""
    lines = [line.strip() for line in reply.splitlines() if line.strip()]
    if not lines:
        return False
    return all(
        PENDING_WORDS.match(line) or (len(line) <= PENDING_MAX_CHARS and line.endswith(("\u2026", "...")))
        for line in lines
    )


SEPARATORS = " \t|\u00b7\u2022/\\-\u2013\u2014,;:"


def strip_controls(reply: str, controls: set[str]) -> str:
    """The reply without the lines that consist only of the labels of buttons and links on the page ("NEW CHAT",
    "COPY | LISTEN | REGENERATE"): page furniture that appeared with the answer, not part of it.

    A line with any other word in it is kept whole, so an answer that happens to contain a label is not touched."""
    labels = sorted((c for c in controls if c), key=len, reverse=True)
    kept: list[str] = []
    for line in reply.splitlines():
        rest = " ".join(line.split()).casefold()
        for label in labels:
            rest = rest.replace(label, " ")
        if rest.strip(SEPARATORS):
            kept.append(line)
    return "\n".join(kept).strip()


async def page_controls(page: Any) -> set[str]:
    """The labels of the visible buttons, links and menu items of the page, lower-cased."""
    try:
        labels = await page.eval_on_selector_all(
            "button, a, [role=button], [role=menuitem], summary",
            "els => els.filter(e => e.offsetParent !== null).map(e => (e.innerText || e.getAttribute('aria-label') || '').trim())",
        )
    except Exception:
        return set()
    return {" ".join(t.split()).casefold() for t in labels if t and len(t) <= MAX_CONTROL}


async def is_busy(page: Any, busy_selector: str | None) -> bool:
    """Whether the page says it is still writing: a visible *Stop* button, or the element the owner named."""
    candidates = [page.locator(busy_selector).first] if busy_selector else []
    candidates.append(page.get_by_role("button", name=STOP_BUTTON).first)
    for locator in candidates:
        try:
            if await locator.count() and await locator.is_visible():
                return True
        except Exception:  # noqa: S112 - a selector that cannot be evaluated says nothing about the page
            continue
    return False


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
    busy_selector: str | None = None,
    wait_seconds: float = 20.0,
) -> tuple[str, bool]:
    """Type ``text`` into the page's message box, send it and return ``(reply, replied)``.

    The reply is final when it has stopped changing **and** the page is not still working: a status line such as
    "Processing" or "Thinking\u2026", a visible *Stop* button, or the element named by ``busy_selector`` all mean the
    answer is still being written, however long they stay on screen. Without ``message_selector`` the labels of the
    page's own buttons and links are removed from what is read.

    ``replied`` is False when no answer appeared in time (the reply is then empty): a page that only ever showed a status
    line did not answer."""
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
            reply = strip_controls(new_text(before, now_text, text), await page_controls(page))
        if reply != last:
            last, stable_since = reply, time.monotonic()
        elif (
            reply
            and time.monotonic() - stable_since >= QUIET_SECONDS
            and not is_pending(reply)
            and not await is_busy(page, busy_selector)
        ):
            return reply, True
    # The time is up. A status line is not an answer; what was written so far, while the page was still busy, is returned.
    if is_pending(last):
        return "", False
    return last, bool(last)
