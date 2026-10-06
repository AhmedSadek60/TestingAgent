"""Skip helper for tests that need a reachable Redis server (marker: redis).

Set ``AGENTLAB_TEST_REDIS_URL`` (for example ``redis://127.0.0.1:6379/0``) to a server that can be written to. Every test
uses keys under a prefix of its own and removes them afterwards, so the server may be shared; without the variable these
tests are skipped and no server is assumed.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

import pytest


def redis_url() -> str | None:
    """The configured server when it answers, else ``None``."""
    url = os.environ.get("AGENTLAB_TEST_REDIS_URL", "")
    if not url:
        return None
    try:
        import redis

        redis.Redis.from_url(url, socket_connect_timeout=2).ping()
    except Exception:
        return None
    return url


_URL = redis_url()
_skip_without_redis = pytest.mark.skipif(_URL is None, reason="no Redis server (set AGENTLAB_TEST_REDIS_URL)")


def needs_redis(test: Callable[..., Any]) -> Callable[..., Any]:
    """Marks a test as one that talks to a real Redis server and skips it when there is none."""
    return pytest.mark.redis(_skip_without_redis(test))


redis_param = pytest.param("redis", marks=[pytest.mark.redis, _skip_without_redis])


def redis_test_url() -> str:
    assert _URL is not None
    return _URL
