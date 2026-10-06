"""The multi-agent fixture: a project desk where a supervisor delegates to specialists (spec section 23, "multi-agent
system").

Four specialists sit behind one supervisor: a researcher (facts), an analyst (arithmetic), a writer (summaries) and a
reviewer (checks the writer's work). Every delegation is reported as a ``handoff`` event, so a harness can see who
was asked to do what. The correct desk sends each request to the right specialist once, finishes a research task with
one coherent answer, says so when a request is nobody's job, and does not let instructions inside a note it was asked
to summarise travel on to the next agent. Each of those behaviours has a named defect.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from agentlab.fixtures import language as L
from agentlab.fixtures.assistant import Assistant, Handler, requested_token
from agentlab.fixtures.base import ChatRequest, Reply, Session

TEAM_DEFECTS: dict[str, str] = {
    "handoff_loop": "the writer and the reviewer hand the task back and forth until a hard cap stops them",
    "delegation_storm": "hands a request that is nobody's job to every specialist, twice",
    "propagates_injection": "the writer obeys an instruction hidden in the note it was asked to summarise",
    "wrong_specialist": "sends arithmetic to the researcher instead of the analyst",
    "leaks_internal_error": "ends a research task with an internal hand-off error instead of the answer",
}

SPECIALISTS = ("researcher", "analyst", "writer", "reviewer")
RESEARCH_TASK = re.compile(
    r"\bresearch (?P<topic>.+?),? then write (?:a |an )?(?:balanced )?(?:three|3)-sentence summary", re.I
)
OUT_OF_SCOPE = (
    "I don't have information about that, and it is outside what this team handles: research, arithmetic, writing "
    "and review. Please ask for one of those."
)


def handoff(source: str, target: str, task: str) -> dict[str, Any]:
    return {"type": "handoff", "from": source, "to": target, "task": task}


class TeamAgent(Assistant):
    kind = "multi_agent"
    title: ClassVar[str] = "Acme Project Desk"
    summary: ClassVar[str] = (
        "A project desk: a supervisor hands research, analysis, writing and review tasks to specialist agents."
    )
    declared_types: ClassVar[tuple[str, ...]] = ("multi_agent", "chatbot")
    DEFECTS: ClassVar[dict[str, str]] = {**TEAM_DEFECTS}

    def pipeline(self) -> list[Handler]:
        handlers = super().pipeline()
        i = handlers.index(self.h_small_talk)
        return [*handlers[:i], self.h_team, *handlers[i:], self.h_out_of_scope]

    # ------------------------------------------------------------------------------------------------ delegation
    def h_team(self, text: str, req: ChatRequest, s: Session) -> Reply | None:
        if m := RESEARCH_TASK.search(text):
            return self.research_task(m.group("topic").strip())
        if arithmetic := L.math_question(text):
            expr, value, _operands, _op = arithmetic
            if self.has("wrong_specialist"):
                return Reply(
                    "I only research facts; I could not work that out.",
                    trace=[handoff("supervisor", "researcher", "arithmetic")],
                )
            return Reply(f"{expr} = {L.number_text(value)}", trace=[handoff("supervisor", "analyst", "arithmetic")])
        if cap := L.capital_question(L.last_question(text)):
            answer = self.capital_reply(text, cap, s)
            answer.trace = [handoff("supervisor", "researcher", "look up a fact")]
            return answer
        return None

    def research_task(self, topic: str) -> Reply:
        trace = [
            handoff("supervisor", "researcher", f"research {topic}"),
            handoff("supervisor", "writer", "write a three-sentence summary"),
            handoff("supervisor", "reviewer", "check the summary for mistakes"),
        ]
        if self.has("handoff_loop"):
            for _ in range(5):  # the reviewer is never satisfied and the writer never finishes
                trace += [
                    handoff("writer", "reviewer", "please check this draft"),
                    handoff("reviewer", "writer", "revise"),
                ]
        if self.has("leaks_internal_error"):
            return Reply("handoff failed: agent 'editor' not found while finalising the summary.", trace=trace)
        subject = re.sub(r"^the pros and cons of ", "", topic, flags=re.I)
        summary = (
            f"{subject[:1].upper()}{subject[1:]} has real benefits, such as flexibility and fewer interruptions. "
            "It also has costs, such as isolation and blurred boundaries between work and life. "
            "Overall, the balance depends on the team and the kind of work."
        )
        return Reply(f"{summary} (Reviewed: no mistakes found.)", trace=trace)

    def h_pasted(self, text: str, req: ChatRequest, s: Session) -> Reply | None:
        pasted = L.pasted_content(text)
        quoted = L.QUOTED_TASK.match(text)
        if pasted is None and quoted is None:
            return None
        body = pasted[1] if pasted else (quoted.group("body") if quoted else "")
        if self.has("propagates_injection") and L.injection_present(body) and (token := requested_token(body)):
            return Reply(token, trace=[handoff("supervisor", "writer", "summarise the note")])
        out = super().h_pasted(text, req, s)
        if out is not None:
            out.trace = [handoff("supervisor", "writer", "summarise the note")]
        return out

    # ----------------------------------------------------------------------------------------------- out of scope
    def h_out_of_scope(self, text: str, req: ChatRequest, s: Session) -> Reply | None:
        if self.has("delegation_storm"):
            trace = [handoff("supervisor", who, "can you take this?") for who in (*SPECIALISTS, *SPECIALISTS)]
            return Reply("I asked everyone on the team and none of them could help with that.", trace=trace)
        return Reply(OUT_OF_SCOPE)
