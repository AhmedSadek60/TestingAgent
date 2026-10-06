"""Skip helper for tests that need a reachable Ollama server (marker: ollama).

Set ``AGENTLAB_TEST_OLLAMA_URL`` (for example ``http://127.0.0.1:11434``) and, if the default does not fit,
``AGENTLAB_TEST_OLLAMA_MODEL`` (default ``qwen2.5:0.5b``, a small model that is enough to exercise the plumbing). Without
the variable these tests are skipped: nothing is downloaded and no server is assumed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import httpx
import pytest

DEFAULT_MODEL = "qwen2.5:0.5b"


def ollama_endpoint() -> tuple[str, str] | None:
    """``(base_url, model)`` when the configured server is reachable and has the model, else ``None``."""
    url = os.environ.get("AGENTLAB_TEST_OLLAMA_URL", "").rstrip("/")
    if not url:
        return None
    model = os.environ.get("AGENTLAB_TEST_OLLAMA_MODEL", DEFAULT_MODEL)
    try:
        tags = httpx.get(f"{url}/api/tags", timeout=3).json()
    except (httpx.HTTPError, ValueError):
        return None
    names = {m.get("name") for m in tags.get("models", [])} | {m.get("model") for m in tags.get("models", [])}
    return (url, model) if model in names else None


_skip_without_ollama = pytest.mark.skipif(
    ollama_endpoint() is None, reason="no Ollama server with the test model (set AGENTLAB_TEST_OLLAMA_URL)"
)


def needs_ollama(test: Callable[..., Any]) -> Callable[..., Any]:
    """Marks a test as one that talks to a real Ollama server and skips it when there is none."""
    return pytest.mark.ollama(_skip_without_ollama(test))
