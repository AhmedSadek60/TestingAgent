"""Deleting a submission over HTTP: who may, what is asked for, and what is answered.

Every secret below is invented and assembled from fragments so no scanner mistakes this file for a leak."""

from __future__ import annotations

import json
from pathlib import Path

from sqlalchemy import text

from agentlab.core.config import RetentionConfig
from tests.api.test_accounts_api import PASSWORD, _own_password, accounts, csrf, sign_in
from tests.api.test_feedback_api import ISSUE, feedback_server, maintainer, reporter

OPENAI = "sk-" + "proj" + "-" + "Zq81LmN4xW7sR2pK9vB3cD6e"


async def administrator(api, name: str = "root"):  # type: ignore[no-untyped-def]
    svc = accounts(api)
    user, _ = svc.create_user("test", name, role="admin", password=PASSWORD)
    _own_password(svc, user.id)
    client, login = await sign_in(api, name)
    return client, csrf(login)


async def seeded(api, alice, ah, **extra):  # type: ignore[no-untyped-def]
    made = await alice.post("/submissions", json={**ISSUE, **extra}, headers=ah)
    assert made.status_code == 201, made.text
    sid = made.json()["id"]
    up = await alice.post(
        f"/submissions/{sid}/attachments", files={"file": ("run.log", b"INFO started\n", "text/plain")}, headers=ah
    )
    assert up.status_code == 201, up.text
    return made.json()


async def test_an_administrator_deletes_a_submission_and_everyone_else_is_refused(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        maria, mh = await maintainer(api)
        root, rh = await administrator(api)
        made = await seeded(api, alice, ah)
        sid = made["id"]
        url = f"/submissions/{sid}"
        # a reporter and a maintainer are told only an administrator may; the shared token is a maintainer here
        denied = await alice.delete(url, params={"reason": "no longer needed"}, headers=ah)
        assert denied.status_code == 403 and denied.json()["error"]["message"] == "only maintainers may do this"
        for who, headers in ((maria, mh), (api.client, None)):
            r = await who.delete(url, params={"reason": "no longer needed"}, headers=headers)
            assert r.status_code == 403, (r.status_code, r.text)
            assert r.json()["error"]["message"] == "only an administrator may delete a submission"
        assert (await alice.get(url)).status_code == 200, "nothing was deleted by a refusal"

        short = await root.delete(url, params={"reason": "x"}, headers=rh)
        assert short.status_code == 422
        # a cookie session must prove the request came from the page
        forged = await root.delete(url, params={"reason": "erasure requested"})
        assert forged.status_code == 403
        done = await root.delete(url, params={"reason": f"erasure requested, they pasted {OPENAI}"}, headers=rh)
        assert done.status_code == 200, done.text
        body = done.json()
        assert body["number"] == made["number"] and body["attachments"] == 1 and body["files_deleted"] >= 1
        assert OPENAI not in done.text

        for who, headers in ((alice, ah), (root, rh), (maria, mh)):
            gone = await who.get(url, headers=headers)
            assert gone.status_code == 404
        assert (await alice.get("/submissions", headers=ah)).json()["total"] == 0
        trail = api.app.state.agentlab.feedback.db.session()
        with trail as s:
            detail = s.execute(
                text("select detail from submission_audit_events where event = 'submission_deleted'")
            ).scalar()
        detail = json.loads(detail) if isinstance(detail, str) else detail
        assert OPENAI not in json.dumps(detail) and detail["number"] == made["number"]
        for client in (alice, maria, root):
            await client.aclose()


async def test_a_private_security_report_that_is_not_closed_needs_to_be_acknowledged(tmp_path: Path) -> None:
    async with feedback_server(tmp_path) as api:
        alice, ah = await reporter(api)
        root, rh = await administrator(api)
        made = await seeded(api, alice, ah, suspected_security=True)
        url = f"/submissions/{made['id']}"
        refused = await root.delete(url, params={"reason": "requested by the reporter"}, headers=rh)
        assert refused.status_code == 422 and "private security report that is not closed" in refused.text
        assert (await root.get(url, headers=rh)).status_code == 200
        done = await root.delete(
            url, params={"reason": "requested by the reporter", "acknowledge_open_security_report": "true"}, headers=rh
        )
        assert done.status_code == 200, done.text
        await alice.aclose()
        await root.aclose()


async def test_an_unknown_submission_is_not_found_and_the_deletion_is_in_the_api_description(tmp_path: Path) -> None:
    async with feedback_server(tmp_path, retention=RetentionConfig(attachments_days=30)) as api:
        root, rh = await administrator(api)
        ghost = "00000000-0000-4000-8000-000000000000"
        r = await root.delete(f"/submissions/{ghost}", params={"reason": "cleaning up"}, headers=rh)
        assert r.status_code == 404
        doc = api.app.openapi()
        op = doc["paths"]["/submissions/{submission_id}"]["delete"]
        assert op["tags"] == ["Feedback & Support"] and "Administrators only" in op["description"]
        assert {"401", "403", "404"} <= set(op["responses"])
        await root.aclose()


async def test_the_server_applies_the_policy_by_itself_and_a_failed_pass_does_not_stop_it() -> None:
    import asyncio
    from datetime import UTC, datetime

    from agentlab.api.app import retention_forever
    from agentlab.feedback.models import RetentionReport

    passes: list[int] = []

    class Service:
        def run(self) -> RetentionReport:
            passes.append(1)
            if len(passes) == 1:
                raise RuntimeError("the disk is full")
            return RetentionReport(
                ran_at=datetime.now(UTC),
                dry_run=False,
                submissions=0,
                attachments=0,
                diagnostic_items=0,
                handoffs=0,
                internal_notes=0,
                files_deleted=0,
            )

    task = asyncio.create_task(retention_forever(Service(), 0.01, first_after=0.0))  # type: ignore[arg-type]
    for _ in range(200):
        if len(passes) >= 3:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert len(passes) >= 3, "the first pass failed and the next ones still ran"


async def test_the_server_starts_the_loop_only_when_a_period_is_set(tmp_path: Path) -> None:
    import asyncio

    def loops() -> list[asyncio.Task[None]]:
        return [t for t in asyncio.all_tasks() if t.get_name() == "agentlab-api-retention"]

    async with feedback_server(tmp_path):
        assert loops() == [], "no period is set, so nothing is scheduled"
    async with feedback_server(tmp_path / "again", retention=RetentionConfig(submissions_days=365)):
        assert len(loops()) == 1
    async with feedback_server(tmp_path / "manual", retention=RetentionConfig(submissions_days=365, automatic=False)):
        assert loops() == [], "automatic is off: only the command applies the policy"
