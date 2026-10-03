"""Private selected Steps and durable casting leases; public combat stays closed."""

from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.vocabulary import Facing
from wayfarer.engine.simulation.hex_geometry import Hex, HexFacing
from wayfarer.engine.simulation.magic.great_haste_state import (
    CastGreatHaste,
    DeclareGreatHasteChannel,
)
from wayfarer.engine.simulation.resources import Command, ResourceState
from wayfarer.models import Id, Record

PREFIX = "great-haste-step-lease:"
RESOLVED = "great-haste-step-resolved:"


class CastingStep(Record):
    destination: GridPoint | None = None
    facing: Facing | None = None
    hex_path: tuple[Hex, ...] = Field(default=(), max_length=100)
    hex_facing: HexFacing | None = None

    @model_validator(mode="after")
    def selected(self) -> CastingStep:
        square = self.destination is not None or self.facing is not None
        hexagonal = bool(self.hex_path) or self.hex_facing is not None
        if square == hexagonal:
            raise ValueError("Select exactly one square or hex Step")
        return self


class StepCastGreatHaste(Command):
    kind: Literal["step-great-haste"] = "step-great-haste"
    operation: Literal["start", "concentrate"]
    channel_id: Id
    cast_id: Id
    step: CastingStep

    @model_validator(mode="after")
    def casting_only(self) -> StepCastGreatHaste:
        if self.operation not in ("start", "concentrate"):
            raise ValueError("A selected Step requires a casting Concentrate")
        return self


class NamedStepCastGreatHaste(Command):
    kind: Literal["named-step-great-haste"] = "named-step-great-haste"
    operation: Literal["start", "concentrate"]
    channel_id: Id
    cast_id: Id
    known_fact_id: Id
    step: CastingStep


StepCommand = Annotated[StepCastGreatHaste | NamedStepCastGreatHaste, Field(discriminator="kind")]


class CastingStepLease(Record):
    command: StepCommand
    ritual_step: bool = Field(default=False, exclude_if=lambda value: not value)
    named_origin_json: str | None = Field(default=None, exclude_if=lambda value: value is None)
    encounter_id: Id
    build_revision: str = Field(min_length=1)
    energy: int = Field(ge=0)
    round: int = Field(ge=1)
    turn_index: int = Field(ge=0)
    game_time: int = Field(ge=0)
    completed: bool = False


def leases(resources: ResourceState) -> dict[str, CastingStepLease]:
    return {
        lease.command.id: lease
        for event in resources.events
        if event.id.startswith(PREFIX)
        for lease in (CastingStepLease.model_validate_json(event.kind),)
    }


HostCommand = Annotated[
    DeclareGreatHasteChannel | CastGreatHaste | StepCastGreatHaste | NamedStepCastGreatHaste,
    Field(discriminator="kind"),
]
HOST_ADAPTER: TypeAdapter[HostCommand] = TypeAdapter(HostCommand)


def cast_command(command: StepCommand) -> CastGreatHaste:
    return CastGreatHaste(
        id=command.id,
        actor_id=command.actor_id,
        expected_revision=command.expected_revision,
        operation=command.operation,
        channel_id=command.channel_id,
        cast_id=command.cast_id,
    )


class ResolvedStepDistraction(Record):
    lease_id: Id
    cast_id: Id
    command_id: Id
    revision: int = Field(ge=0)
    effect_digest: str = Field(min_length=64, max_length=64)
    hp: int
    spell_event_id: str = Field(min_length=1)
    spell_event_digest: str = Field(min_length=64, max_length=64)
    check: CheckTrace


def distractions(resources: ResourceState) -> dict[str, ResolvedStepDistraction]:
    return {
        record.lease_id: record
        for event in resources.events
        if event.id.startswith(RESOLVED)
        for record in (ResolvedStepDistraction.model_validate_json(event.kind),)
    }
