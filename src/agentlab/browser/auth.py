"""Signing the browser in as a test user (spec sections 11 and 18).

A web target names a credential profile (``web.auth_credential``). What the profile contains decides how the browser
uses it:

==============================  ==========================================================================
``browser_state``               a Playwright storage state (cookies and local storage) loads into the context
``cookies``                     the cookies are added for the target's URL only
``bearer``, ``api_key`` ...     headers added to the requests that go *to the target's origin*, never elsewhere
``basic`` with ``login_url``    the login form is filled in and submitted (see :meth:`BrowserSession.form_login`)
``basic`` without ``login_url`` HTTP basic authentication for the target's origin
==============================  ==========================================================================

A profile that is not configured, expired or not scoped for the target's host raises ``CredentialError``; the executor
turns that into a BLOCKED test (never a failed one). Secret values are only ever handed to Playwright: they are not
written to the trace, the action list or any artifact.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agentlab.browser.pool import BrowserPool, GuardedContext, origin_of
from agentlab.core.errors import CredentialError
from agentlab.core.models import WebConfig
from agentlab.security.credentials import CredentialManager

HEADER_KINDS = {"bearer", "oauth_token", "api_key", "headers"}


@dataclass
class OpenedContext:
    guarded: GuardedContext
    #: (username, password) to type into the login form, when the profile is a login rather than a ready session
    login: tuple[str, str] | None = None


async def open_context(
    pool: BrowserPool,
    web: WebConfig | None,
    credentials: CredentialManager | None,
    *,
    record_video_dir: str | None = None,
) -> OpenedContext:
    """A clean browser context for ``web``, signed in as the configured test user when the target names one."""
    name = web.auth_credential if web else None
    if not name or web is None:
        return OpenedContext(await pool.context(record_video_dir=record_video_dir))
    if credentials is None:
        raise CredentialError(f"credential profile '{name}' is needed but no credential store is available")
    profile = credentials.get_profile(name)
    origin = origin_of(web.url)
    kind = profile.kind
    if kind == "browser_state":
        with credentials.browser_state_file(name, web.url) as path:
            return OpenedContext(await pool.context(record_video_dir=record_video_dir, storage_state=path))
    if kind in HEADER_KINDS:
        headers = credentials.auth_headers(name, web.url)
        return OpenedContext(await pool.context(record_video_dir=record_video_dir, inject_headers={origin: headers}))
    if kind == "cookies":
        fields = credentials.fields(name, web.url)
        guarded = await pool.context(record_video_dir=record_video_dir)
        await guarded.context.add_cookies([{"name": k, "value": v, "url": web.url} for k, v in fields.items()])
        return OpenedContext(guarded)
    if kind == "basic":
        fields = credentials.fields(name, web.url)
        user, password = fields["username"], fields["password"]
        if web.login_url:
            return OpenedContext(await pool.context(record_video_dir=record_video_dir), login=(user, password))
        options: dict[str, Any] = {"http_credentials": {"username": user, "password": password, "origin": origin}}
        return OpenedContext(await pool.context(record_video_dir=record_video_dir, **options))
    raise CredentialError(f"credential kind '{kind}' of profile '{name}' cannot sign a browser in")
