"""Private owner choice over a launched inventory weapon's damage."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.encounter import PendingDefense
from wayfarer.engine.simulation.combat.melee.damage_records import PreparedMeleeDamage
from wayfarer.engine.simulation.combat.ranged.damage_records import PreparedRangedDamage
from wayfarer.engine.simulation.magic.missile_damage_records import PreparedMissileDamage
from wayfarer.models import Id, Record
from wayfarer.orchestration.owner_damage_records import OwnerDamageOutcome


class InventoryDamagePending(Record):
    kind: Literal["inventory-owner-damage"] = "inventory-owner-damage"
    id: Id
    actor_id: Id
    encounter_id: Id
    opened_elapsed_microseconds: int = Field(ge=0)
    preparation: PreparedMeleeDamage | PreparedRangedDamage | PreparedMissileDamage
    response: ChooseDefense
    selected_defense: Literal["dodge", "parry", "block", "none"]
    captured_attacker: ValidatedBuild

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

    @property
    def pending_attack(self) -> PendingDefense:
        return (
            self.preparation.context.pending
            if isinstance(self.preparation, PreparedRangedDamage)
            else self.preparation.inputs.pending
        )

    @property
    def modifier(self) -> int:
        if isinstance(self.preparation, PreparedMissileDamage):
            return 0
        return (
            self.preparation.inputs.adds
            if isinstance(self.preparation, PreparedMeleeDamage)
            else self.preparation.context.adds
        )

    @model_validator(mode="after")
    def owner_identity(self) -> InventoryDamagePending:
        if (
            self.actor_id != self.pending_attack.attacker_id
            or isinstance(self.preparation, (PreparedMeleeDamage, PreparedMissileDamage))
            and not self.preparation.rollable
        ):
            raise ValueError("Pending inventory damage requires its owner's actual random damage")
        return self
