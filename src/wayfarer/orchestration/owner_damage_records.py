"""Private B66 owner-damage commands and captured composed continuations."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.resources import Command
from wayfarer.engine.simulation.traits.attack_defense import PreparedOwnerDamage, TraitAttackOutcome
from wayfarer.engine.simulation.traits.composed_host import ResistComposedAttack
from wayfarer.engine.simulation.traits.composed_phases import ComposedDelivery
from wayfarer.models import Id, Record


class PrepareOwnerDamage(Command):
    """Execute this target's real response and stop at the owner's damage roll."""

    kind: Literal["prepare-owner-damage"] = "prepare-owner-damage"
    response: ChooseDefense | ResistComposedAttack
    secret: bool = False

    @model_validator(mode="after")
    def response_identity(self) -> PrepareOwnerDamage:
        if (self.id, self.actor_id, self.expected_revision) != (
            self.response.id,
            self.response.actor_id,
            self.response.expected_revision,
        ):
            raise ValueError("Damage preparation must identify its actual target response")
        return self


class ChooseOwnerDamage(Command):
    kind: Literal["choose-owner-damage"] = "choose-owner-damage"
    pending_id: Id
    choice: Literal["accept", "use-luck"]


class OwnerDamagePending(Record):
    kind: Literal["owner-damage"] = "owner-damage"
    id: Id
    actor_id: Id
    encounter_id: Id
    opened_elapsed_microseconds: int = Field(ge=0)
    preparation: PreparedOwnerDamage
    continuation: ComposedDelivery

    @property
    def secret(self) -> bool:
        return self.preparation.secret

    @property
    def original(self) -> tuple[int, ...] | None:
        return self.preparation.original

    @property
    def original_json(self) -> str | None:
        return (
            None if self.secret else OwnerDamageOutcome(dice=self.original or ()).model_dump_json()
        )

    @model_validator(mode="after")
    def owner_identity(self) -> OwnerDamagePending:
        if self.actor_id != self.preparation.actor_id or not self.preparation.rollable:
            raise ValueError("Pending owner damage requires its actual owner's random damage")
        return self


class OwnerDamageOutcome(Record):
    dice: tuple[int, ...]
    combat: CombatResult | None = None
    attack: TraitAttackOutcome | None = None
