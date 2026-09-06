"""Bounded authored noncombat encounters and persisted player choices."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.rules.checks import CheckTrace
from wayfarer.simulation.resources import Id, Record


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
