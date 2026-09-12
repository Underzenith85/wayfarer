"""Server-authored ruling alternatives and durable, revision-bound decisions.

These records contain no executable narrative. An alternative can only adjust
an existing check and explicitly reframe a social proposal as diplomacy.
"""

from __future__ import annotations

import hashlib
from typing import Literal

from pydantic import Field, model_validator

from wayfarer.models import Id, Record


class RulingAlternative(Record):
    id: Id
    label: str = Field(min_length=1, max_length=500)
    check_rule_id: Id
    modifier: int = Field(ge=-20, le=20)
    affected_mechanic: Literal["social.diplomacy.target"] = "social.diplomacy.target"


class RulingPolicy(Record):
    id: Id
    version: int = Field(ge=1)
    minimum_modifier: int = Field(default=-4, ge=-20, le=20)
    maximum_modifier: int = Field(default=4, ge=-20, le=20)
    automatic: bool = False
    automatic_minimum: int = Field(default=-1, ge=-20, le=20)
    automatic_maximum: int = Field(default=1, ge=-20, le=20)
    player_approval: bool = False
    lifetime_ticks: int = Field(default=10, ge=1, le=10000)
    alternatives: tuple[RulingAlternative, ...] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def validate_bounds(self) -> RulingPolicy:
        if not (
            self.minimum_modifier
            <= self.automatic_minimum
            <= self.automatic_maximum
            <= self.maximum_modifier
        ):
            raise ValueError("Automatic bounds must be within campaign bounds")
        if len({a.id for a in self.alternatives}) != len(self.alternatives):
            raise ValueError("Duplicate ruling alternative ID")
        if any(
            not self.minimum_modifier <= a.modifier <= self.maximum_modifier
            for a in self.alternatives
        ):
            raise ValueError("Ruling alternative exceeds campaign bounds")
        return self

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


RulingStatus = Literal["pending", "approved", "rejected", "expired", "executed"]


class Ruling(Record):
    id: Id
    campaign_id: Id
    actor_id: Id
    original_action_json: str
    configuration_digest: str
    policy_digest: str
    opened_revision: int = Field(ge=1)
    opened_at: int = Field(ge=0)
    valid_revision: int = Field(ge=1)
    expires_at: int = Field(ge=1)
    alternatives: tuple[RulingAlternative, ...]
    status: RulingStatus = "pending"
    selected_id: str | None = None
    approver_id: str | None = None
    authority: Literal["gm", "player", "policy"] | None = None
    reason: str = ""
    decided_revision: int | None = None
    executed_revision: int | None = None

    def current_status(self, revision: int, game_time: int) -> RulingStatus:
        if self.status in ("pending", "approved") and (
            revision != self.valid_revision or game_time >= self.expires_at
        ):
            return "expired"
        return self.status


def expire_rulings(
    rulings: tuple[Ruling, ...], revision: int, game_time: int
) -> tuple[Ruling, ...]:
    """Called on every play commit; an unrelated state change revokes consent."""
    return tuple(
        ruling.model_copy(update={"status": "expired"})
        if ruling.current_status(revision, game_time) == "expired"
        else ruling
        for ruling in rulings
    )
