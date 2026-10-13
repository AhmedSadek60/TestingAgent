"""GitHub as an issue tracker: create an issue, find one made before, comment, close.

Everything goes to the one repository the owner named, with the one token the owner gave, over HTTPS. The token is read
when a call is made and is never logged, stored or returned. A refusal is turned into a sentence a person can act on: the
rate limit says when to try again, a missing permission says it is missing.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from agentlab.core.config import GitHubConfig
from agentlab.feedback.integrations import ExternalIssue, IssueDraft, TrackerError, marker

API_VERSION = "2022-11-28"
SCAN = 50  # how many of the newest issues are looked through to find one that was made before


class GitHubTracker:
    name = "github"

    def __init__(
        self,
        config: GitHubConfig,
        token: Callable[[], str | None],
        *,
        transport: httpx.BaseTransport | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self.config = config
        self._token = token
        self._transport = transport
        self._now = clock

    # ------------------------------------------------------------------------------------------------------ calls
    def _client(self) -> httpx.Client:
        token = self._token()
        if not token:
            raise TrackerError("unauthorized", "no GitHub token is available (feedback.integrations.github.token_ref)")
        return httpx.Client(
            base_url=self.config.api_url.rstrip("/"),
            transport=self._transport,
            timeout=self.config.timeout_seconds,
            follow_redirects=False,
            headers={
                "Authorization": f"Bearer {token}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": API_VERSION,
                "User-Agent": "AgentLab",
            },
        )

    def _send(self, method: str, path: str, **kwargs: Any) -> Any:
        try:
            with self._client() as client:
                response = client.request(method, path, **kwargs)
        except TrackerError:
            raise
        except httpx.TimeoutException as exc:
            raise TrackerError("unavailable", "GitHub did not answer in time") from exc
        except httpx.HTTPError as exc:
            raise TrackerError("unavailable", "GitHub could not be reached") from exc
        if response.status_code >= 300:  # a redirect is not followed and is not an answer
            raise self._failure(response)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise TrackerError("unavailable", "GitHub's answer could not be read") from exc

    def _failure(self, response: httpx.Response) -> TrackerError:
        status = response.status_code
        headers = response.headers
        try:
            detail = str(response.json().get("message", ""))[:200]
        except (ValueError, AttributeError):
            detail = ""
        limited = status == 429 or (
            status == 403 and (headers.get("x-ratelimit-remaining") == "0" or "rate limit" in detail.lower())
        )
        if limited:
            wait = 60
            try:
                if headers.get("retry-after"):
                    wait = max(1, int(headers["retry-after"]))
                elif headers.get("x-ratelimit-reset"):
                    wait = max(1, int(headers["x-ratelimit-reset"]) - int(self._now()))
            except ValueError:
                pass
            minutes = max(1, round(wait / 60))
            return TrackerError(
                "rate_limited",
                f"GitHub's rate limit was reached: try again in about {minutes} minute(s). Nothing was published.",
                retry_after=wait,
            )
        if status == 401:
            return TrackerError(
                "unauthorized", "GitHub did not accept the token (feedback.integrations.github.token_ref)"
            )
        if status == 403:
            return TrackerError("forbidden", "the token is not allowed to do this in that repository")
        if status == 404:
            return TrackerError("not_found", "GitHub does not know that repository, or the token cannot see it")
        if status == 410:
            return TrackerError("disabled", "issues are switched off in that repository")
        if status == 422:
            return TrackerError("rejected", f"GitHub refused the issue{': ' + detail if detail else ''}")
        return TrackerError("unavailable", f"GitHub answered with an error ({status})")

    # ------------------------------------------------------------------------------------------------ the tracker
    def _issue(self, data: dict[str, Any]) -> ExternalIssue:
        url = str(data.get("html_url") or "")
        if not url.startswith("https://"):
            url = ""  # never a link a page could be made to run
        return ExternalIssue(
            provider=self.name,
            external_id=str(data.get("id", "")),
            number=int(data["number"]) if isinstance(data.get("number"), int) else None,
            url=url,
            state=str(data.get("state", "open")),
        )

    @property
    def _repo(self) -> str:
        return f"/repos/{self.config.repository}"

    def find(self, key: str) -> ExternalIssue | None:
        found = self._send(
            "GET",
            f"{self._repo}/issues",
            params={"state": "all", "sort": "created", "direction": "desc", "per_page": SCAN},
        )
        wanted = marker(key)
        for item in found or []:
            if isinstance(item, dict) and "pull_request" not in item and wanted in str(item.get("body") or ""):
                return self._issue(item)
        return None

    def create(self, draft: IssueDraft) -> ExternalIssue:
        payload: dict[str, Any] = {"title": draft.title, "body": draft.body}
        if draft.labels:
            payload["labels"] = list(draft.labels)
        if draft.assignees:
            payload["assignees"] = list(draft.assignees)
        data = self._send("POST", f"{self._repo}/issues", json=payload)
        if not isinstance(data, dict):
            raise TrackerError("unavailable", "GitHub's answer could not be read")
        return self._issue(data)

    def comment(self, issue: ExternalIssue, body: str) -> None:
        if issue.number is None:
            raise TrackerError("rejected", "the issue has no number to comment on")
        self._send("POST", f"{self._repo}/issues/{issue.number}/comments", json={"body": body})

    def close(self, issue: ExternalIssue) -> None:
        if issue.number is None:
            raise TrackerError("rejected", "the issue has no number to close")
        self._send("PATCH", f"{self._repo}/issues/{issue.number}", json={"state": "closed"})
