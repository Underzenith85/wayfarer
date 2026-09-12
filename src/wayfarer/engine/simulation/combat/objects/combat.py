"""Object effects inside the existing campaign combat CAS (B483-485, B556)."""

import hashlib
from typing import Literal

from wayfarer.engine.rules.checks import CheckTrace, Outcome, draw_dice
from wayfarer.engine.rules.tables.combat import (
    shield_cover_dr,
    shield_defense_bonus,
    weapon_target_penalty,
)
from wayfarer.engine.rules.types.object import ObjectResult, residual_definition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.combat import Encounter, GridPoint
from wayfarer.engine.simulation.combat.critical import Die, TableRoll
from wayfarer.engine.simulation.equipment.catalog import (
    Damage,
    EquipmentProfile,
    MeleeMode,
    RangedMode,
)
from wayfarer.engine.simulation.equipment.objects import StressObject, apply_object
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.resources import Item, ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record


def effective_entry(runtime: RulesContext, item: Item) -> EquipmentProfile:
    from wayfarer.engine.simulation.actors import catalog

    if item.firearm_failure is not None and item.firearm_failure.kind in (
        "destroyed",
        "explosion",
        "dud",
    ):
        raise ValidationError("Destroyed firearm has no usable weapon mode")
    entries = {e.definition_id: e for e in catalog(runtime).entries}
    entry = entries[item.definition_id]
    residual = residual_definition(entry.durability, item.condition)
    if item.condition and item.condition.disabled and residual is None:
        raise ValidationError("Disabled equipment has no usable weapon mode")
    return entries[residual] if residual else entry


def synchronize(state: PlayState, encounter: Encounter) -> Encounter:
    return encounter.model_copy(
        update={
            "participants": tuple(
                p.model_copy(
                    update={
                        "ready_item_ids": tuple(
                            i.id
                            for i in state.resources.items
                            if i.owner_id == p.actor_id and i.ready and i.equipped
                        ),
                        "hand_bindings": tuple(
                            (i, h)
                            for i, h in p.hand_bindings
                            if any(
                                x.id == i and x.owner_id == p.actor_id and x.ready and x.equipped
                                for x in state.resources.items
                            )
                        ),
                    }
                )
                for p in encounter.participants
            )
        }
    )


def stress(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
    item_ids: tuple[str, ...],
) -> tuple[PlayState, Encounter]:
    """Only equipment actually used by this action; idle equipment never rolls."""
    for item_id in dict.fromkeys(item_ids):
        item = next(i for i in state.resources.items if i.id == item_id)
        condition = item.condition
        if (
            condition is None
            or condition.disabled
            or condition.hp > 0
            or condition.last_stress_at == state.resources.game_time
        ):
            continue
        resources, _ = apply_object(
            runtime.resources,
            state.resources,
            StressObject(
                id="combat-stress:"
                + hashlib.sha256(f"{command_id}:{item_id}".encode()).hexdigest(),
                actor_id=actor_id,
                expected_revision=state.resources.revision,
                item_id=item_id,
            ),
            system=True,
            rng=runtime.rng,
        )
        state = state.model_copy(update={"resources": resources})
    return state, synchronize(state, encounter)


def shock(state: PlayState, item_id: str) -> int:
    item = next(i for i in state.resources.items if i.id == item_id)
    condition = item.condition
    return (
        condition.shock
        if condition
        and condition.shock_until is not None
        and (state.resources.game_time <= condition.shock_until)
        else 0
    )


class BreakageResult(Record):
    kind: Literal["critical-breakage-v1"] = "critical-breakage-v1"
    table: TableRoll
    confirmation: TableRoll | None = None
    residual_die: Die | None = None
    item_id: str
    broken: bool


def critical_breakage(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    *,
    table: tuple[int, ...],
    defender_item: str | None,
    parrying: bool,
) -> tuple[PlayState, Encounter, tuple[int, ...], bool]:
    from wayfarer.engine.simulation.actors import catalog

    pending = encounter.pending_defense
    assert pending is not None
    item_id = defender_item if parrying else pending.weapon_id
    item = next(i for i in state.resources.items if i.id == item_id)
    entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
    profile = entry.durability
    number = sum(table)
    drop = number in (9, 10, 11) or (
        number == 14
        and (
            parrying
            or any(m.id == pending.mode_id and m.damage.basis != "swing" for m in entry.modes)
        )
    )
    if number not in (3, 4, 17, 18) and not (
        drop and profile and entry.critical_breakage == "cheap"
    ):
        return state, encounter, (), False
    if profile is None or item.condition is None or entry.critical_breakage is None:
        return state, encounter, (), False
    event_id = "critical-breakage:" + hashlib.sha256(pending.id.encode()).hexdigest()
    prior = next((e for e in state.resources.events if e.id == event_id), None)
    if prior:
        saved = BreakageResult.model_validate_json(prior.kind)
        if saved.table != table or saved.item_id != item_id:
            raise ConflictError("Recorded weapon breakage cannot be replaced")
        return (
            state,
            synchronize(state, encounter),
            ((saved.confirmation or ()) + ((saved.residual_die,) if saved.residual_die else ())),
            True,
        )
    confirmation = None
    broken = True
    if entry.critical_breakage == "resistant":
        confirmation = draw_dice(runtime.rng, 3)
        broken = sum(confirmation) in (3, 4, 17, 18)
    residual = draw_dice(runtime.rng, 1)[0] if broken and profile.residual_definitions else None
    condition = (
        item.condition.model_copy(update={"disabled": True, "residual_roll": residual})
        if broken
        else item.condition
    )
    usable = residual_definition(profile, condition) is not None
    from wayfarer.engine.simulation.combat.thrown.flight import position

    subject = next(p for p in encounter.participants if p.actor_id == item.owner_id)
    updated = item.model_copy(
        update={
            "condition": condition,
            "ready": usable,
            "equipped": item.equipped if broken else False,
            "ground": item.ground if broken else position(encounter, subject),
        }
    )
    saved = BreakageResult.model_validate(
        {
            "table": table,
            "confirmation": confirmation,
            "residual_die": residual,
            "item_id": item.id,
            "broken": broken,
        }
    )
    resources = state.resources.model_copy(
        update={
            "items": tuple(updated if i.id == item.id else i for i in state.resources.items),
            "events": state.resources.events
            + (
                ResourceEvent(
                    id=event_id,
                    at=state.resources.game_time,
                    target_id=item.id,
                    kind=saved.model_dump_json(),
                ),
            ),
        }
    )
    runtime.resources.validate(resources)
    state = state.model_copy(update={"resources": resources})
    return (
        state,
        synchronize(state, encounter),
        (confirmation or ()) + ((residual,) if residual else ()),
        True,
    )


def intercepting_shield(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    defense: CheckTrace | None,
    *,
    require_durable: bool = True,
    rapid_fire: bool = False,
) -> str | None:
    """B484: the DB must change an ordinary failed defense into success."""
    from wayfarer.engine.simulation.actors import catalog
    from wayfarer.engine.simulation.combat.objects.locations import item_hands
    from wayfarer.engine.simulation.health.hit_locations import disabled

    if defense is None or defense.outcome is not Outcome.SUCCESS:
        return None
    pending = encounter.pending_defense
    assert pending is not None
    if pending.target_item_id:
        return None
    entries = {e.definition_id: e for e in catalog(runtime).entries}
    unavailable = disabled(state.resources, pending.defender_id)
    shields = []
    for item in state.resources.items:
        entry = entries[item.definition_id]
        if (
            item.owner_id != pending.defender_id
            or not item.ready
            or not item.equipped
            or not entry.shield
        ):
            continue
        bonus = shield_defense_bonus(
            entry.shield.defense_bonus,
            any(
                h.replace("hand", "arm") in unavailable
                for h in item_hands(state, item.owner_id, item.id)
            ),
        )
        shields.append((bonus, item.id, entry.durability is not None))
    if not shields:
        return None
    bonus, item_id, durable = max(shields)
    return (
        item_id
        if (durable or not require_durable)
        and (rapid_fire or defense.total > defense.effective_target - bonus)
        else None
    )


def shield_damage(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    item_id: str,
    basic: int,
    weapon: MeleeMode | RangedMode | Damage,
    *,
    impact: int = 0,
) -> tuple[PlayState, Encounter, int]:
    """Cover DR is computed from the pinned maximum HP before the blow, B408/B484."""

    from wayfarer.engine.simulation.equipment.objects import DamageObject

    pending = encounter.pending_defense
    assert pending is not None
    item = next(i for i in state.resources.items if i.id == item_id)
    profile = runtime.resources.specs[item.definition_id].durability
    assert profile is not None
    damage = weapon if isinstance(weapon, Damage) else weapon.damage
    resources, _ = apply_object(
        runtime.resources,
        state.resources,
        DamageObject.model_validate(
            {
                "id": "shield-hit:" + hashlib.sha256(f"{pending.id}:{impact}".encode()).hexdigest(),
                "actor_id": pending.attacker_id,
                "expected_revision": state.resources.revision,
                "item_id": item_id,
                "basic_damage": basic,
                "damage_type": damage.damage_type,
                "armor_divisor": damage.armor_divisor,
            }
        ),
        system=True,
        shield=True,
        rng=runtime.rng,
    )
    state = state.model_copy(update={"resources": resources})
    cover = shield_cover_dr(profile.dr, profile.hp, damage.armor_divisor)
    return state, synchronize(state, encounter), max(0, basic - cover)


def target_modifier(runtime: RulesContext, state: PlayState, actor_id: str, item_id: str) -> int:
    """B400 weapon sizes; other equipped targets require a pinned SM (B483)."""
    from wayfarer.engine.simulation.actors import catalog

    item = next((i for i in state.resources.items if i.id == item_id), None)
    if item is None or item.owner_id != actor_id or (not item.equipped and not item.ground):
        raise ValidationError("Object target must be equipped or at a recorded ground position")
    entry = next(e for e in catalog(runtime).entries if e.definition_id == item.definition_id)
    if entry.durability is None or item.condition is None or item.condition.destroyed:
        raise ValidationError(
            "Object target requires an initialized, non-destroyed durability profile"
        )
    reaches = [max(m.reach) for m in entry.modes if isinstance(m, MeleeMode)]
    if reaches:
        reach = max(reaches)
        return weapon_target_penalty(reach)
    if entry.durability.size_modifier is None:
        raise ValidationError("Object target requires a pinned size modifier")
    return entry.durability.size_modifier


def damage_target(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    item_id: str,
    basic: int,
    damage: Damage,
    *,
    impact: int,
) -> tuple[PlayState, Encounter, ObjectResult | None]:
    """One authoritative object receipt per projectile; destroyed targets absorb no more rolls."""
    from wayfarer.engine.simulation.equipment.objects import DamageObject

    pending = encounter.pending_defense
    assert pending is not None
    item = next(i for i in state.resources.items if i.id == item_id)
    if item.condition and item.condition.destroyed:
        return state, encounter, None
    resources, result = apply_object(
        runtime.resources,
        state.resources,
        DamageObject.model_validate(
            {
                "id": "target-object:"
                + hashlib.sha256(f"{pending.id}:{impact}".encode()).hexdigest(),
                "actor_id": pending.attacker_id,
                "expected_revision": state.resources.revision,
                "item_id": item_id,
                "basic_damage": basic,
                "damage_type": damage.damage_type,
                "armor_divisor": damage.armor_divisor,
            }
        ),
        system=True,
        rng=runtime.rng,
    )
    state = state.model_copy(update={"resources": resources})
    return state, synchronize(state, encounter), result


def weapon_target(runtime: RulesContext, state: PlayState, item_id: str | None) -> bool:
    """B401 restricts defenses for weapon targets, including ranged weapons."""
    if item_id is None:
        return False
    from wayfarer.engine.simulation.actors import catalog

    item = next(i for i in state.resources.items if i.id == item_id)
    return any(e.definition_id == item.definition_id and e.modes for e in catalog(runtime).entries)


def defense_stress(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
    item_id: str | None,
) -> tuple[PlayState, Encounter]:
    """Stress the selected implement and shields that contribute defense bonus."""
    from wayfarer.engine.simulation.actors import catalog

    entries = {e.definition_id: e for e in catalog(runtime).entries}
    pending = encounter.pending_defense
    targeted_weapon = weapon_target(runtime, state, pending.target_item_id if pending else None)
    items = tuple(
        i.id
        for i in state.resources.items
        if i.owner_id == actor_id
        and i.equipped
        and i.ready
        and (i.id == item_id or (not targeted_weapon and entries[i.definition_id].shield))
    )
    return stress(runtime, state, encounter, actor_id, command_id, items)


def intercepted_projectiles(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    defense: CheckTrace | None,
    selected: str | None,
    hits: int,
) -> tuple[str | None, int]:
    """B373/B484: only projectiles avoided because of DB strike the shield."""
    shield = intercepting_shield(runtime, state, encounter, defense, rapid_fire=selected == "dodge")
    if shield is None or defense is None:
        return None, 0
    if selected != "dodge":
        return shield, 1
    from wayfarer.engine.simulation.combat.melee import defense_value

    pending = encounter.pending_defense
    assert pending is not None
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    without = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"ready": False}) if i.id == shield else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    value, _ = defense_value(runtime, without, defender, "dodge")
    assert value is not None
    avoided = min(hits, max(0, 1 + defense.effective_target - defense.total))
    without_avoided = min(hits, max(0, 1 + int(value.value) - defense.total))
    count = avoided - without_avoided
    return (shield if count else None), count


def target_positions(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    item_id: str,
) -> tuple[GridPoint | Hex, ...]:
    """B400–401: held weapon length occupies forward hexes; ground items keep their landing."""
    from wayfarer.engine.simulation.combat.combat import GridPoint
    from wayfarer.engine.simulation.hex_geometry import DIRECTIONS, Hex

    item = next(i for i in state.resources.items if i.id == item_id)
    owner = next(p for p in encounter.participants if p.actor_id == item.owner_id)
    if item.ground:
        if item.ground.encounter_id != encounter.id:
            raise ValidationError("Ground target belongs to another encounter")
        try:
            return (
                Hex(q=item.ground.x, r=item.ground.y)
                if item.ground.geometry == "hex"
                else GridPoint(x=item.ground.x, y=item.ground.y),
            )
        except ValueError as exc:
            raise ValidationError("Ground target is outside the combat board") from exc
    entry = effective_entry(runtime, item) if item.ready else None
    reach = (
        max((max(m.reach) for m in entry.modes if isinstance(m, MeleeMode)), default=0)
        if entry
        else 0
    )
    if not isinstance(owner.position, Hex) or reach == 0:
        return (owner.position,)
    assert owner.hex_facing is not None
    dq, dr = DIRECTIONS[owner.hex_facing]
    lengths = (0, 1) if reach == 1 else tuple(range(1, reach + 1))
    return tuple(Hex(q=owner.position.q + dq * n, r=owner.position.r + dr * n) for n in lengths)


def target_geometry(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    item_id: str,
    reach: frozenset[int] | None = None,
) -> Encounter:
    """Use a reachable visible part of the item for geometry, never move its owner."""
    from wayfarer.engine.simulation.combat.combat import CombatEngine, GridPoint
    from wayfarer.engine.simulation.combat.tactical import attack_geometry
    from wayfarer.engine.simulation.hex_geometry import Hex

    pending = encounter.pending_defense
    assert pending is not None
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    owner = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    for position in sorted(
        target_positions(runtime, state, encounter, item_id),
        key=lambda point: CombatEngine.distance(actor.position, point),
    ):
        assert isinstance(position, (GridPoint, Hex))
        target = owner.model_copy(update={"position": position})
        try:
            attack_geometry(encounter, actor, target, reach, board=runtime.hex_map(encounter))
            if reach is not None and CombatEngine.distance(actor.position, position) not in reach:
                continue
            return CombatEngine._replace(encounter, target)
        except ValidationError, ValueError:
            continue
    raise ValidationError("Object target has no reachable visible occupied position")


def worn_stress(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
) -> tuple[PlayState, Encounter]:
    """Worn protection is in use even while its wearer does not attack."""
    from wayfarer.engine.simulation.actors import catalog

    armor = {e.definition_id for e in catalog(runtime).entries if e.armor is not None}
    return stress(
        runtime,
        state,
        encounter,
        actor_id,
        command_id,
        tuple(
            i.id
            for i in state.resources.items
            if i.owner_id == actor_id and i.equipped and i.definition_id in armor
        ),
    )
