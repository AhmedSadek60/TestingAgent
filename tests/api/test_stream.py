"""Following a run live over server-sent events, on a real port: nothing missing, nothing twice, and a clean end."""

from __future__ import annotations

from pathlib import Path

import httpx

from tests.support.api import Frame, frames, live_api, mock_target

TOKEN = "t" * 8 + "stream-token-" + "x" * 8
RUN = {"target": mock_target(), "options": {"intensity": "quick", "max_tests": 6, "second_wave": False}}
SLOW = {
    "target": mock_target("demo", "success", "slow"),
    "options": {"intensity": "quick", "second_wave": False},
    "overrides": {"max_parallel": 1},
}


async def follow(client: httpx.AsyncClient, run_id: str, **kw: object) -> list[Frame]:
    out: list[Frame] = []
    async with client.stream("GET", f"/test-runs/{run_id}/stream", timeout=120, **kw) as r:  # type: ignore[arg-type]
        assert r.status_code == 200, await r.aread()
        assert r.headers["content-type"].startswith("text/event-stream")
        async for frame in frames(r):
            out.append(frame)
            if frame.event == "end":
                break
    return out


async def test_a_run_is_followed_live_from_its_first_event_to_its_end(tmp_path: Path) -> None:
    with live_api(tmp_path) as server:
        async with httpx.AsyncClient(base_url=server.url, timeout=60) as c:
            run_id = (await c.post("/test-runs", json=RUN)).json()["run_id"]
            got = await follow(c, run_id)  # opened while the run is in progress or just starting
            assert got[0].retry == 2000, "tells a browser how soon to reconnect"
            events = [f for f in got if f.id]
            assert events and got[-1].event == "end"
            assert got[-1].json()["status"] == "completed" and got[-1].json()["run_id"] == run_id

            kinds = [f.event for f in events]
            assert kinds[0] == "RunStarted" and "TestStarted" in kinds and "TestCompleted" in kinds
            assert kinds[-1] == "RunCompleted", "the end of the run is the last event, then the stream says it is over"
            assert [f.json()["type"] for f in events] == kinds
            assert all(f.json()["run_id"] == run_id for f in events)

            ids = [f.id for f in events]
            assert len(set(ids)) == len(ids), "no event is sent twice"
            assert ids == sorted(ids), "in the order they happened"

            listed = (await c.get(f"/test-runs/{run_id}/events", params={"limit": 1000})).json()
            assert [e["event_id"] for e in listed] == [f.json()["event_id"] for f in events], (
                "the same events the REST list has"
            )

            again = await follow(c, run_id)  # a finished run is replayed and ends at once
            assert [f.id for f in again if f.id] == ids and again[-1].event == "end"


async def test_a_reader_that_was_cut_off_resumes_where_it_stopped(tmp_path: Path) -> None:
    with live_api(tmp_path) as server:
        async with httpx.AsyncClient(base_url=server.url, timeout=60) as c:
            run_id = (await c.post("/test-runs", json=RUN)).json()["run_id"]
            first: list[Frame] = []
            async with c.stream("GET", f"/test-runs/{run_id}/stream") as r:
                async for frame in frames(r):
                    if frame.id:
                        first.append(frame)
                    if len(first) == 7:
                        break  # the connection drops here
            last = first[-1].id
            rest = await follow(c, run_id, headers={"Last-Event-ID": last})
            resumed = [f for f in rest if f.id]
            whole = await follow(c, run_id)
            assert [f.id for f in first] + [f.id for f in resumed] == [f.id for f in whole if f.id], (
                "everything exactly once: nothing lost across the break, nothing repeated"
            )
            assert rest[-1].event == "end"
            by_query = await follow(c, run_id, params={"after": last})
            assert [f.id for f in by_query if f.id] == [f.id for f in resumed], "`after` does the same as Last-Event-ID"

            bad = await c.get(f"/test-runs/{run_id}/stream", params={"after": "not-a-cursor"})
            assert bad.status_code == 422 and bad.json()["error"]["kind"] in {"user_error", "invalid_request"}


async def test_the_stream_needs_the_token_like_everything_else(tmp_path: Path) -> None:
    with live_api(tmp_path, token=TOKEN) as server:
        async with httpx.AsyncClient(base_url=server.url, timeout=60) as anonymous:
            assert (await anonymous.get("/test-runs/x/stream")).status_code == 401
        auth = {"Authorization": f"Bearer {TOKEN}"}
        async with httpx.AsyncClient(base_url=server.url, timeout=60, headers=auth) as c:
            run_id = (await c.post("/test-runs", json=RUN)).json()["run_id"]
            got = await follow(c, run_id)
            assert got[-1].event == "end"
            missing = await c.get("/test-runs/no-such-run/stream")
            assert missing.status_code == 404 and missing.json()["error"]["kind"] == "not_found"
        async with httpx.AsyncClient(base_url=server.url, headers={"Authorization": "Bearer wrong"}) as wrong:
            assert (await wrong.get(f"/test-runs/{run_id}/stream")).status_code == 401


async def test_cancelling_while_following_ends_the_stream_with_the_cancellation(tmp_path: Path) -> None:
    with live_api(tmp_path) as server:
        async with httpx.AsyncClient(base_url=server.url, timeout=120) as c:
            run_id = (await c.post("/test-runs", json=SLOW)).json()["run_id"]
            seen: list[Frame] = []
            cancelled_at = None
            async with c.stream("GET", f"/test-runs/{run_id}/stream") as r:
                async for frame in frames(r):
                    seen.append(frame)
                    if cancelled_at is None and sum(f.event == "TestCompleted" for f in seen) >= 3:
                        reply = await c.post(f"/test-runs/{run_id}/cancel", json={"reason": "enough"})
                        assert reply.status_code == 202
                        cancelled_at = len(seen)
                    if frame.event == "end":
                        break
            assert cancelled_at is not None, "the run was still going when it was cancelled"
            kinds = [f.event for f in seen]
            assert kinds[-1] == "end" and seen[-1].json()["status"] == "cancelled"
            assert kinds[-2] == "RunCancelled", "the reader is told, then the stream ends"
            assert len(seen) > cancelled_at, "the step in progress and the end of the run still reached the reader"
            assert "RunCompleted" not in kinds and "RunFailed" not in kinds
            final = (await c.get(f"/test-runs/{run_id}")).json()
            assert final["status"] == "cancelled" and final["progress"]["running_tests"] == []
            assert len((await c.get(f"/test-runs/{run_id}/reports")).json()) == 1
