"""Ranged dispatch in the encounter transaction (Lite 27-29; Basic B372-375).

Numeric baseline: Fourth Edition (2004). Exact-printing audit remains a
certification gate. No alternative inventory, injury or command receipt engine.
"""

from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING, cast

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import Outcome
from wayfarer.rules.effects import DerivedValue
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.location_types import HitLocation, HumanLocation
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import (
    CombatEngine,
    Defense,
    Encounter,
    InjuryTrace,
    RangedSituation,
)
from wayfarer.simulation.fatigue import fatigue_value
from wayfarer.simulation.gurps_equipment import RangedMode
from wayfarer.simulation.hit_locations import (
    attack_penalty,
    missing_location,
    part,
    select_location,
)
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.maneuvers import ATTACK_MANEUVERS
from wayfarer.simulation.resources import AmmunitionLoad, ResourceState

if TYPE_CHECKING:
    from wayfarer.orchestration.combat import TakeCombatTurn
    from wayfarer.orchestration.play import PlayService


def range_penalty(yards: float) -> int:
    """B550 size/speed/range progression, rounded up to the next entry."""
    if yards <= 2:
        return 0
    scale = 1
    penalty = 0
    while True:
        for entry in (3, 5, 7, 10, 15, 20):
            penalty += 1
            if yards <= entry * scale:
                return -penalty
        scale *= 10


def rapid_fire_bonus(shots: int) -> int:
    # B373, deliberately bounded to the supported non-shotgun RoF <= 100.
    return next(
        b
        for limit, b in ((4, 0), (8, 1), (12, 2), (16, 3), (24, 4), (49, 5), (99, 6), (100, 7))
        if shots <= limit
    )


def declare(
    play: PlayService, encounter: Encounter, situations: tuple[RangedSituation, ...]
) -> Encounter:
    if not situations:
        return encounter
    from wayfarer.orchestration.gurps_melee import catalog

    catalog(play)
    keys = [(s.attacker_id, s.defender_id) for s in situations]
    if len(set(keys)) != len(keys) or any(
        a == b or a not in encounter.turn_order or b not in encounter.turn_order for a, b in keys
    ):
        raise ValidationError("Ranged scene requires unique directed participant pairs")
    return encounter.model_copy(update={"ranged_situations": situations})


def situation(
    encounter: Encounter,
    attacker: str,
    defender: str,
    weapon: RangedMode | None = None,
) -> RangedSituation:
    value = next(
        (
            s
            for s in encounter.ranged_situations
            if (s.attacker_id, s.defender_id) == (attacker, defender)
        ),
        None,
    )
    if value is None:
        raise ValidationError("Ranged attack requires declared scene distance, speed and size")
    if encounter.hex_battlefield is not None:
        from wayfarer.simulation.hex_geometry import ranged_distance
        from wayfarer.simulation.tactical import attack_geometry, pose

        actor = next(p for p in encounter.participants if p.actor_id == attacker)
        target = next(p for p in encounter.participants if p.actor_id == defender)
        attack_geometry(encounter, actor, target)
        distance = ranged_distance(
            encounter.hex_battlefield,
            pose(actor).position,
            pose(target).position,
            beam=bool(weapon and weapon.damage.tight_beam),
        )
        value = value.model_copy(update={"distance_yards": float(distance)})
    return value


def validate_command(
    play: PlayService, state: PlayState, encounter: Encounter, command: TakeCombatTurn
) -> None:
    if command.braced and command.maneuver != "aim":
        raise ValidationError("Bracing is selected as part of Aim")
    if command.step_timing == "after" and command.maneuver != "attack":
        raise ValidationError("Only Attack permits a step after the attack")
    if any(
        value is not None
        for value in (command.second_item_id, command.second_target_id, command.second_mode_id)
    ) and not (command.maneuver == "all_out_attack" and command.attack_option == "double"):
        raise ValidationError("Second attack choices require All-Out Attack (Double)")
    if command.wait_trigger is not None and command.wait_trigger.stop_thrust:
        from wayfarer.orchestration.gurps_melee import mode
        from wayfarer.simulation.gurps_equipment import MeleeMode

        trigger = command.wait_trigger
        selected = mode(play, state, command.actor_id, trigger.item_id, trigger.mode_id)
        if not isinstance(selected, MeleeMode) or selected.damage.basis != "thrust":
            raise ValidationError("Stop thrust requires a ready thrusting melee mode")
    if (
        encounter.hex_battlefield is None
        and encounter.ranged_situations
        and (command.destination is not None or command.maneuver == "move")
    ):
        raise ValidationError("Declared ranged scene movement requires the tactical adapter")
    if command.shots != 1 and (
        play.engine.rules.combat is None or play.engine.rules.combat.gurps_equipment is None
    ):
        raise ValidationError("Shot count requires GURPS ranged dispatch")
    if (
        command.maneuver == "ready"
        and command.mode_id is not None
        and command.reload_ammunition_id is None
        and not command.unload_ammunition
    ):
        raise ValidationError("Ready mode selection requires a reload")
    if command.shots != 1 and command.maneuver not in ATTACK_MANEUVERS:
        raise ValidationError("Shot count requires an attack")
    if command.unload_ammunition:
        if command.maneuver != "ready" or command.reload_ammunition_id is not None:
            raise ValidationError("Unload requires a separate Ready maneuver")
        unload_weapon(play, state, command)
    if command.reload_ammunition_id is not None:
        if command.maneuver != "ready":
            raise ValidationError("Reload requires a Ready maneuver")
        reload_weapon(play, state, command)  # Validate before consciousness/exertion dice.


def unload_weapon(play: PlayService, state: PlayState, command: TakeCombatTurn) -> ResourceState:
    """Release a removable magazine's reservation; no rounds are minted or spent."""
    from wayfarer.orchestration.gurps_melee import catalog

    equipment = catalog(play)
    if equipment.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Unload requires the exact Basic Set profile")
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    if item is None or item.owner_id != command.actor_id:
        raise ValidationError("Unload requires an owned weapon")
    loaded = next(
        (load for load in state.resources.ammunition_loads if load.weapon_id == item.id), None
    )
    if loaded is None or command.mode_id not in (None, loaded.mode_id):
        raise ValidationError("Unload requires the loaded weapon mode")
    entry = next(e for e in equipment.entries if e.definition_id == item.definition_id)
    weapon = next((m for m in entry.modes if m.id == loaded.mode_id), None)
    if not isinstance(weapon, RangedMode) or weapon.reload_protocol != "magazine":
        raise ValidationError("Individual-round unloading requires its own timing protocol")
    result = state.resources.model_copy(
        update={
            "ammunition_loads": tuple(
                load for load in state.resources.ammunition_loads if load.weapon_id != item.id
            )
        }
    )
    play.engine.resources.validate(result)
    return result


def reload_weapon(play: PlayService, state: PlayState, command: TakeCombatTurn) -> ResourceState:
    from wayfarer.orchestration.gurps_melee import catalog

    equipment = catalog(play)
    item = next((i for i in state.resources.items if i.id == command.item_id), None)
    ammo = next((i for i in state.resources.items if i.id == command.reload_ammunition_id), None)
    if (
        item is None
        or ammo is None
        or item.id == ammo.id
        or (item.owner_id != command.actor_id or ammo.owner_id != command.actor_id)
    ):
        raise ValidationError("Reload requires owned weapon and ammunition")
    entry = next(e for e in equipment.entries if e.definition_id == item.definition_id)
    modes = [
        m
        for m in entry.modes
        if isinstance(m, RangedMode) and (command.mode_id is None or m.id == command.mode_id)
    ]
    if len(modes) != 1 or modes[0].thrown:
        raise ValidationError("Reload requires one projectile mode")
    weapon = modes[0]
    if weapon.reload_protocol == "per-round" and equipment.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Per-round reload requires the exact Basic Set profile")
    if ammo.definition_id != weapon.ammunition_id or item.quantity != 1:
        raise ValidationError("Reload ammunition does not match this individual weapon")
    old = next(
        (loaded for loaded in state.resources.ammunition_loads if loaded.weapon_id == item.id), None
    )
    if old and (old.mode_id != weapon.id or old.ammunition_item_id != ammo.id):
        raise ValidationError("Unload the existing ammunition before changing reload source")
    rounds = old.rounds if old else 0
    if rounds >= weapon.shots:
        raise ValidationError("Weapon is already fully loaded")
    reserved = sum(
        loaded.rounds
        for loaded in state.resources.ammunition_loads
        if loaded.ammunition_item_id == ammo.id
    )
    if ammo.quantity <= reserved:
        raise ValidationError("No unreserved ammunition remains")
    progress = (old.reload_progress if old else 0) + 1
    if progress >= max(1, weapon.reload_seconds):
        rounds += min(
            1 if weapon.reload_protocol == "per-round" else weapon.shots - rounds,
            ammo.quantity - reserved,
        )
        progress = 0
    load = AmmunitionLoad(
        weapon_id=item.id,
        mode_id=weapon.id,
        ammunition_item_id=ammo.id,
        rounds=rounds,
        reload_progress=progress,
    )
    result = state.resources.model_copy(
        update={
            "ammunition_loads": tuple(
                loaded for loaded in state.resources.ammunition_loads if loaded.weapon_id != item.id
            )
            + (load,)
        }
    )
    play.engine.resources.validate(result)
    return result


def prepare(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    *,
    shots: int,
    hit_location: HitLocation | None,
) -> Encounter:
    from wayfarer.orchestration.gurps_melee import build, catalog, defense_value

    pending = encounter.pending_defense
    assert pending is not None
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    scene = situation(encounter, actor.actor_id, target.actor_id, weapon)
    from wayfarer.orchestration.location_combat import disabled

    if len(disabled(state, actor.actor_id) & {"left-eye", "right-eye"}) == 2:
        raise ValidationError("Blind ranged attacks require an explicit sensory targeting adapter")
    stats = build(play, state, actor.actor_id).statistics
    assert stats is not None
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
    st = fatigue_value(fp, stats.st)
    if scene.distance_yards > float(weapon.maximum_range) * (
        st if weapon.range_basis == "st" else 1
    ):
        raise ValidationError("Target exceeds maximum ranged weapon range")
    if shots > min(weapon.rate_of_fire, 100) or (
        shots > 1 and catalog(play).profile_id != "gurps-basic-set-4e-2004"
    ):
        raise ValidationError("Unsupported fire mode or shot count for profile")
    from wayfarer.orchestration.location_combat import validate_target

    validate_target(play, state, encounter, actor.actor_id, target.actor_id, weapon, hit_location)
    if actor.last_maneuver == "feint" or (
        actor.last_maneuver == "all_out_attack" and actor.maneuver_state.attack_bonus != 4
    ):
        raise ValidationError("Ranged All-Out Attack supports Determined only")
    item = next(i for i in state.resources.items if i.id == pending.weapon_id)
    if item.quantity != 1:
        raise ValidationError("Ranged weapon requires an individual inventory item")
    if not weapon.thrown:
        load = next(
            (loaded for loaded in state.resources.ammunition_loads if loaded.weapon_id == item.id),
            None,
        )
        if (
            load is None
            or load.mode_id != weapon.id
            or load.rounds < shots
            or (load.reload_progress and weapon.reload_protocol != "per-round")
        ):
            raise ValidationError("Weapon is unloaded or reload is incomplete")
    allowed: list[Defense] = ["none"]
    for candidate in ("dodge", "block", "parry"):
        if candidate == "parry" and (
            not weapon.thrown or catalog(play).profile_id != "gurps-basic-set-4e-2004"
        ):
            continue
        if candidate == "block" and not (weapon.thrown or weapon.blockable):
            continue
        try:
            from wayfarer.simulation.tactical import defense_adjustment

            defense_adjustment(encounter, actor, target)
            defense_value(play, state, target, candidate)
        except ValidationError:
            continue
        allowed.append(candidate)
    return encounter.model_copy(
        update={
            "pending_defense": pending.model_copy(
                update={
                    "mode_id": weapon.id,
                    "shots": shots,
                    "allowed": tuple(allowed),
                    "hit_location": hit_location,
                }
            )
        }
    )


def expend(
    play: PlayService, state: PlayState, encounter: Encounter, weapon: RangedMode
) -> tuple[PlayState, Encounter]:
    pending = encounter.pending_defense
    assert pending is not None
    resources = state.resources
    if weapon.thrown:
        item = next(i for i in resources.items if i.id == pending.weapon_id)
        resources = resources.model_copy(
            update={
                "items": tuple(i for i in resources.items if i.id != item.id),
                "expended_items": resources.expended_items
                + (
                    item.model_copy(
                        update={"equipped": False, "ready": False, "container_id": None}
                    ),
                ),
            }
        )
        actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
        encounter = CombatEngine._replace(
            encounter,
            actor.model_copy(
                update={
                    "ready_item_ids": tuple(
                        i for i in actor.ready_item_ids if i != pending.weapon_id
                    ),
                    "hand_bindings": tuple(
                        (i, h) for i, h in actor.hand_bindings if i != pending.weapon_id
                    ),
                }
            ),
        )
    else:
        load = next(
            loaded for loaded in resources.ammunition_loads if loaded.weapon_id == pending.weapon_id
        )
        loads = tuple(
            loaded.model_copy(
                update={"rounds": loaded.rounds - pending.shots, "reload_progress": 0}
            )
            if loaded == load
            else loaded
            for loaded in resources.ammunition_loads
        )
        items = tuple(
            i.model_copy(update={"quantity": i.quantity - pending.shots})
            if i.id == load.ammunition_item_id
            else i
            for i in resources.items
            if i.id != load.ammunition_item_id or i.quantity > pending.shots
        )
        loads = tuple(loaded for loaded in loads if loaded.rounds or loaded.reload_progress)
        resources = resources.model_copy(update={"items": items, "ammunition_loads": loads})
    play.engine.resources.validate(resources)
    return state.model_copy(update={"resources": resources}), encounter


def resolve(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    weapon: RangedMode,
    selected: Defense,
    item_id: str | None,
    *,
    second_defense: Defense | None,
    second_item_id: str | None,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    from wayfarer.orchestration.gurps_maneuvers import distracted
    from wayfarer.orchestration.gurps_melee import build, catalog, defense_value, level

    original_resources = state.resources
    pending = encounter.pending_defense
    assert pending is not None
    if selected not in pending.allowed or (
        second_defense and second_defense not in pending.allowed
    ):
        raise ValidationError("Defense cannot stop this projectile")
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    original_target = target
    scene = situation(encounter, actor.actor_id, target.actor_id, weapon)
    equipment = catalog(play)
    compiled = build(play, state, actor.actor_id)
    defender_build = build(play, state, target.actor_id)
    stats = compiled.statistics
    defender_stats = defender_build.statistics
    assert stats is not None and defender_stats is not None
    value = level(compiled, weapon.skill_id)
    actor_hp = next(p for p in state.resources.pools if p.id == f"hp:{actor.actor_id}")
    hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor.actor_id}")
    st = fatigue_value(fp, stats.st)
    aim = actor.maneuver_state
    aimed = (aim.aim_item_id, aim.aim_mode_id, aim.aim_target_id) == (
        pending.weapon_id,
        weapon.id,
        target.actor_id,
    ) and aim.aim_seconds > 0
    bonus = aim.aim_bonus if aimed else 0
    if actor.last_maneuver == "move_and_attack":
        bonus = min(-2, weapon.bulk)
    elif actor.last_maneuver == "all_out_attack":
        bonus += 1
    attack_target = (
        int(value.value)
        + bonus
        + scene.size_modifier
        + range_penalty(scene.distance_yards + scene.speed_yards_per_second)
        + rapid_fire_bonus(pending.shots)
        - max(0, weapon.minimum_st - st)
    )
    attack_target -= actor_hp.injury.shock if actor_hp.injury else 0
    from wayfarer.orchestration.location_combat import disabled

    eyes = disabled(state, actor.actor_id) & {"left-eye", "right-eye"}
    if eyes:
        attack_target -= (
            6 if len(eyes) == 2 else 1 if aimed and actor.last_maneuver != "move_and_attack" else 3
        )
    if pending.hit_location:
        entries = {e.definition_id: e for e in equipment.entries}
        shield_side = next(
            (
                hand.split("-")[0]
                for item, hand in target.hand_bindings
                if any(
                    i.id == item and entries[i.definition_id].shield for i in state.resources.items
                )
            ),
            None,
        )
        attack_target += attack_penalty(pending.hit_location, shield_side=shield_side)
    defense_value_, defense_item = defense_value(play, state, target, selected, item_id)
    second_value, second_item = defense_value(
        play, state, target, second_defense or "none", second_item_id
    )
    if weapon.thrown:
        thrown_item = next(i for i in state.resources.items if i.id == pending.weapon_id)
        entry = next(e for e in equipment.entries if e.definition_id == thrown_item.definition_id)
        penalty = 2 if entry.weight_millipounds <= 1000 else 1
        if selected == "parry" and defense_value_ is not None:
            defense_value_ = DerivedValue(defense_value_.target, defense_value_.value - penalty, ())
        if second_defense == "parry" and second_value is not None:
            second_value = DerivedValue(second_value.target, second_value.value - penalty, ())
    attack = success_roll(equipment.profile_id, attack_target, rng=play.rng)
    # B382 excludes ranged attacks from the generic failure-by-ten rule.
    if equipment.profile_id == "gurps-basic-set-4e-2004":
        attack = replace(attack, rule_id="gurps.combat.ranged_attack")
        if attack.outcome is Outcome.CRITICAL_FAILURE and attack.total < 17:
            attack = replace(attack, outcome=Outcome.FAILURE)
    from wayfarer.simulation.hit_locations import location_special_effects, torso_near_miss

    near_miss = torso_near_miss(pending.hit_location, attack)
    hits = (
        min(pending.shots, 1 + max(0, attack_target - sum(attack.dice)) // weapon.recoil)
        if attack.outcome.succeeded
        else int(near_miss)
    )
    defense = None
    second_trace = None
    if hits and attack.outcome is not Outcome.CRITICAL_SUCCESS and defense_value_ is not None:
        defense = success_roll(equipment.profile_id, int(defense_value_.value), rng=play.rng)
        if defense.outcome.succeeded:
            avoided = (
                hits
                if defense.outcome is Outcome.CRITICAL_SUCCESS
                else (
                    1 + int(defense_value_.value) - sum(defense.dice) if selected == "dodge" else 1
                )
            )
            hits = max(0, hits - avoided)
        if selected == "parry" and defense_item is not None:
            target = target.model_copy(update={"parries": target.parries + (defense_item,)})
        if selected == "block":
            target = target.model_copy(update={"block_used": True})
        if defense.outcome is Outcome.CRITICAL_FAILURE and selected == "dodge":
            target = target.model_copy(update={"posture": "prone"})
    if hits and defense is not None and not defense.outcome.succeeded and second_value is not None:
        second_trace = success_roll(equipment.profile_id, int(second_value.value), rng=play.rng)
        if second_trace.outcome.succeeded:
            avoided = (
                hits
                if second_trace.outcome is Outcome.CRITICAL_SUCCESS
                else (
                    1 + int(second_value.value) - sum(second_trace.dice)
                    if second_defense == "dodge"
                    else 1
                )
            )
            hits = max(0, hits - avoided)
        if second_defense == "parry" and second_item is not None:
            target = target.model_copy(update={"parries": target.parries + (second_item,)})
        if second_defense == "block":
            target = target.model_copy(update={"block_used": True})
        if second_trace.outcome is Outcome.CRITICAL_FAILURE and second_defense == "dodge":
            target = target.model_copy(update={"posture": "prone"})
    dropped = {
        equipment_id
        for choice, roll, equipment_id in (
            (selected, defense, defense_item),
            (second_defense, second_trace, second_item),
        )
        if choice == "block"
        and roll is not None
        and roll.outcome is Outcome.CRITICAL_FAILURE
        and equipment_id is not None
    }
    if dropped:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False}) if i.id in dropped else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
    critical_table = (
        tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        if equipment.profile_id == "gurps-basic-set-4e-2004"
        and attack.outcome in (Outcome.CRITICAL_FAILURE, Outcome.CRITICAL_SUCCESS)
        else ()
    )
    critical = sum(critical_table) if attack.outcome is Outcome.CRITICAL_SUCCESS else 0
    blocked = "ranged-critical-table" if critical_table and not critical else None
    parry_item = next(
        (
            equipment_id
            for choice, roll, equipment_id in (
                (selected, defense, defense_item),
                (second_defense, second_trace, second_item),
            )
            if choice == "parry" and roll is not None and roll.outcome is Outcome.CRITICAL_FAILURE
        ),
        None,
    )
    if parry_item is not None:
        critical_table = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        blocked = "ranged-critical-parry"
    critical_rolls: tuple[tuple[int, int, int], ...] = (
        (cast(tuple[int, int, int], critical_table),) if critical_table else ()
    )
    miss_effect_dice: tuple[int, ...] = ()
    miss_lasting_ids: tuple[str, ...] = ()
    if blocked:
        from wayfarer.orchestration.ranged_misses import resolve_miss

        # Preserve defense counters/posture before applying consequences to the defender.
        encounter = CombatEngine._replace(encounter, target)
        state, encounter, miss, blocked = resolve_miss(
            play,
            state,
            encounter,
            critical_table,
            parry_item=parry_item,
        )
        critical_rolls = miss.table_rolls
        critical_table = miss.table_rolls[-1]
        miss_effect_dice = miss.location_dice + miss.damage_dice
        miss_lasting_ids = miss.lasting_injury_ids
        if parry_item is not None:
            target = next(p for p in encounter.participants if p.actor_id == target.actor_id)
    state, encounter = expend(play, state, encounter, weapon)
    location: HumanLocation | None = None
    location_dice: tuple[int, ...] = ()
    if hits and pending.hit_location:
        from wayfarer.orchestration.location_combat import from_behind

        location, location_dice = select_location(
            "torso" if near_miss else pending.hit_location,
            rng=play.rng,
            from_behind=from_behind(actor, target),
        )
        if pending.hit_location == "random" and hp.injury and missing_location(hp.injury, location):
            location = "torso"
    if critical and pending.shots > 1:
        blocked = "ranged-critical-burst-table"
    head = (
        location in ("skull", "face", "left-eye", "right-eye")
        and weapon.damage.damage_type != "tox"
        and hp.injury is not None
        and location_special_effects(hp.injury, location)
    )
    critical_eye = False
    lasting_ids: tuple[str, ...] = miss_lasting_ids
    effect_dice: tuple[int, ...] = miss_effect_dice
    if critical and head and blocked is None:
        from wayfarer.orchestration.location_combat import from_behind

        if critical in (6, 7) and location in ("face", "skull"):
            if from_behind(actor, target) or (
                hp.injury and hp.injury.tolerance and hp.injury.tolerance.no_eyes
            ):
                critical = 4
            else:
                eye_die = play.rng.randbelow(6) + 1
                effect_dice += (eye_die,)
                location = "right-eye" if eye_die <= 3 else "left-eye"
                critical_eye = True
        if critical == 8:
            target = target.model_copy(update={"forced_do_nothing": True})
    damages: list[int] = []
    injuries: list[int] = []
    damage_dice: list[int] = []
    entries = {e.definition_id: e for e in equipment.entries}

    def armor_dr() -> int:
        return max(
            (
                armor.dr
                for i in state.resources.items
                if i.owner_id == target.actor_id
                and i.equipped
                and (i.condition is None or not i.condition.disabled)
                for armor in (entries[i.definition_id].armor,)
                if armor is not None
                and (
                    (location or "torso") in armor.locations
                    or (part(location) + "s" if location else "torso") in armor.locations
                )
            ),
            default=0,
        )

    dr_bonus = 0
    from wayfarer.simulation.abilities import damage_resistance

    if play.engine.rules.abilities is not None:
        dr_bonus = damage_resistance(
            state.resources, target.actor_id, build_revision=defender_build.revision
        )
    dr = armor_dr() + dr_bonus
    first_location, first_location_dice, first_dr = location, location_dice, dr
    hit_resistances: list[int] = []
    hit_locations: list[HumanLocation | None] = []
    hit_location_dice: list[tuple[int, ...]] = []
    expression = stats.swing if weapon.damage.basis == "swing" else stats.thrust
    count = weapon.damage.dice or expression.dice
    adds = weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add)
    half = weapon.half_damage_range is not None and scene.distance_yards > float(
        weapon.half_damage_range
    ) * (st if weapon.range_basis == "st" else 1)
    for index in range(hits if blocked is None else 0):
        if index and pending.hit_location == "random":
            from wayfarer.orchestration.location_combat import from_behind

            location, location_dice = select_location(
                "random",
                rng=play.rng,
                from_behind=from_behind(actor, target),
            )
            current_hp = next(p for p in state.resources.pools if p.id == hp.id)
            if current_hp.injury and missing_location(current_hp.injury, location):
                location = "torso"
            dr = armor_dr() + dr_bonus
        hit_resistances.append(dr)
        hit_locations.append(location)
        hit_location_dice.append(location_dice)
        hit_critical = critical
        maximum = hit_critical in ((3, 15) if head else (6, 15)) or (
            equipment.profile_id == "gurps-lite-4e-2004" and sum(attack.dice) <= 4
        )
        dice = () if maximum else tuple(play.rng.randbelow(6) + 1 for _ in range(count))
        damage_dice.extend(dice)
        damage = max(
            0 if weapon.damage.damage_type == "cr" else 1,
            (6 * count if maximum else sum(dice)) + adds,
        )
        damage *= (
            3
            if hit_critical in ((18,) if head else (3, 18))
            else 2
            if hit_critical in ((16,) if head else (5, 16))
            else 1
        )
        if half:
            damage //= 2
        resources, result = apply_injury(
            state.resources,
            Wound(
                id=f"{pending.id}:hit:{index}",
                actor_id=target.actor_id,
                expected_revision=state.resources.revision,
                basic_damage=damage,
                resistance=dr,
                damage_type=weapon.damage.damage_type,
                location=location,
                critical_eye=critical_eye,
                armor_divisor=weapon.damage.armor_divisor,
                tight_beam=weapon.damage.tight_beam,
            ),
            ht=defender_stats.ht,
            rng=play.rng,
            system=True,
            dx=defender_stats.dx,
            force_major_wound=hit_critical in ((4, 5) if head else (7, 13, 14)),
            double_shock=hit_critical == 8 and not head,
            funny_bone=hit_critical == 8 and not head,
            halve_dr=("up" if head else "down")
            if hit_critical in ((4, 5, 17) if head else (4, 17))
            else None,
            ignore_dr=head and hit_critical == 3,
            held_item_locations=target.hand_bindings,
            shield_item_ids=tuple(
                i.id
                for i in state.resources.items
                if i.owner_id == target.actor_id
                and i.ready
                and i.equipped
                and entries[i.definition_id].shield
            ),
            held_item_ids=tuple(
                i.id
                for i in state.resources.items
                if i.owner_id == target.actor_id and i.ready and i.equipped
            ),
            head_trauma=(
                "deafened"
                if head and hit_critical in (12, 13) and weapon.damage.damage_type == "cr"
                else "scarred"
                if head and hit_critical in (12, 13)
                else None
            ),
            scar_levels=2 if weapon.damage.damage_type in ("burn", "cor") else 1,
        )
        state = state.model_copy(update={"resources": resources})
        damages.append(damage)
        injuries.append(result.injury)
        lasting_ids += result.lasting_injury_ids
        effect_dice += result.location_dice
    if critical == 12 and not head and blocked is None:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False, "equipped": False})
                            if i.owner_id == target.actor_id and i.ready
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
    if head and critical == 14 and blocked is None:
        held_weapons = tuple(
            i.id
            for i in state.resources.items
            if i.owner_id == target.actor_id
            and i.ready
            and i.equipped
            and entries[i.definition_id].modes
        )
        if held_weapons:
            die = play.rng.randbelow(6) + 1 if len(held_weapons) > 1 else None
            if die is not None:
                effect_dice += (die,)
            drop = held_weapons[0 if die is None or die <= 3 else 1]
            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                i.model_copy(update={"ready": False, "equipped": False})
                                if i.id == drop
                                else i
                                for i in state.resources.items
                            )
                        }
                    )
                }
            )
    updated = next(p for p in state.resources.pools if p.id == hp.id)
    assert updated.injury is not None
    target = target.model_copy(
        update={
            "posture": "prone" if updated.injury.prone else target.posture,
            "ready_item_ids": tuple(
                sorted(
                    i.id
                    for i in state.resources.items
                    if i.owner_id == target.actor_id and i.ready and i.equipped
                )
            ),
        }
    )
    encounter = CombatEngine._replace(encounter, target).model_copy(
        update={"blocked_reason": blocked}
    )
    encounter = distracted(
        play,
        state,
        encounter,
        target.actor_id,
        defended=defense is not None,
        injured=sum(injuries) > 0,
    )
    if updated.injury.incapacitated:
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(
                        update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                    )
                    if a.actor_id == target.actor_id
                    else a
                    for a in state.actors
                )
            }
        )
    trace = InjuryTrace(
        attack=attack,
        defense=defense,
        second_defense=second_trace,
        attack_value=value,
        defense_value=defense_value_,
        damage_dice=tuple(damage_dice),
        basic_damage=sum(damages),
        resistance=first_dr,
        injury=sum(injuries),
        hp_before=hp.current,
        hp_after=updated.current,
        incapacitated=updated.injury.incapacitated,
        profile_id=equipment.profile_id,
        rules_version="2004",
        critical_table=critical_table,
        location=first_location,
        location_dice=first_location_dice,
        effect_dice=effect_dice,
        lasting_injury_ids=lasting_ids,
        adjudication_required=blocked,
        shots_fired=pending.shots,
        hits=hits,
        per_hit_damage=tuple(damages),
        per_hit_injury=tuple(injuries),
        per_hit_resistance=tuple(hit_resistances) if pending.shots > 1 else (),
        per_hit_locations=tuple(hit_locations) if pending.shots > 1 else (),
        per_hit_location_dice=tuple(hit_location_dice) if pending.shots > 1 else (),
    )
    if critical_table:
        from wayfarer.simulation.ranged_critical import RangedCritical, save_ranged_critical

        state = state.model_copy(
            update={
                "resources": save_ranged_critical(
                    state.resources,
                    RangedCritical(
                        id=pending.id,
                        encounter_id=encounter.id,
                        created_at=state.resources.game_time,
                        attacker=actor,
                        defender=original_target,
                        attacker_build_revision=compiled.revision,
                        defender_build_revision=defender_build.revision,
                        catalog=equipment,
                        weapon=weapon,
                        scene=scene,
                        ammunition_load=next(
                            (
                                load
                                for load in original_resources.ammunition_loads
                                if load.weapon_id == pending.weapon_id
                            ),
                            None,
                        ),
                        items=tuple(
                            i
                            for i in original_resources.items
                            if i.owner_id in (actor.actor_id, target.actor_id)
                        ),
                        pools=tuple(
                            p
                            for p in original_resources.pools
                            if p.id
                            in (
                                f"hp:{actor.actor_id}",
                                f"fp:{actor.actor_id}",
                                f"hp:{target.actor_id}",
                                f"fp:{target.actor_id}",
                            )
                        ),
                        trace=trace,
                        table_rolls=critical_rolls,
                        subject_id=target.actor_id if parry_item else actor.actor_id,
                        affected_item_id=parry_item or pending.weapon_id,
                    ),
                )
            }
        )
    return state, encounter, trace
