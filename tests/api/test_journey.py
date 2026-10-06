"""A person's whole journey through the REST API: design a plan, approve it, run it, watch it, read what it found, review a
result, get reports and compare two runs. Everything goes through HTTP; nothing is reached into except to prove that the
original evaluation survived a human review."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from tests.support.api import SMALL_RUN, mock_target, running_api


async def test_plan_then_approved_run_then_everything_that_can_be_read_about_it(tmp_path: Path) -> None:
    async with running_api(tmp_path) as api:
        c = api.client

        # ---- 1. an explainable plan, before anything is executed -------------------------------------------------
        posted = await c.post("/test-plans", json={"target": mock_target(), "options": SMALL_RUN, "wait_seconds": 30})
        assert posted.status_code == 200, posted.text  # 200: it was ready in time (202 would mean "keep polling")
        plan_doc = posted.json()
        assert plan_doc["status"] == "completed"
        plan = plan_doc["plan"]
        assert plan["tests"], "a plan lists its tests"
        runnable = [t for t in plan["tests"] if t["selected"] and t["predicted"] == "runnable"]
        assert len(runnable) >= 10
        assert all(t["reasons"] for t in plan["tests"]), "every planned test says why it is there"
        assert plan["coverage"] and plan["budget"]["tests"] and plan["summary"]
        assert plan_doc["profile"]["interfaces"], "what discovery learned travels with the plan"
        again = (await c.get(f"/test-plans/{plan_doc['run_id']}")).json()
        assert again["plan"]["plan_hash"] == plan["plan_hash"]

        # nothing was executed for a plan, and it is listed as a plan
        listed = (await c.get("/test-runs", params={"kind": "plan"})).json()
        assert [r["id"] for r in listed] == [plan_doc["run_id"]]
        assert listed[0]["kind"] == "plan" and listed[0]["status"] == "completed"
        assert (await c.get(f"/test-runs/{plan_doc['run_id']}/results")).json() == []

        # ---- 2. approve it (minus two tests) and run exactly that -----------------------------------------------
        dropped = [runnable[0]["test"]["id"], runnable[1]["test"]["id"]]
        accepted = await c.post(
            "/test-runs", json={"plan_id": plan_doc["run_id"], "deselect": dropped, "project": "journey"}
        )
        assert accepted.status_code == 202, accepted.text
        ack = accepted.json()
        assert ack["kind"] == "run" and ack["status"] == "pending" and ack["queue"] == "inline"
        run_id = ack["run_id"]
        assert ack["links"]["results"] == f"/test-runs/{run_id}/results"
        final = await api.wait(run_id)
        assert final["status"] == "completed", final["error"]
        assert final["kind"] == "run" and final["project"] == "journey"
        assert final["started_at"] and final["finished_at"]
        progress = final["progress"]
        assert progress["tests_done"] == progress["tests_total"] > 0
        assert (
            progress["passed"] + progress["failed"] + progress["blocked"] + progress["errors"] + progress["skipped"]
            == (progress["tests_done"])
        )
        assert progress["phase"] is None and progress["running_tests"] == []
        assert final["overall"] is not None and final["grade"]

        # ---- 3. the run executed the plan that was approved, not another one --------------------------------------
        results = (await c.get(f"/test-runs/{run_id}/results")).json()
        executed = {r["test_id"] for r in results}
        approved = {t["test"]["id"] for t in plan["tests"] if t["selected"]} - set(dropped)
        assert not executed & set(dropped), "a deselected test must not run"
        assert approved <= executed, f"approved tests that never ran: {sorted(approved - executed)[:5]}"
        assert final["manifest"]["plan"]["hash"] or final["manifest"], "the manifest says what was run"

        failed = (await c.get(f"/test-runs/{run_id}/results", params={"status": "failed"})).json()
        assert failed and all(r["status"] == "failed" for r in failed)
        one = (await c.get(f"/test-runs/{run_id}/results/{failed[0]['test_id']}")).json()
        assert one["id"] == failed[0]["id"] and one["attempts"], "a result carries its attempts"
        by_text = (await c.get(f"/test-runs/{run_id}/results", params={"q": failed[0]["test_id"][:12].lower()})).json()
        assert failed[0]["test_id"] in {r["test_id"] for r in by_text}
        assert (await c.get(f"/test-runs/{run_id}/results/NO-SUCH-TEST")).status_code == 404

        findings = (await c.get(f"/test-runs/{run_id}/findings")).json()
        assert findings
        order = ["critical", "high", "medium", "low", "info"]
        ranks = [order.index(f["severity"]) for f in findings]
        assert ranks == sorted(ranks), "findings come most severe first"
        for f in findings:
            assert f["expected"] and f["observed"] and f["recommendation"] and f["reproduction"]
        security = (await c.get(f"/test-runs/{run_id}/findings", params={"security": "true"})).json()
        other = (await c.get(f"/test-runs/{run_id}/findings", params={"security": "false"})).json()
        assert len(security) + len(other) == len(findings), "the security filter splits the findings in two"
        assert all(f["is_security"] for f in security) and not any(f["is_security"] for f in other)

        card = (await c.get(f"/test-runs/{run_id}/scorecard")).json()
        assert card["overall"] == final["overall"] and card["categories"]

        # ---- 4. events: ordered, filterable and resumable --------------------------------------------------------
        first = await c.get(f"/test-runs/{run_id}/events", params={"limit": 5})
        page1 = first.json()
        assert len(page1) == 5 and first.headers["X-Next-Cursor"]
        second = await c.get(
            f"/test-runs/{run_id}/events", params={"limit": 5, "after": first.headers["X-Next-Cursor"]}
        )
        page2 = second.json()
        assert page2 and not {e["event_id"] for e in page1} & {e["event_id"] for e in page2}, "no overlap between pages"
        stamps = [e["timestamp"] for e in page1 + page2]
        assert stamps == sorted(stamps)
        everything: list[dict] = []
        cursor = None
        for _ in range(200):
            r = await c.get(
                f"/test-runs/{run_id}/events", params={"limit": 500, **({"after": cursor} if cursor else {})}
            )
            body = r.json()
            if not body:
                break
            everything += body
            cursor = r.headers["X-Next-Cursor"]
        ids = [e["event_id"] for e in everything]
        assert len(ids) == len(set(ids)), "paging never repeats an event"
        kinds = [e["type"] for e in everything]
        assert kinds[0] == "RunStarted" and kinds[-1] in {"RunCompleted"}
        assert {"PhaseStarted", "TestStarted", "TestCompleted", "FindingCreated"} <= set(kinds)
        phases = [e["payload"]["phase"] for e in everything if e["type"] == "PhaseStarted"]
        assert len(phases) == len(set(phases)) == 17, phases  # the seventeen phases, each once and in order
        only_findings = (await c.get(f"/test-runs/{run_id}/events", params={"type": "FindingCreated"})).json()
        assert only_findings and {e["type"] for e in only_findings} == {"FindingCreated"}
        assert (await c.get(f"/test-runs/{run_id}/events", params={"after": "not-a-cursor"})).status_code == 422

        # ---- 5. traces and artifacts ---------------------------------------------------------------------------
        traces = (await c.get(f"/test-runs/{run_id}/traces")).json()
        assert len(traces) >= len(results) - progress["blocked"]
        trace = (await c.get(f"/test-runs/{run_id}/traces/{traces[0]['id']}")).json()
        assert trace["events"] and trace["test_id"] == traces[0]["test_id"]
        assert (await c.get(f"/test-runs/{run_id}/traces/nope")).status_code == 404
        artifacts = (await c.get(f"/test-runs/{run_id}/artifacts")).json()
        kinds_stored = {a["kind"] for a in artifacts}
        assert {"plan", "analysis", "manifest"} <= kinds_stored, kinds_stored
        plan_artifact = next(a for a in artifacts if a["kind"] == "plan" and a["name"].endswith(".json"))
        blob = await c.get(plan_artifact["url"])
        assert blob.status_code == 200 and blob.headers["content-disposition"].startswith("attachment")
        assert json.loads(blob.content)["tests"]

        # ---- 6. reports: made while the run was finishing, versioned, never overwritten ---------------------------
        reports = (await c.get(f"/test-runs/{run_id}/reports")).json()
        assert len(reports) == 1 and reports[0]["report_version"] == 1, "a finished run already has its report"
        v1 = reports[0]
        assert {f["format"] for f in v1["formats"]} == {"json", "md"} and v1["bundle_id"]
        as_json = await c.get(next(f["url"] for f in v1["formats"] if f["format"] == "json"))
        assert as_json.status_code == 200 and as_json.headers["content-type"].startswith("application/json")
        assert as_json.json()["run"]["run_id"] == run_id
        made = await c.post(f"/test-runs/{run_id}/reports", json={"formats": ["html"]})
        assert made.status_code == 201, made.text
        v2 = made.json()
        assert v2["report_version"] == 2 and {f["format"] for f in v2["formats"]} == {"html"}
        page = await c.get(v2["formats"][0]["url"])
        assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
        assert "sandbox" in page.headers["content-security-policy"], (
            "a report is a document, never a page of the server"
        )
        assert page.headers["content-disposition"].startswith("inline")
        assert page.headers["x-content-type-options"] == "nosniff"
        same = await c.post(f"/reports/{v1['id']}/export", json={"format": "md"})
        assert same.status_code == 200 and same.json()["created_new_version"] is False
        newer = await c.post(f"/reports/{v1['id']}/export", json={"format": "html"})
        assert newer.json()["created_new_version"] is True and newer.json()["report"]["report_version"] == 3
        assert (await c.get(f"/reports/{v1['id']}")).json()["formats"] == v1["formats"], (
            "an old report is never changed"
        )
        assert (await c.get(f"/reports/{v1['id']}/files/pdf")).status_code == 404
        assert (await c.get("/reports/does-not-exist")).status_code == 404

        # ---- 7. human review: kept beside the original, never instead of it ----------------------------------------
        target_result = failed[0]
        before = {r.id: r for r in api.services.store.list_results(run_id)}[target_result["id"]]
        refused = await c.post(
            f"/test-runs/{run_id}/reviews",
            json={
                "subject": "result",
                "subject_id": target_result["test_id"],
                "decision": "false_positive",
                "reviewer": "ana",
            },
        )
        assert refused.status_code == 422 and "reason" in refused.text, "a decision needs a stated reason"
        review = await c.post(
            f"/test-runs/{run_id}/reviews",
            json={
                "subject": "result",
                "subject_id": target_result["test_id"],
                "decision": "false_positive",
                "reviewer": "ana",
                "reason": "the reply is acceptable for this agent's policy",
            },
        )
        assert review.status_code == 201, review.text
        assert review.json()["original"]["status"] == "failed" and review.json()["reviewed"]["status"] == "passed"
        assert review.json()["subject_label"] == target_result["test_id"]
        seen = (await c.get(f"/test-runs/{run_id}/results/{target_result['test_id']}")).json()
        assert seen["status"] == "passed" and seen["review"], (
            "what readers see has the human decision applied and says so"
        )
        stored = {r.id: r for r in api.services.store.list_results(run_id)}[target_result["id"]]
        assert stored.status == before.status and stored.score == before.score, "the original evaluation is untouched"
        history = (await c.get(f"/test-runs/{run_id}/reviews")).json()
        assert [h["reviewer"] for h in history] == ["ana"]

        # ---- 8. the same plan again, compared with the first run ----------------------------------------------------
        second_run = (await c.post("/test-runs", json={"plan_id": plan_doc["run_id"], "deselect": dropped})).json()[
            "run_id"
        ]
        done = await api.wait(second_run)
        assert done["status"] == "completed"
        cmp = (await c.get("/comparisons", params={"run_a": run_id, "run_b": second_run})).json()
        assert cmp["schema"].startswith("agentlab") and cmp["run_a"]["run_id"] == run_id
        assert cmp["compatibility"]["verdict"] in {"comparable", "comparable_with_caveats"}, cmp["compatibility"]
        assert cmp["compatibility"]["shared_tests"] > 0 and cmp["compatibility"]["only_a"] == 0
        assert cmp["verdict"] in {"unchanged", "mixed", "improved", "regressed", "inconclusive"} and cmp["summary"]

        # ---- 9. runs list: newest first, filterable ----------------------------------------------------------------
        runs = (await c.get("/test-runs", params={"kind": "run"})).json()
        assert [r["id"] for r in runs][:2] == [second_run, run_id]
        assert [r["id"] for r in (await c.get("/test-runs", params={"project": "journey"})).json()] == [run_id]
        assert (await c.get("/test-runs", params={"status": "completed", "limit": 1})).json()[0][
            "status"
        ] == "completed"
        assert (await c.get(f"/test-runs/{run_id[:8]}")).json()["id"] == run_id, "a unique id prefix is enough"


async def test_a_run_is_not_over_until_its_report_and_its_end_event_exist(tmp_path: Path) -> None:
    """Whoever sees a run end can fetch its report straight away: the status flips last."""
    async with running_api(tmp_path) as api:
        c = api.client
        accepted = await c.post("/test-runs", json={"target": mock_target(), "options": SMALL_RUN})
        run_id = accepted.json()["run_id"]
        statuses: list[str] = []
        while True:
            body = (await c.get(f"/test-runs/{run_id}")).json()
            statuses.append(body["status"])
            if body["status"] not in {"pending", "running"}:
                break
            await asyncio.sleep(0.005)
        assert statuses[-1] == "completed"
        reports = (await c.get(f"/test-runs/{run_id}/reports")).json()
        assert len(reports) == 1, "the report exists the moment the run reads completed"
        last = (await c.get(f"/test-runs/{run_id}/events", params={"type": "RunCompleted"})).json()
        assert len(last) == 1, "so does the event that says it ended"
        assert "pending" not in statuses[statuses.index("running") :] if "running" in statuses else True
