"""Durable Basic critical-miss context, independent of command transports."""

from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, model_validator

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.location_types import Hand, HitLocation, HumanLocation
from wayfarer.simulation.gurps_equipment import DamageType, MeleeMode
from wayfarer.simulation.resources import Id, Record, ResourceEvent, ResourceState

Die = Annotated[int, Field(ge=1, le=6)]
TableRoll = tuple[Die, Die, Die]
Digest = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]


class CriticalWeapon(Record):
    mode: MeleeMode
    dice: int = Field(ge=1)
    adds: int


class IncomingWound(Record):
    actor_id: Id
    dice: int = Field(ge=1)
    adds: int
    damage_type: DamageType
    resistance: int = Field(ge=0)
    ht: int = Field(ge=1)
    dx: int = Field(ge=1)
    hit_location: HitLocation | None = None
    armor_divisor: Decimal = Field(default=Decimal(1), gt=0, allow_inf_nan=False)
    tight_beam: bool = False
    from_behind: bool = False
    held_item_ids: tuple[str, ...] = ()
    hand_bindings: tuple[tuple[Id, Hand], ...] = ()
    shield_item_ids: tuple[Id, ...] = ()


class CriticalMiss(Record):
    kind: Literal["basic-critical-miss-v1"] = "basic-critical-miss-v1"
    id: Digest
    encounter_id: Id
    subject_id: Id
    item_id: Id
    action: Literal["attack", "parry"]
    table_rolls: tuple[TableRoll, ...] = Field(min_length=1, max_length=2)
    weapons: tuple[CriticalWeapon, ...] = Field(min_length=1)
    build_revision: Id
    catalog_digest: Digest
    ht: int = Field(ge=1)
    created_at: int = Field(ge=0)
    position: tuple[int, int]
    facing: Literal["north", "east", "south", "west"]
    anatomy: Literal["human"] | None = None
    limb_dr: tuple[tuple[HumanLocation, Annotated[int, Field(ge=0)]], ...]
    hand_bindings: tuple[tuple[Id, Hand], ...] = ()
    held_item_ids: tuple[Id, ...] = ()
    incoming: IncomingWound | None = None
    blocker: str = Field(min_length=1)

    @model_validator(mode="after")
    def validate_context(self) -> CriticalMiss:
        modes = tuple(weapon.mode.id for weapon in self.weapons)
        if len(modes) != len(set(modes)):
            raise ValueError("Critical weapon modes must be unique")
        if self.action == "attack" and self.incoming is not None:
            raise ValueError("An attacking critical miss cannot have an incoming wound")
        if self.action == "parry" and (
            self.incoming is None or self.incoming.actor_id != self.subject_id
        ):
            raise ValueError("A parrying critical miss requires its own incoming wound")
        if len(self.held_item_ids) != len(set(self.held_item_ids)):
            raise ValueError("Critical held item IDs must be unique")
        return self

    @property
    def table_total(self) -> int:
        return sum(self.table_rolls[-1])


def save_critical(state: ResourceState, record: CriticalMiss) -> ResourceState:
    record = CriticalMiss.model_validate(record)
    previous = load_critical(state, record.id)
    if previous is not None:
        if previous == record:
            return state
        raise ConflictError("A recorded critical context cannot be replaced")
    if record.created_at != state.game_time:
        raise ValidationError("Critical context must be captured at the current game time")
    return state.model_copy(
        update={
            "events": state.events
            + (
                ResourceEvent(
                    id=f"critical:{record.id}:{state.revision}",
                    at=state.game_time,
                    target_id=record.subject_id,
                    kind=record.model_dump_json(),
                ),
            )
        }
    )


def load_critical(state: ResourceState, critical_id: str) -> CriticalMiss | None:
    for event in reversed(state.events):
        if event.id.startswith(f"critical:{critical_id}:"):
            record = CriticalMiss.model_validate_json(event.kind)
            if record.id != critical_id or record.subject_id != event.target_id:
                raise ValidationError("Critical event identity does not match its context")
            return record
    return None
