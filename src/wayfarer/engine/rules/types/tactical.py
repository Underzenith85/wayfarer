"""Typed state for Campaigns B393-B394 special combat situations."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.models import Id, Record


class InitiativeSideResult(Record):
    """One declared side's replayable initiative outcome."""

    actor_ids: tuple[Id, ...] = Field(min_length=1)
    leader_id: Id | None = None
    roll: int | None = Field(default=None, ge=1, le=6)
    modifier: int = Field(ge=-2, le=5)
    score: int = Field(ge=-1, le=11)


class EncounterSurprise(Record):
    """The resolved opening state; actor stun remains in the injury aggregate."""

    trigger_id: Id
    kind: Literal["total", "partial"]
    sides: tuple[InitiativeSideResult, InitiativeSideResult]
    initiative_winner: Literal[0, 1] | None = None
    frozen_side: Literal[0, 1] | None = None
    freeze_turns: int = Field(default=0, ge=0, le=6)


class HighSpeedState(Record):
    """Personal high-speed movement state, separate from vehicle operation."""

    velocity: int = Field(ge=1, le=200)
    straight_yards: int = Field(default=0, ge=0, le=10000)
    direction: int | None = Field(default=None, ge=0, le=5)


class CombatVisibility(Record):
    """Modifiers fixed before an attack roll from authoritative visibility facts."""

    attack_penalty: Literal[-10, -6, -4, 0] = 0
    defense_penalty: Literal[-4, 0] = 0
    defenses: tuple[Literal["dodge", "parry", "block"], ...] = (
        "dodge",
        "parry",
        "block",
    )

    @model_validator(mode="after")
    def defense_consistency(self) -> CombatVisibility:
        if self.defense_penalty and not self.defenses:
            raise ValueError("A defense penalty requires an available defense")
        return self
