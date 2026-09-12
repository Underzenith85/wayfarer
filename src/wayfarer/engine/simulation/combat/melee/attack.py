"""Preparing the blow the defender must answer."""

from __future__ import annotations

from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.encounter import Encounter, basic_distance
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.equipment_entry import weapon_target
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.objects.combat import target_geometry, target_modifier
from wayfarer.engine.simulation.combat.objects.locations import validate_target
from wayfarer.engine.simulation.combat.ranged.attack import prepare
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.tactical import attack_geometry, defense_adjustment
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def prepare_attack(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    mode_id: str | None,
    *,
    hit_location: HitLocation | None = None,
    target_item_id: str | None = None,
    shots: int = 1,
) -> Encounter:
    pending = encounter.pending_defense
    assert pending is not None
    selected = mode(runtime, state, pending.attacker_id, pending.weapon_id, mode_id)
    if target_item_id:
        if selected.damage.damage_type not in (
            "cr",
            "cut",
            "imp",
            "pi-",
            "pi",
            "pi+",
            "pi++",
            "burn",
        ):
            raise ValidationError("Object target requires a supported damage mode")
        target_modifier(runtime, state, pending.defender_id, target_item_id)
    if isinstance(selected, RangedMode):
        return prepare(
            runtime,
            state,
            encounter,
            selected,
            shots=shots,
            hit_location=hit_location,
            target_item_id=target_item_id,
        )
    if shots != 1:
        raise ValidationError("Shot count requires a ranged mode")
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)

    validate_target(
        runtime, state, encounter, pending.attacker_id, pending.defender_id, selected, hit_location
    )
    if attacker.maneuver_state.strong and selected.damage.basis == "fixed":
        raise ValidationError("Strong requires ST-based melee damage")
    if (
        attacker.maneuver_state.attacks_remaining
        and selected.ready_after_attack
        and attacker.maneuver_state.second_attack_item_id is None
    ):
        raise ValidationError("Double attack requires a weapon usable twice without readying")

    geometry = encounter
    if target_item_id:
        geometry = target_geometry(
            runtime, state, encounter, target_item_id, frozenset(selected.reach)
        )
    target_position = next(p for p in geometry.participants if p.actor_id == defender.actor_id)
    attack_geometry(
        geometry,
        attacker,
        target_position,
        frozenset(selected.reach),
        location=hit_location,
        board=runtime.hex_map(geometry),
    )
    selected_distance = (
        basic_distance(geometry, attacker.actor_id, target_position.actor_id)
        if isinstance(geometry.spatial, BasicSpatialContext)
        else float(CombatEngine.distance(attacker.position, target_position.position))
    )
    if selected_distance not in selected.reach:
        raise ValidationError("Target is outside selected weapon reach")
    allowed: list[Defense] = ["none"]
    for candidate in ("dodge", "parry", "block"):
        if (
            target_item_id
            and next(i for i in state.resources.items if i.id == target_item_id).ground
        ):
            continue
        targeting_weapon = weapon_target(runtime, state, target_item_id)
        if targeting_weapon and candidate == "block":
            continue
        try:
            defense_adjustment(encounter, attacker, defender)
            defense_value(
                runtime,
                state,
                defender,
                candidate,
                target_item_id if targeting_weapon and candidate == "parry" else None,
                incoming_item_id=pending.weapon_id,
                incoming_mode_id=selected.id,
            )
        except ValidationError:
            continue
        allowed.append(candidate)
    return encounter.model_copy(
        update={
            "pending_defense": pending.model_copy(
                update={
                    "mode_id": selected.id,
                    "allowed": tuple(allowed),
                    "hit_location": hit_location,
                    "target_item_id": target_item_id,
                }
            )
        }
    )


def waive_off_hand_penalty(
    runtime: RulesContext, state: PlayState, encounter: Encounter, actor_id: str
) -> Encounter:
    """B39: Ambidexterity removes the off-hand penalty from both blows of a two-weapon Double.

    The maneuver commits the penalties from the declared hands; the trait, read from
    the approved build, waives them before either blow is prepared.
    """

    compiled = build(runtime, state, actor_id)
    if not any(p.definition_id == "trait:ambidexterity" for p in compiled.purchases):
        return encounter
    attacker = next(p for p in encounter.participants if p.actor_id == actor_id)
    return CombatEngine._replace(
        encounter,
        attacker.model_copy(
            update={
                "maneuver_state": attacker.maneuver_state.model_copy(
                    update={"attack_bonus": 0, "second_attack_penalty": 0}
                )
            }
        ),
    )
