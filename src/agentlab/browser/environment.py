"""What the machine can provide for a run: an isolated Docker sandbox and a browser engine (spec sections 17 and 18).

AgentLab never runs untrusted code on the host and never pretends a browser exists: when one of these is missing the
tests that need it are BLOCKED with the reason, and the plan says so before anything runs.
"""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

from agentlab.core.config import AgentLabConfig


def find_chromium(config: AgentLabConfig) -> str | None:
    """Path of a Chromium executable Playwright can launch, or ``None``."""
    explicit = config.browser.executable_path
    if explicit:
        return explicit if Path(explicit).exists() else None
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH"), str(Path.home() / ".cache" / "ms-playwright")]
    for root in filter(None, roots):
        base = Path(root)
        if not base.is_dir():
            continue
        for pattern in ("chromium", "chromium-*/chrome-linux*/chrome", "chromium_headless_shell-*/chrome-linux*/*"):
            for hit in sorted(base.glob(pattern), reverse=True):
                if hit.is_file() and os.access(hit, os.X_OK):
                    return str(hit)
    return None


def browser_status(config: AgentLabConfig) -> tuple[bool, str]:
    if not config.browser.enabled:
        return False, "browser testing is disabled in the configuration (browser.enabled: false)"
    if importlib.util.find_spec("playwright") is None:
        return False, "the 'playwright' package is not installed"
    exe = find_chromium(config)
    if exe is None:
        return False, "no Chromium found (run `playwright install chromium` or set browser.executable_path)"
    return True, f"chromium at {exe}"
