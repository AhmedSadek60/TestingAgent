"""Publishing a submission to an issue tracker: off until switched on, only a summary, only what was previewed, never twice,
never a private report, and every failure said in words. GitHub is a fake that keeps its own issues.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import json
import re
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import ValidationError
from sqlalchemy import text

from agentlab.core.config import GitHubConfig, IntegrationsConfig
from agentlab.core.errors import InfrastructureError, NotFoundError, PolicyBlocked, UserError
from agentlab.feedback import QuotaExceeded, VersionConflict
from agentlab.feedback.integrations import ExternalIssue, IssueDraft, TrackerError, marker
from agentlab.feedback.integrations.github import GitHubTracker
from agentlab.feedback.models import CommentIn, PublishIn
from agentlab.feedback.publication import PublicationService, tracker_for
from agentlab.security.diagnostic_redactor import DiagnosticRedactor
from tests.unit.test_feedback_handoff import FULL_ISSUE, HandoffWorld, outside_fences
from tests.unit.test_feedback_service import FEATURE, Clock

TOKEN = "ghp_" + "Zq81LmN4xW7sR2pK9vB3cD6eHj4TnUa0"
OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"
OPAQUE = "q9XvK2mLpR7tYw3bNc8Zf5HjD1sGa6Ue"
REPO = "acme/agentlab-feedback"


class FakeGitHub:
    """The parts of GitHub's API that AgentLab uses, with a memory, and ways to fail."""

    def __init__(self) -> None:
        self.issues: list[dict[str, Any]] = []
        self.requests: list[httpx.Request] = []
        self.fail: list[
            tuple[str, int, dict[str, str], dict[str, Any]]
        ] = []  # (method path prefix, status, headers, body)
        self.lose_next_create_response = False

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        key = f"{request.method} {request.url.path}"
        for i, (prefix, status, headers, body) in enumerate(self.fail):
            if key.startswith(prefix):
                self.fail.pop(i)
                return httpx.Response(status, headers=headers, json=body)
        if request.method == "POST" and request.url.path == f"/repos/{REPO}/issues":
            data = json.loads(request.content)
            number = len(self.issues) + 1
            issue = {
                "id": 9000 + number,
                "number": number,
                "state": "open",
                "title": data["title"],
                "body": data["body"],
                "labels": data.get("labels", []),
                "assignees": data.get("assignees", []),
                "html_url": f"https://github.com/{REPO}/issues/{number}",
            }
            self.issues.append(issue)
            if self.lose_next_create_response:
                self.lose_next_create_response = False
                raise httpx.ReadTimeout("the answer never came", request=request)
            return httpx.Response(201, json=issue)
        if request.method == "GET" and request.url.path == f"/repos/{REPO}/issues":
            return httpx.Response(200, json=list(reversed(self.issues)))
        match = re.fullmatch(rf"/repos/{REPO}/issues/(\d+)(/comments)?", request.url.path)
        if match and request.method in ("POST", "PATCH"):
            issue = self.issues[int(match.group(1)) - 1]
            if match.group(2):
                issue.setdefault("comments", []).append(json.loads(request.content)["body"])
                return httpx.Response(201, json={"id": 1})
            issue["state"] = json.loads(request.content)["state"]
            return httpx.Response(200, json=issue)
        return httpx.Response(404, json={"message": "Not Found"})

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handler)


def github_config(**over: Any) -> GitHubConfig:
    base = {
        "enabled": True,
        "repository": REPO,
        "token_ref": "env:GITHUB_TOKEN",
        "labels": ["from-agentlab"],
        "assignee": "octocat",
        "internal_url": "https://agentlab.example.org/#/feedback/submissions/{number}",
        "publish_roles": ["maintainer", "admin"],
        "status_sync": True,
    }
    return GitHubConfig(**{**base, **over})


class PublishWorld(HandoffWorld):
    def __init__(self, tmp_path: Path, clock: Clock, fake: FakeGitHub | None = None, **feedback: Any) -> None:
        feedback.setdefault("integrations", IntegrationsConfig(github=github_config()))
        super().__init__(tmp_path, clock, **feedback)
        self.fake = fake or FakeGitHub()
        trackers = {}
        if self.config.integrations.github.enabled:
            trackers["github"] = GitHubTracker(
                self.config.integrations.github, lambda: TOKEN, transport=self.fake.transport(), clock=lambda: 1_000.0
            )
        self.publish = PublicationService(self.svc, trackers, self.config, DiagnosticRedactor(), clock=clock)

    def admin(self, name: str = "root") -> Any:
        return self.person(name, "admin")

    def first_hash(self, actor: Any, ident: str) -> str:
        return self.publish.preview(actor, ident, "github").content_hash

    def go(self, actor: Any, ident: str, **extra: Any) -> Any:
        return self.publish.publish(
            actor, ident, PublishIn(provider="github", content_hash=self.first_hash(actor, ident), **extra)
        )


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make(tmp_path: Path, clock: Clock):  # type: ignore[no-untyped-def]
    return lambda **feedback: PublishWorld(tmp_path, clock, **feedback)


@pytest.fixture
def world(make):  # type: ignore[no-untyped-def]
    return make()


# =============================================================================================== off until switched on
def test_nothing_is_ever_sent_to_github_unless_the_owner_switched_it_on(make) -> None:  # type: ignore[no-untyped-def]
    w = make(integrations=IntegrationsConfig())
    made = w.file(w.person("alice"), FULL_ISSUE)
    maria = w.maintainer()
    (listed,) = w.publish.integrations(maria)
    assert listed.provider == "github" and listed.enabled is False and listed.ready is False
    assert "switched off" in (listed.reason or "") and listed.repository is None
    with pytest.raises(UserError, match="not switched on"):
        w.publish.preview(maria, made.id, "github")
    with pytest.raises(UserError, match="not switched on"):
        w.publish.publish(maria, made.id, PublishIn(provider="github", content_hash="0" * 64))
    assert tracker_for(w.config, lambda ref: TOKEN) == {}
    assert w.fake.requests == []
    # even with a tracker wired in by mistake, a configuration that is off sends nothing
    wired = PublicationService(
        w.svc,
        {"github": GitHubTracker(github_config(), lambda: TOKEN, transport=w.fake.transport())},
        w.config,
        DiagnosticRedactor(),
    )
    with pytest.raises(UserError, match="not switched on"):
        wired.preview(maria, made.id, "github")
    with pytest.raises(UserError, match="not switched on"):
        wired.publish(maria, made.id, PublishIn(provider="github", content_hash="0" * 64))
    assert w.fake.requests == []


def test_the_configuration_refuses_an_incomplete_or_insecure_integration() -> None:
    assert GitHubConfig().enabled is False
    with pytest.raises(ValidationError, match="repository and a token_ref"):
        GitHubConfig(enabled=True)
    with pytest.raises(ValidationError, match="https"):
        github_config(api_url="http://github.example.org/api")
    with pytest.raises(ValidationError, match=r"\{number\}"):
        github_config(internal_url="https://agentlab.example.org/")
    with pytest.raises(ValidationError):
        github_config(repository="not a repository")
    with pytest.raises(ValidationError):
        github_config(assignee="not a user!")


# ======================================================================================================== the content
def test_the_preview_is_a_sanitized_summary_with_the_things_that_must_not_go_left_out(world: PublishWorld) -> None:
    alice, maria = world.person("alice", "reporter"), world.maintainer()
    made = world.file(
        alice,
        {**FULL_ISSUE, "description": f"It never finishes. My key is {OPENAI} and mail is jane@example.com"},
        related_run_id="run-42",
    )
    world.svc.comment(maria, made.id, CommentIn(body="Customer is on a trial plan", visibility="internal"))
    world.attach_file(alice, made.id, "secret-notes.txt", b"the contents of a private file\n")
    world.attach_diagnostics(alice, made.id, "environment")
    preview = world.publish.preview(maria, made.id, "github")
    assert preview.repository == REPO and preview.labels == ["from-agentlab"] and preview.assignees == ["octocat"]
    assert preview.title == f"[{made.number}] A run stays on Processing forever"
    body = preview.body
    assert f"AgentLab submission **{made.number}**" in body
    assert f"https://agentlab.example.org/#/feedback/submissions/{made.number}" in body
    assert "It never finishes." in body and "The run ends in a few minutes" in body
    for must_not in (
        OPENAI,
        "jane@example.com",
        "alice",
        "Customer is on a trial plan",
        "secret-notes",
        "the contents of a private file",
        "run-42",
        "agentlab_version",
    ):
        assert must_not not in body and must_not not in preview.title, must_not
    assert len(preview.content_hash) == 64 and re.search(r"<!-- agentlab-publication:[0-9a-f]{32} -->", body)
    assert "attachments and screenshots" in preview.left_out and "internal notes" in preview.left_out
    assert any("visible to everyone" in w for w in preview.warnings)
    assert world.fake.requests == [], "a preview calls nobody"


def test_what_a_person_wrote_cannot_mention_anyone_close_an_issue_or_pull_in_a_picture(world: PublishWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    hostile = (
        "@octocat @acme/maintainers please look. Fixes #1, closes acme/other#7.\n"
        "![tracking](http://evil.example/p.png) [click](javascript:alert(1))\n"
        "```\n## Not a heading\n````\n"
    )
    made = world.file(
        alice,
        {
            **FULL_ISSUE,
            "title": "Please @octocat fix #1 `now`",
            "description": hostile,
            "issue": {**FULL_ISSUE["issue"], "actual_behavior": hostile, "reproduction_steps": hostile},
        },
    )
    body = world.publish.preview(maria, made.id, "github").body
    outside = "\n".join(outside_fences(body))
    for fragment in (
        "@octocat",
        "@acme",
        "Fixes #1",
        "closes acme/other#7",
        "![tracking]",
        "javascript:alert",
        "Not a heading",
    ):
        assert fragment not in outside, fragment
    assert [line for line in outside.splitlines() if line.startswith("#")] == [
        "### Description",
        "### Expected",
        "### Actual",
        "### Steps to reproduce (as reported)",
        "### How it affects their work (as reported)",
    ]
    assert body.count("@octocat") >= 3, "the words are there, quoted, so they do not notify anyone"


def test_a_feature_request_is_published_as_the_request_and_not_as_the_triage(world: PublishWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, FEATURE)
    world.triage(maria, made.id, assigned_priority="critical", severity="high", labels=["internal-only"])
    preview = world.publish.preview(maria, made.id, "github")
    assert "The problem or opportunity" in preview.body and "JSON cannot be opened in a spreadsheet" in preview.body
    assert "Requested business priority: high." in preview.body
    for private in ("critical", "internal-only", "maria"):
        assert private not in preview.body.replace("critical_business_need", "")


# ====================================================================================================== publishing
def test_publishing_makes_exactly_the_previewed_issue_once_and_records_where(world: PublishWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, FULL_ISSUE)
    preview = world.publish.preview(maria, made.id, "github")
    done = world.publish.publish(maria, made.id, PublishIn(provider="github", content_hash=preview.content_hash))
    assert done.state == "published" and done.number == 1 and done.repository == REPO
    assert done.url == f"https://github.com/{REPO}/issues/1" and done.published_by == "maria"
    (issue,) = world.fake.issues
    assert (issue["title"], issue["body"], issue["labels"], issue["assignees"]) == (
        preview.title,
        preview.body,
        ["from-agentlab"],
        ["octocat"],
    )
    sent = [r for r in world.fake.requests if r.method == "POST"][0]
    assert sent.headers["authorization"] == f"Bearer {TOKEN}" and sent.headers["x-github-api-version"] == "2022-11-28"
    assert str(sent.url).startswith("https://api.github.com/repos/acme/agentlab-feedback/issues")
    listed = world.publish.publications(maria, made.id)
    assert [p.url for p in listed] == [done.url]
    # asking again is not a second issue
    again = world.publish.publish(maria, made.id, PublishIn(provider="github", content_hash=preview.content_hash))
    assert again.id == done.id and len(world.fake.issues) == 1
    # the token is nowhere AgentLab keeps or says anything
    with world.db.session() as s:
        stored = " ".join(
            str(r)
            for table in ("external_issue_links", "submission_audit_events")
            for r in s.execute(text(f"select * from {table}")).all()
        )
    assert TOKEN not in stored and TOKEN not in json.dumps(done.model_dump(mode="json"))
    published = [e for e in world.svc.audit_trail(maria, made.id) if e.event == "publication_published"]
    assert (
        len(published) == 1 and published[0].detail["url"] == done.url and published[0].detail["external_number"] == 1
    )


def test_something_that_was_not_previewed_is_not_sent(world: PublishWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, FULL_ISSUE)
    stale = world.first_hash(maria, made.id)
    world.triage(maria, made.id, status="Investigating")  # the status is in the issue, so what would go has changed
    with pytest.raises(VersionConflict, match="not what you previewed"):
        world.publish.publish(maria, made.id, PublishIn(provider="github", content_hash=stale))
    with pytest.raises(VersionConflict):
        world.publish.publish(maria, made.id, PublishIn(provider="github", content_hash="f" * 64))
    assert world.fake.issues == []
    assert world.publish.publications(maria, made.id) == []


def test_a_retry_after_a_lost_answer_finds_the_issue_that_was_made_and_makes_no_second(world: PublishWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, FULL_ISSUE)
    world.fake.lose_next_create_response = True
    with pytest.raises(InfrastructureError, match="did not answer in time"):
        world.go(maria, made.id)
    assert len(world.fake.issues) == 1, "GitHub made the issue; AgentLab never heard"
    (failed,) = world.publish.publications(maria, made.id)
    assert failed.state == "failed" and failed.error_kind == "unavailable" and failed.url == ""
    done = world.go(maria, made.id)
    assert done.state == "published" and done.number == 1 and done.id == failed.id
    assert len(world.fake.issues) == 1, "found by its key, not made again"


def test_the_rate_limit_says_when_to_try_again_and_nothing_is_recorded_as_published(world: PublishWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, FULL_ISSUE)
    world.fake.fail.append(
        (
            f"POST /repos/{REPO}/issues",
            403,
            {"x-ratelimit-remaining": "0", "x-ratelimit-reset": str(1_000 + 600)},
            {"message": "API rate limit exceeded for user ID 1"},
        )
    )
    with pytest.raises(QuotaExceeded, match="about 10 minute") as caught:
        world.go(maria, made.id)
    assert caught.value.retry_after_seconds == 600
    assert world.fake.issues == []
    (link,) = world.publish.publications(maria, made.id)
    assert link.state == "failed" and link.error_kind == "rate_limited"
    assert world.go(maria, made.id).state == "published" and len(world.fake.issues) == 1


@pytest.mark.parametrize(
    ("status", "exception", "message"),
    [
        (401, InfrastructureError, "did not accept the token"),
        (403, InfrastructureError, "not allowed to do this"),
        (404, InfrastructureError, "does not know that repository"),
        (410, UserError, "switched off in that repository"),
        (422, UserError, "refused the issue: Validation Failed"),
        (500, InfrastructureError, "answered with an error"),
    ],
)
def test_each_refusal_is_said_in_words_and_leaves_the_submission_as_it_was(
    world: PublishWorld, status: int, exception: type[Exception], message: str
) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, FULL_ISSUE)
    version = world.svc.get(maria, made.id).version
    world.fake.fail.append((f"POST /repos/{REPO}/issues", status, {}, {"message": "Validation Failed"}))
    with pytest.raises(exception, match=message):
        world.go(maria, made.id)
    (link,) = world.publish.publications(maria, made.id)
    assert link.state == "failed" and link.url == ""
    assert world.svc.get(maria, made.id).version == version, "a failed publication changes nothing about the submission"
    assert TOKEN not in link.error_detail


def test_a_publication_that_is_in_progress_is_waited_for_and_one_that_was_abandoned_is_taken_over(
    world: PublishWorld, clock: Clock
) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, FULL_ISSUE)
    h = world.first_hash(maria, made.id)
    world.fake.fail.append((f"POST /repos/{REPO}/issues", 500, {}, {}))
    with pytest.raises(InfrastructureError):
        world.publish.publish(maria, made.id, PublishIn(provider="github", content_hash=h))
    with world.db.session() as s:
        s.execute(text("update external_issue_links set status = 'publishing'"))
    with pytest.raises(VersionConflict, match="already in progress"):
        world.publish.publish(maria, made.id, PublishIn(provider="github", content_hash=h))
    clock.advance(minutes=5)
    with world.db.session() as s:
        s.execute(text("update external_issue_links set updated_at = :t"), {"t": clock.now - timedelta(minutes=5)})
    assert world.publish.publish(maria, made.id, PublishIn(provider="github", content_hash=h)).state == "published"


# ================================================================================================ who, and what never
def test_a_private_security_report_is_never_published_by_anyone(world: PublishWorld) -> None:
    alice, root = world.person("alice"), world.admin()
    private = world.file(alice, FULL_ISSUE, suspected_security=True, title="Tokens show on a page")
    assert private.visibility == "security"
    with pytest.raises(PolicyBlocked, match="never published to an external service"):
        world.publish.preview(root, private.id, "github")
    with pytest.raises(PolicyBlocked, match="never published"):
        world.publish.publish(root, private.id, PublishIn(provider="github", content_hash="a" * 64))
    categorised = world.file(alice, {**FULL_ISSUE, "category": "Suspected Security Issue"}, title="Another one")
    with pytest.raises(PolicyBlocked, match="never published"):
        world.publish.preview(root, categorised.id, "github")
    assert world.fake.requests == []


def test_only_the_roles_the_owner_named_may_publish(make) -> None:  # type: ignore[no-untyped-def]
    w = make(integrations=IntegrationsConfig(github=github_config(publish_roles=["admin"])))
    alice, maria, root = w.person("alice"), w.maintainer(), w.admin()
    made = w.file(alice, FULL_ISSUE)
    for who in (alice, maria, w.token):
        with pytest.raises(PolicyBlocked):
            w.publish.preview(who, made.id, "github")
    assert [i.may_publish for i in w.publish.integrations(maria)] == [False]
    assert [i.may_publish for i in w.publish.integrations(root)] == [True]
    assert w.go(root, made.id).state == "published"


def test_a_maintainer_role_includes_administrators(make) -> None:  # type: ignore[no-untyped-def]
    w = make(integrations=IntegrationsConfig(github=github_config(publish_roles=["maintainer"])))
    made = w.file(w.person("bob"), FULL_ISSUE)
    assert w.go(w.maintainer(), made.id).state == "published"
    assert (
        w.publish.publish(
            w.admin(), made.id, PublishIn(provider="github", content_hash=w.first_hash(w.admin(), made.id))
        ).state
        == "published"
    ), "an administrator is at least a maintainer"
    assert len(w.fake.issues) == 1


def test_a_submission_the_caller_may_not_see_is_not_found(world: PublishWorld) -> None:
    alice, bob = world.person("alice"), world.person("bob")
    made = world.file(alice, FULL_ISSUE)
    with pytest.raises((NotFoundError, PolicyBlocked)):
        world.publish.preview(bob, made.id, "github")
    with pytest.raises(NotFoundError):
        world.publish.preview(world.maintainer(), "IR-2099-00001", "github")


def test_text_the_second_inspection_doubts_needs_the_maintainer_to_say_they_read_it(world: PublishWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, {**FULL_ISSUE, "description": f"The session id {OPAQUE} was rejected"})
    preview = world.publish.preview(maria, made.id, "github")
    assert preview.needs_review is True and OPAQUE not in preview.body
    assert any("still looks like a secret" in w for w in preview.warnings)
    with pytest.raises(UserError, match="acknowledge_unreviewed"):
        world.publish.publish(maria, made.id, PublishIn(provider="github", content_hash=preview.content_hash))
    assert world.fake.issues == []
    done = world.publish.publish(
        maria, made.id, PublishIn(provider="github", content_hash=preview.content_hash, acknowledge_unreviewed=True)
    )
    assert done.state == "published" and OPAQUE not in world.fake.issues[0]["body"]


# ============================================================================================== status, and storage
def test_a_status_is_not_pushed_unless_the_owner_allowed_it(make) -> None:  # type: ignore[no-untyped-def]
    off = make(integrations=IntegrationsConfig(github=github_config(status_sync=False)))
    made = off.file(off.person("alice"), FULL_ISSUE)
    off.go(off.maintainer(), made.id)
    with pytest.raises(UserError, match="not switched on"):
        off.publish.sync_status(off.maintainer(), made.id, "github")
    assert off.fake.issues[0].get("comments") is None


def test_a_status_is_pushed_only_when_allowed_and_asked_for_and_carries_only_its_name(make) -> None:  # type: ignore[no-untyped-def]
    w = make()
    alice, maria = w.person("alice"), w.maintainer()
    made = w.file(alice, {**FULL_ISSUE, "description": "A secret-ish description that must not be echoed"})
    with pytest.raises(UserError, match="has not been published"):
        w.publish.sync_status(maria, made.id, "github")
    w.go(maria, made.id)
    w.triage(maria, made.id, status="Investigating")
    synced = w.publish.sync_status(maria, made.id, "github")
    assert synced.synced_status == "Investigating" and synced.external_state == "open"
    assert w.fake.issues[0]["comments"] == ["AgentLab status: **Investigating**."]
    w.triage(maria, made.id, status="Closed")
    closed = w.publish.sync_status(maria, made.id, "github")
    assert closed.external_state == "closed" and w.fake.issues[0]["state"] == "closed"
    assert w.fake.issues[0]["comments"][-1] == "AgentLab status: **Closed**. This submission is closed."
    assert not any("secret-ish" in c for c in w.fake.issues[0]["comments"])


def test_deleting_a_submission_takes_its_link_and_leaves_the_issue_where_it_is(world: PublishWorld) -> None:
    from agentlab.feedback.retention import RetentionService

    alice, maria, root = world.person("alice"), world.maintainer(), world.admin()
    made = world.file(alice, FULL_ISSUE)
    world.go(maria, made.id)
    RetentionService(world.svc, world.artifacts, world.config).delete_submission(
        root, made.id, reason="erasure", acknowledge_open_security_report=False
    )
    with world.db.session() as s:
        assert s.execute(text("select count(*) from external_issue_links")).scalar() == 0
    assert len(world.fake.issues) == 1, "AgentLab cannot delete an issue in another system: the documentation says so"


def test_the_migration_creates_the_link_table_with_one_row_per_submission_and_provider_and_can_be_undone(
    tmp_path: Path,
) -> None:
    from sqlalchemy import inspect

    from agentlab.storage.db import Database
    from agentlab.storage.migrate import current_head, downgrade

    db = Database(f"sqlite:///{tmp_path}/m.db")
    db.migrate()
    assert current_head() >= "0008" and "external_issue_links" in set(inspect(db.engine).get_table_names())
    uniques = [u["name"] for u in inspect(db.engine).get_unique_constraints("external_issue_links")]
    assert "uq_external_issue_link" in uniques
    downgrade(db.url, "0007")
    assert "external_issue_links" not in set(inspect(db.engine).get_table_names())
    db.migrate()
    assert "external_issue_links" in set(inspect(db.engine).get_table_names())


# =========================================================================================== the GitHub client itself
def test_the_client_never_follows_a_redirect_and_never_returns_a_link_that_is_not_https() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.method == "POST":
            return httpx.Response(201, json={"id": 1, "number": 3, "state": "open", "html_url": "javascript:alert(1)"})
        return httpx.Response(302, headers={"location": "https://evil.example/"})

    tracker = GitHubTracker(github_config(), lambda: TOKEN, transport=httpx.MockTransport(handler))
    made = tracker.create(IssueDraft(title="t", body="b", labels=(), assignees=(), key="k" * 32))
    assert made.number == 3 and made.url == "", "a link that is not https is dropped"
    with pytest.raises(TrackerError, match="answered with an error"):
        tracker.find("k" * 32)
    assert len(seen) == 2 and all(r.url.host == "api.github.com" for r in seen)


def test_the_client_without_a_token_says_so_and_calls_nobody() -> None:
    calls: list[int] = []
    tracker = GitHubTracker(
        github_config(), lambda: None, transport=httpx.MockTransport(lambda r: calls.append(1) or httpx.Response(200))
    )
    with pytest.raises(TrackerError, match="no GitHub token"):
        tracker.create(IssueDraft(title="t", body="b", labels=(), assignees=(), key="k"))
    assert calls == []


def test_a_find_looks_only_at_issues_with_the_key_and_ignores_pull_requests() -> None:
    items = [
        {
            "id": 1,
            "number": 5,
            "state": "open",
            "html_url": "https://github.com/a/b/pull/5",
            "body": marker("k1"),
            "pull_request": {},
        },
        {
            "id": 2,
            "number": 4,
            "state": "closed",
            "html_url": "https://github.com/a/b/issues/4",
            "body": "x " + marker("k1"),
        },
        {"id": 3, "number": 3, "state": "open", "html_url": "https://github.com/a/b/issues/3", "body": marker("k2")},
    ]
    tracker = GitHubTracker(
        github_config(), lambda: TOKEN, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=items))
    )
    found = tracker.find("k1")
    assert isinstance(found, ExternalIssue) and found.number == 4 and found.state == "closed"
    assert tracker.find("nothing") is None
