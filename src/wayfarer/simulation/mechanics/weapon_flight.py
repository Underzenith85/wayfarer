"""B556-557 weapon flight: persisted landing, collision and local retrieval."""

import hashlib
from typing import Literal

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Record
from wayfarer.rules.checks import CheckTrace, draw_dice
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.object_types import GroundPosition
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Combatant, Encounter
from wayfarer.simulation.condition_checks import check_modifiers
from wayfarer.simulation.critical import Die, TableRoll
from wayfarer.simulation.gurps_equipment import MeleeMode
from wayfarer.simulation.hex_geometry import DIRECTIONS, Hex
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.resources import ResourceEvent
from wayfarer.simulation.rules_context import RulesContext


def position(encounter: Encounter, subject: Combatant) -> GroundPosition:
    point = subject.position
    return GroundPosition(
        encounter_id=encounter.id,
        geometry="hex" if isinstance(point, Hex) else "grid",
        x=point.q if isinstance(point, Hex) else point.x,
        y=point.r if isinstance(point, Hex) else point.y,
    )


def retrieve(state: PlayState, encounter: Encounter, actor_id: str, item_id: str) -> PlayState:
    item = next((i for i in state.resources.items if i.id == item_id), None)
    if item is None or item.ground is None:
        return state
    subject = next(p for p in encounter.participants if p.actor_id == actor_id)
    if item.owner_id != actor_id or item.ground != position(encounter, subject):
        raise ValidationError("Ready requires retrieval at the weapon's recorded location")
    return state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"ground": None}) if i.id == item_id else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )


class FlightCollision(Record):
    actor_id: str
    check: CheckTrace
    damage_dice: tuple[Die, ...] = ()
    injury: int = 0
    lasting_injury_ids: tuple[str, ...] = ()


class FlightResult(Record):
    kind: Literal["critical-flight-v1"] = "critical-flight-v1"
    table: TableRoll
    distance_die: Die
    direction_die: Die
    landing: GroundPosition
    collisions: tuple[FlightCollision, ...] = ()


def resolve_flight(
    runtime: RulesContext, state: PlayState, encounter: Encounter, table: tuple[int, ...]
) -> tuple[PlayState, Encounter, tuple[int, ...]]:
    from wayfarer.simulation.mechanics.gurps_melee import build, catalog, mode
    from wayfarer.simulation.mechanics.object_combat import synchronize

    pending = encounter.pending_defense
    assert pending is not None
    event_id = "critical-flight:" + hashlib.sha256(pending.id.encode()).hexdigest()
    previous = next((e for e in state.resources.events if e.id == event_id), None)
    if previous:
        saved = FlightResult.model_validate_json(previous.kind)
        if saved.table != table or previous.target_id != pending.weapon_id:
            raise ConflictError("Recorded weapon flight cannot be replaced")
        return state, synchronize(state, encounter), ()
    subject = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    weapon = mode(runtime, state, subject.actor_id, pending.weapon_id, pending.mode_id)
    assert isinstance(weapon, MeleeMode)
    landing = position(encounter, subject)
    distance, direction = (draw_dice(runtime.rng, 1)[0] for _ in range(2))
    if isinstance(subject.position, Hex):
        assert subject.hex_facing is not None
        dx, dy = DIRECTIONS[subject.hex_facing]
    else:
        dx, dy = {"north": (0, -1), "east": (1, 0), "south": (0, 1), "west": (-1, 0)}[
            subject.facing
        ]
    sign = 1 if direction <= 3 else -1
    landing = landing.model_copy(
        update={"x": landing.x + sign * distance * dx, "y": landing.y + sign * distance * dy}
    )
    compiled = build(runtime, state, subject.actor_id)
    assert compiled.statistics is not None
    expression = compiled.statistics.swing
    entries = {e.definition_id: e for e in catalog(runtime).entries}
    collisions: list[FlightCollision] = []
    effect_dice: tuple[int, ...] = (distance, direction)
    for target in encounter.participants:
        if position(encounter, target) != landing:
            continue
        target_build = build(runtime, state, target.actor_id)
        assert target_build.statistics is not None
        check = success_roll(
            catalog(runtime).profile_id,
            target_build.statistics.dx,
            check_modifiers(state.resources, target.actor_id, "dx"),
            rng=runtime.rng,
        )
        effect_dice += check.dice
        dice: tuple[int, ...] = ()
        injury = 0
        lasting: tuple[str, ...] = ()
        if not check.outcome.succeeded:
            dice = draw_dice(runtime.rng, weapon.damage.dice or expression.dice)
            basic = (
                max(
                    0 if weapon.damage.damage_type == "cr" else 1,
                    sum(dice)
                    + weapon.damage.adds
                    + (0 if weapon.damage.basis == "fixed" else expression.add),
                )
                // 2
            )
            armor = max(
                (
                    e.armor.dr
                    for i in state.resources.items
                    if i.owner_id == target.actor_id
                    and i.equipped
                    and (i.condition is None or not i.condition.disabled)
                    for e in (entries[i.definition_id],)
                    if e.armor and "torso" in e.armor.locations
                ),
                default=0,
            )
            if runtime.rules.abilities:
                from wayfarer.simulation.abilities import damage_resistance

                armor += damage_resistance(
                    state.resources, target.actor_id, build_revision=target_build.revision
                )
            resources, result = apply_injury(
                state.resources,
                Wound(
                    id=event_id + ":" + hashlib.sha256(target.actor_id.encode()).hexdigest()[:16],
                    actor_id=target.actor_id,
                    expected_revision=state.resources.revision,
                    basic_damage=basic,
                    resistance=armor,
                    damage_type=weapon.damage.damage_type,
                    armor_divisor=weapon.damage.armor_divisor,
                    tight_beam=weapon.damage.tight_beam,
                ),
                ht=target_build.statistics.ht,
                dx=target_build.statistics.dx,
                rng=runtime.rng,
                system=True,
                held_item_ids=target.ready_item_ids,
                held_item_locations=target.hand_bindings,
                shield_item_ids=tuple(
                    i.id
                    for i in state.resources.items
                    if i.id in target.ready_item_ids and entries[i.definition_id].shield
                ),
            )
            injury, lasting = result.injury, result.lasting_injury_ids
            state = state.model_copy(update={"resources": resources})
            effect_dice += dice + result.location_dice
        collisions.append(
            FlightCollision.model_validate(
                {
                    "actor_id": target.actor_id,
                    "check": check,
                    "damage_dice": dice,
                    "injury": injury,
                    "lasting_injury_ids": lasting,
                }
            )
        )
    saved = FlightResult.model_validate(
        {
            "table": table,
            "distance_die": distance,
            "direction_die": direction,
            "landing": landing,
            "collisions": tuple(collisions),
        }
    )
    incapacitated = frozenset(
        p.id.removeprefix("hp:")
        for p in state.resources.pools
        if p.injury and p.injury.incapacitated
    )
    state = state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(
                    update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                )
                if a.actor_id in incapacitated
                else a
                for a in state.actors
            )
        }
    )
    encounter = encounter.model_copy(
        update={
            "participants": tuple(
                p.model_copy(update={"posture": "prone"})
                if any(
                    hp.id == f"hp:{p.actor_id}" and hp.injury and hp.injury.prone
                    for hp in state.resources.pools
                )
                else p
                for p in encounter.participants
            )
        }
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(
                            update={
                                "ground": landing,
                                "ready": False,
                                "equipped": False,
                                "container_id": None,
                            }
                        )
                        if i.id == pending.weapon_id
                        else i
                        for i in state.resources.items
                    ),
                    "events": state.resources.events
                    + (
                        ResourceEvent(
                            id=event_id,
                            at=state.resources.game_time,
                            target_id=pending.weapon_id,
                            kind=saved.model_dump_json(),
                        ),
                    ),
                }
            )
        }
    )
    return state, synchronize(state, encounter), effect_dice
