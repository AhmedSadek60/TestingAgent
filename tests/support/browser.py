"""Skip helper for tests that need Playwright and a Chromium build (marker: browser)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from agentlab.browser.environment import browser_status
from agentlab.core.config import AgentLabConfig


def browser_ok() -> bool:
    return browser_status(AgentLabConfig())[0]


_skip_without_browser = pytest.mark.skipif(not browser_ok(), reason="Playwright with Chromium is not available")


def needs_browser(test: Callable[..., Any]) -> Callable[..., Any]:
    """Marks a test as one that starts a browser (``-m "not browser"`` deselects it) and skips it without one."""
    return pytest.mark.browser(_skip_without_browser(test))
