"""Cookie and consent dialogs that cover the page under test.

A dialog like "Before you continue" or "Consent to cookies" sits on top of the page and swallows every click, so the test
ends in a click timeout that says nothing about the agent. AgentLab closes such a dialog the way a person would, in the
mode the target asks for (``web.consent``):

* ``reject`` (the default): press the button that refuses optional cookies ("Reject all", "Necessary only", ...). If the
  dialog offers no such button, nothing is pressed: AgentLab never accepts tracking on its own.
* ``accept``: press the button that accepts ("Accept all", "I agree", ...). The browser context is thrown away after the
  test, so nothing it accepts outlives the attempt.
* ``off``: do nothing.

``web.dismiss_selectors`` lists the buttons of a dialog that no pattern knows; they are pressed in any mode but ``off``.
"""

from __future__ import annotations

import re
from typing import Any

REJECT = re.compile(
    r"^\s*(reject( all)?( cookies)?|decline( all)?( cookies)?|deny( all)?|refuse( all)?|disagree|"
    r"(only )?(necessary|essential|required)( cookies)?( only)?|continue without (accepting|agreeing)|no,? thanks)\s*$",
    re.I,
)
ACCEPT = re.compile(
    r"^\s*(accept( all)?( cookies)?|allow( all)?( cookies)?|i agree|agree( and (continue|close|proceed))?|"
    r"got it|ok(ay)?|consent|yes,? i agree)\s*$",
    re.I,
)
#: where consent managers put their dialog: a real dialog, or the ids and classes the common managers use
CONTAINERS = (
    "[role=dialog]",
    "[role=alertdialog]",
    "[aria-modal=true]",
    "#onetrust-banner-sdk",
    "#CybotCookiebotDialog",
    ".fc-consent-root",
    ".qc-cmp2-container",
    "[id*=cookie i]",
    "[class*=cookie i]",
    "[id*=consent i]",
    "[class*=consent i]",
    "[id*=gdpr i]",
    "[class*=gdpr i]",
)
CONSENT_WORDS = re.compile(r"cookie|consent|gdpr|privacy|data processing|your data|partners", re.I)
BUTTONS = "button, [role=button], a[href], input[type=button], input[type=submit]"
SETTLE_MS = 3000


async def _visible(locator: Any) -> bool:
    try:
        return bool(await locator.count()) and await locator.first.is_visible()
    except Exception:
        return False


async def _press_matching(container: Any, pattern: re.Pattern[str]) -> str | None:
    buttons = container.locator(BUTTONS)
    try:
        total = min(await buttons.count(), 40)
    except Exception:
        return None
    for i in range(total):
        button = buttons.nth(i)
        try:
            if not await button.is_visible():
                continue
            label = ((await button.inner_text()) or (await button.get_attribute("value")) or "").strip()
            if not label or not pattern.match(" ".join(label.split())):
                continue
            await button.click(timeout=2000)
            return label
        except Exception:  # noqa: S112 - a button that cannot be pressed is skipped, the next may work
            continue
    return None


async def dismiss_consent(page: Any, mode: str = "reject", selectors: tuple[str, ...] | list[str] = ()) -> str | None:
    """Close a consent dialog if one is showing. Returns the label of the button that was pressed, or None."""
    if mode == "off":
        return None
    for selector in selectors:
        locator = page.locator(selector).first
        if await _visible(locator):
            try:
                await locator.click(timeout=2000)
                await page.wait_for_timeout(300)
                return selector
            except Exception:  # noqa: S112 - the owner's selector did not work this time; try the patterns
                continue
    pattern = ACCEPT if mode == "accept" else REJECT
    for css in CONTAINERS:
        containers = page.locator(css)
        try:
            n = min(await containers.count(), 5)
        except Exception:
            continue
        for i in range(n):
            container = containers.nth(i)
            try:
                if not await container.is_visible():
                    continue
                if not CONSENT_WORDS.search((await container.inner_text())[:1500]):
                    continue
            except Exception:  # noqa: S112 - a container that vanished while it was read is skipped
                continue
            pressed = await _press_matching(container, pattern)
            if pressed:
                try:
                    await container.wait_for(state="hidden", timeout=SETTLE_MS)
                except Exception:  # noqa: S110 - the dialog may animate away or be replaced; the next check decides
                    pass
                return pressed
    return None


async def blocking_consent(page: Any) -> str | None:
    """A short description of a consent dialog that is still showing, or None."""
    for css in CONTAINERS:
        containers = page.locator(css)
        try:
            n = min(await containers.count(), 5)
        except Exception:
            continue
        for i in range(n):
            container = containers.nth(i)
            try:
                if not await container.is_visible():
                    continue
                text = " ".join((await container.inner_text())[:300].split())
            except Exception:  # noqa: S112 - a container that vanished while it was read is skipped
                continue
            if CONSENT_WORDS.search(text):
                return text[:90]
    return None


def consent_hint(shown: str, mode: str) -> str:
    """What to tell the person who wrote the target when a consent dialog could not be closed."""
    if mode == "reject":
        advice = (
            "it has no button that refuses cookies; set web.consent: accept to accept them (the browser is discarded "
            "after the test), or name its button in web.dismiss_selectors"
        )
    elif mode == "accept":
        advice = "no accept button was recognised; name its button in web.dismiss_selectors"
    else:
        advice = "web.consent is off; set web.consent: reject or accept, or name its button in web.dismiss_selectors"
    return f'a consent dialog covers the page ("{shown}") and AgentLab could not close it: {advice}'
