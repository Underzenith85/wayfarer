"""Pinned, authored supernatural channels; never accepted as player input."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.rules.ability_types import AbilitySpec as AbilitySpec
from wayfarer.rules.checks import CheckTrace
from wayfarer.simulation.resources import Command, Id, Record


class AbilityChannel(Record):
    """An authored permission, not general access to the target's knowledge.

    Distance is valid only in the named location. Moving out invalidates it.
    Detect facts encode presence/direction/quantity separately from analysis;
    thought facts contain only currently authored surface thoughts.
    """

    id: Id
    ability_id: Id
    actor_id: Id
    target_id: Id
    location_id: Id
    distance_yards: int = Field(default=0, ge=0, le=1000000)
    presence_fact_ids: tuple[str, ...] = ()
    detection_fact_ids: tuple[str, ...] = ()
    precise_fact_ids: tuple[str, ...] = ()
    analysis_fact_ids: tuple[str, ...] = ()
    thought_fact_ids: tuple[str, ...] = ()
    shared_language: bool = True
    digital_mind: bool = False
    psionic_blocked: bool = False


class AbilityRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    abilities: tuple[AbilitySpec, ...]
    channels: tuple[AbilityChannel, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> AbilityRules:
        if len({a.definition_id for a in self.abilities}) != len(self.abilities):
            raise ValueError("Duplicate ability definition")
        if len({c.id for c in self.channels}) != len(self.channels):
            raise ValueError("Duplicate ability channel")
        if any(
            c.ability_id not in {a.definition_id for a in self.abilities} for c in self.channels
        ):
            raise ValueError("Unknown channel ability")
        return self


class AbilityCommand(Command):
    kind: Literal["activate", "resolve", "maintain", "cancel", "analyze"] = "activate"
    ability_id: Id
    channel_id: str | None = None


class AbilityEffect(Record):
    distracted: bool = False
    analyzing: bool = False
    activation_shock: int = 0
    build_revision: str = ""
    actor_id: Id
    ability_id: Id
    kind: str
    target_id: Id
    started_at: int
    expires_at: int | None = None
    active: bool = True
    level: int = Field(ge=1)
    maintenance_cost: int = Field(default=0, ge=0)
    concentrating: bool = False
    ready_at: int = 0
    hp_at_start: int = 0
    channel_id: str | None = None


AbilityOutcomeKind = Literal[
    "concentrating",
    "interrupted",
    "active",
    "cancelled",
    "resisted",
    "detected",
    "nothing",
    "analyzed",
    "hit",
    "unavailable",
]


class AbilityOutcome(Record):
    outcome: AbilityOutcomeKind
    revealed_fact_ids: tuple[str, ...] = ()


class AbilityEvent(Record):
    actor_id: Id
    ability_id: Id
    target_id: Id
    outcome: AbilityOutcome
    effect: AbilityEffect | None = None
    failed: bool = False
    critical_failure: bool = False
    checks: tuple[CheckTrace, ...] = ()
    damage_dice: tuple[int, ...] = ()
