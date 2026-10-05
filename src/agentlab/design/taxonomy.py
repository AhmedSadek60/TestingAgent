"""The test taxonomy A-Q (spec section 8) and the 28 security categories of taxonomy N.

Coverage is computed from the tests a plan contains, so the plan can say which areas are covered, which only partly
(blocked tests) and which are not covered at all, with the reason.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from agentlab.core.models import TestCase

TAXONOMY: dict[str, str] = {
    "A": "Basic functional",
    "B": "Conversational",
    "C": "Memory",
    "D": "RAG",
    "E": "Tool and function calling",
    "F": "Planning",
    "G": "Autonomous agents",
    "H": "Multi-agent systems",
    "I": "MCP",
    "J": "Browser and UI",
    "K": "Coding agents",
    "L": "Document agents",
    "M": "Multimodal",
    "N": "Safety and security",
    "O": "Reliability",
    "P": "Performance",
    "Q": "Cost",
}

# Which agent type (or interface) makes a letter relevant. A letter nobody expects is "not applicable", not a gap.
LETTER_RELEVANCE: dict[str, list[str]] = {
    "C": ["memory"],
    "D": ["rag", "research"],
    "E": ["tool_calling", "function_calling", "mcp", "react"],
    "F": ["planning", "autonomous", "react", "workflow", "supervisor"],
    "G": ["autonomous", "long_running", "computer_use", "event_driven"],
    "H": ["multi_agent", "supervisor", "sub_agents"],
    "I": ["mcp"],
    "J": ["browser", "computer_use", "web"],
    "K": ["coding", "repository"],
    "L": ["document"],
    "M": ["multimodal"],
}
ALWAYS_RELEVANT = set("ABNOPQ")


@dataclass(frozen=True)
class SecurityCategory:
    code: str
    name: str
    pattern: str  # matched against the test id
    not_covered_hint: str
    skills: tuple[str, ...] = ()  # built-in skills that generate these tests (to explain a gap)


SECURITY_CATEGORIES: list[SecurityCategory] = [
    SecurityCategory(
        "N1",
        "Direct prompt injection",
        r"^INJ-",
        "no conversational interface",
        skills=("prompt-injection-testing",),
    ),
    SecurityCategory(
        "N2",
        "Indirect prompt injection",
        r"^IND-(INLINE|FILE)",
        "no conversational interface",
        skills=("indirect-prompt-injection-testing",),
    ),
    SecurityCategory(
        "N3",
        "System instruction override",
        r"^(INJ-(OVERRIDE|SYSTEM-UPDATE|DELIMITER|AUTHORITY)|EXFIL-EXTRACT-(PROMPT-DUMP|DEBUG))",
        "no conversational interface",
        skills=("prompt-injection-testing", "data-exfiltration-testing"),
    ),
    SecurityCategory(
        "N4",
        "Tool injection",
        r"^(ABUSE-|MCP-(PATH|COMMAND|SQL|SSRF))",
        "no tool takes a path, command, SQL or URL argument, or tool calls are not observable",
        skills=("tool-abuse-testing", "mcp-testing"),
    ),
    SecurityCategory(
        "N5",
        "Tool output poisoning",
        r"^IND-TOOL-",
        "the interface cannot replace a tool result, or the target has no read tool",
        skills=("indirect-prompt-injection-testing",),
    ),
    SecurityCategory(
        "N6",
        "RAG document injection",
        r"^(IND-KB-|RAG-.*INJECTION|DOC-.*INJECTION)",
        "the interface cannot add documents to a session and no supplied document carries injection text",
        skills=("indirect-prompt-injection-testing", "rag-testing", "document-agent-testing"),
    ),
    SecurityCategory(
        "N7",
        "Browser content injection",
        r"^BROW-",
        "the target is not a browser agent",
        skills=("browser-agent-testing",),
    ),
    SecurityCategory(
        "N8",
        "Memory poisoning",
        r"^MEM-POISONING",
        "the target shows no memory capability",
        skills=("memory-testing",),
    ),
    SecurityCategory(
        "N9",
        "Goal hijacking",
        r"^(INJ-(TASK-HIJACK|ESCALATION)|MULTI-INJECTION)",
        "no conversational interface",
        skills=("prompt-injection-testing", "multi-agent-testing"),
    ),
    SecurityCategory(
        "N10",
        "Excessive agency",
        r"^(AGENCY-|AUTO-NO-ESCALATION)",
        "no tools were discovered, or tool calls are not observable",
        skills=("excessive-agency-testing", "autonomous-agent-testing"),
    ),
    SecurityCategory(
        "N11",
        "Unauthorized tool usage",
        r"^AUTHZ-UNVERIFIED",
        "no privileged-looking tool was discovered",
        skills=("authorization-testing",),
    ),
    SecurityCategory(
        "N12",
        "Privilege escalation",
        r"^AUTHZ-CLAIMED",
        "authorization skill not applicable to this target",
        skills=("authorization-testing",),
    ),
    SecurityCategory(
        "N13",
        "Data exfiltration",
        r"^(EXFIL-(CHANNEL|SEND)|IND-TOOL-HIJACK)",
        "no conversational interface (or no way to plant a secret)",
        skills=("data-exfiltration-testing", "indirect-prompt-injection-testing"),
    ),
    SecurityCategory(
        "N14",
        "Cross-user data leakage",
        r"^MEM-ISOLATION",
        "the target shows no memory capability",
        skills=("memory-testing",),
    ),
    SecurityCategory(
        "N15",
        "Cross-session leakage",
        r"^MEM-ISOLATION",
        "the target shows no memory capability",
        skills=("memory-testing",),
    ),
    SecurityCategory(
        "N16",
        "Secret leakage",
        r"^EXFIL-EXTRACT",
        "no conversational interface",
        skills=("data-exfiltration-testing",),
    ),
    SecurityCategory(
        "N17",
        "Credential exposure",
        r"^(EXFIL-(CREDENTIALS|ENVIRONMENT)|CODE-NO-SECRET)",
        "no conversational interface",
        skills=("data-exfiltration-testing", "coding-agent-testing"),
    ),
    SecurityCategory(
        "N18",
        "Unsafe destructive action",
        r"^(AGENCY-(CONFIRM|BULK)|BROW-DESTRUCTIVE|CODE-SANDBOX|AUTO-NO-ESCALATION)",
        "no destructive tool was discovered",
        skills=("excessive-agency-testing", "browser-agent-testing", "coding-agent-testing"),
    ),
    SecurityCategory(
        "N19",
        "Missing confirmation",
        r"^AGENCY-(CONFIRM|BULK)",
        "no destructive tool was discovered",
        skills=("excessive-agency-testing",),
    ),
    SecurityCategory(
        "N20",
        "SSRF-like unsafe network behaviour",
        r"^(ABUSE-SSRF|MCP-SSRF)",
        "no tool takes a URL argument",
        skills=("tool-abuse-testing", "mcp-testing"),
    ),
    SecurityCategory(
        "N21",
        "Path traversal",
        r"^(ABUSE-PATH|MCP-PATH)",
        "no tool takes a path argument",
        skills=("tool-abuse-testing", "mcp-testing"),
    ),
    SecurityCategory(
        "N22",
        "Command-execution safety",
        r"^(ABUSE-COMMAND|MCP-COMMAND)",
        "no tool executes commands",
        skills=("tool-abuse-testing", "mcp-testing"),
    ),
    SecurityCategory(
        "N23",
        "Unsafe generated-code execution",
        r"^CODE-(SANDBOX|PROTECTED|CODE-INJECTION)",
        "the target is not a coding agent (or Docker is unavailable)",
        skills=("coding-agent-testing",),
    ),
    SecurityCategory(
        "N24",
        "Denial-of-wallet / runaway loops",
        r"^(COST-RUNAWAY|AUTO-(PERSISTENT|BOUNDED)|BROW-STEP|MULTI-TERMINATION)",
        "tool calls are not observable",
        skills=("cost-testing", "autonomous-agent-testing", "browser-agent-testing", "multi-agent-testing"),
    ),
    SecurityCategory(
        "N25",
        "Recursive agent delegation",
        r"^MULTI-(TERMINATION|OUT-OF-SCOPE)",
        "the target is not a multi-agent system or reports no handoff events",
        skills=("multi-agent-testing",),
    ),
    SecurityCategory(
        "N26",
        "Malicious external content",
        r"^(IND-|BROW-HIDDEN|MM-IMAGE-INJECTION)",
        "no conversational interface",
        skills=("indirect-prompt-injection-testing", "browser-agent-testing", "multimodal-agent-testing"),
    ),
    SecurityCategory(
        "N27",
        "Malicious skill / tool instructions",
        r"^MCP-DESCRIPTION",
        "the target is not an MCP server or exposes no tools with metadata",
        skills=("mcp-testing",),
    ),
    SecurityCategory(
        "N28",
        "Insecure fallback behaviour",
        r"^AUTHZ-FAIL-CLOSED",
        "needs an authorisation-check tool and tool-result replacement",
        skills=("authorization-testing",),
    ),
]
_RULES = [(c, re.compile(c.pattern)) for c in SECURITY_CATEGORIES]

# Discovered signals that make a security category *applicable*. A category not listed applies to every target
# that can be talked to; a listed one is "not applicable" (not a gap) when none of its signals was observed.
N_RELEVANCE: dict[str, tuple[str, ...]] = {
    "N4": ("tools", "mcp"),
    "N5": ("tools",),
    "N6": ("rag", "documents"),
    "N7": ("browser",),
    "N8": ("memory",),
    "N10": ("tools",),
    "N11": ("tools",),
    "N12": ("tools",),
    "N14": ("memory",),
    "N15": ("memory",),
    "N18": ("tools", "browser", "coding"),
    "N19": ("tools",),
    "N20": ("tools", "mcp"),
    "N21": ("tools", "mcp"),
    "N22": ("tools", "mcp", "coding"),
    "N23": ("coding",),
    "N24": ("tools", "autonomous", "browser", "multi_agent"),
    "N25": ("multi_agent",),
    "N27": ("mcp",),
    "N28": ("tools", "mcp"),
}


def security_codes(test: TestCase) -> list[str]:
    """The N-categories a test contributes to (by its id), e.g. ``MEM-ISOLATION-001`` -> N14, N15."""
    return [c.code for c, rx in _RULES if rx.search(test.id)]


# test category -> taxonomy letter, used for tests that do not come from a skill with declared letters
CATEGORY_LETTER = {
    "functional": "A",
    "api": "A",
    "conversational": "B",
    "memory": "C",
    "rag": "D",
    "tool_calling": "E",
    "function_calling": "E",
    "tools": "E",
    "planning": "F",
    "autonomy": "G",
    "autonomous": "G",
    "multi_agent": "H",
    "mcp": "I",
    "browser": "J",
    "ui": "J",
    "coding": "K",
    "document": "L",
    "multimodal": "M",
    "security": "N",
    "safety": "N",
    "reliability": "O",
    "regression": "O",
    "performance": "P",
    "cost": "Q",
}
_HINTS = {
    "B": re.compile(r"multi-?turn|context|follow-?up|clarif|persona|consisten|ambig|tone|conversation|turn", re.I),
    "O": re.compile(r"retry|timeout|concurren|idempot|reliab|rate|flak|stabil|repeat|recover", re.I),
}


def letters_for(test: TestCase, skill_taxonomy: list[str]) -> list[str]:
    """Taxonomy letters one test contributes to.

    Adversarial tests count for N. A skill that is about exactly one other area (memory, browser, coding, ...) also
    credits that area, so a memory-poisoning test counts for both C and N. A skill spanning several areas credits
    the one the test's own wording points to."""
    cat = test.category.lower()
    adversarial = cat in {"security", "safety"} or bool(security_codes(test))
    rest = [x for x in skill_taxonomy if x != "N"]
    letters: list[str] = ["N"] if adversarial else []
    if len(rest) == 1:
        letters.append(rest[0])
    elif len(rest) > 1 and not adversarial:
        text = f"{test.subcategory} {test.name}"
        letters.append(next((x for x in rest[1:] if _HINTS.get(x) and _HINTS[x].search(text)), rest[0]))
    if not letters:
        letters.append(CATEGORY_LETTER.get(cat, "A"))
    return list(dict.fromkeys(letters))
