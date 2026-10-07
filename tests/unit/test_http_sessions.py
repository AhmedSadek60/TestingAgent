"""``api.response.session_id``: an agent that assigns its own conversation ids.

The setting was documented ("JSONPath of the conversation id it returned") and accepted, and nothing read it, so a second
turn to such an agent started a new conversation and every memory and multi-turn test measured nothing.
"""

from __future__ import annotations

import asyncio
import json

import httpx

from agentlab.adapters.base import AdapterContext
from agentlab.adapters.http import AgentApiAdapter
from agentlab.core.config import AgentLabConfig
from agentlab.core.models import AgentRequest, ApiConfig, TargetSpec

URL = "http://agent.test/api"


def agent(seen: list[tuple[str, str | None]], **api: object) -> AgentApiAdapter:
    """An agent that ignores the id it is given on a new conversation and hands out its own (``srv-1``, ``srv-2``, ...)."""
    assigned = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append((body["conversation"], request.headers.get("x-conv")))
        if str(body["conversation"]).startswith("srv-"):
            return httpx.Response(200, json={"reply": "again", "conversation": {"id": body["conversation"]}})
        assigned["count"] += 1
        return httpx.Response(200, json={"reply": "hello", "conversation": {"id": f"srv-{assigned['count']}"}})

    spec = TargetSpec(
        name="agent",
        api=ApiConfig(
            url=URL,
            request_template={"input": "{{input}}", "conversation": "{{session_id}}"},
            response={"output": "$.reply", "session_id": "$.conversation.id"},
            session_header="X-Conv",
            **api,  # type: ignore[arg-type]
        ),
    )
    made = AgentApiAdapter(spec, AdapterContext(config=AgentLabConfig()))
    made._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)
    return made


async def say(made: AgentApiAdapter, session: str, text: str) -> str:
    return (await made.send(AgentRequest(input=text, session_id=session))).output


def test_the_id_the_agent_assigned_is_sent_on_the_following_turns_of_that_conversation_only() -> None:
    seen: list[tuple[str, str | None]] = []

    async def scenario() -> None:
        made = agent(seen)
        await say(made, "agentlab-a", "hi")  # the agent answers with its own id for this conversation
        await say(made, "agentlab-a", "remember me?")
        await say(made, "agentlab-b", "hi")  # another conversation gets another id, and never inherits the first
        await say(made, "agentlab-b", "and me?")
        await say(made, "agentlab-a", "still there?")

    asyncio.run(scenario())
    assert seen == [
        ("agentlab-a", "agentlab-a"),  # nothing is known yet, so AgentLab's id
        ("srv-1", "srv-1"),  # the body and the header both carry what the agent assigned
        ("agentlab-b", "agentlab-b"),
        ("srv-2", "srv-2"),
        ("srv-1", "srv-1"),
    ]


def test_without_the_mapping_the_id_stays_agentlabs_own() -> None:
    seen: list[tuple[str, str | None]] = []

    async def scenario() -> None:
        made = agent(seen)
        made.cfg.response.session_id = None
        await say(made, "agentlab-a", "hi")
        await say(made, "agentlab-a", "again")

    asyncio.run(scenario())
    assert [s for s, _ in seen] == ["agentlab-a", "agentlab-a"]


def test_an_answer_that_has_no_id_or_cannot_be_read_changes_nothing() -> None:
    seen: list[tuple[str, str | None]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append((json.loads(request.content)["conversation"], None))
        return httpx.Response(200, json={"reply": "hello", "conversation": {"id": None}})

    spec = TargetSpec(
        name="agent",
        api=ApiConfig(
            url=URL,
            request_template={"input": "{{input}}", "conversation": "{{session_id}}"},
            response={"output": "$.reply", "session_id": "$.conversation.id"},
        ),
    )
    made = AgentApiAdapter(spec, AdapterContext(config=AgentLabConfig()))
    made._client = httpx.AsyncClient(transport=httpx.MockTransport(handler), timeout=5.0)

    async def scenario() -> None:
        await say(made, "agentlab-a", "hi")
        await say(made, "agentlab-a", "again")

    asyncio.run(scenario())
    assert [s for s, _ in seen] == ["agentlab-a", "agentlab-a"]
