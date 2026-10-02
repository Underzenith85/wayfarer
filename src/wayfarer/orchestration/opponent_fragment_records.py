"""Private B66 choices over a source-bound B415 fragment attack."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.explosion import BlastResponse
from wayfarer.engine.simulation.combat.blast_phases import PreparedFragmentAttack
from wayfarer.engine.simulation.combat.blast_phases import (
    RecordedFragmentLaunch as RecordedFragmentLaunch,
)
from wayfarer.engine.simulation.combat.commands import ResolveWeaponExplosion
from wayfarer.engine.simulation.resources import Command
from wayfarer.models import Id, Record


class PrepareOpponentFragment(Command):
    kind: Literal["prepare-opponent-fragment"] = "prepare-opponent-fragment"
    resolution: ResolveWeaponExplosion
    launch_command_id: Id
    owner_ids: tuple[Id, ...] = ()
    secret: bool = False
    incendiary_objects: bool = Field(default=False, exclude_if=lambda value: not value)
    launch: RecordedFragmentLaunch | None = Field(
        default=None, exclude_if=lambda value: value is None
    )

    @model_validator(mode="after")
    def same_source_command(self) -> PrepareOpponentFragment:
        if self.owner_ids and (
            self.actor_id not in self.owner_ids or len(set(self.owner_ids)) != len(self.owner_ids)
        ):
            raise ValueError("Fragment plan requires distinct owners including its first owner")
        if self.launch is not None and self.launch.command.id != self.launch_command_id:
            raise ValueError("Fragment source differs from its named launch")
        return self


class ChooseOpponentFragment(Command):
    kind: Literal["choose-opponent-fragment"] = "choose-opponent-fragment"
    pending_id: Id
    choice: Literal["accept", "use-luck", "cancel"]


class AmendFragmentResponses(Command):
    kind: Literal["amend-fragment-responses"] = "amend-fragment-responses"
    pending_id: Id
    responses: tuple[BlastResponse, ...] = Field(min_length=1)


class OpponentFragmentPending(Record):
    kind: Literal["opponent-fragment"] = "opponent-fragment"
    id: Id
    actor_id: Id
    opened_elapsed_microseconds: int = Field(ge=0)
    launch: RecordedFragmentLaunch
    resolution: ResolveWeaponExplosion
    preparation: PreparedFragmentAttack
    original: CheckTrace | None
    secret: bool = False

    @property
    def attacker_id(self) -> str:
        return self.launch.command.actor_id

    @model_validator(mode="after")
    def exact_actor(self) -> OpponentFragmentPending:
        if self.actor_id != self.preparation.target.actor_id or self.actor_id == self.attacker_id:
            raise ValueError("Fragment attack must be against its controlling owner")
        if (self.original is None) != self.secret:
            raise ValueError("Fragment original does not match its declared visibility")
        if (
            self.original is not None
            and self.preparation.spec.score(self.original.dice) != self.original
        ):
            raise ValueError("Fragment original differs from its canonical captured target")
        return self
