"""Diagnostics, attacked: another person's lines, another person's collection, ids that are not the caller's to use,
secrets on their way in and out, and bounded work.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

import httpx

from agentlab.feedback.logcapture import LogCapture
from tests.api.test_feedback_api import ISSUE, feedback_server, maintainer, reporter
from tests.security.test_accounts_security import REPORTER_MAY, operations
from tests.support.api import product_logging

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"
DIAGNOSTIC_OPERATIONS = {
    ("GET", "/diagnostics/available"),
    ("POST", "/diagnostics/preview"),
    ("POST", "/diagnostics/collect"),
    ("POST", "/diagnostics/redact-preview"),
    ("GET", "/diagnostics/{collection_id}"),
    ("GET", "/diagnostics/{collection_id}/download"),
    ("POST", "/submissions/{submission_id}/diagnostics"),
    ("GET", "/submissions/{submission_id}/diagnostics"),
}


def flush_logs() -> None:
    for handler in logging.getLogger("agentlab").handlers:
        if isinstance(handler, LogCapture):
            handler.write_batch()


async def logs_of(client: httpx.AsyncClient, headers: dict[str, str], **extra: Any) -> str:
    flush_logs()
    r = await client.post(
        "/diagnostics/collect", json={"categories": ["application_logs", "errors"], **extra}, headers=headers
    )
    assert r.status_code == 201, r.text
    return "\n".join((i["preview"] or "") for i in r.json()["items"])


# ===================================================================================== who may call what
async def test_the_diagnostic_operations_are_classified_for_a_reporter_and_refused_to_anonymous_callers(
    tmp_path: Path,
) -> None:
    async with feedback_server(tmp_path) as api:
        found = {(m, p) for m, p in operations(api) if p.startswith("/diagnostics") or p.endswith("/diagnostics")}
        assert found == DIAGNOSTIC_OPERATIONS and DIAGNOSTIC_OPERATIONS <= REPORTER_MAY
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=api.app), base_url="http://testserver"
        ) as nobody:
            for method, path in sorted(found):
                url = path.replace("{submission_id}", "x1x1x1").replace("{collection_id}", "x1x1x1x1")
                r = await nobody.request(method, url, json={} if method == "POST" else None)
                assert r.status_code == 401, (method, path, r.status_code)


# ================================================================================================ whose lines
async def test_a_person_never_receives_another_persons_requests_even_in_the_same_minutes(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        a = await alice.get("/test-runs/alice-was-here")  # 403 for a reporter: logged with alice's account
        b = await bob.get("/test-runs/bob-was-here")
        assert a.status_code == b.status_code == 403
        alice_lines = await logs_of(alice, ah)
        bob_lines = await logs_of(bob, bh)
        assert a.headers["x-request-id"] in alice_lines and b.headers["x-request-id"] not in alice_lines
        assert b.headers["x-request-id"] in bob_lines and a.headers["x-request-id"] not in bob_lines
        # the shared token is a different caller again: it sees neither of theirs
        tokens = await logs_of(api.client, {})
        assert a.headers["x-request-id"] not in tokens and b.headers["x-request-id"] not in tokens
        await alice.aclose()
        await bob.aclose()


async def test_naming_someone_elses_request_id_in_a_browser_report_pulls_in_nothing_of_theirs(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        victim = await alice.get("/test-runs/private-to-alice")
        rid = victim.headers["x-request-id"]
        flush_logs()
        mine = await bob.post(
            "/diagnostics/collect",
            json={
                "categories": ["application_logs", "browser"],
                "browser": {
                    "network": [{"method": "GET", "url": "https://app.test/x", "status": 403, "request_id": rid}]
                },
            },
            headers=bh,
        )
        assert mine.status_code == 201
        items = {i["category"]: i for i in mine.json()["items"]}
        assert "private-to-alice" not in (items["application_logs"]["preview"] or "")
        assert rid not in (items["application_logs"]["preview"] or ""), (
            "the id is data the browser reported; it does not select anyone's log lines"
        )
        assert items["browser"]["outcome"] == "collected" and rid in (items["browser"]["preview"] or "")
        for who in (alice, bob):
            await who.aclose()


async def test_a_reporter_cannot_tell_a_real_run_from_a_made_up_one(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        run = await api.run()
        answers = []
        for run_id in (run["id"], "00000000-0000-4000-8000-000000000000"):
            r = await alice.post(
                "/diagnostics/preview",
                json={
                    "categories": ["run_metadata", "test_summary", "trace", "errors"],
                    "run_id": run_id,
                    "test_id": "T-1",
                },
                headers=ah,
            )
            assert r.status_code == 200
            answers.append([(i["category"], i["outcome"], i["detail"], i["size"]) for i in r.json()["items"]])
        assert answers[0] == answers[1], "a real run and a missing one are answered alike"
        assert all(outcome != "collected" for _, outcome, _, _ in answers[0])
        await alice.aclose()


async def test_a_maintainer_asking_for_a_run_gets_that_runs_lines_and_not_the_whole_server(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, _ = await reporter(api, "alice")
        refused = await alice.get("/test-runs/alices-request")
        flush_logs()
        r = await api.client.post(
            "/diagnostics/preview",
            json={"categories": ["application_logs"], "run_id": "00000000-0000-4000-8000-000000000001"},
        )
        assert r.status_code == 200
        text = "\n".join((i["preview"] or "") for i in r.json()["items"])
        assert refused.headers["x-request-id"] not in text, "a maintainer is not handed every person's requests either"
        await alice.aclose()


# ============================================================================================ collections
async def test_a_collection_is_not_reachable_by_another_person_and_a_hidden_one_looks_like_a_missing_one(
    tmp_path: Path,
) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        mine = (await alice.post("/diagnostics/collect", json={"categories": ["environment"]}, headers=ah)).json()
        ghost = "00000000-0000-4000-8000-000000000000"
        for method, tail in (("GET", ""), ("GET", "/download")):
            real = await bob.request(method, f"/diagnostics/{mine['id']}{tail}")
            none = await bob.request(method, f"/diagnostics/{ghost}{tail}")
            assert real.status_code == none.status_code == 404 and real.json() == none.json()
        sid = (await bob.post("/submissions", json=ISSUE, headers=bh)).json()["id"]
        stolen = await bob.post(
            f"/submissions/{sid}/diagnostics", json={"collection_id": mine["id"], "consent": True}, headers=bh
        )
        missing = await bob.post(
            f"/submissions/{sid}/diagnostics", json={"collection_id": ghost, "consent": True}, headers=bh
        )
        assert stolen.status_code == missing.status_code == 404 and stolen.json() == missing.json()
        assert (await alice.get(f"/diagnostics/{mine['id']}")).status_code == 200
        await alice.aclose()
        await bob.aclose()


async def test_a_collection_cannot_be_attached_to_a_submission_the_person_may_not_see(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        theirs = (await bob.post("/submissions", json=ISSUE, headers=bh)).json()["id"]
        draft = (await alice.post("/diagnostics/collect", json={"categories": ["environment"]}, headers=ah)).json()
        r = await alice.post(
            f"/submissions/{theirs}/diagnostics", json={"collection_id": draft["id"], "consent": True}, headers=ah
        )
        assert r.status_code == 404
        assert (await alice.get(f"/diagnostics/{draft['id']}")).json()["status"] == "draft"
        await alice.aclose()
        await bob.aclose()


# ================================================================================================ secrets
async def test_a_secret_pasted_into_a_browser_report_or_a_preview_never_comes_back_or_reaches_the_log(
    tmp_path: Path,
) -> None:
    with product_logging() as log:
        async with feedback_server(tmp_path) as api:
            alice, ah = await reporter(api)
            body = {
                "categories": ["browser", "environment"],
                "browser": {
                    "console": [
                        {"message": f"Authorization: Bearer {OPENAI}", "source": f"https://x.test/a.js?key={OPENAI}"}
                    ],
                    "network": [
                        {
                            "method": "POST",
                            "url": f"https://x.test/api?access_token={OPENAI}&q=1",
                            "error": f"bad key {OPENAI}",
                        }
                    ],
                },
            }
            seen = []
            preview = await alice.post("/diagnostics/preview", json=body, headers=ah)
            collected = await alice.post("/diagnostics/collect", json=body, headers=ah)
            seen += [preview.text, collected.text]
            seen.append((await alice.get(f"/diagnostics/{collected.json()['id']}")).text)
            seen.append(
                (await alice.post("/diagnostics/redact-preview", json={"text": f"key={OPENAI}"}, headers=ah)).text
            )
            stored = api.services.artifacts
            for artifact in stored.list():
                seen.append(stored.get(artifact.id).decode("utf-8", errors="replace"))
            await alice.aclose()
    for where, blob in (*[(f"response {i}", t) for i, t in enumerate(seen)], ("log", log.getvalue())):
        assert OPENAI not in blob and "Zq81LmN4xW7sR2pK9vB3cD6e" not in blob, where


# ============================================================================================== bounded work
async def test_hostile_requests_are_bounded(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        many = [{"message": "x"} for _ in range(2001)]
        r = await alice.post(
            "/diagnostics/preview", json={"categories": ["browser"], "browser": {"console": many}}, headers=ah
        )
        assert r.status_code == 422
        ok = [{"message": f"error {n}", "source": "https://x.test/" + "a" * 250} for n in range(2000)]
        started = time.perf_counter()
        r = await alice.post(
            "/diagnostics/preview", json={"categories": ["browser"], "browser": {"console": ok}}, headers=ah
        )
        assert r.status_code == 200 and time.perf_counter() - started < 15
        console = next(i for i in r.json()["items"] if i["category"] == "browser")
        assert console["size"] <= 512 * 1024, "an item is cut to the size limit"
        text = "a" * 199_999 + "="  # the longest text a preview takes, and a hostile shape
        started = time.perf_counter()
        r = await alice.post("/diagnostics/redact-preview", json={"text": text}, headers=ah)
        assert r.status_code == 200 and time.perf_counter() - started < 15
        too_long = await alice.post("/diagnostics/redact-preview", json={"text": "a" * 200_001}, headers=ah)
        assert too_long.status_code == 422
        await alice.aclose()


async def test_the_collection_allowance_is_per_person(tmp_path: Path) -> None:
    from agentlab.core.config import DiagnosticsConfig

    async with feedback_server(tmp_path, diagnostics=DiagnosticsConfig(max_collections_per_hour=2)) as api:
        alice, ah = await reporter(api, "alice")
        bob, bh = await reporter(api, "bob")
        body = {"categories": ["environment"]}
        assert (await alice.post("/diagnostics/collect", json=body, headers=ah)).status_code == 201
        assert (await alice.post("/diagnostics/collect", json=body, headers=ah)).status_code == 201
        refused = await alice.post("/diagnostics/collect", json=body, headers=ah)
        assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 0
        assert (await bob.post("/diagnostics/collect", json=body, headers=bh)).status_code == 201
        assert (await alice.post("/diagnostics/preview", json=body, headers=ah)).status_code == 200
        maria, _ = await maintainer(api)
        for c in (alice, bob, maria):
            await c.aclose()
