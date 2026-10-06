"""Cancelling: a run that has not started is withdrawn; a running one stops at a safe point and is still reported."""

from __future__ import annotations

import asyncio
from pathlib import Path

from tests.support.api import SMALL_RUN, mock_target, running_api


async def test_a_run_that_has_not_started_is_cancelled_at_once_and_never_runs(tmp_path: Path) -> None:
    async with running_api(tmp_path, start_worker=False) as api:  # nobody is working the queue
        c = api.client
        accepted = await c.post("/test-runs", json={"target": mock_target(), "options": SMALL_RUN})
        run_id = accepted.json()["run_id"]
        assert (await c.get(f"/test-runs/{run_id}")).json()["status"] == "pending"
        assert (await c.get("/health")).json()["queued"] == 1

        cancelled = await c.post(f"/test-runs/{run_id}/cancel", json={"reason": "wrong target"})
        assert cancelled.status_code == 202 and cancelled.json()["status"] == "cancelled"
        body = (await c.get(f"/test-runs/{run_id}")).json()
        assert body["status"] == "cancelled" and body["finished_at"] and body["started_at"] is None
        assert (await c.get("/health")).json()["queued"] == 0, "it was taken out of the queue"
        assert (await c.get(f"/test-runs/{run_id}/results")).json() == []

        again = await c.post(f"/test-runs/{run_id}/cancel")
        assert again.status_code == 409 and again.json()["error"]["kind"] == "conflict"


async def test_a_running_run_stops_at_a_safe_point_and_what_ran_is_still_reported(tmp_path: Path) -> None:
    async with running_api(tmp_path) as api:
        c = api.client
        # one test at a time, each a little slow: there is time to cancel in the middle
        accepted = await c.post(
            "/test-runs",
            json={
                "target": mock_target("demo", "success", "slow"),
                "options": {"intensity": "quick", "second_wave": False},
                "overrides": {"max_parallel": 1},
            },
        )
        run_id = accepted.json()["run_id"]
        for _ in range(600):
            body = (await c.get(f"/test-runs/{run_id}")).json()
            if body["status"] == "running" and body["progress"]["tests_done"] >= 3:
                break
            await asyncio.sleep(0.05)
        else:
            raise AssertionError(f"the run never got going: {body}")

        cancel = await c.post(f"/test-runs/{run_id}/cancel", json={"reason": "enough"})
        assert cancel.status_code == 202 and cancel.json()["status"] == "cancelling"
        final = await api.wait(run_id)
        assert final["status"] == "cancelled"
        assert final["finished_at"]
        done = final["progress"]["tests_done"]
        assert done >= 3, "what had run is kept"
        results = (await c.get(f"/test-runs/{run_id}/results")).json()
        statuses = {r["status"] for r in results}
        assert "skipped" in statuses, "what had not run is recorded as skipped, not as passed or failed"
        assert not (statuses - {"passed", "failed", "blocked", "skipped", "error", "timeout"})
        assert final["progress"]["running_tests"] == []

        reports = (await c.get(f"/test-runs/{run_id}/reports")).json()
        assert len(reports) == 1, "a cancelled run is still analysed and reported"
        json_report = next(f for f in reports[0]["formats"] if f["format"] == "json")
        doc = (await c.get(json_report["url"])).json()
        assert doc["run"]["status"] == "cancelled" and doc["run"]["complete"] is False, "the report says it is partial"

        events = (await c.get(f"/test-runs/{run_id}/events", params={"type": "RunCancelled"})).json()
        assert len(events) == 1
        assert events[0]["payload"]["cancel_reason"] == "enough", "the reason given is kept with the event"
        assert (await c.get(f"/test-runs/{run_id}")).json()["totals"]["cancel_reason"] == "enough"
        assert (await c.post(f"/test-runs/{run_id}/cancel")).status_code == 409


async def test_cancelling_a_finished_or_unknown_run_is_refused_clearly(tmp_path: Path) -> None:
    async with running_api(tmp_path) as api:
        c = api.client
        done = await api.run()
        finished = await c.post(f"/test-runs/{done['id']}/cancel")
        assert finished.status_code == 409
        assert "already finished" in finished.json()["error"]["message"]
        missing = await c.post("/test-runs/no-such-run/cancel")
        assert missing.status_code == 404 and missing.json()["error"]["kind"] == "not_found"
