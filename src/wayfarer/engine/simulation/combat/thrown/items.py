"""B376/B381/B383 thrown identity, witnessed landings and local recovery."""

import hashlib
from typing import TYPE_CHECKING, Literal

from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.combat import Battlefield, CombatEngine, Encounter
from wayfarer.engine.simulation.resources import Item, ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Record

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import ChooseDefense, TakeCombatTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


class ThrownRecord(Record):
    kind: Literal["thrown-item-v1"] = "thrown-item-v1"
    attack_id: str
    encounter_id: str
    item_id: str
    thrower_id: str
    observed_by: tuple[str, ...]
    landing: GroundPosition | None = None
    caught_by: str | None = None


def record(resources: ResourceState, item_id: str) -> ThrownRecord | None:
    for event in reversed(resources.events):
        if event.id.startswith("thrown-item:") and event.target_id == item_id:
            return ThrownRecord.model_validate_json(event.kind)
    return None


def save(resources: ResourceState, value: ThrownRecord, cause_id: str) -> ResourceState:
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id="thrown-item:" + hashlib.sha256(cause_id.encode()).hexdigest(),
                    at=resources.game_time,
                    target_id=value.item_id,
                    kind=value.model_dump_json(),
                ),
            )
        }
    )


def landed(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    item: Item,
    *,
    hit: bool,
    catcher_id: str | None = None,
    hand: str | None = None,
) -> tuple[ResourceState, Encounter]:
    from wayfarer.engine.simulation.combat.thrown.flight import position
    from wayfarer.engine.simulation.combat.visibility import visible_actors

    pending = encounter.pending_defense
    assert pending is not None
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    observers = tuple(
        p.actor_id
        for p in encounter.participants
        if pending.attacker_id
        in visible_actors(state, encounter, p.actor_id, board=runtime.hex_map(encounter))
    )
    target_item = next((i for i in state.resources.items if i.id == pending.target_item_id), None)
    landing = (
        (target_item.ground if target_item and target_item.ground else position(encounter, target))
        if hit
        else item.ground
    )
    value = ThrownRecord(
        attack_id=pending.id,
        encounter_id=encounter.id,
        item_id=item.id,
        thrower_id=pending.attacker_id,
        observed_by=observers,
        landing=landing,
        caught_by=catcher_id,
    )
    resources = state.resources
    if catcher_id is not None:
        assert hand is not None
        caught = item.model_copy(
            update={
                "owner_id": catcher_id,
                "container_id": None,
                "ground": None,
                "equipped": True,
                "ready": True,
            }
        )
        resources = resources.model_copy(
            update={"items": tuple(i for i in resources.items if i.id != item.id) + (caught,)}
        )
        target = target.model_copy(
            update={
                "ready_item_ids": target.ready_item_ids + (item.id,),
                "hand_bindings": target.hand_bindings + ((item.id, hand),),
            }
        )
        encounter = CombatEngine._replace(encounter, target)
    else:
        resources = resources.model_copy(
            update={
                "items": tuple(i for i in resources.items if i.id != item.id),
                "expended_items": resources.expended_items
                + (
                    item.model_copy(
                        update={
                            "ready": False,
                            "equipped": False,
                            "container_id": None,
                            "ground": landing,
                        }
                    ),
                ),
            }
        )
    return save(resources, value, pending.id), encounter


def declare_landing(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    item_id: str,
    landing: GroundPosition,
    command_id: str,
) -> ResourceState:
    from wayfarer.engine.simulation.combat.melee import catalog

    if catalog(runtime).profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Thrown landing requires the exact Basic Set profile")
    item = next((i for i in state.resources.expended_items if i.id == item_id), None)
    value = record(state.resources, item_id)
    if item is None or value is None or value.encounter_id != encounter.id or value.landing:
        raise ValidationError("No unresolved thrown landing is available")
    if landing.encounter_id != encounter.id:
        raise ValidationError("Landing must belong to the original encounter")
    if encounter.spatial_kind == "hex":
        from wayfarer.engine.simulation.hex_geometry import Hex

        if landing.geometry != "hex" or Hex(q=landing.x, r=landing.y) not in {
            c.position for c in runtime.require_hex(encounter).cells
        }:
            raise ValidationError("Landing must be a declared battlefield hex")
    else:
        rules = runtime.rules.combat
        assert rules is not None
        field = next(f for f in rules.battlefields if f.id == encounter.battlefield_id)
        if not isinstance(field, Battlefield):
            raise ValidationError("Square encounter requires a square template")
        if landing.geometry != "grid" or not (
            0 <= landing.x < field.width and 0 <= landing.y < field.height
        ):
            raise ValidationError("Landing must be a declared battlefield position")
    resources = state.resources.model_copy(
        update={
            "expended_items": tuple(
                i.model_copy(update={"ground": landing}) if i.id == item_id else i
                for i in state.resources.expended_items
            )
        }
    )
    return save(resources, value.model_copy(update={"landing": landing}), command_id)


def recover(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: TakeCombatTurn
) -> PlayState:
    from wayfarer.engine.simulation.combat.explosions import guard as blast_guard
    from wayfarer.engine.simulation.combat.melee import build, catalog
    from wayfarer.engine.simulation.combat.thrown.flight import position
    from wayfarer.engine.simulation.combat.unarmed import free_hands

    blast_guard(state.resources)
    equipment = catalog(runtime)
    if equipment.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Thrown recovery requires the exact Basic Set profile")
    item = next((i for i in state.resources.expended_items if i.id == command.item_id), None)
    value = record(state.resources, command.item_id or "")
    if (
        item is None
        or value is None
        or command.actor_id not in value.observed_by
        or value.encounter_id != encounter.id
        or value.landing is None
    ):
        raise ValidationError("Thrown item is unavailable")
    actor = next(p for p in encounter.participants if p.actor_id == command.actor_id)
    if value.landing != position(encounter, actor):
        raise ValidationError("Move to the thrown item's recorded position before recovery")
    if actor.posture not in ("kneeling", "sitting", "prone"):
        raise ValidationError("Ground recovery requires kneeling, sitting or lying down")
    if command.ready_hand not in free_hands(state, encounter, command.actor_id):
        raise ValidationError("Recovery requires an explicit free usable hand")
    if item.firearm_failure and item.firearm_failure.kind in ("dud", "destroyed", "explosion"):
        raise ValidationError("Spent or exploded weapons cannot be readied")
    if item.condition and item.condition.disabled:
        raise ValidationError("A broken thrown weapon cannot be readied")
    stats = build(runtime, state, command.actor_id).statistics
    assert stats is not None
    entry = next(e for e in equipment.entries if e.definition_id == item.definition_id)
    if entry.weight_millipounds > stats.basic_lift * 1000:
        raise ValidationError("Heavy ground items require a multi-Ready lifting procedure")
    recovered = item.model_copy(
        update={
            "owner_id": command.actor_id,
            "ground": None,
            "ready": False,
            "equipped": False,
            "container_id": None,
        }
    )
    resources = state.resources.model_copy(
        update={
            "items": state.resources.items + (recovered,),
            "expended_items": tuple(i for i in state.resources.expended_items if i.id != item.id),
        }
    )
    runtime.resources.validate(resources)
    return state.model_copy(update={"resources": resources})


def undo_recovery(
    before: ResourceState, resources: ResourceState, item_id: str | None
) -> ResourceState:
    original = next(i for i in before.expended_items if i.id == item_id)
    return resources.model_copy(
        update={
            "items": tuple(i for i in resources.items if i.id != item_id),
            "expended_items": tuple(i for i in resources.expended_items if i.id != item_id)
            + (original,),
        }
    )


def validate_catch(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: ChooseDefense
) -> None:
    from wayfarer.engine.simulation.combat.melee import catalog, mode
    from wayfarer.engine.simulation.combat.unarmed import free_hands
    from wayfarer.engine.simulation.equipment.catalog import RangedMode

    pending = encounter.pending_defense
    if (
        pending is None
        or pending.defender_id != command.actor_id
        or catalog(runtime).profile_id != "gurps-basic-set-4e-2004"
    ):
        raise ValidationError("Catching requires a pending Basic Set thrown attack")
    weapon = mode(runtime, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    if not isinstance(weapon, RangedMode) or not weapon.catchable:
        raise ValidationError("Catching requires an opted-in thrown mode")
    if not any(
        choice == "parry" and hand in free_hands(state, encounter, command.actor_id)
        for choice, hand in (
            (command.defense, command.item_id),
            (command.second_defense, command.second_item_id),
        )
    ):
        raise ValidationError("Catching requires a selected free usable bare hand")
