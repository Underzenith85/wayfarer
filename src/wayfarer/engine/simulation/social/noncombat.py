"""Bounded authored noncombat encounters and persisted player choices."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState


class Approach(Record):
    id: Id
    check_rule_id: Id
    success_progress: int = Field(default=2, ge=1, le=100)
    failure_progress: int = Field(default=1, ge=0, le=100)
    fatigue_cost: int = Field(default=0, ge=0, le=100)
    failure_hp_cost: int = Field(default=0, ge=0, le=100)
    success_fact_ids: tuple[Id, ...] = ()
    failure_fact_ids: tuple[Id, ...] = ()


class NoncombatRule(Record):
    id: Id
    scene_id: Id
    category: Literal["negotiation", "investigation", "infiltration", "pursuit", "hazard"]
    stakes: str = Field(min_length=1, max_length=1000)
    required_progress: int = Field(ge=1, le=100)
    maximum_failures: int = Field(default=3, ge=1, le=100)
    approaches: tuple[Approach, ...] = Field(min_length=1, max_length=10)
    completion_fact_ids: tuple[Id, ...] = ()
    defeat_fact_ids: tuple[Id, ...] = ()
    withdrawal_fact_ids: tuple[Id, ...] = ()

    @model_validator(mode="after")
    def unique(self) -> NoncombatRule:
        if len({a.id for a in self.approaches}) != len(self.approaches):
            raise ValueError("Duplicate approach")
        return self


class NoncombatRules(Record):
    id: Id
    version: int = Field(ge=1)
    encounters: tuple[NoncombatRule, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique(self) -> NoncombatRules:
        if len({e.id for e in self.encounters}) != len(self.encounters):
            raise ValueError("Duplicate noncombat encounter rule")
        return self


class NoncombatEncounter(Record):
    id: Id
    rule_id: Id
    actor_id: Id
    status: Literal["choice", "success", "failure", "withdrawn"] = "choice"
    progress: int = Field(default=0, ge=0)
    failures: int = Field(default=0, ge=0)
    # No consequential choice is made automatically on disconnect or timeout.
    pending_choices: tuple[Id, ...]
    checks: tuple[CheckTrace, ...] = ()
    revealed_fact_ids: tuple[Id, ...] = ()
    revision: int = Field(ge=1)


def validate_state(
    rules: NoncombatRules | None,
    state: PlayState,
    *,
    check_ids: frozenset[str],
    scene_ids: frozenset[str],
) -> None:
    """Encounter rules must bind to scenes, checks and facts; instances must bind to rules."""
    if rules is None:
        if state.noncombat:
            raise ValidationError("Noncombat state requires rules")
        return
    facts = {f.id for f in state.world.facts}
    for encounter_rule in rules.encounters:
        if encounter_rule.scene_id not in scene_ids:
            raise ValidationError("Noncombat encounter requires a scene")
        for approach in encounter_rule.approaches:
            if approach.check_rule_id not in check_ids:
                raise ValidationError("Unknown noncombat check")
            if not set((*approach.success_fact_ids, *approach.failure_fact_ids)) <= facts:
                raise ValidationError("Unknown approach consequence")
        if (
            not set(
                (
                    *encounter_rule.completion_fact_ids,
                    *encounter_rule.defeat_fact_ids,
                    *encounter_rule.withdrawal_fact_ids,
                )
            )
            <= facts
        ):
            raise ValidationError("Unknown encounter consequence")
    if len({e.id for e in state.noncombat}) != len(state.noncombat):
        raise ValidationError("Duplicate noncombat instance")
    rule_ids = {r.id for r in rules.encounters}
    actor_ids = {a.actor_id for a in state.actors}
    for encounter_state in state.noncombat:
        if encounter_state.rule_id not in rule_ids or encounter_state.actor_id not in actor_ids:
            raise ValidationError("Invalid noncombat instance")
