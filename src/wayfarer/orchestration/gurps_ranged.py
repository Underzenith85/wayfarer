"""Ranged dispatch in the encounter transaction (Lite 27-29; Basic B372-375).

Numeric baseline: Fourth Edition (2004). Exact-printing audit remains a
certification gate. No alternative inventory, injury or command receipt engine.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wayfarer.errors import ValidationError
from wayfarer.rules.checks import Outcome
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.location_types import HitLocation
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


def situation(encounter: Encounter, attacker: str, defender: str) -> RangedSituation:
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
    return value


def validate_command(
    play: PlayService, state: PlayState, encounter: Encounter, command: TakeCombatTurn
) -> None:
    if encounter.ranged_situations and (
        command.destination is not None or command.maneuver == "move"
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
    ):
        raise ValidationError("Ready mode selection requires a reload")
    if command.shots != 1 and command.maneuver not in ATTACK_MANEUVERS:
        raise ValidationError("Shot count requires an attack")
    if command.reload_ammunition_id is not None:
        if command.maneuver != "ready":
            raise ValidationError("Reload requires a Ready maneuver")
        reload_weapon(play, state, command)  # Validate before consciousness/exertion dice.


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
        rounds += min(weapon.shots - rounds, ammo.quantity - reserved)
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
    scene = situation(encounter, actor.actor_id, target.actor_id)
    from wayfarer.orchestration.location_combat import disabled

    if disabled(state, actor.actor_id) & {"left-eye", "right-eye"}:
        raise ValidationError("Ranged vision impairment requires the vision modifier adapter")
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
    if hit_location not in (None, "torso"):
        raise ValidationError("Ranged hit locations require the ranged location adapter")
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
        if load is None or load.mode_id != weapon.id or load.rounds < shots or load.reload_progress:
            raise ValidationError("Weapon is unloaded or reload is incomplete")
    allowed: list[Defense] = ["none"]
    for candidate in ("dodge", "block"):
        if candidate == "block" and not (weapon.thrown or weapon.blockable):
            continue
        try:
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
            loaded.model_copy(update={"rounds": loaded.rounds - pending.shots})
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

    pending = encounter.pending_defense
    assert pending is not None
    if selected not in pending.allowed or (
        second_defense and second_defense not in pending.allowed
    ):
        raise ValidationError("Defense cannot stop this projectile")
    actor = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    scene = situation(encounter, actor.actor_id, target.actor_id)
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
    bonus = (
        aim.aim_bonus
        if (aim.aim_item_id, aim.aim_mode_id, aim.aim_target_id)
        == (pending.weapon_id, weapon.id, target.actor_id)
        else 0
    )
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
    defense_value_, defense_item = defense_value(play, state, target, selected, item_id)
    second_value, second_item = defense_value(
        play, state, target, second_defense or "none", second_item_id
    )
    state, encounter = expend(play, state, encounter, weapon)
    attack = success_roll(equipment.profile_id, attack_target, rng=play.rng)
    hits = (
        min(pending.shots, 1 + max(0, attack_target - sum(attack.dice)) // weapon.recoil)
        if attack.outcome.succeeded
        else 0
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
    # Ranged-specific critical tables must never dispatch to the melee miss table.
    blocked = (
        "ranged-critical-table"
        if equipment.profile_id == "gurps-basic-set-4e-2004"
        and attack.outcome in (Outcome.CRITICAL_FAILURE, Outcome.CRITICAL_SUCCESS)
        else None
    )
    critical_table = tuple(play.rng.randbelow(6) + 1 for _ in range(3)) if blocked else ()
    damages: list[int] = []
    injuries: list[int] = []
    damage_dice: list[int] = []
    entries = {e.definition_id: e for e in equipment.entries}
    dr = max(
        (
            armor.dr
            for i in state.resources.items
            if i.owner_id == target.actor_id
            and i.equipped
            and (i.condition is None or not i.condition.disabled)
            for armor in (entries[i.definition_id].armor,)
            if armor is not None and "torso" in armor.locations
        ),
        default=0,
    )
    from wayfarer.simulation.abilities import damage_resistance

    if play.engine.rules.abilities is not None:
        dr += damage_resistance(
            state.resources, target.actor_id, build_revision=defender_build.revision
        )
    expression = stats.swing if weapon.damage.basis == "swing" else stats.thrust
    count = weapon.damage.dice or expression.dice
    adds = weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add)
    half = weapon.half_damage_range is not None and scene.distance_yards > float(
        weapon.half_damage_range
    ) * (st if weapon.range_basis == "st" else 1)
    for index in range(hits if blocked is None else 0):
        maximum = equipment.profile_id == "gurps-lite-4e-2004" and sum(attack.dice) <= 4
        dice = () if maximum else tuple(play.rng.randbelow(6) + 1 for _ in range(count))
        damage_dice.extend(dice)
        damage = max(
            0 if weapon.damage.damage_type == "cr" else 1,
            (6 * count if maximum else sum(dice)) + adds,
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
                armor_divisor=weapon.damage.armor_divisor,
                tight_beam=weapon.damage.tight_beam,
            ),
            ht=defender_stats.ht,
            rng=play.rng,
            system=True,
            held_item_ids=tuple(
                i.id
                for i in state.resources.items
                if i.owner_id == target.actor_id and i.ready and i.equipped
            ),
        )
        state = state.model_copy(update={"resources": resources})
        damages.append(damage)
        injuries.append(result.injury)
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
    return (
        state,
        encounter,
        InjuryTrace(
            attack=attack,
            defense=defense,
            second_defense=second_trace,
            attack_value=value,
            defense_value=defense_value_,
            damage_dice=tuple(damage_dice),
            basic_damage=sum(damages),
            resistance=dr,
            injury=sum(injuries),
            hp_before=hp.current,
            hp_after=updated.current,
            incapacitated=updated.injury.incapacitated,
            profile_id=equipment.profile_id,
            rules_version="2004",
            critical_table=critical_table,
            adjudication_required=blocked,
            shots_fired=pending.shots,
            hits=hits,
            per_hit_damage=tuple(damages),
            per_hit_injury=tuple(injuries),
        ),
    )
