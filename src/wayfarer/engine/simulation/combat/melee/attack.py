"""Preparing the blow the defender must answer."""

from __future__ import annotations

from typing import Literal

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.close_combat import (
    opponents_in_close_combat,
    pair,
    stray_target_order,
)
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter, basic_distance
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.equipment_entry import weapon_target
from wayfarer.engine.simulation.combat.melee.defense import defense_value
from wayfarer.engine.simulation.combat.melee.modes import mode
from wayfarer.engine.simulation.combat.objects.combat import target_geometry, target_modifier
from wayfarer.engine.simulation.combat.objects.locations import validate_target
from wayfarer.engine.simulation.combat.ranged.attack import prepare
from wayfarer.engine.simulation.combat.spatial import BasicSpatialContext
from wayfarer.engine.simulation.combat.special_melee import actor_reaches, validate_special_attack
from wayfarer.engine.simulation.combat.tactical import attack_geometry, defense_adjustment
from wayfarer.engine.simulation.combat.visibility import combat_visibility
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.engine.simulation.equipment.catalog import MeleeMode, RangedMode
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def _mounted_lance_damage(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    attacker_id: str,
    selected: MeleeMode,
    mounted_charge: bool,
) -> tuple[int, int | None]:
    if not mounted_charge:
        return 0, None
    if not selected.mounted_lance:
        raise ValidationError("Mounted charge requires a lance mode")
    relationship = next((r for r in encounter.mounted_combat if r.rider_id == attacker_id), None)
    if (
        relationship is None
        or not relationship.saddle
        or not relationship.stirrups
        or relationship.control != "controlled"
    ):
        raise ValidationError("A couched lance requires a controlled mount, saddle, and stirrups")
    transport = next(
        (t for t in state.resources.transports if t.id == relationship.transport_id), None
    )
    if transport is None or transport.locomotion != "ground-mount" or transport.straight_yards < 1:
        raise ValidationError("Mounted lance damage requires recorded forward movement")
    mount = build(runtime, state, relationship.mount_id)
    assert mount.statistics is not None
    dice = mount.statistics.st * transport.straight_yards // 100
    if dice < 1:
        raise ValidationError("Mounted lance charge is too slow to inflict one die")
    return dice, relationship.riding_skill


def _validate_dual_weapon_targets(
    encounter: Encounter,
    attacker: Combatant,
    defender: Combatant,
    selected: MeleeMode,
) -> None:
    if not attacker.maneuver_state.dual_weapon_attack:
        return
    if selected.hands != 1:
        raise ValidationError("Dual-Weapon Attack requires one-handed weapons")
    if attacker.maneuver_state.second_attack_target_id == defender.actor_id:
        return
    second_target = next(
        (
            participant
            for participant in encounter.participants
            if participant.actor_id == attacker.maneuver_state.second_attack_target_id
        ),
        None,
    )
    if second_target is None:
        raise ValidationError("Dual-Weapon Attack requires a present second target")
    target_distance = (
        basic_distance(encounter, defender.actor_id, second_target.actor_id)
        if isinstance(encounter.spatial, BasicSpatialContext)
        else CombatEngine.distance(defender.position, second_target.position)
    )
    if target_distance > 1:
        raise ValidationError("Dual melee targets must be adjacent")


def prepare_attack(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    mode_id: str | None,
    *,
    hit_location: HitLocation | None = None,
    armor_chink: bool = False,
    strike_strength: int | None = None,
    subdual_mode: Literal["flat", "blunt-end"] | None = None,
    target_item_id: str | None = None,
    cover_item_id: str | None = None,
    overpenetration_target_id: str | None = None,
    area_aim_point: GroundPosition | None = None,
    scatter_squared: bool = False,
    shots: int = 1,
    mounted_charge: bool = False,
) -> Encounter:
    pending = encounter.pending_defense
    assert pending is not None
    if pending.shield_rush:
        # deferred: shield-rush preparation imports melee defense scoring.
        from wayfarer.engine.simulation.combat.shield_rush import prepare as prepare_shield_rush

        return prepare_shield_rush(runtime, state, encounter)
    selected = mode(runtime, state, pending.attacker_id, pending.weapon_id, mode_id)
    item = next(
        candidate for candidate in state.resources.items if candidate.id == pending.weapon_id
    )
    if pending.electrical_contact_seconds and item.definition_id != "equipment:cattle-prod":
        raise ValidationError("Maintained electrical contact requires a cattle prod")
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    validate_special_attack(
        runtime,
        state,
        selected,
        attacker_id=pending.attacker_id,
        defender_id=pending.defender_id,
        hit_location=hit_location,
        armor_chink=armor_chink,
        strike_strength=strike_strength,
        subdual_mode=subdual_mode,
        target_item_id=target_item_id,
    )
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
            cover_item_id=cover_item_id,
            overpenetration_target_id=overpenetration_target_id,
            area_aim_point=area_aim_point,
            scatter_squared=scatter_squared,
        )
    if cover_item_id is not None or overpenetration_target_id is not None or area_aim_point:
        raise ValidationError("Cover and overpenetration require a ranged mode")
    if shots != 1:
        raise ValidationError("Shot count requires a ranged mode")
    lance_dice, riding_cap = _mounted_lance_damage(
        runtime, state, encounter, pending.attacker_id, selected, mounted_charge
    )
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    _validate_dual_weapon_targets(encounter, attacker, defender, selected)
    visibility = combat_visibility(encounter, attacker.actor_id, defender.actor_id)
    close = bool(opponents_in_close_combat(encounter, attacker.actor_id))
    defender_close = bool(opponents_in_close_combat(encounter, defender.actor_id))
    bystanders = tuple(
        actor.actor_id
        for actor in encounter.participants
        if actor.actor_id not in (attacker.actor_id, defender.actor_id)
        and pair(actor.actor_id, defender.actor_id) in encounter.close_pairs
    )
    selectors = draw_dice(runtime.rng, len(bystanders)) if bystanders else ()
    stray_order = stray_target_order(bystanders, selectors) if bystanders else ()

    validate_target(
        runtime, state, encounter, pending.attacker_id, pending.defender_id, selected, hit_location
    )
    if attacker.maneuver_state.strong and selected.damage.basis == "fixed":
        raise ValidationError("Strong requires ST-based melee damage")
    attack_build = build(runtime, state, pending.attacker_id)
    assert attack_build.statistics is not None
    if (
        attacker.maneuver_state.attacks_remaining
        and selected.becomes_unready_after_attack(attack_build.statistics.st)
        and attacker.maneuver_state.second_attack_item_id is None
    ):
        raise ValidationError("Double attack requires a weapon usable twice without readying")

    reaches = (
        frozenset(actor_reaches(runtime, state, pending.attacker_id, selected.reach))
        if isinstance(selected, MeleeMode)
        else None
    )
    geometry = encounter
    if target_item_id:
        geometry = target_geometry(runtime, state, encounter, target_item_id, reaches)
    target_position = next(p for p in geometry.participants if p.actor_id == defender.actor_id)
    attack_geometry(
        geometry,
        attacker,
        target_position,
        reaches,
        location=hit_location,
        board=runtime.hex_map(geometry),
    )
    selected_distance = (
        basic_distance(geometry, attacker.actor_id, target_position.actor_id)
        if isinstance(geometry.spatial, BasicSpatialContext)
        else float(CombatEngine.distance(attacker.position, target_position.position))
    )
    if reaches is not None and selected_distance not in reaches:
        raise ValidationError("Target is outside selected weapon reach")
    allowed: list[Defense] = ["none"]
    candidates = tuple(
        candidate for candidate in ("dodge", "parry", "block") if candidate in visibility.defenses
    )
    for candidate in candidates:
        if defender_close and candidate == "block":
            continue
        if (
            target_item_id
            and next(i for i in state.resources.items if i.id == target_item_id).ground
        ):
            continue
        targeting_weapon = weapon_target(runtime, state, target_item_id)
        if targeting_weapon and candidate == "block":
            continue
        try:
            defense_adjustment(encounter, attacker, defender, approach=pending.tactical_approach)
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
                    "armor_chink": armor_chink,
                    "strike_strength": strike_strength,
                    "subdual_mode": subdual_mode,
                    "target_item_id": target_item_id,
                    "visibility_attack_penalty": visibility.attack_penalty,
                    "visibility_defense_penalty": visibility.defense_penalty,
                    "attention_defense_penalty": (
                        -1
                        if attacker.maneuver_state.dual_weapon_attack
                        and attacker.maneuver_state.second_attack_target_id == defender.actor_id
                        else 0
                    ),
                    "close_combat": close,
                    "defender_close_combat": defender_close,
                    "stray_target_order": stray_order,
                    "mounted_lance_dice": lance_dice,
                    "mounted_skill_cap": riding_cap,
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
    retained = -4 if attacker.maneuver_state.dual_weapon_attack else 0
    return CombatEngine._replace(
        encounter,
        attacker.model_copy(
            update={
                "maneuver_state": attacker.maneuver_state.model_copy(
                    update={"attack_bonus": retained, "second_attack_penalty": retained}
                )
            }
        ),
    )
