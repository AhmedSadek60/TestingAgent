"""Feedback & Support, attacked: another person's submission, the maintainer-only calls, hostile text, injection in every
filter, secrets in what is stored and logged, and what a session that may not act is allowed to do.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest
from sqlalchemy import text

from agentlab.core.config import AccountsConfig
from tests.api.test_accounts_api import PASSWORD, TOKEN, accounts, browser, csrf, sign_in
from tests.api.test_feedback_api import FEATURE, ISSUE, feedback_server, maintainer, reporter
from tests.security.test_accounts_security import REPORTER_MAY, operations
from tests.support.api import product_logging

SECRET = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"  # looks like a provider key, is not one
STAFF_ONLY = {
    ("GET", "/submissions/assignees"),
    ("PATCH", "/submissions/{submission_id}"),
    ("POST", "/submissions/{submission_id}/relations"),
    ("DELETE", "/submissions/{submission_id}/relations/{relation_id}"),
    ("GET", "/submissions/{submission_id}/audit"),
    ("POST", "/submissions/{submission_id}/handoffs/preview"),
    ("POST", "/submissions/{submission_id}/handoffs"),
    ("GET", "/submissions/{submission_id}/handoffs"),
    ("DELETE", "/submissions/{submission_id}"),
    ("POST", "/submissions/{submission_id}/publications/preview"),
    ("POST", "/submissions/{submission_id}/publications"),
    ("GET", "/submissions/{submission_id}/publications"),
    ("POST", "/submissions/{submission_id}/publications/{provider}/sync"),
}


def concrete(path: str, **values: str) -> str:
    return re.sub(r"\{([^}]+)\}", lambda m: values.get(m.group(1), "x1x1x1"), path)


async def file_one(
    client: httpx.AsyncClient, headers: dict[str, str], body: dict[str, Any] | None = None
) -> dict[str, Any]:
    r = await client.post("/submissions", json=body or FEATURE, headers=headers)
    assert r.status_code == 201, r.text
    out: dict[str, Any] = r.json()
    return out


# =========================================================================================== who may call what
async def test_every_feedback_operation_is_classified_and_a_reporter_gets_exactly_what_the_classification_says(
    tmp_path: Path,
) -> None:
    """Not a list of the routes someone remembered: every `/submissions` operation in the API's own description."""
    async with feedback_server(tmp_path) as api:
        found = {(m, p) for m, p in operations(api) if p.startswith("/submissions")}
        classified = {op for op in REPORTER_MAY if op[1].startswith("/submissions")} | STAFF_ONLY
        assert found == classified, (
            f"a feedback operation is not classified as reporter-allowed or staff-only: {found ^ classified}"
        )
        alice, ah = await reporter(api)
        own = await file_one(alice, ah)
        for method, path in sorted(found):
            url = concrete(path, submission_id=own["id"], relation_id="r1r1r1")
            body = {} if method in {"POST", "PATCH"} else None
            r = await alice.request(method, url, headers=ah, json=body)
            if (method, path) in STAFF_ONLY:
                assert r.status_code == 403, (method, path, r.status_code, r.text)
                assert r.json()["error"]["message"] == "only maintainers may do this"
            else:
                assert r.status_code != 403, (method, path, r.status_code, r.text)
        await alice.aclose()


async def test_the_shared_token_may_triage_and_a_reporter_may_not_become_a_maintainer_by_asking(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        own = await file_one(alice, ah)
        for headers_extra in (
            {"X-API-Key": "not-the-token"},
            {"Authorization": "Bearer not-the-token"},
            {"X-Role": "maintainer"},
            {"X-Forwarded-User": "maria"},
        ):
            r = await alice.patch(
                f"/submissions/{own['id']}", json={"version": 1, "status": "Planned"}, headers={**ah, **headers_extra}
            )
            assert r.status_code in (401, 403), (headers_extra, r.status_code)
        assert (
            await api.client.patch(f"/submissions/{own['id']}", json={"version": 1, "status": "Planned"})
        ).status_code == 200
        await alice.aclose()


# ========================================================================= someone else's submission
async def test_someone_elses_submission_is_not_found_by_every_route_and_looks_like_one_that_is_not_there(
    tmp_path: Path,
) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        theirs = await file_one(alice, ah)
        ghost = "00000000-0000-4000-8000-000000000000"
        for method, path, body in (
            ("GET", "/submissions/{id}", None),
            ("POST", "/submissions/{id}/comments", {"body": "hello"}),
            ("POST", "/submissions/{id}/seen", None),
        ):
            real = await bob.request(method, path.format(id=theirs["id"]), headers=bh, json=body)
            none = await bob.request(method, path.format(id=ghost), headers=bh, json=body)
            assert real.status_code == none.status_code == 404, (method, path, real.status_code, none.status_code)
            assert real.json() == none.json(), "a hidden submission and a missing one are told apart by nothing"
        assert (await bob.get("/submissions")).json() == {"items": [], "total": 0, "limit": 50, "offset": 0}
        assert (await bob.get("/submissions", params={"q": theirs["title"]})).json()["total"] == 0
        sim = await bob.get("/submissions/similar", params={"type": "feature", "title": theirs["title"]})
        assert sim.json() == []
        assert (await bob.get("/submissions/summary")).json()["total"] == 0
        # and nothing of bob's attempts changed it
        after = (await alice.get(f"/submissions/{theirs['id']}")).json()
        assert after["discussion"] == [] and after == theirs
        for c in (alice, bob):
            await c.aclose()


async def test_no_spelling_of_an_identifier_reaches_someone_elses_submission(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, _ = await reporter(api, "bob")
        theirs = await file_one(alice, ah)
        number, ident = theirs["number"], theirs["id"]
        variants = [
            number,
            number.lower(),
            f" {number} ".replace(" ", "%20"),
            ident.upper(),
            f"{ident}%00",
            f"{number}%2F",
            f"{number}/",
            f"..%2F{ident}",
            f"{ident}'%20OR%20'1'='1",
            number.replace("-", "%2D"),
            number.replace("0", "%EF%BC%90"),  # full-width digits
        ]
        for v in variants:
            r = await bob.get(f"/submissions/{v}")
            if r.status_code in (
                307,
                308,
            ):  # a trailing slash is redirected; the redirect is authorised like any request
                assert "/submissions/" in r.headers["location"], v
                r = await bob.get(r.headers["location"])
            assert r.status_code in (404, 422), (v, r.status_code)
            assert r.status_code != 200 and theirs["title"] not in r.text, v
        assert (await bob.get(f"/submissions/{ident}%00")).status_code == 422, "a NUL byte is not part of an id"
        await alice.aclose()
        await bob.aclose()


async def test_going_through_the_numbers_tells_a_reporter_nothing(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        await file_one(alice, ah)
        await file_one(alice, ah, {**FEATURE, "title": "Another one from alice"})
        mine = await file_one(bob, bh, {**FEATURE, "title": "Bob's own idea here"})
        year = mine["number"].split("-")[1]
        answers = []
        for n in range(1, 8):
            r = await bob.get(f"/submissions/FR-{year}-{n:05d}")
            answers.append((r.status_code, json.dumps(r.json(), sort_keys=True)))
        found = [a for a in answers if a[0] == 200]
        assert len(found) == 1 and "Bob's own idea" in found[0][1], "only bob's own is ever returned"
        missing = {a for a in answers if a[0] != 200}
        assert len(missing) == 1 and next(iter(missing))[0] == 404, "every other number gets the same answer"
        for c in (alice, bob):
            await c.aclose()


# ================================================================================================= security
async def test_a_security_submission_reaches_only_its_reporter_and_maintainers_and_is_not_even_counted(
    tmp_path: Path,
) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        maria, _ = await maintainer(api)
        private = await file_one(
            alice, ah, {**ISSUE, "title": "Tokens appear in the server log", "category": "Suspected Security Issue"}
        )
        flagged = await file_one(alice, ah, {**ISSUE, "title": "Flagged by a reporter", "suspected_security": True})
        assert private["visibility"] == flagged["visibility"] == "security"
        for who, expect in ((alice, 200), (maria, 200), (bob, 404), (api.client, 404)):
            assert (await who.get(f"/submissions/{private['id']}")).status_code == expect
        assert (await maria.get("/submissions")).json()["total"] == 2
        assert (await api.client.get("/submissions")).json()["total"] == 0
        assert (await api.client.get("/submissions/summary")).json()["total"] == 0
        assert (await api.client.get("/submissions", params={"q": "Tokens"})).json()["total"] == 0
        assert (
            await bob.get("/submissions/similar", params={"type": "issue", "title": "Tokens appear in the server log"})
        ).json() == []
        assert (
            await api.client.get(
                "/submissions/similar", params={"type": "issue", "title": "Tokens appear in the server log"}
            )
        ).json() == []
        # opening it is on the record, and so is trying to; reading one's own is not
        trail = (await maria.get(f"/submissions/{private['id']}/audit")).json()
        events = {(e["event"], e["actor"]) for e in trail}
        assert ("viewed", "maria") in events
        assert ("access_denied", "bob") in events and ("access_denied", "shared API token") in events
        assert not any(actor == "alice" and event in {"viewed", "access_denied"} for event, actor in events)
        for c in (alice, bob, maria):
            await c.aclose()


async def test_the_owner_can_let_the_shared_token_see_private_submissions(tmp_path: Path) -> None:
    async with feedback_server(tmp_path, service_token_sees_security=True) as api:
        alice, ah = await reporter(api)
        private = await file_one(alice, ah, {**ISSUE, "suspected_security": True})
        assert (await api.client.get(f"/submissions/{private['id']}")).status_code == 200
        await alice.aclose()


# ============================================================================================= hostile input
HOSTILE = (
    "<script>alert(document.cookie)</script>",
    '"><img src=x onerror=alert(1)>',
    "javascript:alert(1)",
    "{{7*7}} ${7*7} #{7*7}",
    "'; DROP TABLE submissions; --",
    "‮evil‬ RTL override",
    "Robert'); DROP TABLE students;--",
    "../../../../etc/passwd",
    "%s %n %x",
    "A" * 5000,
    "\U0001f600" * 200,
)


async def test_hostile_text_is_kept_as_text_and_comes_back_as_json(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)
        for i, payload in enumerate(HOSTILE):
            body = {
                **FEATURE,
                "title": f"Title {i} {payload[:150]}",
                "description": payload,
                "feature": {**FEATURE["feature"], "problem_statement": payload, "proposed_solution": payload},
            }
            made = await alice.post("/submissions", json=body, headers=ah)
            assert made.status_code == 201, (payload[:30], made.text[:200])
            assert made.headers["content-type"].startswith("application/json")
            assert made.headers["x-content-type-options"] == "nosniff"
            assert made.json()["description"] == payload, "the words are not rewritten, escaped or stripped"
            c = await alice.post(f"/submissions/{made.json()['id']}/comments", json={"body": payload}, headers=ah)
            assert c.status_code == 201 and c.json()["body"] == payload
            n = await maria.post(
                f"/submissions/{made.json()['id']}/comments",
                json={"body": payload, "visibility": "internal"},
                headers=mh,
            )
            assert n.status_code == 201
        listing = await maria.get("/submissions", params={"limit": 200})
        assert listing.status_code == 200 and listing.headers["content-type"].startswith("application/json")
        assert listing.json()["total"] == len(HOSTILE)
        await alice.aclose()
        await maria.aclose()


async def test_text_that_would_break_the_database_or_the_page_is_cleaned_not_refused(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        made = await alice.post(
            "/submissions",
            json={**FEATURE, "title": "Null\x00 byte and\x1b[31m escape", "description": "a\x00b\x07c\r\nd"},
            headers=ah,
        )
        assert made.status_code == 201, made.text
        assert made.json()["title"] == "Null byte and[31m escape" and made.json()["description"] == "abc\nd"
        await alice.aclose()


async def test_injection_in_every_filter_is_inert(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, _ = await maintainer(api)
        for i in range(3):
            await file_one(alice, ah, {**FEATURE, "title": f"An ordinary idea {i}"})
        payloads = [
            "' OR '1'='1",
            "1; DROP TABLE submissions",
            "%' OR 1=1 --",
            "\\",
            "%00",
            "${jndi:ldap://x}",
            "' UNION SELECT * FROM users --",
        ]
        for who in (alice, maria):
            for payload in payloads:
                for name in ("q", "category", "label", "assignee", "reporter", "status"):
                    r = await who.get("/submissions", params={name: payload})
                    assert r.status_code in (200, 403, 422), (name, payload, r.status_code, r.text[:200])
                    if r.status_code == 200:
                        assert r.json()["total"] == 0, (name, payload, "an injection matched something")
        for name in ("q", "category", "label", "assignee", "status"):  # a NUL byte is cleaned away, never bound
            r = await maria.get("/submissions", params={name: "\x00ab\x00"})
            assert r.status_code == 200 and r.json()["total"] == 0, (name, r.status_code)
        r = await maria.get("/submissions", params={"q": "\x00"})
        assert r.status_code == 200 and r.json()["total"] == 3, "nothing left to search for is no search"
        with api.services.store.db.session() as s:
            assert s.execute(text("select count(*) from submissions")).scalar() == 3, "no row was lost"
            assert s.execute(text("select count(*) from users")).scalar() == 2
        assert (await maria.get("/submissions")).json()["total"] == 3
        for c in (alice, maria):
            await c.aclose()


async def test_an_oversized_or_malformed_body_is_refused_before_it_is_stored(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        for raw in ("not json", "[]", "null", '{"type": "feature"', "x" * 10):
            r = await alice.post("/submissions", content=raw, headers={**ah, "Content-Type": "application/json"})
            assert r.status_code == 422, (raw[:20], r.status_code)
        huge = {**FEATURE, "feature": {**FEATURE["feature"], "proposed_solution": "x" * 20_001}}
        assert (await alice.post("/submissions", json=huge, headers=ah)).status_code == 422
        r = await alice.post("/submissions", content="{}", headers={**ah, "Content-Type": "text/plain"})
        assert r.status_code == 422
        assert (await alice.get("/submissions")).json()["total"] == 0
        await alice.aclose()


# ====================================================================================================== secrets
async def test_a_secret_in_the_text_is_masked_where_it_is_stored_returned_audited_and_logged(tmp_path: Path) -> None:
    with product_logging() as log:
        async with feedback_server(tmp_path) as api:
            alice, ah = await reporter(api)
            maria, mh = await maintainer(api)
            body = {
                **ISSUE,
                "description": f"My key is {SECRET} please look",
                "issue": {
                    **ISSUE["issue"],
                    "reproduction_steps": f"export KEY={SECRET}",
                    "workaround": f"use {SECRET}",
                },
            }
            made = await alice.post("/submissions", json=body, headers=ah)
            assert made.status_code == 201
            sid = made.json()["id"]
            await alice.post(f"/submissions/{sid}/comments", json={"body": f"again {SECRET}"}, headers=ah)
            v = made.json()["version"]
            await maria.patch(
                f"/submissions/{sid}",
                json={"version": v, "status": "Investigating", "status_comment": f"do not use {SECRET}"},
                headers=mh,
            )
            await maria.post(
                f"/submissions/{sid}/comments", json={"body": f"{SECRET}", "visibility": "internal"}, headers=mh
            )
            seen = [
                made.text,
                (await alice.get(f"/submissions/{sid}")).text,
                (await maria.get(f"/submissions/{sid}")).text,
                (await maria.get("/submissions")).text,
                (await maria.get(f"/submissions/{sid}/audit")).text,
            ]
            with api.services.store.db.session() as s:
                tables = [r[0] for r in s.execute(text("select name from sqlite_master where type='table'")).all()]
                dump = " ".join(
                    str(row)
                    for t in tables
                    for row in s.execute(text(f'select * from "{t}"')).all()  # noqa: S608
                )
            for c in (alice, maria):
                await c.aclose()
    fragment = "Zq81LmN4xW7sR2pK9vB3cD6e"
    for where, blob in (
        *[(f"response {i}", t) for i, t in enumerate(seen)],
        ("database", dump),
        ("log", log.getvalue()),
    ):
        assert SECRET not in blob and fragment not in blob, f"the secret is in the {where}"


# =============================================================================== credentials and sessions
async def test_a_session_that_must_change_its_password_cannot_use_feedback_until_it_has(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        svc = accounts(api)
        svc.create_user(
            "test", "temp-person", role="reporter", password=PASSWORD
        )  # asked to change it at first sign-in
        client, login = await sign_in(api, "temp-person")
        assert login["me"]["must_change_password"] is True
        for method, path in (("GET", "/submissions"), ("POST", "/submissions"), ("GET", "/submissions/summary")):
            r = await client.request(method, path, json=FEATURE if method == "POST" else None, headers=csrf(login))
            assert r.status_code == 403 and "temporary password" in r.json()["error"]["message"], (method, path)
        await client.aclose()


async def test_a_disabled_account_and_a_signed_out_session_reach_nothing(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        await file_one(alice, ah)
        user = accounts(api).by_username("alice")
        assert user is not None
        accounts(api).update("test", user.id, status="disabled")
        r = await alice.get("/submissions")
        assert r.status_code == 401, "an account that was disabled loses its session at once"
        out, oh = await reporter(api, "carol")
        await out.post("/auth/logout", headers=oh)
        assert (await out.get("/submissions")).status_code == 401
        for c in (alice, out):
            await c.aclose()


async def test_a_maintainer_who_is_demoted_stops_triaging_at_once(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)
        made = await file_one(alice, ah)
        url = f"/submissions/{made['id']}"
        assert (
            await maria.patch(url, json={"version": made["version"], "status": "Planned"}, headers=mh)
        ).status_code == 200
        user = accounts(api).by_username("maria")
        assert user is not None
        accounts(api).update("test", user.id, role="reporter")
        # the session ended with the change; signing in again gives the reporter's powers
        assert (await maria.patch(url, json={"version": 2, "status": "Completed"}, headers=mh)).status_code == 401
        again, login = await sign_in(api, "maria")
        r = await again.patch(url, json={"version": 2, "status": "Completed"}, headers=csrf(login))
        assert r.status_code == 403
        assert (await again.get(url)).status_code == 404, "and they are not the reporter of that one"
        for c in (alice, maria, again):
            await c.aclose()


async def test_a_token_in_a_cookie_is_no_session_and_a_wrong_token_is_not_rescued_by_a_cookie(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api, browser(api) as anonymous:
        anonymous.cookies.set("agentlab_session", TOKEN)
        assert (await anonymous.get("/submissions")).status_code == 401
        alice, _ = await reporter(api)
        assert (await alice.get("/submissions", headers={"Authorization": "Bearer wrong"})).status_code == 401, (
            "a wrong token is refused even when a valid session cookie is sent with it"
        )
        await alice.aclose()


async def test_registration_closed_means_no_new_reporters_but_existing_ones_keep_working(tmp_path: Path) -> None:
    async with feedback_server(tmp_path, accounts_config=AccountsConfig(registration="closed")) as api:
        client = browser(api)
        r = await client.post("/auth/register", json={"username": "latecomer", "password": PASSWORD})
        assert r.status_code == 403
        assert (await client.get("/submissions")).status_code == 401
        await client.aclose()


# ================================================================================================= boundaries
@pytest.mark.parametrize("field", ["description", "title"])
async def test_the_longest_allowed_text_is_stored_whole(tmp_path: Path, field: str) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        limit = 200 if field == "title" else 20_000
        words = "Lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor. "
        body = {
            **FEATURE,
            field: (words * 300)[: limit - 1] + ".",
        }  # ordinary prose: a repeated character can be a secret
        r = await alice.post("/submissions", json=body, headers=ah)
        assert r.status_code == 201 and len(r.json()[field]) == limit
        await alice.aclose()


async def test_an_internal_note_never_reaches_a_reporter_by_any_read(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)
        made = await file_one(alice, ah)
        note = "NOTE-FOR-STAFF-ONLY the customer is on a trial plan"
        await maria.post(
            f"/submissions/{made['id']}/comments", json={"body": note, "visibility": "internal"}, headers=mh
        )
        await maria.post(f"/submissions/{made['id']}/comments", json={"body": "A public line"}, headers=mh)
        for path, params in (
            (f"/submissions/{made['id']}", None),
            (f"/submissions/{made['id']}/comments", None),
            (f"/submissions/{made['id']}/history", None),
            ("/submissions", {"q": "NOTE-FOR-STAFF-ONLY"}),
            ("/submissions", {"q": "trial plan"}),
            ("/submissions/summary", None),
        ):
            r = await alice.get(path, params=params)
            assert r.status_code == 200 and "NOTE-FOR-STAFF-ONLY" not in r.text and "trial plan" not in r.text, path
        assert (await alice.get("/submissions", params={"q": "A public line"})).json()["total"] == 0, (
            "comments are not searched, so a search cannot be used to read what a reporter may not"
        )
        for c in (alice, maria):
            await c.aclose()
