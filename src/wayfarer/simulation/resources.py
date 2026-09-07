"""Immutable inventory and game-time commands, suitable for atomic persistence.

A command reducer performs no I/O. Persist its complete returned state under a
campaign revision lock; JSON checkpoints retain receipts and fired schedules.
"""

from __future__ import annotations

import hashlib
from copy import copy
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    RulesCatalog,
)
from wayfarer.rules.effects import Effect
from wayfarer.rules.injury_types import InjuryStatus
from wayfarer.world import EntityKind, World

Id = Annotated[str, Field(min_length=1, max_length=200)]
Count = Annotated[int, Field(ge=1, le=1000000)]
Tick = Annotated[int, Field(ge=0)]


class Record(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class EquipmentSpec(Record):
    """Trusted server mechanics bound to an implemented pinned catalog entry."""

    definition_id: Id
    unit_weight: int = Field(ge=0)
    stackable: bool = True
    container_capacity: int | None = Field(default=None, ge=0)
    slot: str | None = None
    ammunition: bool = False
    technology_level: int = Field(default=0, ge=0)
    required_definitions: tuple[str, ...] = ()
    effects: tuple[Effect, ...] = ()


class Item(Record):
    id: Id
    definition_id: Id
    owner_id: Id
    quantity: Count = 1
    container_id: str | None = None
    equipped: bool = False
    ready: bool = False


class Owner(Record):
    actor_id: Id
    capacity: int = Field(ge=0)
    definitions: tuple[str, ...] = ()


class Pool(Record):
    id: Id
    current: int
    maximum: int = Field(ge=0)
    injury: InjuryStatus | None = None

    @model_validator(mode="after")
    def validate_limits(self) -> Pool:
        if self.current > self.maximum:
            raise ValueError("Pool exceeds maximum")
        if self.injury is None and self.current < 0:
            raise ValueError("Prototype and fatigue pools cannot be negative")
        if self.injury is not None and (not self.id.startswith("hp:") or self.maximum < 1):
            raise ValueError("Profile injury requires a positive-maximum HP pool")
        return self


class Scheduled(Record):
    id: Id
    due: Tick
    kind: Literal["expire", "recover", "consequence"]
    target_id: Id
    amount: int = Field(default=0, ge=0)


class Receipt(Record):
    command_id: Id
    digest: str


class ResourceEvent(Record):
    id: Id
    at: Tick
    kind: str
    target_id: str


class ResourceState(Record):
    revision: Tick = 0
    game_time: Tick = 0
    items: tuple[Item, ...] = ()
    owners: tuple[Owner, ...] = ()
    pools: tuple[Pool, ...] = ()
    active_effect_ids: tuple[str, ...] = ()
    scheduled: tuple[Scheduled, ...] = ()
    fired: tuple[str, ...] = ()
    receipts: tuple[Receipt, ...] = ()
    events: tuple[ResourceEvent, ...] = ()


class Command(Record):
    id: Id
    actor_id: Id
    expected_revision: Tick


class Transfer(Command):
    kind: Literal["transfer"] = "transfer"
    item_id: Id
    quantity: Count
    owner_id: Id
    container_id: str | None = None
    new_item_id: str | None = None


class Consume(Command):
    kind: Literal["consume"] = "consume"
    item_id: Id
    quantity: Count
    require_ammunition: bool = False


class Equip(Command):
    kind: Literal["equip"] = "equip"
    item_id: Id
    ready: bool = True


class Unequip(Command):
    kind: Literal["unequip"] = "unequip"
    item_id: Id


class Schedule(Command):
    kind: Literal["schedule"] = "schedule"
    entry: Scheduled


class Advance(Command):
    kind: Literal["advance"] = "advance"
    to: Tick


ResourceCommand = Annotated[
    Transfer | Consume | Equip | Unequip | Schedule | Advance, Field(discriminator="kind")
]
COMMAND_ADAPTER: TypeAdapter[ResourceCommand] = TypeAdapter(ResourceCommand)


class ResourceEngine:
    def __init__(
        self,
        world: World,
        catalog: RulesCatalog,
        rules: CampaignRules,
        policy: CampaignPolicy,
        specs: tuple[EquipmentSpec, ...],
    ) -> None:
        world.validate()
        if (rules.policy_id, rules.policy_version) != (policy.id, policy.version):
            raise ValidationError("Campaign policy pin does not resolve")
        packages = tuple(catalog.package(pin) for pin in rules.packages)
        if (
            not packages
            or len(set(rules.packages)) != len(rules.packages)
            or any(p.edition != rules.edition for p in packages)
        ):
            raise ValidationError("Invalid equipment package pins")
        if any(dep not in {p.id for p in packages} for p in packages for dep in p.dependencies):
            raise ValidationError("Missing pinned equipment dependency")
        definitions = {d.id: d for p in packages for d in p.definitions}
        if len(definitions) != sum(len(p.definitions) for p in packages):
            raise ValidationError("Ambiguous equipment definitions")
        self.specs = {s.definition_id: s for s in specs}
        if len(self.specs) != len(specs):
            raise ValidationError("Duplicate equipment specification")
        for spec in specs:
            definition = definitions.get(spec.definition_id)
            if (
                definition is None
                or definition.kind is not DefinitionKind.EQUIPMENT
                or definition.status is not ImplementationStatus.IMPLEMENTED
                or definition.source_id not in policy.permitted_sources
                or definition.id not in policy.allowed_equipment
            ):
                raise ValidationError("Equipment is not implemented and permitted")
            if (
                policy.technology_level is not None
                and spec.technology_level > policy.technology_level
            ):
                raise ValidationError("Equipment exceeds campaign technology")
            if "supernatural" in definition.hooks and not policy.allow_supernatural:
                raise ValidationError("Supernatural equipment is forbidden")
            if any(key not in definitions for key in spec.required_definitions):
                raise ValidationError("Unknown equipment prerequisite")
            if not set(definition.prerequisites) <= set(spec.required_definitions):
                raise ValidationError("Equipment specification omits catalog prerequisites")
            for effect in spec.effects:
                if not any(
                    definition in p.definitions
                    and effect.source_id == definition.id
                    and effect.source_version == p.version
                    for p in packages
                ):
                    raise ValidationError("Equipment effect provenance mismatch")
        self.rules = rules
        self.actors = frozenset(e.id for e in world.entities if e.kind is EntityKind.ACTOR)

    def for_world(self, world: World) -> ResourceEngine:
        """Bind trusted equipment mechanics to a newly validated scenario's owners."""
        world.validate()
        engine = copy(self)
        engine.specs = dict(self.specs)
        engine.actors = frozenset(e.id for e in world.entities if e.kind is EntityKind.ACTOR)
        return engine

    def validate(self, state: ResourceState) -> None:
        def unique(values: tuple[str, ...]) -> None:
            if len(set(values)) != len(values):
                raise ValidationError("Duplicate resource ID")

        unique(tuple(i.id for i in state.items))
        unique(tuple(o.actor_id for o in state.owners))
        unique(tuple(p.id for p in state.pools))
        unique(tuple(s.id for s in state.scheduled))
        unique(tuple(r.command_id for r in state.receipts))
        unique(state.active_effect_ids)
        unique(state.fired)
        unique(tuple(s.target_id for s in state.scheduled if s.kind == "expire"))
        unique(tuple(e.id for e in state.events))
        if any(p.current > p.maximum for p in state.pools):
            raise ValidationError("Resource pool exceeds maximum")
        owners = {o.actor_id: o for o in state.owners}
        if not set(owners) <= self.actors:
            raise ValidationError("Inventory owner is not a world actor")
        items = {i.id: i for i in state.items}
        occupied: set[tuple[str, str]] = set()
        for item in state.items:
            spec = self.specs.get(item.definition_id)
            if spec is None or item.owner_id not in owners:
                raise ValidationError("Unknown equipment or owner")
            if (
                not spec.stackable or spec.container_capacity is not None or item.equipped
            ) and item.quantity != 1:
                raise ValidationError("Equipment instances must have quantity one")
            if item.ready and not item.equipped:
                raise ValidationError("Unequipped item cannot be ready")
            ancestors: set[str] = {item.id}
            parent_id = item.container_id
            while parent_id is not None:
                if parent_id in ancestors:
                    raise ValidationError("Container cycle")
                ancestors.add(parent_id)
                parent = items.get(parent_id)
                if parent is None or parent.owner_id != item.owner_id:
                    raise ValidationError("Container must exist with the same owner")
                parent_spec = self.specs.get(parent.definition_id)
                if parent_spec is None or parent_spec.container_capacity is None:
                    raise ValidationError("Item is not a container")
                parent_id = parent.container_id
            if item.equipped:
                if item.container_id is not None or spec.slot is None:
                    raise ValidationError("Equipment must be accessible and have a slot")
                if not set(spec.required_definitions) <= set(owners[item.owner_id].definitions):
                    raise ValidationError("Equipment prerequisites are not satisfied")
                slot = (item.owner_id, spec.slot)
                if slot in occupied:
                    raise ValidationError("Equipment slot is occupied")
                occupied.add(slot)

        contents = dict.fromkeys(items, 0)
        for item in state.items:
            weight = self.specs[item.definition_id].unit_weight * item.quantity
            parent_id = item.container_id
            while parent_id is not None:
                contents[parent_id] += weight
                parent_id = items[parent_id].container_id
        for item in state.items:
            capacity = self.specs[item.definition_id].container_capacity
            if capacity is not None and contents[item.id] > capacity:
                raise ValidationError("Container capacity exceeded")
        for owner in state.owners:
            if self.carried_weight(state, owner.actor_id) > owner.capacity:
                raise ValidationError("Carrying capacity exceeded")
        pending = {s.id for s in state.scheduled}
        if pending & set(state.fired):
            raise ValidationError("Fired schedule is still pending")
        for entry in state.scheduled:
            if entry.due < state.game_time:
                raise ValidationError("Overdue schedule")
            self._validate_schedule(state, entry)

    def _validate_schedule(self, state: ResourceState, entry: Scheduled) -> None:
        if entry.kind == "expire" and entry.target_id not in state.active_effect_ids:
            raise ValidationError("Unknown active effect")
        if entry.kind == "recover" and entry.target_id not in {p.id for p in state.pools}:
            raise ValidationError("Unknown recovery pool")
        if entry.kind == "consequence" and entry.target_id not in self.actors:
            raise ValidationError("Unknown delayed consequence actor")

    def carried_weight(self, state: ResourceState, actor_id: str) -> int:
        """Integer weight units; callers may feed this into an encumbrance effect."""
        return sum(
            self.specs[i.definition_id].unit_weight * i.quantity
            for i in state.items
            if i.owner_id == actor_id
        )

    def equipment_effects(self, state: ResourceState, actor_id: str) -> tuple[Effect, ...]:
        self.validate(state)
        return tuple(
            effect
            for item in state.items
            if item.owner_id == actor_id and item.ready
            for effect in self.specs[item.definition_id].effects
        )

    def apply(
        self, state: ResourceState, command: ResourceCommand, *, system: bool = False
    ) -> ResourceState:
        """system is a trusted call-site capability, never a command payload field."""
        self.validate(state)
        if command.actor_id not in self.actors:
            raise ValidationError("Command actor is not a world actor")
        if isinstance(command, (Schedule, Advance)) and not system:
            raise ValidationError("Scheduling and clock advancement require engine authority")
        digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
        previous = next((r for r in state.receipts if r.command_id == command.id), None)
        if previous:
            if previous.digest != digest:
                raise ConflictError("Command ID reused with a different payload")
            return state
        if command.expected_revision != state.revision:
            raise ConflictError("Resource revision changed")
        items = {i.id: i for i in state.items}
        updated = state
        if isinstance(command, (Transfer, Consume, Equip, Unequip)):
            item = items.get(command.item_id)
            if item is None or item.owner_id != command.actor_id:
                raise ValidationError("Item is not owned by command actor")
            spec = self.specs[item.definition_id]
            if isinstance(command, (Transfer, Consume)):
                if command.quantity > item.quantity:
                    raise ValidationError("Insufficient quantity")
                if item.equipped:
                    raise ValidationError("Unequip before transfer or consumption")
                if any(i.container_id == item.id for i in state.items):
                    raise ValidationError("Empty the container before transfer or consumption")
            if isinstance(command, Transfer):
                if command.owner_id not in {o.actor_id for o in state.owners}:
                    raise ValidationError("Unknown receiving owner")
                if command.quantity == item.quantity:
                    if command.new_item_id is not None:
                        raise ValidationError("Full transfers retain their stable item ID")
                    items[item.id] = Item(
                        **{
                            **item.model_dump(),
                            "owner_id": command.owner_id,
                            "container_id": command.container_id,
                        }
                    )
                else:
                    if not command.new_item_id or command.new_item_id in items:
                        raise ValidationError("Split requires a new unique item ID")
                    items[item.id] = Item(
                        **{**item.model_dump(), "quantity": item.quantity - command.quantity}
                    )
                    items[command.new_item_id] = Item(
                        id=command.new_item_id,
                        definition_id=item.definition_id,
                        owner_id=command.owner_id,
                        quantity=command.quantity,
                        container_id=command.container_id,
                    )
            elif isinstance(command, Consume):
                if command.require_ammunition and not spec.ammunition:
                    raise ValidationError("Item is not ammunition")
                if command.quantity == item.quantity:
                    del items[item.id]
                else:
                    items[item.id] = Item(
                        **{**item.model_dump(), "quantity": item.quantity - command.quantity}
                    )
            elif isinstance(command, Equip):
                items[item.id] = Item(
                    **{**item.model_dump(), "equipped": True, "ready": command.ready}
                )
            else:
                items[item.id] = Item(**{**item.model_dump(), "equipped": False, "ready": False})
            updated = state.model_copy(
                update={"items": tuple(sorted(items.values(), key=lambda i: i.id))}
            )
        elif isinstance(command, Schedule):
            entry = command.entry
            if (
                entry.due < state.game_time
                or entry.id in state.fired
                or entry.id in {s.id for s in state.scheduled}
            ):
                raise ValidationError("Schedule is past due or its ID was already used")
            self._validate_schedule(state, entry)
            if entry.kind == "expire" and any(
                s.kind == "expire" and s.target_id == entry.target_id for s in state.scheduled
            ):
                raise ValidationError("Effect already has an expiration")
            updated = state.model_copy(update={"scheduled": state.scheduled + (entry,)})
        elif isinstance(command, Advance):
            if command.to < state.game_time:
                raise ValidationError("Game time cannot move backwards")
            due = sorted(
                (s for s in state.scheduled if s.due <= command.to), key=lambda s: (s.due, s.id)
            )
            pools = {p.id: p for p in state.pools}
            effects = set(state.active_effect_ids)
            for entry in due:
                if entry.kind == "expire":
                    effects.remove(entry.target_id)
                elif entry.kind == "recover":
                    pool = pools[entry.target_id]
                    pools[pool.id] = Pool(
                        id=pool.id,
                        current=min(pool.maximum, pool.current + entry.amount),
                        maximum=pool.maximum,
                        injury=pool.injury,
                    )
            updated = state.model_copy(
                update={
                    "game_time": command.to,
                    "pools": tuple(pools.values()),
                    "active_effect_ids": tuple(sorted(effects)),
                    "scheduled": tuple(s for s in state.scheduled if s.due > command.to),
                    "fired": state.fired + tuple(s.id for s in due),
                    "events": state.events
                    + tuple(
                        ResourceEvent(
                            id=f"schedule:{s.id}", at=s.due, kind=s.kind, target_id=s.target_id
                        )
                        for s in due
                    ),
                }
            )
        updated = updated.model_copy(
            update={
                "revision": state.revision + 1,
                "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            }
        )
        self.validate(updated)
        return updated
