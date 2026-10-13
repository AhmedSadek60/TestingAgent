"""Feedback & Support over HTTP: filing, following, triaging, and what each kind of caller is told."""

from __future__ import annotations

import asyncio
import contextlib
import re
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx

from agentlab.core.config import AccountsConfig, FeedbackConfig
from tests.api.test_accounts_api import PASSWORD, TOKEN, _own_password, accounts, browser, csrf, sign_in
from tests.support.api import Api, api_config, running_api

FEATURE: dict[str, Any] = {
    "type": "feature",
    "title": "Export reports as CSV",
    "category": "Reporting and Analytics",
    "description": "A spreadsheet would help.",
    "feature": {
        "problem_statement": "JSON cannot be opened in a spreadsheet",
        "acceptance_criteria": "A button on the report page downloads a .csv file",
        "requested_priority": "high",
    },
}
ISSUE: dict[str, Any] = {
    "type": "issue",
    "title": "A run stays on Processing forever",
    "category": "Test Execution Failure",
    "description": "It never finishes.",
    "issue": {
        "impact_summary": "I cannot get results",
        "expected_behavior": "The run ends in a few minutes",
        "actual_behavior": "It says Processing for an hour",
        "reproduction_steps": "1. Start a run\n2. Wait",
        "frequency": "every_time",
        "reported_impact": "blocks_project",
    },
    "related_run_id": "run-2026-10-12-abc",
}


@contextlib.asynccontextmanager
async def feedback_server(
    tmp_path: Path, *, token: str | None = TOKEN, accounts_config: AccountsConfig | None = None, **feedback: Any
) -> AsyncIterator[Api]:
    config = api_config(tmp_path, accounts=accounts_config or AccountsConfig(), feedback=FeedbackConfig(**feedback))
    async with running_api(tmp_path, token=token, config=config) as api:
        yield api


async def reporter(api: Api, name: str = "alice") -> tuple[httpx.AsyncClient, dict[str, str]]:
    client = browser(api)
    r = await client.post("/auth/register", json={"username": name, "password": PASSWORD})
    assert r.status_code == 201, r.text
    return client, {"X-CSRF-Token": r.json()["me"]["csrf_token"]}


async def maintainer(api: Api, name: str = "maria") -> tuple[httpx.AsyncClient, dict[str, str]]:
    svc = accounts(api)
    user, _ = svc.create_user("test", name, role="maintainer", password=PASSWORD)
    _own_password(svc, user.id)
    client, login = await sign_in(api, name)
    return client, csrf(login)


# ================================================================================================ the happy path
async def test_a_person_files_a_feature_request_and_follows_it_until_it_is_done(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)

        options = (await alice.get("/submissions/options")).json()
        assert FEATURE["category"] in options["feature_categories"] and options["can_triage"] is False
        assert options["feature_statuses"][0] == "Submitted" and options["max_text_chars"] == 20_000

        made = await alice.post("/submissions", json=FEATURE, headers=ah)
        assert made.status_code == 201, made.text
        body = made.json()
        assert re.fullmatch(r"FR-\d{4}-00001", body["number"]), body["number"]
        assert (body["status"], body["visibility"], body["mine"], body["reporter"]) == (
            "Submitted",
            "normal",
            True,
            "alice",
        )
        assert body["feature"]["acceptance_criteria"].startswith("A button")
        stored = (await alice.get(f"/submissions/{body['id']}")).json()
        assert stored == body, "the answer to the post is exactly what a later read returns: it was stored first"

        # the maintainer triages it
        queue = (await maria.get("/submissions", params={"status": "Submitted"})).json()
        assert queue["total"] == 1 and queue["items"][0]["reporter"] == "alice"
        assert (await maria.get("/submissions/summary")).json()["unassigned"] == 1
        v = body["version"]
        triaged = await maria.patch(
            f"/submissions/{body['id']}",
            json={
                "version": v,
                "status": "Under Review",
                "status_comment": "Thanks, we are looking at it",
                "assigned_priority": "high",
                "labels": ["reports", "csv"],
            },
            headers=mh,
        )
        assert triaged.status_code == 200, triaged.text
        assert triaged.json()["version"] == v + 1 and triaged.json()["labels"] == ["csv", "reports"]
        ask = await maria.post(
            f"/submissions/{body['id']}/comments",
            json={"body": "Which columns do you need?", "kind": "info_request"},
            headers=mh,
        )
        assert ask.status_code == 201 and ask.json()["author"] == "maria"

        # the reporter sees the answer and none of the triage
        mine = (await alice.get("/submissions", params={"unread": "true"})).json()
        assert mine["total"] == 1 and mine["items"][0]["unread"] is True
        d = (await alice.get(f"/submissions/{body['id']}")).json()
        assert d["status"] == "Under Review" and d["assigned_priority"] is None and d["labels"] == []
        assert [c["author"] for c in d["discussion"]] == ["AgentLab maintainer"]
        assert d["history"][-1]["comment"] == "Thanks, we are looking at it"
        assert (await alice.post(f"/submissions/{body['id']}/seen", headers=ah)).status_code == 204
        assert (await alice.get("/submissions/summary")).json()["unread"] == 0

        reply = await alice.post(
            f"/submissions/{body['id']}/comments", json={"body": "Name, date and score."}, headers=ah
        )
        assert reply.status_code == 201 and reply.json()["mine"] is True and reply.json()["author"] == "alice"
        done = await maria.patch(
            f"/submissions/{body['id']}",
            json={"version": triaged.json()["version"], "status": "Completed"},
            headers=mh,
        )
        assert done.json()["closed_at"] is not None and done.json()["resolved_at"] is not None
        assert (await alice.get("/submissions/summary")).json()["open"] == 0
        for c in (alice, maria):
            await c.aclose()


async def test_an_issue_report_keeps_its_own_details_and_the_related_run(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        r = await alice.post("/submissions", json=ISSUE, headers=ah)
        assert r.status_code == 201, r.text
        d = r.json()
        assert d["number"].startswith("IR-") and d["type"] == "issue" and d["requested_priority"] is None
        assert d["issue"]["frequency"] == "every_time" and d["issue"]["reported_impact"] == "blocks_project"
        assert d["issue"]["reproduction_steps"].count("\n") == 1 and d["related_run_id"] == ISSUE["related_run_id"]
        assert d["statuses"][0] == "Submitted" and "Awaiting User" in d["statuses"]
        await alice.aclose()


async def test_a_submission_is_found_by_its_number_in_any_case_as_well_as_by_its_id(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        made = (await alice.post("/submissions", json=FEATURE, headers=ah)).json()
        for ident in (made["id"], made["number"], made["number"].lower()):
            r = await alice.get(f"/submissions/{ident}")
            assert r.status_code == 200 and r.json()["id"] == made["id"], ident
        assert (await alice.get("/submissions/FR-2026-99999")).status_code == 404
        await alice.aclose()


# ================================================================================================ credentials
async def test_a_change_from_a_browser_session_needs_the_csrf_token_and_one_with_the_api_token_does_not(
    tmp_path: Path,
) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        assert (await alice.post("/submissions", json=FEATURE)).status_code == 403, "a cookie alone is not enough"
        assert (await alice.post("/submissions", json=FEATURE, headers={"X-CSRF-Token": "wrong"})).status_code == 403
        assert (await alice.post("/submissions", json=FEATURE, headers=ah)).status_code == 201
        # the shared token is sent as a header no other site can make a browser add
        made = await api.client.post("/submissions", json=ISSUE)
        assert made.status_code == 201 and made.json()["reporter"] == "shared API token"
        assert made.json()["reporter_id"] is None and made.json()["mine"] is True
        await alice.aclose()


async def test_with_no_token_configured_the_local_machine_is_a_maintainer(tmp_path: Path) -> None:
    async with feedback_server(tmp_path, token=None) as api:
        made = await api.client.post("/submissions", json=ISSUE)
        assert made.status_code == 201 and made.json()["reporter"] == "local"
        v = made.json()["version"]
        r = await api.client.patch(f"/submissions/{made.json()['id']}", json={"version": v, "status": "Investigating"})
        assert r.status_code == 200 and r.json()["status"] == "Investigating"
        assert (await api.client.get(f"/submissions/{made.json()['id']}/audit")).status_code == 200


# ================================================================================================= validation
async def test_a_bad_submission_is_refused_and_says_which_field(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)

        async def post(body: dict[str, Any]) -> httpx.Response:
            return await alice.post("/submissions", json=body, headers=ah)

        r = await post({**FEATURE, "feature": None})
        assert r.status_code == 422 and "feature" in r.json()["error"]["message"]
        r = await post({**FEATURE, "title": "ab"})
        assert r.status_code == 422 and "title" in r.json()["error"]["message"]
        r = await post({**FEATURE, "title": "x" * 201})
        assert r.status_code == 422
        r = await post({**FEATURE, "description": "x" * 20_001})
        assert r.status_code == 422
        r = await post({**FEATURE, "category": "Not a category"})
        assert r.status_code == 422 and "category must be one of" in r.json()["error"]["message"]
        r = await post({**FEATURE, "related_run_id": "../../etc/passwd"})
        assert r.status_code == 422
        r = await post({**ISSUE, "issue": {**ISSUE["issue"], "frequency": "sometimes"}})
        assert r.status_code == 422
        for forbidden in ("status", "assigned_priority", "severity", "visibility", "reporter_user_id", "id", "version"):
            r = await post({**FEATURE, forbidden: "x"})
            assert r.status_code == 422, f"{forbidden} was accepted when filing"
        assert (await alice.get("/submissions")).json()["total"] == 0, "nothing was stored by a refused request"
        await alice.aclose()


async def test_a_triage_with_a_wrong_value_is_refused_and_changes_nothing(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        made = (await alice.post("/submissions", json=FEATURE, headers=ah)).json()
        v = made["version"]
        patch = f"/submissions/{made['id']}"
        for body in (
            {"version": v, "status": "Closed"},  # an issue status
            {"version": v, "assigned_priority": "urgent"},
            {"version": v, "severity": "huge"},
            {"version": v, "category": "Nope"},
            {"version": v, "assignee_id": alice_id(api)},  # a reporter is not assignable
            {"version": v, "labels": ["<script>"]},
            {"version": v, "unknown": 1},
            {"status": "Planned"},  # no version
            {"version": 0, "status": "Planned"},
        ):
            r = await api.client.patch(patch, json=body)
            assert r.status_code == 422, (body, r.status_code, r.text)
        assert (await api.client.get(patch)).json()["version"] == v
        await alice.aclose()


def alice_id(api: Api) -> str:
    user = accounts(api).by_username("alice")
    assert user is not None
    return user.id


# ======================================================================================= concurrency, limits
async def test_two_maintainers_changing_the_same_submission_at_once_cannot_both_win(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api, "maria")
        nina, nh = await maintainer(api, "nina")
        made = (await alice.post("/submissions", json=FEATURE, headers=ah)).json()
        v = made["version"]
        url = f"/submissions/{made['id']}"
        first, second = await asyncio.gather(
            maria.patch(url, json={"version": v, "status": "Under Review"}, headers=mh),
            nina.patch(url, json={"version": v, "status": "Declined"}, headers=nh),
        )
        assert sorted([first.status_code, second.status_code]) == [200, 409], (first.text, second.text)
        loser = first if first.status_code == 409 else second
        assert loser.json()["error"]["kind"] == "conflict" and "reload" in loser.json()["error"]["message"]
        final = (await maria.get(url)).json()
        assert final["version"] == v + 1 and len(final["history"]) == 2
        for c in (alice, maria, nina):
            await c.aclose()


async def test_too_many_submissions_in_a_day_are_refused_with_when_to_try_again(tmp_path: Path) -> None:
    async with feedback_server(tmp_path, max_submissions_per_day=2) as api:
        alice, ah = await reporter(api)
        for i in range(2):
            assert (
                await alice.post("/submissions", json={**FEATURE, "title": f"Idea {i} here"}, headers=ah)
            ).status_code == 201
        r = await alice.post("/submissions", json={**FEATURE, "title": "Idea 3 here"}, headers=ah)
        assert r.status_code == 429 and r.json()["error"]["kind"] == "rate_limited"
        assert int(r.headers["retry-after"]) > 0 and "2 submissions" in r.json()["error"]["message"]
        # the allowance is the reporter's own, and the shared token has none
        bob, bh = await reporter(api, "bob")
        assert (await bob.post("/submissions", json=FEATURE, headers=bh)).status_code == 201
        assert (await api.client.post("/submissions", json=FEATURE)).status_code == 201
        for c in (alice, bob):
            await c.aclose()


# ========================================================================================= list and search
async def test_the_list_filters_sorts_and_pages(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)
        ids = []
        for i, (kind, category) in enumerate(
            [("feature", "Reporting and Analytics"), ("issue", "Test Execution Failure")] * 3
        ):
            body = {**(FEATURE if kind == "feature" else ISSUE), "title": f"Item number {i}", "category": category}
            r = await alice.post("/submissions", json=body, headers=ah)
            assert r.status_code == 201, r.text
            ids.append(r.json()["id"])
        get = lambda **params: maria.get("/submissions", params=params)  # noqa: E731
        assert (await get()).json()["total"] == 6
        assert (await get(type="feature")).json()["total"] == 3
        assert (await get(category="Test Execution Failure")).json()["total"] == 3
        assert (await get(q="number 4")).json()["total"] == 1
        page = (await get(sort="created", order="asc", limit=4, offset=4)).json()
        assert [i["title"] for i in page["items"]] == ["Item number 4", "Item number 5"] and page["total"] == 6
        v = (await maria.get(f"/submissions/{ids[0]}")).json()["version"]
        await maria.patch(
            f"/submissions/{ids[0]}",
            json={"version": v, "status": "Planned", "assigned_priority": "critical", "labels": ["urgent"]},
            headers=mh,
        )
        assert (await get(status=["Planned", "Needs Triage"])).json()["total"] == 1
        assert (await get(priority="critical")).json()["items"][0]["id"] == ids[0]
        assert (await get(label="urgent")).json()["total"] == 1
        assert (await get(sort="priority")).json()["items"][0]["id"] == ids[0]
        assert (await get(assignee="none")).json()["total"] == 6
        assert (await get(created_from="2999-01-01T00:00:00Z")).json()["total"] == 0
        assert (await get(limit=0)).status_code == 422 and (await get(limit=201)).status_code == 422
        assert (await get(sort="nonsense")).status_code == 422
        for c in (alice, maria):
            await c.aclose()


async def test_similar_titles_are_suggested_and_never_block_filing(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        first = (await alice.post("/submissions", json=FEATURE, headers=ah)).json()
        r = await alice.get("/submissions/similar", params={"type": "feature", "title": "export the reports as csv"})
        assert r.status_code == 200 and [s["id"] for s in r.json()] == [first["id"]] and r.json()[0]["score"] > 0.5
        again = await alice.post("/submissions", json=FEATURE, headers=ah)
        assert again.status_code == 201, "a duplicate suggestion is never a refusal"
        assert (await alice.get("/submissions/similar", params={"type": "feature", "title": "ab"})).status_code == 422
        await alice.aclose()


# ================================================================================================ the contract
async def test_the_api_description_lists_every_feedback_operation_with_its_errors(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        doc = api.app.openapi()
        found = {
            (method.upper(), path): op
            for path, methods in doc["paths"].items()
            if path.startswith("/submissions")
            for method, op in methods.items()
        }
        assert len(found) == 21, sorted(found)
        for (method, path), op in found.items():
            assert op["tags"] == ["Feedback & Support"], (method, path)
            assert op["summary"] and len(op["description"]) > 40, (method, path)
            assert {"401", "403"} <= set(op["responses"]), (method, path)
        assert "409" in found[("PATCH", "/submissions/{submission_id}")]["responses"]
        assert any(t["name"] == "Feedback & Support" for t in doc["tags"])


# ===================================================================== what the specification asked for
async def test_an_issue_report_without_a_description_is_refused_and_a_feature_request_without_one_is_not(
    tmp_path: Path,
) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        refused = await alice.post("/submissions", json={**ISSUE, "description": ""}, headers=ah)
        assert refused.status_code == 422 and "description of the problem" in refused.json()["error"]["message"]
        assert (await alice.post("/submissions", json={**FEATURE, "description": ""}, headers=ah)).status_code == 201
        await alice.aclose()


async def test_the_discussion_and_the_history_are_read_on_their_own_with_the_same_rules(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        bob, bh = await reporter(api, "bob")
        maria, mh = await maintainer(api)
        made = (await alice.post("/submissions", json=ISSUE, headers=ah)).json()
        sid = made["id"]
        await maria.post(
            f"/submissions/{sid}/comments",
            json={"body": "Try again please", "kind": "reproduction_request"},
            headers=mh,
        )
        await maria.post(
            f"/submissions/{sid}/comments", json={"body": "internal only", "visibility": "internal"}, headers=mh
        )
        await maria.patch(
            f"/submissions/{sid}",
            json={"version": made["version"], "status": "Investigating", "status_comment": "Looking"},
            headers=mh,
        )

        mine = (await alice.get(f"/submissions/{sid}/comments")).json()
        assert [(c["author"], c["kind"]) for c in mine] == [("AgentLab maintainer", "reproduction_request")]
        theirs = (await maria.get(f"/submissions/{sid}/comments")).json()
        assert [c["visibility"] for c in theirs] == ["public", "internal"] and theirs[0]["author"] == "maria"
        page = (await maria.get(f"/submissions/{sid}/comments", params={"limit": 1, "offset": 1})).json()
        assert [c["body"] for c in page] == ["internal only"]

        history = (await alice.get(f"/submissions/{sid}/history")).json()
        assert [(h["new_status"], h["changed_by"]) for h in history] == [
            ("Submitted", "alice"),
            ("Investigating", "AgentLab maintainer"),
        ]
        assert history[-1]["comment"] == "Looking"
        for who, h in ((bob, bh), (api.client, {})):
            for tail in ("comments", "history"):
                r = await who.get(f"/submissions/{sid}/{tail}", headers=h)
                if who is api.client:  # the shared token sees an ordinary submission
                    assert r.status_code == 200
                else:
                    assert r.status_code == 404 and r.json()["error"]["message"] == "no such submission"
        assert (await alice.get(f"/submissions/{sid}/comments", params={"limit": 0})).status_code == 422
        for c in (alice, bob, maria):
            await c.aclose()


async def test_the_queue_filters_on_requested_priority_project_version_and_run(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        await alice.post("/submissions", json={**ISSUE, "project_id": "proj-a", "app_version": "0.4.0"}, headers=ah)
        await alice.post(
            "/submissions",
            json={
                **FEATURE,
                "project_id": "proj-b",
                "app_version": "0.5.0",
                "feature": {**FEATURE["feature"], "requested_priority": "low"},
            },
            headers=ah,
        )
        get = lambda **p: api.client.get("/submissions", params=p)  # noqa: E731
        assert (await get(requested_priority="low")).json()["total"] == 1
        assert (await get(project="proj-a")).json()["items"][0]["type"] == "issue"
        assert (await get(version="0.5.0")).json()["items"][0]["type"] == "feature"
        assert (await get(run=ISSUE["related_run_id"])).json()["total"] == 1
        assert (await get(q=ISSUE["related_run_id"])).json()["total"] == 1
        assert (await get(requested_priority="urgent")).status_code == 422
        await alice.aclose()
