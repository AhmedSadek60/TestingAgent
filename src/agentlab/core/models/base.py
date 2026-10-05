"""Shared base classes for AgentLab domain models."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from agentlab.core.ids import new_id, utcnow


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True, use_enum_values=False)


class OpenModel(BaseModel):
    """Model that tolerates unknown keys (used for data received from targets)."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)


class Record(Model):
    """Every important record carries id, timestamps, version and status (spec section 25)."""

    id: str = Field(default_factory=new_id)
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    version: int = 1
