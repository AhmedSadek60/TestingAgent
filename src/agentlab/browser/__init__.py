"""The browser engine: a Playwright browser pool, sessions that run declarative steps, chat-page helpers and the
instrumented local test site (spec sections 17 and 18)."""

from agentlab.browser.pool import BrowserPool, GuardedContext
from agentlab.browser.session import BrowserSession, Recording, StepOutcome

__all__ = ["BrowserPool", "BrowserSession", "GuardedContext", "Recording", "StepOutcome"]
