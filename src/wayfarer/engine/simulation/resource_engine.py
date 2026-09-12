"""Applying and validating one resource command against the ledger.

The engine is the kernel that calls up into the domain reducers -- explosions,
fright, repairs, backfires, rest -- so it sits above both them and the ledger
records in ``resources``.  Modules that only annotate a parameter with
``ResourceEngine`` import it under ``TYPE_CHECKING``; importing it eagerly from
a domain ledger would put the cycle back.
"""

from __future__ import annotations

import hashlib
from copy import copy
from fractions import Fraction

from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    RulesCatalog,
)
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.effects import Effect
from wayfarer.engine.rules.types.hazard import require_hazards_settled
from wayfarer.engine.rules.types.object import residual_definition
from wayfarer.engine.rules.types.recovery import require_settled, retire_tasks
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.simulation.combat.explosions import guard as blast_guard
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.health.fright import advance
from wayfarer.engine.simulation.health.fright import effects as fright_effects
from wayfarer.engine.simulation.health.medical.rest import accrue_rest
from wayfarer.engine.simulation.magic.backfires import backfires
from wayfarer.engine.simulation.resources import (
    Advance,
    Consume,
    Equip,
    EquipmentSpec,
    Item,
    Pool,
    Receipt,
    RechargePowerCell,
    ResourceCommand,
    ResourceEvent,
    ResourceState,
    Schedule,
    Scheduled,
    Transfer,
    Unequip,
)
from wayfarer.engine.world import EntityKind, World
from wayfarer.errors import ConflictError, ValidationError


def _require_splittable(item: Item) -> None:
    if item.enchantments:
        raise ValidationError("Enchanted items cannot be split")


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
        if any(not b.resolved and b.due < state.game_time for b in blasts(state)):
            raise ValidationError("Unresolved explosion deadline cannot be in the past")

        def unique(values: tuple[str, ...]) -> None:
            if len(set(values)) != len(values):
                raise ValidationError("Duplicate resource ID")

        unique(tuple(i.id for i in state.items + state.expended_items))
        unique(tuple(o.actor_id for o in state.owners))
        unique(tuple(p.id for p in state.pools))
        unique(tuple(s.id for s in state.scheduled))
        unique(tuple(r.command_id for r in state.receipts))
        unique(tuple(r.command_id for r in state.object_results))
        if not {r.command_id for r in state.object_results} <= {
            r.command_id for r in state.receipts
        }:
            raise ValidationError("Object result requires a matching receipt")
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
        unique(tuple(load.weapon_id for load in state.ammunition_loads))
        reserved: dict[str, int] = {}
        for load in state.ammunition_loads:
            weapon = items.get(load.weapon_id)
            ammo = items.get(load.ammunition_item_id)
            if weapon is None or ammo is None or weapon.owner_id != ammo.owner_id:
                raise ValidationError("Loaded ammunition must remain with its weapon owner")
            if (
                ammo.definition_id not in self.specs
                or not self.specs[ammo.definition_id].ammunition
            ):
                raise ValidationError("Loaded item must be ammunition")
            reserved[ammo.id] = reserved.get(ammo.id, 0) + load.rounds
        if any(
            (ammo.quantity if ammo.charges is None else ammo.charges) < amount
            for ammo, amount in ((items[key], count) for key, count in reserved.items())
        ):
            raise ValidationError("Cannot consume or transfer reserved ammunition")
        occupied: dict[tuple[str, str], int] = {}
        for item in state.items:
            spec = self.specs.get(item.definition_id)
            if spec is None or item.owner_id not in owners:
                raise ValidationError("Unknown equipment or owner")
            if (
                not spec.stackable or spec.container_capacity is not None or item.equipped
            ) and item.quantity != 1:
                raise ValidationError("Equipment instances must have quantity one")
            if spec.durability is not None:
                if item.quantity != 1 or item.condition is None:
                    raise ValidationError(
                        "Durable objects require individual initialized instances"
                    )
                condition = item.condition
                if condition.hp > spec.durability.hp:
                    raise ValidationError("Object HP exceeds maximum")
                if condition.hp <= -5 * spec.durability.hp and not condition.destroyed:
                    raise ValidationError("Object below destruction threshold")
                if (
                    condition.last_stress_at is not None
                    and condition.last_stress_at > state.game_time
                ):
                    raise ValidationError("Object stress time is in the future")

                if (
                    condition.disabled
                    and item.ready
                    and not residual_definition(spec.durability, condition)
                ):
                    raise ValidationError("Disabled equipment cannot be ready")
            elif item.condition is not None:
                raise ValidationError("Object condition requires a pinned durability profile")
            self._validate_item_charge(item, spec)
            if len({binding.id for binding in item.enchantments}) != len(item.enchantments) or any(
                binding.item_id != item.id or binding.owner_id != item.owner_id
                for binding in item.enchantments
            ):
                raise ValidationError(
                    "Magic-item bindings must be unique and follow item ownership"
                )
            if spec.smartgun:
                if (
                    not item.authorized_actor_ids
                    or len(set(item.authorized_actor_ids)) != len(item.authorized_actor_ids)
                    or not set(item.authorized_actor_ids) <= set(owners)
                ):
                    raise ValidationError("Smartgun requires explicit valid authorized actors")
            elif item.authorized_actor_ids:
                raise ValidationError("Authorization facts require a pinned smartgun")
            if item.ready and not item.equipped:
                raise ValidationError("Unequipped item cannot be ready")
            if item.ground is not None and (item.equipped or item.ready or item.container_id):
                raise ValidationError("Ground equipment cannot be equipped or contained")
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
                count = occupied.get(slot, 0) + 1
                if count > (2 if spec.slot == "hand" else 1):
                    raise ValidationError("Equipment slot is occupied")
                occupied[slot] = count

        contents: dict[str, int | Fraction] = dict.fromkeys(items, 0)
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

    @staticmethod
    def _validate_item_charge(item: Item, spec: EquipmentSpec) -> None:
        capacity = spec.power_cell_capacity
        message = "Power cell requires explicit charges within pinned capacity"
        if spec.electronics is not None and spec.electronics.power_capacity_seconds is not None:
            capacity = spec.electronics.power_capacity_seconds
            message = "Electronic device requires explicit charge within pinned capacity"
        if capacity is not None and (
            item.quantity != 1 or item.charges is None or item.charges > capacity
        ):
            raise ValidationError(message)
        if capacity is None and item.charges is not None:
            raise ValidationError("Charges require a pinned power-cell definition")

    def _validate_schedule(self, state: ResourceState, entry: Scheduled) -> None:
        if entry.kind == "expire" and entry.target_id not in state.active_effect_ids:
            raise ValidationError("Unknown active effect")
        if entry.kind == "recover" and entry.target_id not in {p.id for p in state.pools}:
            raise ValidationError("Unknown recovery pool")
        if entry.kind == "consequence" and entry.target_id not in self.actors:
            raise ValidationError("Unknown delayed consequence actor")

    def carried_weight(self, state: ResourceState, actor_id: str) -> int | Fraction:
        """Exact weight units; fractional units remain rational through conservation."""
        return sum(
            self.specs[i.definition_id].unit_weight * i.quantity
            for i in state.items
            if i.owner_id == actor_id and i.ground is None
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
        self,
        state: ResourceState,
        command: ResourceCommand,
        *,
        system: bool = False,
        rng: RandomSource | None = None,
    ) -> ResourceState:
        """system is a trusted call-site capability, never a command payload field."""
        self.validate(state)
        if command.actor_id not in self.actors:
            raise ValidationError("Command actor is not a world actor")
        if isinstance(command, (Schedule, Advance, RechargePowerCell)) and not system:
            raise ValidationError(
                "Scheduling, clock advancement and recharge require engine authority"
            )
        digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
        previous = next((r for r in state.receipts if r.command_id == command.id), None)
        if previous:
            if previous.digest != digest:
                raise ConflictError("Command ID reused with a different payload")
            return state
        if command.expected_revision != state.revision:
            raise ConflictError("Resource revision changed")
        if isinstance(command, Advance) and rng is not None:
            return advance(self, state, command, rng=rng)
        if not isinstance(command, Advance):
            blast_guard(state)
            require_settled(state.recovery_tasks, frozenset({command.actor_id}), state.game_time)
            require_hazards_settled(state.hazards, frozenset({command.actor_id}), state.game_time)
        items = {i.id: i for i in state.items}
        updated = state
        if isinstance(command, (Transfer, Consume, Equip, Unequip)):
            item = items.get(command.item_id)
            if item is None or item.owner_id != command.actor_id:
                raise ValidationError("Item is not owned by command actor")
            if item.ground is not None:
                raise ValidationError(
                    "Ground equipment requires authoritative retrieval at its location"
                )
            if any(
                t.status == "pending" and item.id in (t.item_id, t.tool_id) for t in tasks(state)
            ):
                raise ConflictError("Equipment is committed to a pending repair")
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
                            "enchantments": tuple(
                                binding.model_copy(update={"owner_id": command.owner_id})
                                for binding in item.enchantments
                            ),
                        }
                    )
                else:
                    _require_splittable(item)
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
                if (
                    item.condition is not None
                    and item.condition.disabled
                    and not residual_definition(spec.durability, item.condition)
                ):
                    raise ValidationError("Disabled equipment cannot be equipped")
                items[item.id] = Item(
                    **{**item.model_dump(), "equipped": True, "ready": command.ready}
                )
            else:
                items[item.id] = Item(**{**item.model_dump(), "equipped": False, "ready": False})
            updated = state.model_copy(
                update={"items": tuple(sorted(items.values(), key=lambda i: i.id))}
            )
        elif isinstance(command, RechargePowerCell):
            item = items.get(command.item_id)
            if item is None or item.owner_id != command.actor_id:
                raise ValidationError("Recharge requires an owned power cell")
            spec = self.specs[item.definition_id]
            if spec.power_cell_capacity is None or item.charges is None:
                raise ValidationError("Recharge requires a pinned physical power cell")
            if any(load.ammunition_item_id == item.id for load in state.ammunition_loads):
                raise ValidationError("Unload a power cell before recharging it")
            if item.charges + command.charges > spec.power_cell_capacity:
                raise ValidationError("Recharge exceeds pinned power-cell capacity")
            items[item.id] = item.model_copy(update={"charges": item.charges + command.charges})
            updated = state.model_copy(
                update={
                    "items": tuple(sorted(items.values(), key=lambda entry: entry.id)),
                    "events": state.events
                    + (
                        ResourceEvent(
                            id=f"recharge:{command.id}",
                            at=state.game_time,
                            target_id=item.id,
                            kind=f"power-cell:{command.source_id}:{command.charges}",
                        ),
                    ),
                }
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
            blast_guard(state, advance_to=command.to)
            if any(
                i.active and i.due is not None and i.due < command.to for i in fright_effects(state)
            ):
                raise ConflictError("Advance to the fright recovery deadline first")

            if any(
                b.stunned
                and b.stun_due_at is not None
                and state.game_time < b.stun_due_at < command.to
                for b in backfires(state)
            ):
                raise ConflictError("Advance to the mental-stun recovery deadline first")
            if command.to < state.game_time:
                raise ValidationError("Game time cannot move backwards")
            living = {
                p.id.removeprefix("hp:") for p in state.pools if p.injury and not p.injury.dead
            }
            if any(h.active and h.actor_id in living and h.due < command.to for h in state.hazards):
                raise ConflictError(
                    "Advance to the hazard deadline and resolve it before continuing"
                )
            if any(
                p.injury is not None
                and p.injury.mortal_wound
                and not p.injury.dead
                and (p.injury.mortal_wound_due is None or command.to > p.injury.mortal_wound_due)
                for p in state.pools
            ):
                raise ConflictError("Settle the mortal-wound survival check before advancing")
            if any(
                t.status == "pending" and not t.settled and command.to > t.due
                for t in state.recovery_tasks
            ):
                raise ConflictError(
                    "Advance to the recovery deadline and settle it before continuing"
                )
            due = sorted(
                (s for s in state.scheduled if s.due <= command.to), key=lambda s: (s.due, s.id)
            )
            pools = {p.id: p for p in state.pools}
            terminal_events: list[ResourceEvent] = []
            for fp_pool in state.pools:
                fatigue = fp_pool.fatigue
                if (
                    fatigue is None
                    or not fatigue.heart_attack
                    or fatigue.heart_attack_deadline is None
                    or fatigue.heart_attack_deadline > command.to
                ):
                    continue
                actor_id = fp_pool.id.removeprefix("fp:")
                hp_pool = pools.get(f"hp:{actor_id}")
                if hp_pool is None or hp_pool.injury is None or hp_pool.injury.dead:
                    continue
                pools[hp_pool.id] = hp_pool.model_copy(
                    update={"injury": hp_pool.injury.model_copy(update={"dead": True})}
                )
                terminal_events.append(
                    ResourceEvent(
                        id=f"heart-attack:{actor_id}:{fatigue.heart_attack_deadline}",
                        at=fatigue.heart_attack_deadline,
                        target_id=actor_id,
                        kind="heart-attack-death",
                    )
                )
            effects = set(state.active_effect_ids)
            for entry in due:
                if entry.kind == "expire":
                    effects.remove(entry.target_id)
                elif entry.kind == "recover":
                    pool = pools[entry.target_id]
                    if pool.injury is not None or pool.fatigue is not None:
                        raise ValidationError(
                            "Profile recovery requires a timed GURPS recovery task"
                        )
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
                    + tuple(terminal_events)
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
                    "recovery_tasks": retire_tasks(
                        updated.recovery_tasks,
                        frozenset(
                            p.id.removeprefix("hp:")
                            for p in updated.pools
                            if p.injury is not None and p.injury.dead
                        ),
                        command.to,
                    )
                }
            )
            updated = accrue_rest(updated, command.to)
        updated = updated.model_copy(
            update={
                "revision": state.revision + 1,
                "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            }
        )
        self.validate(updated)
        return updated
