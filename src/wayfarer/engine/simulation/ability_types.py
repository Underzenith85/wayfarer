"""Pinned, authored supernatural channels; never accepted as player input."""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from pydantic import Field, ValidationInfo, model_validator

from wayfarer.engine.rules.checks import CheckTrace, Outcome
from wayfarer.engine.rules.gurps_checks import ResistanceTrace
from wayfarer.engine.rules.resistance_attempts import (
    ResistanceAttempt,
    ResistancePreparation,
    resistance_attempt_checks,
    validate_current_resistance_attempt,
)
from wayfarer.engine.rules.supernatural.ability_types import AbilitySpec as AbilitySpec
from wayfarer.engine.simulation.resources import Command
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState


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
    resistance_attempt: ResistanceAttempt | None = Field(
        default=None, exclude_if=lambda value: value is None
    )
    resistance_preparation: ResistancePreparation | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def consistent_current_resistance(self, info: ValidationInfo) -> AbilityEvent:
        attempt = self.resistance_attempt
        if attempt is None:
            if self.resistance_preparation is not None or (
                info.mode == "json"
                and {"resistance_attempt", "resistance_preparation"} & self.model_fields_set
            ):
                raise ValidationError("Current ability preparation is missing its actual attempt")
            return self
        if self.resistance_preparation is None:
            raise ValidationError("Current ability attempt is missing its captured preparation")
        validate_current_resistance_attempt(self.resistance_preparation, attempt)
        actors = (
            (attempt.contest.first_id, attempt.contest.second_id)
            if isinstance(attempt, ResistanceTrace)
            else (attempt.declared_attacker.id, attempt.declared_resister.id)
        )
        actual_checks = resistance_attempt_checks(attempt)
        if (
            actors != (self.actor_id, self.target_id)
            or len(self.checks) < len(actual_checks)
            or self.checks[-len(actual_checks) :] != actual_checks
            or self.failed != (not attempt.affected)
            or self.critical_failure != (attempt.attacker.outcome is Outcome.CRITICAL_FAILURE)
            or self.outcome.outcome not in ({"active", "hit"} if attempt.affected else {"resisted"})
            or not attempt.affected
            and (self.damage_dice or self.outcome.revealed_fact_ids)
        ):
            raise ValidationError("Current ability resistance receipt contradicts its event")
        if self.effect is not None and (
            (self.effect.actor_id, self.effect.ability_id, self.effect.target_id)
            != (self.actor_id, self.ability_id, self.target_id)
            or not attempt.affected
            and self.effect.active
        ):
            raise ValidationError("Current ability resistance effect contradicts its receipt")
        return self

    @classmethod
    def read_native_json(cls, encoded: str) -> AbilityEvent:
        event = cls.model_validate_json(encoded)
        if event.resistance_attempt is not None and event.model_dump_json() != encoded:
            raise ValidationError("Current ability receipt must retain exact native JSON")
        return event


def validate_channels(rules: AbilityRules, state: PlayState) -> None:
    """Ability channels must reference world entities and facts that exist."""
    entities = {e.id for e in state.world.entities}
    facts = {f.id for f in state.world.facts}
    for channel in rules.channels:
        if not {channel.actor_id, channel.target_id, channel.location_id} <= entities:
            raise ValidationError("Invalid ability channel entity references")
        if (
            not set(
                (
                    *channel.presence_fact_ids,
                    *channel.detection_fact_ids,
                    *channel.precise_fact_ids,
                    *channel.analysis_fact_ids,
                    *channel.thought_fact_ids,
                )
            )
            <= facts
        ):
            raise ValidationError("Invalid ability channel fact references")
