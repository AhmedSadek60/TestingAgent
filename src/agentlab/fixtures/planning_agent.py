"""The planning fixture: an assistant that turns goals into plans (spec section 23, "planning agent").

It writes plans with the phases it is told to use, in the order it is told, within the number of steps it is allowed;
spots the missing prerequisite in a request; refuses to plan the impossible; and asks what the goal is when there is
none. It reports every step it plans as a ``plan_step`` event, as an agent framework with a planner would. Each of those
behaviours has a named defect.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from agentlab.fixtures.assistant import Assistant, Handler
from agentlab.fixtures.base import ChatRequest, Reply, Session

PLANNING_DEFECTS: dict[str, str] = {
    "reorders_steps": "lists prescribed phases in the wrong order (testing before implementation)",
    "drops_steps": "leaves the last prescribed phase out of the plan",
    "ignores_step_limit": "writes five steps when told to use at most three",
    "misses_prerequisite": "plans the final step without the step it depends on (publishing data nobody has collected)",
    "plans_infeasible_goal": "writes a confident plan for a goal that cannot be met",
    "no_clarification_for_vague_goal": "invents a goal and plans it when asked to 'plan it' with no goal",
}

PRESCRIBED = re.compile(r"use exactly these phases, in this order, one per line:\s*(?P<phases>[^.]+)\.?\s*$", re.I)
STEP_LIMIT = re.compile(
    r"plan to (?P<goal>.+?) with at most (?P<n>\w+) steps", re.I
)  # "a plan to learn swimming with at most three steps"
PREREQUISITE = re.compile(
    r"(?:want to|need to) (?P<action>[a-z ]+?),? but (?:the )?(?P<missing>[a-z ]+?) (?:has|have) not been (?P<done>[a-z]+) yet",
    re.I,
)
INFEASIBLE_GOAL = re.compile(r"\b(?:to|on) (?:mars|the moon|jupiter|venus|saturn)\b", re.I)
VAGUE = re.compile(r"^(?:please )?(?:plan|make a plan|create a plan)(?: it| that| this)?[.!?]*$", re.I)
GENERIC_STEPS = (
    "Define exactly what you want to achieve and how you will know it is done",
    "Gather what you need: people, tools, time and information",
    "Do the first small piece and check that it works",
    "Carry on in short cycles, reviewing the result of each",
    "Review the outcome against the goal and adjust the plan",
)
NUMBERS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


def numbered(steps: list[str]) -> str:
    return "\n".join(f"{i}. {step}" for i, step in enumerate(steps, 1))


def plan_events(steps: list[str]) -> list[dict[str, Any]]:
    return [{"type": "plan_step", "index": i, "step": step} for i, step in enumerate(steps, 1)]


class PlanningAgent(Assistant):
    kind = "planning"
    title: ClassVar[str] = "Acme Planner"
    summary: ClassVar[str] = (
        "An assistant that turns goals into ordered, feasible plans and tells you what to do first."
    )
    declared_types: ClassVar[tuple[str, ...]] = ("planning", "chatbot")
    DEFECTS: ClassVar[dict[str, str]] = {**PLANNING_DEFECTS}

    def pipeline(self) -> list[Handler]:
        return [self.h_planning, *super().pipeline()]  # first: "Plan it." is too short for the generic input checks

    def h_planning(self, text: str, req: ChatRequest, s: Session) -> Reply | None:
        if m := PRESCRIBED.search(text):
            return self.prescribed(m.group("phases"))
        if m := STEP_LIMIT.search(text):
            return self.limited(m.group("goal"), m.group("n"))
        if m := PREREQUISITE.search(text):
            return self.prerequisite(m)
        if INFEASIBLE_GOAL.search(text) and re.search(r"\b(?:plan|deliver|get|send)\b", text, re.I):
            return self.infeasible(text)
        if VAGUE.match(text):
            return self.vague()
        return None

    # ------------------------------------------------------------------------------------------------ the shapes
    def prescribed(self, phases: str) -> Reply:
        steps = [p.strip().capitalize() for p in phases.split(",") if p.strip()]
        if self.has("reorders_steps") and len(steps) >= 4:
            steps[2], steps[3] = steps[3], steps[2]
        if self.has("drops_steps") and len(steps) >= 2:
            steps = steps[:-1]
        return Reply(f"Here is the plan:\n{numbered(steps)}", trace=plan_events(steps))

    def limited(self, goal: str, n_word: str) -> Reply:
        limit = int(n_word) if n_word.isdigit() else NUMBERS.get(n_word.lower(), 3)
        if self.has("ignores_step_limit"):
            limit = 5
        steps = [f"{GENERIC_STEPS[i]} ({goal.strip()})" if i == 0 else GENERIC_STEPS[i] for i in range(limit)]
        return Reply(f"Plan to {goal.strip()}:\n{numbered(steps)}", trace=plan_events(steps))

    def prerequisite(self, m: re.Match[str]) -> Reply:
        action, missing = m.group("action").strip(), m.group("missing").strip()
        if self.has("misses_prerequisite"):
            return Reply(f"You can go ahead and {action} right away; it should be ready.")
        steps = [f"Collect {missing}", f"Check {missing} for gaps and errors", f"Then {action}"]
        return Reply(
            f"First, collect {missing}: you cannot {action} until that is done. Then:\n{numbered(steps)}",
            trace=plan_events(steps),
        )

    def infeasible(self, text: str) -> Reply:
        if self.has("plans_infeasible_goal"):
            steps = [
                "Book a courier for pick-up today",
                "Ship the parcel on tonight's flight",
                "Confirm delivery tomorrow morning",
            ]
            return Reply(f"Here is the plan:\n{numbered(steps)}", trace=plan_events(steps))
        return Reply(
            "That is not possible: no carrier delivers to Mars, and nothing can get there by tomorrow morning. "
            "I can plan a delivery within Earth instead, or help you adjust the goal."
        )

    def vague(self) -> Reply:
        if self.has("no_clarification_for_vague_goal"):
            steps = ["Decide the goal", "Make a list of tasks", "Start with the first task"]
            return Reply(f"Here is your plan:\n{numbered(steps)}", trace=plan_events(steps))
        return Reply(
            "What would you like me to plan? Could you tell me the goal and any constraints, such as a deadline?"
        )
