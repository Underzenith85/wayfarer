"""A closed unarmed strike and its still-unresolved damage expression."""

from dataclasses import dataclass

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.unarmed.records import PendingUnarmed, UnarmedTrace
from wayfarer.engine.simulation.equipment.catalog import Damage
from wayfarer.models import Record


class UnarmedDamageInputs(Record):
    pending: PendingUnarmed
    trace: UnarmedTrace
    critical: int
    location: HumanLocation
    dice_count: int = Field(ge=1)
    adds: int
    maximum: bool
    size_ratio: tuple[int, int] = (1, 1)
    captured_attacker: ValidatedBuild

    @model_validator(mode="after")
    def delivered_strike(self) -> UnarmedDamageInputs:
        if (
            self.pending.action not in ("punch", "kick")
            or self.trace.intent != self.pending
            or (self.trace.actor_id, self.trace.target_id)
            != (self.pending.actor_id, self.pending.target_id)
            or not self.trace.won
            or not self.trace.checks
            or self.trace.damage_dice
            or self.trace.injury
            or not 0 < self.size_ratio[0] <= self.size_ratio[1]
        ):
            raise ValueError("Unarmed damage requires one delivered, unresolved strike")
        return self


class PreparedUnarmedDamage(Record):
    inputs: UnarmedDamageInputs
    original: tuple[int, ...] | None
    secret: bool = False

    @property
    def dice_count(self) -> int:
        return self.inputs.dice_count

    @model_validator(mode="after")
    def valid_original(self) -> PreparedUnarmedDamage:
        if (self.original is None) != self.secret or self.inputs.maximum:
            raise ValueError("Unarmed damage requires its actual unresolved random roll")
        if self.original is not None and (
            len(self.original) != self.dice_count
            or any(type(die) is not int or not 1 <= die <= 6 for die in self.original)
        ):
            raise ValueError("Unarmed original differs from its captured dice expression")
        return self


@dataclass(frozen=True)
class UnarmedDamageStage:
    state: PlayState
    encounter: Encounter
    preparation: PreparedUnarmedDamage


class ArmedParryDamageInputs(Record):
    """Accepted B376 counterdamage; the parrying actor owns its damage roll."""

    pending: PendingUnarmed
    check: CheckTrace
    damage: Damage
    dice_count: int = Field(ge=1)
    adds: int
    location: HumanLocation

    @property
    def actor_id(self) -> str:
        return self.pending.target_id

    @property
    def target_id(self) -> str:
        return self.pending.actor_id

    @model_validator(mode="after")
    def accepted_counterdamage(self) -> ArmedParryDamageInputs:
        limb = (
            ("left-leg" if self.pending.foot == "left-foot" else "right-leg")
            if self.pending.action == "kick"
            else (
                "left-arm"
                if self.pending.hands and self.pending.hands[0] == "left-hand"
                else "right-arm"
            )
        )
        if not self.check.outcome.succeeded or self.location != limb:
            raise ValueError("Armed parry damage requires its accepted check and striking limb")
        return self


class PreparedArmedParryDamage(Record):
    inputs: ArmedParryDamageInputs
    trace: UnarmedTrace
    captured_attacker: ValidatedBuild
    original: tuple[int, ...] | None
    secret: bool = False

    @property
    def dice_count(self) -> int:
        return self.inputs.dice_count

    @model_validator(mode="after")
    def accepted_parry(self) -> PreparedArmedParryDamage:
        if (
            self.trace.intent != self.inputs.pending
            or (self.trace.action, self.trace.actor_id, self.trace.target_id)
            != (self.inputs.pending.action, self.inputs.target_id, self.inputs.actor_id)
            or len(self.trace.checks) < 2
            or not self.trace.checks[-1].outcome.succeeded
            or self.trace.damage_dice
            or self.trace.injury
            or self.trace.blocked_reason is not None
            or self.trace.won
            or self.trace.effect_checks != (self.inputs.check,)
            or self.trace.effect_dice
            or (self.original is None) != self.secret
            or self.original is not None
            and (
                len(self.original) != self.dice_count
                or any(type(die) is not int or not 1 <= die <= 6 for die in self.original)
            )
        ):
            raise ValueError("Armed parry preparation requires one unresolved counterdamage roll")
        return self


@dataclass(frozen=True)
class ArmedParryDamageStage:
    state: PlayState
    encounter: Encounter
    preparation: PreparedArmedParryDamage
