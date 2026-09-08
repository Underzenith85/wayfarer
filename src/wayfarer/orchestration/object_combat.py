"""Object effects inside the existing campaign combat CAS (B483-485, B556)."""

import hashlib
from typing import Literal

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import CheckTrace, Outcome
from wayfarer.rules.object_types import residual_definition
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Encounter
from wayfarer.simulation.critical import Die, TableRoll
from wayfarer.simulation.gurps_equipment import EquipmentProfile, MeleeMode
from wayfarer.simulation.objects import StressObject, apply_object
from wayfarer.simulation.resources import Item, Record, ResourceEvent


def effective_entry(play: PlayService, item: Item) -> EquipmentProfile:
    from wayfarer.orchestration.gurps_melee import catalog

    entries = {e.definition_id: e for e in catalog(play).entries}
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
    play: PlayService,
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
            play.engine.resources,
            state.resources,
            StressObject(
                id="combat-stress:"
                + hashlib.sha256(f"{command_id}:{item_id}".encode()).hexdigest(),
                actor_id=actor_id,
                expected_revision=state.resources.revision,
                item_id=item_id,
            ),
            system=True,
            rng=play.rng,
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
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    *,
    table: tuple[int, ...],
    defender_item: str | None,
    parrying: bool,
) -> tuple[PlayState, Encounter, tuple[int, ...], bool]:
    from wayfarer.orchestration.gurps_melee import catalog

    pending = encounter.pending_defense
    assert pending is not None
    item_id = defender_item if parrying else pending.weapon_id
    item = next(i for i in state.resources.items if i.id == item_id)
    entry = next(e for e in catalog(play).entries if e.definition_id == item.definition_id)
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
        confirmation = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        broken = sum(confirmation) in (3, 4, 17, 18)
    residual = play.rng.randbelow(6) + 1 if broken and profile.residual_definitions else None
    condition = (
        item.condition.model_copy(update={"disabled": True, "residual_roll": residual})
        if broken
        else item.condition
    )
    usable = residual_definition(profile, condition) is not None
    from wayfarer.orchestration.weapon_flight import position

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
    play.engine.resources.validate(resources)
    state = state.model_copy(update={"resources": resources})
    return (
        state,
        synchronize(state, encounter),
        (confirmation or ()) + ((residual,) if residual else ()),
        True,
    )


def intercepting_shield(
    play: PlayService, state: PlayState, encounter: Encounter, defense: CheckTrace | None
) -> str | None:
    """B484: the DB must change an ordinary failed defense into success."""
    from wayfarer.orchestration.gurps_melee import catalog
    from wayfarer.orchestration.location_combat import disabled, item_hands

    if defense is None or defense.outcome is not Outcome.SUCCESS:
        return None
    pending = encounter.pending_defense
    assert pending is not None
    entries = {e.definition_id: e for e in catalog(play).entries}
    unavailable = disabled(state, pending.defender_id)
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
        bonus = max(
            0,
            entry.shield.defense_bonus
            - int(
                any(
                    h.replace("hand", "arm") in unavailable
                    for h in item_hands(state, item.owner_id, item.id)
                )
            ),
        )
        shields.append((bonus, item.id, entry.durability is not None))
    if not shields:
        return None
    bonus, item_id, durable = max(shields)
    return item_id if durable and defense.total > defense.effective_target - bonus else None


def shield_damage(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    item_id: str,
    basic: int,
    weapon: MeleeMode,
) -> tuple[PlayState, Encounter, int]:
    """Cover DR is computed from the pinned maximum HP before the blow, B408/B484."""
    from decimal import Decimal

    from wayfarer.simulation.objects import DamageObject

    pending = encounter.pending_defense
    assert pending is not None
    item = next(i for i in state.resources.items if i.id == item_id)
    profile = play.engine.resources.specs[item.definition_id].durability
    assert profile is not None
    resources, _ = apply_object(
        play.engine.resources,
        state.resources,
        DamageObject.model_validate(
            {
                "id": "shield-hit:" + hashlib.sha256(pending.id.encode()).hexdigest(),
                "actor_id": pending.attacker_id,
                "expected_revision": state.resources.revision,
                "item_id": item_id,
                "basic_damage": basic,
                "damage_type": weapon.damage.damage_type,
                "armor_divisor": weapon.damage.armor_divisor,
            }
        ),
        system=True,
        rng=play.rng,
    )
    state = state.model_copy(update={"resources": resources})
    cover = int((Decimal(profile.dr) + Decimal(profile.hp) / 4) / weapon.damage.armor_divisor)
    return state, synchronize(state, encounter), max(0, basic - cover)


def target_modifier(play: PlayService, state: PlayState, actor_id: str, item_id: str) -> int:
    """B400 weapon sizes; other equipped targets require a pinned SM (B483)."""
    from wayfarer.orchestration.gurps_melee import catalog

    item = next((i for i in state.resources.items if i.id == item_id), None)
    if item is None or item.owner_id != actor_id or not item.equipped or item.ground:
        raise ValidationError("Object target must be carried and equipped by the defender")
    entry = next(e for e in catalog(play).entries if e.definition_id == item.definition_id)
    if entry.durability is None or item.condition is None or item.condition.destroyed:
        raise ValidationError(
            "Object target requires an initialized, non-destroyed durability profile"
        )
    reaches = [max(m.reach) for m in entry.modes if isinstance(m, MeleeMode)]
    if reaches:
        reach = max(reaches)
        return -5 if reach == 0 else -4 if reach == 1 else -3
    if entry.durability.size_modifier is None:
        raise ValidationError("Object target requires a pinned size modifier")
    return entry.durability.size_modifier
