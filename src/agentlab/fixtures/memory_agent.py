"""The memory fixture: an assistant that remembers what users tell it (spec section 23, "memory agent")."""

from __future__ import annotations

from typing import ClassVar

from agentlab.fixtures.assistant import CONVERSATION_DEFECTS, MEMORY_DEFECTS, Assistant


class MemoryAgent(Assistant):
    kind = "memory"
    title: ClassVar[str] = "Acme Notes Assistant"
    summary: ClassVar[str] = "A personal assistant that remembers what each user tells it and recalls it on request."
    declared_types: ClassVar[tuple[str, ...]] = ("memory", "chatbot")
    DEFECTS: ClassVar[dict[str, str]] = {
        **MEMORY_DEFECTS,
        "forgets_context": CONVERSATION_DEFECTS["forgets_context"],
        "keeps_old_value": CONVERSATION_DEFECTS["keeps_old_value"],
    }
