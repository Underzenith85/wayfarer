"""Private owner choice at the actual unarmed strike damage boundary."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.combat.unarmed.damage_records import (
    PreparedArmedParryDamage,
    PreparedUnarmedDamage,
)
from wayfarer.models import Id, Record
from wayfarer.orchestration.owner_damage_records import OwnerDamageOutcome


class UnarmedDamagePending(Record):
    kind: Literal["unarmed-owner-damage"] = "unarmed-owner-damage"
    id: Id
    actor_id: Id
    encounter_id: Id
    opened_elapsed_microseconds: int = Field(ge=0)
    preparation: PreparedUnarmedDamage
    response: ChooseDefense
    reacting: bool

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
    def modifier(self) -> int:
        return self.preparation.inputs.adds

    @model_validator(mode="after")
    def owner_identity(self) -> UnarmedDamagePending:
        if self.actor_id != self.preparation.inputs.pending.actor_id:
            raise ValueError("Unarmed damage belongs to its actual striker")
        return self


class ArmedParryDamagePending(Record):
    kind: Literal["armed-parry-owner-damage"] = "armed-parry-owner-damage"
    id: Id
    actor_id: Id
    encounter_id: Id
    opened_elapsed_microseconds: int = Field(ge=0)
    preparation: PreparedArmedParryDamage
    response: ChooseDefense
    reacting: bool

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
    def modifier(self) -> int:
        return self.preparation.inputs.adds

    @model_validator(mode="after")
    def owner_identity(self) -> ArmedParryDamagePending:
        if self.actor_id != self.preparation.inputs.actor_id:
            raise ValueError("Armed parry damage belongs to its actual weapon owner")
        return self
