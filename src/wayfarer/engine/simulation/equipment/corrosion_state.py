"""Immutable B379 armor erosion and item-aware protection projection."""

import hashlib

from pydantic import Field, model_validator

from wayfarer.engine.simulation.equipment.catalog import EquipmentProfile
from wayfarer.engine.simulation.resources import Item, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record

PREFIX = "corrosion-armor:"


def profile_digest(entry: EquipmentProfile) -> str:
    return hashlib.sha256(entry.model_dump_json().encode()).hexdigest()


class CorrosionProtection(Record):
    actor_id: str
    item_id: str | None = None
    definition_id: str | None = None
    profile_digest: str | None = None
    original_dr: int = Field(default=0, ge=0)
    previous_loss: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def exact(self) -> CorrosionProtection:
        if (self.item_id is None) != (self.definition_id is None) or (self.item_id is None) != (
            self.profile_digest is None
        ):
            raise ValueError("Corrosion protection requires exact physical item provenance")
        if self.previous_loss > self.original_dr or (
            self.item_id is None and self.original_dr != 0
        ):
            raise ValueError("Corrosion loss must match its physical protection")
        return self

    @property
    def resistance(self) -> int:
        return self.original_dr - self.previous_loss


class ArmorErosion(Record):
    command_id: str
    protection: CorrosionProtection
    basic_damage: int = Field(ge=5)
    lost_dr: int = Field(gt=0)
    total_loss: int = Field(gt=0)

    @model_validator(mode="after")
    def exact(self) -> ArmorErosion:
        if (
            self.protection.item_id is None
            or self.total_loss != self.protection.previous_loss + self.lost_dr
            or self.total_loss > self.protection.original_dr
            or self.lost_dr != min(self.protection.resistance, self.basic_damage // 5)
        ):
            raise ValueError("Armor erosion requires its exact prior loss and bounded total")
        return self


def erosion(resources: ResourceState, item_id: str) -> ArmorErosion | None:
    previous = None
    for event in resources.events:
        if not event.id.startswith(PREFIX):
            continue
        value = ArmorErosion.model_validate_json(event.kind)
        if value.protection.item_id != item_id:
            continue
        expected = 0 if previous is None else previous.total_loss
        if value.protection.previous_loss != expected or (
            previous is not None
            and (
                value.protection.definition_id != previous.protection.definition_id
                or value.protection.profile_digest != previous.protection.profile_digest
                or value.protection.original_dr != previous.protection.original_dr
            )
        ):
            raise ConflictError("Armor corrosion history has inconsistent physical provenance")
        previous = value
    return previous


def projected_profile(
    resources: ResourceState, item: Item, entry: EquipmentProfile
) -> EquipmentProfile:
    """Catalog values remain pinned; only this exact item's protection is projected."""
    value = erosion(resources, item.id)
    if value is None:
        return entry
    if (
        item.definition_id != value.protection.definition_id
        or entry.definition_id != item.definition_id
        or profile_digest(entry) != value.protection.profile_digest
        or entry.armor is None
        or entry.armor.dr != value.protection.original_dr
    ):
        raise ConflictError("Corroded armor no longer matches its pinned physical profile")
    return entry.model_copy(
        update={"armor": entry.armor.model_copy(update={"dr": entry.armor.dr - value.total_loss})}
    )


def require_current(resources: ResourceState, value: CorrosionProtection, resistance: int) -> None:
    if value.resistance != resistance:
        raise ConflictError("Corrosion protection no longer matches the current damage resistance")
    if value.item_id is None:
        return
    item = next((i for i in resources.items if i.id == value.item_id), None)
    prior = erosion(resources, value.item_id)
    if (
        item is None
        or item.owner_id != value.actor_id
        or not item.equipped
        or item.quantity != 1
        or item.definition_id != value.definition_id
        or item.condition is not None
        and item.condition.disabled
        or value.previous_loss != (0 if prior is None else prior.total_loss)
    ):
        raise ConflictError("Corrosion armor identity, custody or previous protection changed")


def erode(
    resources: ResourceState, value: CorrosionProtection, command_id: str, basic_damage: int
) -> ResourceState:
    """Run after scoring this hit with its original protection, including stopped hits."""
    require_current(resources, value, value.resistance)
    amount = min(value.resistance, basic_damage // 5)
    if value.item_id is None or amount == 0:
        return resources
    event_id = PREFIX + hashlib.sha256(command_id.encode()).hexdigest()
    if any(e.id == event_id for e in resources.events):
        raise ValidationError("Corrosion injury must use its original canonical receipt")
    loss = ArmorErosion(
        command_id=command_id,
        protection=value,
        basic_damage=basic_damage,
        lost_dr=amount,
        total_loss=value.previous_loss + amount,
    )
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=resources.game_time,
                    target_id=value.item_id,
                    kind=loss.model_dump_json(),
                ),
            )
        }
    )
