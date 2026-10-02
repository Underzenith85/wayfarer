"""Private choices over an attack against the controlling Luck owner."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.resources import Command
from wayfarer.engine.simulation.traits.composed_records import ResistComposedAttack
from wayfarer.engine.simulation.traits.opponent_attack import OpponentAttackPreparation
from wayfarer.models import Id, Record


class BeginOpponentAttack(Command):
    kind: Literal["begin-opponent-attack"] = "begin-opponent-attack"
    encounter_id: Id
    attack_id: Id
    visibility: Literal["public", "secret"] = "public"
    resist: bool | None = Field(default=None, exclude_if=lambda value: value is None)
    prepare_owner_damage: bool = Field(default=False, exclude_if=lambda value: not value)


class ChooseOpponentAttack(Command):
    kind: Literal["choose-opponent-attack"] = "choose-opponent-attack"
    pending_id: Id
    choice: Literal["accept", "use-luck", "cancel"]
    response: ChooseDefense | ResistComposedAttack | None = None

    @model_validator(mode="after")
    def exact_response(self) -> ChooseOpponentAttack:
        if self.response is None:
            if self.choice != "cancel":
                raise ValueError("An attack decision requires its actual defense response")
            return self
        if self.choice == "cancel":
            raise ValueError("A secret cancellation does not choose a defense")
        if (self.response.id, self.response.actor_id, self.response.expected_revision) != (
            self.id,
            self.actor_id,
            self.expected_revision,
        ):
            raise ValueError("Attack choice and defense must name the same command and owner")
        return self


class OpponentAttackPending(Record):
    kind: Literal["opponent-attack"] = "opponent-attack"
    id: Id
    actor_id: Id
    opened_elapsed_microseconds: int = Field(ge=0)
    preparation: OpponentAttackPreparation
    prepare_owner_damage: bool = Field(default=False, exclude_if=lambda value: not value)

    @model_validator(mode="after")
    def exact_owner(self) -> OpponentAttackPending:
        if self.actor_id != self.preparation.owner_id:
            raise ValueError("Opponent pending roll must belong to its attacked owner")
        return self

    @property
    def original(self) -> CheckTrace | None:
        return self.preparation.original

    @property
    def secret(self) -> bool:
        return self.preparation.secret

    @property
    def attacker_id(self) -> str:
        return self.preparation.attacker_id
