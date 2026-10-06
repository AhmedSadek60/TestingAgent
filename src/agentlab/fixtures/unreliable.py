"""The unreliable fixture: an assistant that usually works and sometimes does not (spec section 23, "deliberately
unreliable agent").

Its failures are the ones production agents show: an answer that is right two times out of three, a session that dies
after one odd message, replies that arrive far too late, a service that collapses when two people use it at once,
intermittent server errors and a response body cut off in the middle. All of them are deterministic (they depend on how
often a question has been asked, or on how many requests are in flight, never on a random number), so a test that finds
one finds it every time, and the correct variant never fails.
"""

from __future__ import annotations

import asyncio
import re
from typing import ClassVar

from agentlab.fixtures import language as L
from agentlab.fixtures.assistant import CONVERSATION_DEFECTS, Assistant
from agentlab.fixtures.base import ChatRequest, Reply, Session, fake_traceback

UNRELIABLE_DEFECTS: dict[str, str] = {
    "flaky_arithmetic": "gives a wrong sum every third time the same arithmetic question is asked",
    "fails_after_bad_input": "answers every later message of a session with HTTP 500 once it has received malformed input",
    "slow_replies": "takes about a second and a half to answer anything, far above a reasonable budget",
    "breaks_under_load": "answers HTTP 503 when more than two requests are in flight at once",
    "random_server_errors": "answers HTTP 500 every third time the same message is sent",
    "malformed_json_sometimes": "answers with a truncated JSON body every fourth time the same message is sent",
    "token_bloat": CONVERSATION_DEFECTS["token_bloat"],
}
BAD_INPUT = re.compile(r"\}\{|<<<|%s")
SLOW_SECONDS = 1.5


class UnreliableAgent(Assistant):
    kind = "unreliable"
    title: ClassVar[str] = "Acme Flaky Assistant"
    summary: ClassVar[str] = "A general assistant that answers questions, usually correctly."
    declared_types: ClassVar[tuple[str, ...]] = ("chatbot",)
    DEFECTS: ClassVar[dict[str, str]] = {**UNRELIABLE_DEFECTS}

    def setup(self) -> None:
        super().setup()
        self.inflight = 0
        self.asked: dict[str, int] = {}

    def occurrence(self, message: str) -> int:
        """How many times this exact message has been sent to the agent so far (this one included)."""
        key = L.norm(message)
        self.asked[key] = self.asked.get(key, 0) + 1
        return self.asked[key]

    async def reply(self, req: ChatRequest, session: Session) -> Reply:
        n = self.occurrence(req.message)
        self.inflight += 1
        try:
            if self.has("breaks_under_load"):
                await asyncio.sleep(0.15)  # long enough for simultaneous requests to overlap
                if self.inflight > 2:
                    return Reply("The service is overloaded. Please try again later.", status=503)
            if self.has("random_server_errors") and n % 3 == 0:
                return Reply("Internal server error.", status=500)
            if self.has("malformed_json_sometimes") and n % 4 == 0:
                return Reply(raw_body='{"reply": "Hello, I', status=200)
            if self.has("fails_after_bad_input"):
                if session.state.get("poisoned"):
                    return Reply(fake_traceback("RuntimeError", "session state is corrupt"), status=500)
                if BAD_INPUT.search(req.message):
                    session.state["poisoned"] = True
            out = await super().reply(req, session)
            if self.has("flaky_arithmetic") and n % 3 == 0 and L.math_question(req.message):
                out.text = re.sub(r"\d+$", lambda m: str(int(m.group(0)) - 1), out.text)
            if self.has("slow_replies"):
                out.delay = SLOW_SECONDS
            return out
        finally:
            self.inflight -= 1
