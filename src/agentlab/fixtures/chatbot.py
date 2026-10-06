"""The chatbot fixture: a customer-facing conversational assistant (spec section 23, "simple chatbot")."""

from __future__ import annotations

from typing import ClassVar

from agentlab.fixtures.assistant import CONVERSATION_DEFECTS, Assistant


class ChatbotAgent(Assistant):
    kind = "chatbot"
    title: ClassVar[str] = "Acme Assistant"
    summary: ClassVar[str] = "A friendly assistant that answers questions for Acme customers."
    declared_types: ClassVar[tuple[str, ...]] = ("chatbot",)
    DEFECTS: ClassVar[dict[str, str]] = {**CONVERSATION_DEFECTS}
