"""Private identities for B66 choices over real environmental damage."""

from typing import Literal

from pydantic import Field, model_validator

from wayfarer.engine.simulation.health.hazard_damage import (
    Die,
    PreparedHazardDamage,
)
from wayfarer.engine.simulation.health.hazard_damage import (
    NaturalExposureDeclaration as NaturalExposureDeclaration,
)
from wayfarer.engine.simulation.health.hazard_damage import (
    OutsidePrerequisite as OutsidePrerequisite,
)
from wayfarer.engine.simulation.health.hazards import HazardCommand, HazardResult
from wayfarer.engine.simulation.resources import Command
from wayfarer.models import Id, Record


class PrepareOutsideEvent(Command):
    kind: Literal["prepare-outside-event"] = "prepare-outside-event"
    schedule_id: Id
    source: NaturalExposureDeclaration
    secret: bool = False
    exposure_command: HazardCommand | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class ChooseOutsideEvent(Command):
    kind: Literal["choose-outside-event"] = "choose-outside-event"
    pending_id: Id
    choice: Literal["use-luck", "resolve", "cancel"]


class OutsideEventPending(Record):
    kind: Literal["outside-event"] = "outside-event"
    id: Id
    actor_id: Id
    opened_elapsed_microseconds: int = Field(ge=0)
    secret: bool
    original: tuple[Die, ...] | None
    preparation: PreparedHazardDamage
    actor_json: str
    source: NaturalExposureDeclaration
    exposure_command: HazardCommand

    @model_validator(mode="after")
    def complete_original(self) -> OutsideEventPending:
        if self.secret != (self.original is None):
            raise ValueError("Secret outside events must remain unrolled")
        if self.original is not None and len(self.original) != self.preparation.dice_count:
            raise ValueError("Outside original does not match its source expression")
        if self.actor_id != self.preparation.schedule.actor_id:
            raise ValueError("Outside event must affect its recorded owner")
        return self

    @property
    def original_json(self) -> str | None:
        if self.original is None:
            return None
        return OutsideEventOutcome(
            schedule_id=self.preparation.schedule.id,
            dice=self.original,
            basic_damage=max(1, sum(self.original) + self.preparation.modifier),
        ).model_dump_json()


class OutsideEventOutcome(Record):
    schedule_id: Id
    dice: tuple[Die, ...]
    basic_damage: int = Field(ge=0)
    consequence: HazardResult | None = None
