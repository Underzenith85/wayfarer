"""Immutable inventory and game-time commands, suitable for atomic persistence.

A command reducer performs no I/O. Persist its complete returned state under a
campaign revision lock; JSON checkpoints retain receipts and fired schedules.
"""

from __future__ import annotations

from decimal import Decimal
from fractions import Fraction
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.rules.effects import Effect
from wayfarer.engine.rules.magic.protocols import MagicItemInstance
from wayfarer.engine.rules.types.creature import Creature, Swarm
from wayfarer.engine.rules.types.electronics import ElectronicsSuite
from wayfarer.engine.rules.types.firearm import FirearmFailure
from wayfarer.engine.rules.types.hazard import (
    HazardSchedule,
    RecoveryRestriction,
)
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.object import (
    GroundPosition,
    ObjectCondition,
    ObjectProfile,
    ObjectResult,
)
from wayfarer.engine.rules.types.readiness import ProjectileProgress
from wayfarer.engine.rules.types.recovery import (
    FatigueStatus,
    RecoveryTask,
)
from wayfarer.engine.rules.types.survival import (
    SurvivalStatus,
    SurvivalTask,
    validate_survival_records,
)
from wayfarer.engine.rules.types.toxin import DrugDependency, Intoxication, ToxinExposure
from wayfarer.engine.rules.types.transport import Transport
from wayfarer.engine.simulation.magic.enchanting import EnchantmentProject
from wayfarer.engine.simulation.projects.inventions import InventionProject
from wayfarer.models import Count, Id, Record, Tick

ExactWeight = Annotated[int | Fraction, Field(ge=0)]


def decimal_weight(value: int | Fraction) -> Decimal:
    """Project an exact rational weight into Decimal arithmetic at the boundary."""
    if isinstance(value, Fraction):
        return Decimal(value.numerator) / Decimal(value.denominator)
    return Decimal(value)


def wire_weight(value: int | Fraction) -> int | float:
    """Return a JSON-number projection without changing authoritative arithmetic."""
    return float(value) if isinstance(value, Fraction) else value


class EquipmentSpec(Record):
    """Trusted server mechanics bound to an implemented pinned catalog entry."""

    definition_id: Id
    unit_weight: ExactWeight
    stackable: bool = True
    container_capacity: int | None = Field(default=None, ge=0)
    slot: str | None = None
    ammunition: bool = False
    technology_level: int = Field(default=0, ge=0)
    required_definitions: tuple[str, ...] = ()
    effects: tuple[Effect, ...] = ()
    durability: ObjectProfile | None = Field(default=None, exclude_if=lambda v: v is None)
    power_cell_capacity: int | None = Field(default=None, ge=1, exclude_if=lambda v: v is None)
    smartgun: bool = Field(default=False, exclude_if=lambda value: not value)
    electronics: ElectronicsSuite | None = Field(
        default=None, exclude_if=lambda value: value is None
    )


class Item(Record):
    id: Id
    definition_id: Id
    owner_id: Id
    quantity: Count = 1
    container_id: str | None = None
    equipped: bool = False
    ready: bool = False
    condition: ObjectCondition | None = Field(default=None, exclude_if=lambda v: v is None)
    ground: GroundPosition | None = Field(default=None, exclude_if=lambda v: v is None)
    firearm_failure: FirearmFailure | None = Field(default=None, exclude_if=lambda v: v is None)
    charges: int | None = Field(default=None, ge=0, exclude_if=lambda v: v is None)
    authorized_actor_ids: tuple[Id, ...] = Field(default=(), exclude_if=lambda value: not value)
    # Everyone currently serving a mounted weapon, the gunner included (#357).
    mount_crew: tuple[Id, ...] = Field(default=(), exclude_if=lambda v: not v)
    enchantments: tuple[MagicItemInstance, ...] = Field(default=(), exclude_if=lambda v: not v)


class Owner(Record):
    actor_id: Id
    capacity: int = Field(ge=0)
    definitions: tuple[str, ...] = ()


class Pool(Record):
    id: Id
    current: int
    maximum: int = Field(ge=0)
    injury: InjuryStatus | None = None
    fatigue: FatigueStatus | None = None

    @model_validator(mode="after")
    def validate_limits(self) -> Pool:
        if self.current > self.maximum:
            raise ValueError("Pool exceeds maximum")
        if self.injury is None and self.fatigue is None and self.current < 0:
            raise ValueError("Prototype and fatigue pools cannot be negative")
        if self.injury is not None and (not self.id.startswith("hp:") or self.maximum < 1):
            raise ValueError("Profile injury requires a positive-maximum HP pool")
        if self.fatigue is not None:
            if not self.id.startswith("fp:") or self.maximum < 1 or self.injury is not None:
                raise ValueError("Fatigue status requires a positive FP pool")
            if self.current < -self.maximum:
                raise ValueError("FP cannot fall below negative maximum")
            if (
                sum((self.fatigue.starvation, self.fatigue.dehydration, self.fatigue.sleep))
                > self.maximum - self.current
            ):
                raise ValueError("Restricted fatigue exceeds lost FP")
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


class AmmunitionLoad(Record):
    """Reserved inventory rounds; they retain mass and cannot be traded/consumed."""

    weapon_id: Id
    mode_id: Id
    ammunition_item_id: Id
    rounds: int = Field(ge=0)
    reload_progress: int = Field(default=0, ge=0)
    readiness: ProjectileProgress | None = Field(default=None, exclude_if=lambda v: v is None)


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
    ammunition_loads: tuple[AmmunitionLoad, ...] = ()
    expended_items: tuple[Item, ...] = ()
    recovery_tasks: tuple[RecoveryTask, ...] = ()
    survival: tuple[SurvivalStatus, ...] = Field(default=(), exclude_if=lambda value: not value)
    survival_tasks: tuple[SurvivalTask, ...] = Field(default=(), exclude_if=lambda value: not value)
    hazards: tuple[HazardSchedule, ...] = ()
    illnesses: tuple[RecoveryRestriction, ...] = ()
    toxins: tuple[ToxinExposure, ...] = Field(default=(), exclude_if=lambda v: not v)
    intoxications: tuple[Intoxication, ...] = Field(default=(), exclude_if=lambda v: not v)
    dependencies: tuple[DrugDependency, ...] = Field(default=(), exclude_if=lambda v: not v)
    transports: tuple[Transport, ...] = Field(default=(), exclude_if=lambda v: not v)
    creatures: tuple[Creature, ...] = Field(default=(), exclude_if=lambda v: not v)
    swarms: tuple[Swarm, ...] = Field(default=(), exclude_if=lambda v: not v)
    object_results: tuple[ObjectResult, ...] = Field(default=(), exclude_if=lambda v: not v)
    inventions: tuple[InventionProject, ...] = Field(default=(), exclude_if=lambda v: not v)
    enchantment_projects: tuple[EnchantmentProject, ...] = Field(
        default=(), exclude_if=lambda v: not v
    )

    @model_validator(mode="after")
    def validate_recovery_tasks(self) -> ResourceState:
        validate_survival_records(self.survival, self.survival_tasks, self.game_time)
        if len({h.id for h in self.hazards}) != len(self.hazards):
            raise ValueError("Duplicate hazard schedule ID")
        if len({i.id for i in self.illnesses}) != len(self.illnesses):
            raise ValueError("Duplicate illness restriction ID")
        _validate_toxin_state(self)
        if any(h.due < h.started or h.started > self.game_time for h in self.hazards):
            raise ValueError("Invalid hazard timeline")
        if len({t.id for t in self.transports}) != len(self.transports):
            raise ValueError("Duplicate transport ID")
        if len({creature.actor_id for creature in self.creatures}) != len(self.creatures):
            raise ValueError("Duplicate creature actor ID")
        manifest = [
            actor
            for t in self.transports
            for actor in (
                *t.occupants,
                *t.overboard,
                *(e.actor_id for e in t.pending_ejections),
                t.body_id,
            )
        ]
        if len(set(manifest)) != len(manifest):
            raise ValueError("Transport bodies and occupants cannot be shared")
        pools = {p.id: p for p in self.pools}
        for hazard in self.hazards:
            hp = pools.get("hp:" + hazard.actor_id)
            if hp is None or hp.injury is None or hp.injury.profile_id != hazard.spec.profile_id:
                raise ValueError("Hazard requires a matching profile actor")
            if (
                hazard.active
                and not hp.injury.dead
                and (
                    hazard.remaining == 0
                    or (hazard.combat_turn is None and hazard.due < self.game_time)
                )
            ):
                raise ValueError("Active hazard cannot have expired or exhausted cycles")
        if len({task.id for task in self.recovery_tasks}) != len(self.recovery_tasks):
            raise ValueError("Duplicate recovery task ID")
        for task in self.recovery_tasks:
            if task.due <= task.start or task.start > self.game_time:
                raise ValueError("Invalid recovery task timeline")
            if task.status == "interrupted" and (
                task.interrupted_at is None or not task.start <= task.interrupted_at < task.due
            ):
                raise ValueError("Interrupted recovery requires its interruption time")
            if task.status == "completed" and (not task.settled or task.due > self.game_time):
                raise ValueError("Completed recovery must be due and settled")
        return self

    @model_validator(mode="after")
    def validate_swarms(self) -> ResourceState:
        if len({swarm.id for swarm in self.swarms}) != len(self.swarms):
            raise ValueError("Duplicate swarm ID")
        if len({swarm.actor_id for swarm in self.swarms}) != len(self.swarms):
            raise ValueError("Duplicate swarm actor ID")
        if {creature.actor_id for creature in self.creatures} & {
            swarm.actor_id for swarm in self.swarms
        }:
            raise ValueError("A world actor cannot be both one creature and one swarm")
        pools = {pool.id: pool for pool in self.pools}
        for swarm in self.swarms:
            hp = pools.get("hp:" + swarm.actor_id)
            if hp is None or hp.injury is None or hp.injury.profile_id != "gurps-basic-set-4e-2004":
                raise ValueError("Swarm requires a canonical Basic Set injury pool")
            if hp.maximum != swarm.spec.dispersal_hp or hp.current != swarm.remaining_hp:
                raise ValueError("Swarm HP pool must match its dispersal state")
        return self


def _validate_toxin_state(state: ResourceState) -> None:
    if len({t.id for t in state.toxins}) != len(state.toxins):
        raise ValueError("Duplicate toxin exposure ID")
    if len({i.actor_id for i in state.intoxications}) != len(state.intoxications):
        raise ValueError("Duplicate intoxication actor")
    if len({d.id for d in state.dependencies}) != len(state.dependencies):
        raise ValueError("Duplicate drug dependency ID")
    if any(
        t.started > state.game_time or (t.active and t.due < state.game_time) for t in state.toxins
    ):
        raise ValueError("Invalid toxin deadline")
    if any(
        d.started > state.game_time or (d.active and d.due < state.game_time)
        for d in state.dependencies
    ):
        raise ValueError("Invalid withdrawal deadline")


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


class RechargePowerCell(Command):
    kind: Literal["recharge_power_cell"] = "recharge_power_cell"
    item_id: Id
    charges: Count
    source_id: Id


ResourceCommand = Annotated[
    Transfer | Consume | Equip | Unequip | Schedule | Advance | RechargePowerCell,
    Field(discriminator="kind"),
]
COMMAND_ADAPTER: TypeAdapter[ResourceCommand] = TypeAdapter(ResourceCommand)
