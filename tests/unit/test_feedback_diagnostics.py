"""The diagnostics collector without a server: whose lines it takes, what it masks, what it says it could not do, and what
happens to a draft.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import hashlib
import io
import json
import zipfile
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from agentlab.core.config import AgentLabConfig
from agentlab.core.enums import TestStatus
from agentlab.core.errors import NotFoundError, UserError
from agentlab.core.models import AttemptResult, TestResult
from agentlab.feedback import QuotaExceeded
from agentlab.feedback.diagnostics import DiagnosticsService
from agentlab.feedback.models import AttachDiagnosticsIn, DiagnosticsRequest
from agentlab.security.diagnostic_redactor import DiagnosticRedactor
from agentlab.storage import orm
from agentlab.storage.artifacts import LocalArtifactStore
from agentlab.storage.db import Store
from agentlab.tracing.events import Event, EventType
from agentlab.tracing.trace import Trace
from tests.unit.test_feedback_service import ISSUE, Clock, World

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"
OPAQUE = "q9XvK2mLpR7tYw3bNc8Zf5HjD1sGa6Ue"


class DiagWorld(World):
    def __init__(self, tmp_path: Path, clock: Clock, **feedback: Any) -> None:
        super().__init__(tmp_path, clock, **feedback)
        self.root = tmp_path / "artifacts"
        self.artifacts = LocalArtifactStore(self.root)
        self.runs = Store(self.db)
        self.diag = DiagnosticsService(
            self.svc,
            self.runs,
            self.artifacts,
            AgentLabConfig(feedback=self.config),
            DiagnosticRedactor(),
            clock=clock,
        )

    # --- seeding what the server would have logged
    def log(
        self,
        message: str,
        *,
        minutes_ago: float = 2,
        process: str = "api",
        principal: str = "",
        request_id: str | None = None,
        run_id: str | None = None,
        level: str = "WARNING",
        exception: str | None = None,
    ) -> None:
        with self.db.session() as s:
            s.add(
                orm.LogEventRow(
                    ts=self.clock.now - timedelta(minutes=minutes_ago),
                    level=level,
                    logger="agentlab.api.request" if request_id else "agentlab.jobs.runner",
                    process=process,
                    run_id=run_id,
                    request_id=request_id,
                    principal=principal,
                    message=message,
                    exception=exception,
                )
            )

    def seed_run(self, *, with_trace: bool = True) -> tuple[str, str]:
        """A run with one failed test (and its trace) and one that passed. Returns (run id, test id)."""
        project = self.runs.ensure_project("diagnostics")
        run = self.runs.create_run(project["id"], None, None, "full", {}, {})
        run_id = run["id"]
        self.runs.update_run(run_id, status="completed", error=None, totals={"overall": 61, "failed": 1, "passed": 1})
        failed = TestResult(
            run_id=run_id,
            test_id="CONV-ARITHMETIC-001",
            test_name="Arithmetic",
            category="conversation",
            score_category="conversation",
            status=TestStatus.FAILED,
            attempts=[
                AttemptResult(
                    attempt=1,
                    status=TestStatus.FAILED,
                    error=f"connection error: ReadError (key {OPENAI})",
                    outputs=["", "I do not know"],
                )
            ],
        )
        passed = TestResult(
            run_id=run_id,
            test_id="CONV-GREETING-001",
            test_name="Greeting",
            category="conversation",
            score_category="conversation",
            status=TestStatus.PASSED,
            attempts=[AttemptResult(attempt=1, status=TestStatus.PASSED)],
        )
        self.runs.save_result(failed)
        self.runs.save_result(passed)
        if with_trace:
            trace = Trace(run_id=run_id, test_id="CONV-ARITHMETIC-001")
            trace.events.append(
                Event.create(
                    run_id, EventType.ERROR, {"message": f"agent said Authorization: Bearer {OPENAI}"}, trace.test_id
                )
            )
            artifact = self.artifacts.put_json(
                trace.model_dump(mode="json"), kind="trace", sensitivity="restricted", redact=False
            )
            self.runs.save_trace(trace, artifact.id)
        return run_id, "CONV-ARITHMETIC-001"

    def request(self, *categories: str, **extra: Any) -> DiagnosticsRequest:
        return DiagnosticsRequest(categories=list(categories), reference_time=self.clock.now, **extra)  # type: ignore[arg-type]


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def make(tmp_path: Path, clock: Clock):  # type: ignore[no-untyped-def]
    return lambda **feedback: DiagWorld(tmp_path, clock, **feedback)


@pytest.fixture
def world(make):  # type: ignore[no-untyped-def]
    return make()


def item(collection: Any, category: str, path: str | None = None) -> Any:
    found = [i for i in collection.items if i.category == category and (path is None or i.path == path)]
    assert len(found) == 1, (category, [(i.category, i.path) for i in collection.items])
    return found[0]


# ================================================================================================ the menu
def test_the_menu_says_what_can_be_collected_and_why_what_cannot_not(world: DiagWorld) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    menu = {s.id: s for s in world.diag.available(alice).sources}
    assert menu["environment"].available and menu["application_logs"].available
    assert not menu["backend_logs"].available and "No background worker" in menu["backend_logs"].reason  # type: ignore[operator]
    for evaluation in ("run_metadata", "test_summary", "trace"):
        assert not menu[evaluation].available and "maintainers only" in menu[evaluation].reason  # type: ignore[operator]
    assert menu["screenshot"].manual and not menu["screenshot"].available
    assert "Attach one as a file" in menu["screenshot"].reason  # type: ignore[operator]

    staff = {s.id: s for s in world.diag.available(maria, run_id="run-1", test_id="t-1").sources}
    assert staff["run_metadata"].available and staff["test_summary"].available and staff["trace"].available
    assert not {s.id: s for s in world.diag.available(maria).sources}["run_metadata"].available
    assert world.diag.available(maria).consent_text.startswith("The diagnostic information you attach")


def test_a_log_source_that_is_switched_off_says_so(make) -> None:  # type: ignore[no-untyped-def]
    from agentlab.core.config import DiagnosticsConfig

    w = make(diagnostics=DiagnosticsConfig(capture_logs=False))
    menu = {s.id: s for s in w.diag.available(w.person("alice")).sources}
    assert not menu["application_logs"].available and "switched off" in menu["application_logs"].reason  # type: ignore[operator]
    got = w.diag.preview(w.person("alice"), w.request("application_logs", "environment"))
    assert item(got, "application_logs").outcome == "unavailable" and item(got, "environment").outcome == "collected"


# ============================================================================================ whose lines
def test_a_person_gets_their_own_requests_and_system_warnings_and_nobody_elses(world: DiagWorld) -> None:
    alice, bob = world.person("alice"), world.person("bob")
    world.log("GET /submissions answered 500 in 12 ms", principal=alice.user_id, request_id="aaaa1111bbbb2222")  # type: ignore[arg-type]
    world.log(
        "handler failed while serving alice", request_id="aaaa1111bbbb2222"
    )  # same request, no principal of its own
    world.log("POST /submissions answered 422 in 3 ms", principal=bob.user_id, request_id="cccc3333dddd4444")  # type: ignore[arg-type]
    world.log("handler failed while serving bob", request_id="cccc3333dddd4444")
    world.log("database was briefly unreachable")  # system level: no person, no run
    world.log("run r-9 failed", run_id="r-9", process="worker")
    got = world.diag.preview(alice, world.request("application_logs"))
    text_ = item(got, "application_logs").preview or ""
    assert "answered 500" in text_ and "while serving alice" in text_ and "briefly unreachable" in text_
    assert "while serving bob" not in text_ and "answered 422" not in text_ and "run r-9" not in text_
    assert "[req aaaa1111bbbb2222]" in text_, "lines are tied together by the request they belong to"

    other = world.diag.preview(bob, world.request("application_logs"))
    assert "while serving bob" in (item(other, "application_logs").preview or "")
    assert "serving alice" not in (item(other, "application_logs").preview or "")


def test_a_maintainer_also_gets_the_lines_of_the_run_they_name_and_a_reporter_cannot_ask_for_them(
    world: DiagWorld,
) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    run_id, _ = world.seed_run()
    world.log("run failed in the worker", run_id=run_id, process="worker")
    world.log("api noticed run trouble", run_id=run_id)
    world.log("an unrelated run's line", run_id="some-other-run", process="worker")
    got = world.diag.preview(maria, world.request("application_logs", "backend_logs", run_id=run_id))
    assert "api noticed run trouble" in (item(got, "application_logs").preview or "")
    backend = item(got, "backend_logs").preview or ""
    assert "run failed in the worker" in backend and "unrelated run" not in backend
    # alice names the same run and gets nothing of it, and cannot tell whether it exists
    asked = world.diag.preview(alice, world.request("application_logs", "run_metadata", run_id=run_id))
    assert (
        "run trouble" not in (item(asked, "application_logs").preview or "")
        if item(asked, "application_logs").preview
        else True
    )
    meta = item(asked, "run_metadata")
    assert meta.outcome == "unavailable" and "maintainers only" in meta.detail
    ghost = world.diag.preview(alice, world.request("run_metadata", run_id="no-such-run"))
    assert item(ghost, "run_metadata").detail == meta.detail, "an existing run and a missing one are answered alike"


def test_the_period_is_respected(world: DiagWorld) -> None:
    alice = world.person("alice")
    world.log("inside the window", principal=alice.user_id, request_id="aaaa1111bbbb2222", minutes_ago=4)  # type: ignore[arg-type]
    world.log("just outside the window", principal=alice.user_id, request_id="bbbb1111cccc2222", minutes_ago=7)  # type: ignore[arg-type]
    got = world.diag.preview(alice, world.request("application_logs", window="last_5_minutes"))
    shown = item(got, "application_logs").preview or ""
    assert "inside the window" in shown and "outside the window" not in shown
    wider = world.diag.preview(alice, world.request("application_logs", window="last_15_minutes"))
    assert "just outside the window" in (item(wider, "application_logs").preview or "")
    custom = world.diag.preview(
        alice,
        world.request(
            "application_logs",
            window="custom",
            window_from=world.clock.now - timedelta(minutes=8),
            window_to=world.clock.now - timedelta(minutes=6),
        ),
    )
    only = item(custom, "application_logs").preview or ""
    assert "just outside the window" in only and "inside the window" not in only.replace("outside the window", "")
    with pytest.raises(UserError, match="at most 360 minutes"):
        world.diag.preview(
            alice,
            world.request(
                "application_logs",
                window="custom",
                window_from=world.clock.now - timedelta(hours=7),
                window_to=world.clock.now,
            ),
        )
    with pytest.raises(ValidationError):
        world.request("application_logs", window="custom")


# ================================================================================================ the masking
def test_what_is_collected_is_masked_before_it_is_shown_or_kept_and_the_report_holds_no_value(world: DiagWorld) -> None:
    maria = world.maintainer()
    run_id, test_id = world.seed_run()
    world.log(
        f"provider call failed: Authorization: Bearer {OPENAI} for jane@example.com",
        request_id="aaaa1111bbbb2222",
        principal=maria.user_id,
        exception=f"Traceback ...\nRuntimeError: key {OPENAI}",
    )
    got = world.diag.collect(
        maria, world.request("application_logs", "errors", "trace", "test_summary", run_id=run_id, test_id=test_id)
    )
    blob = json.dumps(got.model_dump(mode="json"))
    assert OPENAI not in blob and "Zq81LmN4xW7sR2pK9vB3cD6e" not in blob and "jane@example.com" not in blob
    for category in ("application_logs", "errors", "trace", "test_summary"):
        assert item(got, category).outcome == "collected", category
    assert item(got, "application_logs").redaction.status == "redacted"
    assert item(got, "application_logs").redaction.counts
    with world.db.session() as s:
        stored = " ".join(str(r) for r in s.execute(text("select * from diagnostic_items")).all())
    assert OPENAI not in stored
    for artifact in world.artifacts.list():
        assert OPENAI.encode() not in world.artifacts.get(artifact.id) or artifact.kind == "trace"


def test_an_item_the_second_inspection_doubts_is_marked_and_the_collection_says_so(world: DiagWorld) -> None:
    maria = world.maintainer()
    world.log(f"session id {OPAQUE} was rejected", principal=maria.user_id, request_id="aaaa1111bbbb2222")
    got = world.diag.preview(maria, world.request("application_logs"))
    logs = item(got, "application_logs")
    assert OPAQUE not in (logs.preview or "") and logs.redaction.status == "needs_review"
    assert logs.redaction.external_sharing_blocked and got.any_needs_review
    assert any("needs review" in w for w in got.warnings)


def test_the_browsers_own_errors_are_masked_and_capped(make) -> None:  # type: ignore[no-untyped-def]
    from agentlab.core.config import DiagnosticsConfig

    w = make(diagnostics=DiagnosticsConfig(max_browser_entries=3))
    alice = w.person("alice")
    request = w.request(
        "browser",
        browser={
            "console": [{"message": f"fetch failed for https://app.test/api?access_token={OPENAI}&page=2"}]
            + [{"message": f"noise {n}"} for n in range(10)],
            "network": [{"method": "POST", "url": "https://app.test/api/runs?api_key=" + OPENAI, "status": 500}],
        },
    )
    got = w.diag.preview(alice, request)
    console = item(got, "browser", "browser/console-errors.json")
    network = item(got, "browser", "browser/network-errors.json")
    assert OPENAI not in json.dumps(got.model_dump(mode="json"))
    assert len(json.loads(console.preview or "[]")) == 3, "only the newest entries within the limit"
    assert "[REDACTED:" in (network.preview or "") and '"status": 500' in (network.preview or "")
    empty = w.diag.preview(alice, w.request("browser"))
    assert item(empty, "browser").outcome == "unavailable"


def test_the_environment_says_what_is_switched_on_and_nothing_about_the_host(world: DiagWorld) -> None:
    got = world.diag.preview(world.person("alice"), world.request("environment"))
    facts = json.loads(item(got, "environment").preview or "{}")
    assert facts["agentlab_version"] and facts["database"] == "sqlite" and "deployment" in facts
    assert set(facts["deployment"]) >= {"queue_backend", "browser_enabled", "accounts_registration", "log_capture"}
    flat = json.dumps(facts).lower()
    for private in ("/root", "/home", "hostname", "password", "token", "127.0.0.1"):
        assert private not in flat


# ============================================================================================ failing sources
def test_one_source_failing_never_stops_the_others_and_nothing_stands_in_for_it(
    world: DiagWorld, monkeypatch: pytest.MonkeyPatch
) -> None:
    alice = world.person("alice")

    def broken(self: DiagnosticsService, context: Any) -> list[Any]:
        raise RuntimeError("the log store is on fire")

    monkeypatch.setattr(DiagnosticsService, "_environment", broken)
    world.log("GET /x answered 500 in 1 ms", principal=alice.user_id, request_id="aaaa1111bbbb2222")  # type: ignore[arg-type]
    got = world.diag.collect(alice, world.request("environment", "application_logs", "screenshot"))
    assert item(got, "environment").outcome == "failed" and "RuntimeError" in item(got, "environment").detail
    assert "on fire" not in json.dumps(got.model_dump(mode="json")), "the cause is not echoed to the person"
    assert item(got, "application_logs").outcome == "collected"
    shot = item(got, "screenshot")
    assert shot.outcome == "unavailable" and "attach one" in shot.detail
    assert shot.size == 0 and shot.preview is None
    assert any("Environment and version could not be included" in w for w in got.warnings)
    assert got.total_bytes == item(got, "application_logs").size


def test_an_empty_source_is_reported_as_unavailable_not_as_an_empty_file(world: DiagWorld) -> None:
    got = world.diag.collect(world.person("alice"), world.request("application_logs", "errors", "backend_logs"))
    for category in ("application_logs", "errors", "backend_logs"):
        found = item(got, category)
        assert found.outcome == "unavailable" and found.size == 0 and found.preview is None
    assert got.total_bytes == 0
    with world.db.session() as s:
        assert (
            s.scalar(select(orm.DiagnosticItemRow.artifact_id).where(orm.DiagnosticItemRow.artifact_id.is_not(None)))
            is None
        )


# ========================================================================================== drafts, consent
def test_a_draft_is_kept_for_a_limited_time_and_then_deleted_with_its_files(world: DiagWorld, clock: Clock) -> None:
    alice = world.person("alice")
    first = world.diag.collect(alice, world.request("environment"))
    assert first.status == "draft" and first.id and first.expires_at is not None
    assert world.artifacts.list(), "the masked text is kept in the restricted store"
    clock.advance(hours=25)
    world.diag.collect(world.person("bob"), world.request("environment"))  # anyone's next collection sweeps the stale
    with pytest.raises(NotFoundError):
        world.diag.get(alice, first.id)
    names = [a.name for a in world.artifacts.list() if a.kind == "support_diagnostic"]
    assert len(names) == 1, "the expired draft's bytes were deleted; only bob's remain"


def test_collecting_has_an_hourly_allowance(make, clock) -> None:  # type: ignore[no-untyped-def]
    from agentlab.core.config import DiagnosticsConfig

    w = make(diagnostics=DiagnosticsConfig(max_collections_per_hour=2))
    alice = w.person("alice")
    w.diag.collect(alice, w.request("environment"))
    w.diag.collect(alice, w.request("environment"))
    with pytest.raises(QuotaExceeded) as refused:
        w.diag.collect(alice, w.request("environment"))
    assert 0 < refused.value.retry_after_seconds <= 3600
    clock.advance(minutes=61)
    assert w.diag.collect(alice, w.request("environment")).status == "draft"
    w.diag.preview(alice, w.request("environment"))  # a preview keeps nothing and is not counted


def test_attaching_needs_consent_and_the_owner_and_a_submission_they_may_see(world: DiagWorld) -> None:
    alice, bob = world.person("alice"), world.person("bob")
    made = world.file(alice, ISSUE)
    draft = world.diag.collect(alice, world.request("environment"))
    assert draft.id
    with pytest.raises(UserError, match="explicit consent"):
        world.diag.attach(alice, made.id, AttachDiagnosticsIn(collection_id=draft.id, consent=False))
    with pytest.raises(NotFoundError):  # bob's draft cannot be attached by alice, and alice's not by bob
        world.diag.attach(bob, made.id, AttachDiagnosticsIn(collection_id=draft.id, consent=True))
    theirs = world.file(bob, ISSUE, title="Bob's own report of it")
    with pytest.raises(NotFoundError):
        world.diag.attach(alice, theirs.id, AttachDiagnosticsIn(collection_id=draft.id, consent=True))
    done = world.diag.attach(alice, made.id, AttachDiagnosticsIn(collection_id=draft.id, consent=True))
    assert done.status == "attached" and done.submission_id == made.id and done.consent_at is not None
    assert done.expires_at is None
    with pytest.raises(NotFoundError):
        world.diag.attach(
            alice, made.id, AttachDiagnosticsIn(collection_id=draft.id, consent=True)
        )  # it is attached already
    audit = [e.event for e in world.svc.audit_trail(world.maintainer(), made.id)]
    assert "diagnostics_attached" in audit


def test_items_the_person_leaves_out_are_deleted_and_say_so(world: DiagWorld) -> None:
    alice = world.person("alice")
    made = world.file(alice, ISSUE)
    world.log("GET /x answered 500 in 1 ms", principal=alice.user_id, request_id="aaaa1111bbbb2222")  # type: ignore[arg-type]
    draft = world.diag.collect(alice, world.request("environment", "application_logs"))
    keep = item(draft, "environment")
    leave = item(draft, "application_logs")
    before = len(world.artifacts.list())
    done = world.diag.attach(
        alice, made.id, AttachDiagnosticsIn(collection_id=str(draft.id), include=[keep.id], consent=True)
    )
    assert item(done, "environment").included and item(done, "application_logs").outcome == "excluded"
    assert "left out" in item(done, "application_logs").detail and item(done, "application_logs").preview is None
    assert len(world.artifacts.list()) == before - 1, "what was left out is not kept"
    assert leave.id and keep.id
    again = world.diag.collect(alice, world.request("environment"))
    with pytest.raises(UserError, match="not in the collection"):
        world.diag.attach(
            alice, made.id, AttachDiagnosticsIn(collection_id=str(again.id), include=["nope"], consent=True)
        )


def test_nothing_included_is_not_an_attachment(world: DiagWorld) -> None:
    alice = world.person("alice")
    made = world.file(alice, ISSUE)
    draft = world.diag.collect(alice, world.request("application_logs"))  # nothing to collect: unavailable
    with pytest.raises(UserError, match="nothing is included"):
        world.diag.attach(alice, made.id, AttachDiagnosticsIn(collection_id=str(draft.id), consent=True))


# ================================================================================================ the bundle
def test_the_bundle_has_the_files_the_specification_names_a_manifest_and_a_readme_and_only_what_was_included(
    world: DiagWorld,
) -> None:
    alice, maria = world.person("alice"), world.maintainer()
    made = world.file(alice, ISSUE)
    world.log("GET /x answered 500 in 1 ms", principal=alice.user_id, request_id="aaaa1111bbbb2222")  # type: ignore[arg-type]
    draft = world.diag.collect(
        alice,
        world.request(
            "environment",
            "application_logs",
            "backend_logs",
            "browser",
            "screenshot",
            browser={"console": [{"message": "TypeError: x is undefined"}]},
        ),
    )
    world.diag.attach(alice, made.id, AttachDiagnosticsIn(collection_id=str(draft.id), consent=True))
    data, name = world.diag.bundle(alice, str(draft.id))
    assert name.startswith("diagnostics-") and name.endswith(".zip")
    archive = zipfile.ZipFile(io.BytesIO(data))
    names = set(archive.namelist())
    assert names == {
        "diagnostics/manifest.json",
        "diagnostics/README.txt",
        "diagnostics/environment.json",
        "diagnostics/application/relevant.log",
        "diagnostics/browser/console-errors.json",
    }, "no file stands in for backend logs or the screenshot"
    manifest = json.loads(archive.read("diagnostics/manifest.json"))
    assert manifest["generated"] is True and manifest["submission"] == made.id and manifest["consent"]["given_at"]
    by_path = {i["path"]: i for i in manifest["items"]}
    assert (
        by_path["backend/relevant.log"]["outcome"] == "unavailable" and "sha256" not in by_path["backend/relevant.log"]
    )
    assert by_path["screenshot/missing"]["outcome"] == "unavailable"
    for path in ("environment.json", "application/relevant.log", "browser/console-errors.json"):
        entry = by_path[path]
        body = archive.read(f"diagnostics/{path}")
        assert entry["sha256"] == hashlib.sha256(body).hexdigest() and entry["size"] == len(body)
        assert entry["generated"] is True
    assert "generated copy" in archive.read("diagnostics/README.txt").decode()
    world.diag.bundle(maria, str(draft.id))
    downloads = [e.actor for e in world.svc.audit_trail(maria, made.id) if e.event == "diagnostics_downloaded"]
    assert sorted(downloads) == ["alice", "maria"], "every download of an attached collection is recorded"


def test_a_collection_is_nobody_elses_until_it_is_attached_and_then_only_for_those_who_may_see_the_submission(
    world: DiagWorld,
) -> None:
    alice, bob, maria = world.person("alice"), world.person("bob"), world.maintainer()
    made = world.file(alice, ISSUE)
    draft = world.diag.collect(alice, world.request("environment"))
    for who in (bob, maria):
        with pytest.raises(NotFoundError):
            world.diag.get(who, str(draft.id))
        with pytest.raises(NotFoundError):
            world.diag.bundle(who, str(draft.id))
    world.diag.attach(alice, made.id, AttachDiagnosticsIn(collection_id=str(draft.id), consent=True))
    assert world.diag.get(maria, str(draft.id)).status == "attached"
    with pytest.raises(NotFoundError):
        world.diag.get(bob, str(draft.id))
    assert [c.id for c in world.diag.of_submission(maria, made.id)] == [draft.id]
    with pytest.raises(NotFoundError):
        world.diag.of_submission(bob, made.id)
    private = world.file(alice, ISSUE, suspected_security=True, title="Private report with diagnostics")
    other = world.diag.collect(alice, world.request("environment"))
    world.diag.attach(alice, private.id, AttachDiagnosticsIn(collection_id=str(other.id), consent=True))
    with pytest.raises(NotFoundError):
        world.diag.get(world.token, str(other.id))  # the shared token may not see a private submission


# ======================================================================================== run-linked items
def test_a_maintainer_collects_a_runs_summary_a_tests_result_and_its_trace(world: DiagWorld) -> None:
    maria = world.maintainer()
    run_id, test_id = world.seed_run()
    got = world.diag.collect(
        maria, world.request("run_metadata", "test_summary", "trace", "errors", run_id=run_id, test_id=test_id)
    )
    meta = json.loads(item(got, "run_metadata").preview or "{}")
    assert meta["run_id"] == run_id and meta["status"] == "completed" and meta["totals"]["failed"] == 1
    summary = json.loads(item(got, "test_summary").preview or "{}")
    assert summary["test_id"] == test_id and summary["status"] == "failed"
    assert summary["attempts"][0]["outputs"][1] == "I do not know" and OPENAI not in json.dumps(summary)
    assert item(got, "trace").outcome == "collected" and OPENAI not in (item(got, "trace").preview or "")
    errors = item(got, "errors").preview or ""
    assert "ReadError" in errors and OPENAI not in errors
    ghost = world.diag.preview(maria, world.request("test_summary", run_id=run_id, test_id="NOPE-001"))
    assert "no result" in item(ghost, "test_summary").detail
    untraced = world.diag.preview(maria, world.request("trace", run_id=run_id, test_id="CONV-GREETING-001"))
    assert item(untraced, "trace").outcome == "unavailable"
