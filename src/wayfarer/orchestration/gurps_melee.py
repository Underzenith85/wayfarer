"""Profile-selected weapon melee within the existing encounter transaction.

Numeric baseline: Lite (August 2004), pp. 24-28; Basic Set B369-376,
B381-382 and B556. Complex Basic critical-miss consequences stop play with a
persisted table result rather than silently substituting ordinary damage.
"""

from __future__ import annotations

import hashlib
from decimal import Decimal

from wayfarer.character.compiler import ValidatedBuild
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import Outcome
from wayfarer.rules.effects import DerivedValue
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.rules.recovery_types import interrupt_tasks
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import Combatant, Defense, Encounter, InjuryTrace
from wayfarer.simulation.fatigue import ContinueExertion, apply_fatigue, fatigue_value
from wayfarer.simulation.gurps_equipment import EquipmentCatalog, MeleeMode, inventory_load
from wayfarer.simulation.injury import InjuryTurn, Wound, apply_injury, impaired_movement


def fatigue_ready(state: PlayState, actor_id: str) -> bool:
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor_id}")
    if fp.fatigue is None:
        raise ValidationError("GURPS fatigue requires explicit migration")
    return not (
        fp.fatigue.collapsed
        or fp.fatigue.unconscious
        or fp.fatigue.heart_attack
        or fp.current <= -fp.maximum
    )


def exertion(
    play: PlayService, state: PlayState, actor_id: str, command_id: str
) -> tuple[PlayState, bool]:
    """Begin voluntary physical activity; persist a failed exertion result too."""
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    resources = state.resources.model_copy(
        update={
            "recovery_tasks": interrupt_tasks(
                state.resources.recovery_tasks, frozenset({actor_id}), state.resources.game_time
            )
        }
    )
    resources, result = apply_fatigue(
        resources,
        ContinueExertion(
            id="combat-exertion:" + hashlib.sha256(command_id.encode()).hexdigest(),
            actor_id=actor_id,
            expected_revision=resources.revision,
        ),
        ht=compiled.statistics.ht,
        will=compiled.statistics.will,
        rng=play.rng,
        system=True,
    )
    return state.model_copy(update={"resources": resources}), result.allowed


def movement(play: PlayService, state: PlayState, actor_id: str) -> int:
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    loaded = inventory_load(
        catalog(play), play.engine.resources, state.resources, actor_id, compiled.statistics
    )
    if loaded.move is None:
        return 0
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor_id}")
    return fatigue_value(fp, impaired_movement(hp, loaded.move))


def injury_turn(
    play: PlayService,
    state: PlayState,
    actor_id: str,
    command_id: str,
    *,
    start: bool,
    do_nothing: bool,
) -> PlayState:
    compiled = build(play, state, actor_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    if hp.injury is None or hp.injury.profile_id != compiled.statistics.profile_id:
        raise ValidationError("GURPS injury requires explicit migration")
    resources, _ = apply_injury(
        state.resources,
        InjuryTurn(
            id=f"injury-{'start' if start else 'end'}:"
            + hashlib.sha256(command_id.encode()).hexdigest(),
            actor_id=actor_id,
            expected_revision=state.resources.revision,
            turn=hp.injury.turn + int(start),
            phase="start" if start else "end",
            do_nothing=do_nothing,
        ),
        ht=compiled.statistics.ht,
        rng=play.rng,
        system=True,
    )
    return state.model_copy(update={"resources": resources})


def catalog(play: PlayService) -> EquipmentCatalog:
    rules = play.engine.rules.combat
    if rules is None or rules.gurps_equipment is None:
        raise ValidationError("No GURPS equipment combat binding")
    return rules.gurps_equipment


def build(play: PlayService, state: PlayState, actor_id: str) -> ValidatedBuild:
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    compiled, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
    )
    if (
        compiled.statistics is None
        or compiled.statistics.profile_id != play.engine.reviewer.compiler.statistics_profile
    ):
        raise ValidationError("Melee requires the campaign's exact statistics profile")
    return compiled


def level(compiled: ValidatedBuild, target: str) -> DerivedValue:
    value = next((v for v in compiled.sheet.values if v.target == target), None)
    if value is None:
        raise ValidationError("Weapon skill has no trained or legal default level")
    return value


def mode(
    play: PlayService, state: PlayState, actor_id: str, item_id: str, mode_id: str | None
) -> MeleeMode:
    item = next((i for i in state.resources.items if i.id == item_id), None)
    if item is None or item.owner_id != actor_id or not item.equipped or not item.ready:
        raise ValidationError("Melee requires an owned, equipped, ready weapon")
    entry = next((e for e in catalog(play).entries if e.definition_id == item.definition_id), None)
    if entry is None:
        raise ValidationError("Weapon is not in the pinned combat catalog")
    modes = tuple(
        m for m in entry.modes if isinstance(m, MeleeMode) and (mode_id is None or m.id == mode_id)
    )
    if len(modes) != 1:
        raise ValidationError("Select exactly one supported melee weapon mode")
    selected = modes[0]
    held_others = sum(
        1
        for other in state.resources.items
        if other.owner_id == actor_id
        and other.id != item_id
        and other.equipped
        and other.ready
        and any(
            e.definition_id == other.definition_id and (e.modes or e.shield)
            for e in catalog(play).entries
        )
    )
    if selected.hands + held_others > 2:
        raise ValidationError("Selected grip exceeds available hands")
    level(build(play, state, actor_id), selected.skill_id)
    return selected


def defense_value(
    play: PlayService,
    state: PlayState,
    participant: Combatant,
    selected: Defense,
    item_id: str | None = None,
) -> tuple[DerivedValue | None, str | None]:
    if selected == "none":
        return None, None
    compiled = build(play, state, participant.actor_id)
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{participant.actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{participant.actor_id}")
    if not fatigue_ready(state, participant.actor_id):
        raise ValidationError("Exhausted actor cannot defend")
    if hp.injury is None or hp.injury.incapacitated:
        raise ValidationError("Incapacitated actor cannot defend")
    equipment = catalog(play)
    entries = {e.definition_id: e for e in equipment.entries}
    ready = [
        i
        for i in state.resources.items
        if i.id in participant.ready_item_ids and i.ready and i.equipped
    ]
    shields = [
        (i, entries[i.definition_id].shield) for i in ready if entries[i.definition_id].shield
    ]
    bonus = max((s.defense_bonus for _, s in shields if s is not None), default=0)
    penalty = (
        participant.defense_penalty
        + (-4 if hp.injury.stunned else 0)
        + (-3 if participant.posture == "prone" else -2 if participant.posture == "kneeling" else 0)
    )
    if selected == "dodge":
        loaded = inventory_load(
            equipment,
            play.engine.resources,
            state.resources,
            participant.actor_id,
            compiled.statistics,
        )
        if loaded.dodge is None:
            raise ValidationError("Overloaded actor cannot dodge")
        return DerivedValue(
            "defense:dodge",
            Decimal(fatigue_value(fp, impaired_movement(hp, loaded.dodge)) + bonus + penalty),
            (),
        ), None
    candidates: list[tuple[int, str, str]] = []
    for item in ready:
        if item_id is not None and item.id != item_id:
            continue
        entry = entries[item.definition_id]
        if (
            selected == "block"
            and entry.shield
            and entry.shield.can_block
            and not participant.block_used
        ):
            value = level(compiled, entry.shield.skill_id)
            candidates.append((int(value.value) // 2 + 3, item.id, entry.shield.skill_id))
        if selected == "parry":
            for weapon_mode in entry.modes:
                if not isinstance(weapon_mode, MeleeMode) or weapon_mode.parry is None:
                    continue
                try:
                    mode(play, state, participant.actor_id, item.id, weapon_mode.id)
                except ValidationError:
                    continue
                parry = weapon_mode.parry
                if (
                    parry.unbalanced
                    and participant.last_maneuver == "attack"
                    and participant.last_attack_item_id == item.id
                ):
                    continue
                value = level(compiled, weapon_mode.skill_id)
                repeats = participant.parries.count(item.id)
                # Lite permits only one parry with each weapon per turn.
                if repeats and equipment.profile_id == "gurps-lite-4e-2004":
                    continue
                repeat_penalty = repeats * (2 if parry.fencing else 4)
                candidates.append(
                    (
                        (
                            int(value.value)
                            - max(
                                0,
                                weapon_mode.minimum_st - fatigue_value(fp, compiled.statistics.st),
                            )
                        )
                        // 2
                        + 3
                        + parry.modifier
                        - repeat_penalty,
                        item.id,
                        weapon_mode.skill_id,
                    )
                )
    if not candidates:
        raise ValidationError("No available skill/equipment for this active defense")
    score, selected_item, skill = max(candidates)
    return DerivedValue(
        f"defense:{selected}:{skill}", Decimal(score + bonus + penalty), ()
    ), selected_item


def prepare_attack(
    play: PlayService, state: PlayState, encounter: Encounter, mode_id: str | None
) -> Encounter:
    pending = encounter.pending_defense
    assert pending is not None
    selected = mode(play, state, pending.attacker_id, pending.weapon_id, mode_id)
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    if (
        abs(attacker.position.x - defender.position.x)
        + abs(attacker.position.y - defender.position.y)
        not in selected.reach
    ):
        raise ValidationError("Target is outside selected weapon reach")
    allowed: list[Defense] = ["none"]
    for candidate in ("dodge", "parry", "block"):
        try:
            defense_value(play, state, defender, candidate)
        except ValidationError:
            continue
        allowed.append(candidate)
    return encounter.model_copy(
        update={
            "pending_defense": pending.model_copy(
                update={"mode_id": selected.id, "allowed": tuple(allowed)}
            )
        }
    )


def resolve_melee(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    pending = encounter.pending_defense
    assert pending is not None
    equipment = catalog(play)
    weapon = mode(play, state, pending.attacker_id, pending.weapon_id, pending.mode_id)
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    attack_build = build(play, state, pending.attacker_id)
    defend_build = build(play, state, pending.defender_id)
    assert attack_build.statistics is not None and defend_build.statistics is not None
    attack_value = level(attack_build, weapon.skill_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{pending.defender_id}")
    attacker_hp = next(p for p in state.resources.pools if p.id == f"hp:{pending.attacker_id}")
    attacker_fp = next(p for p in state.resources.pools if p.id == f"fp:{pending.attacker_id}")
    if hp.injury is None or attacker_hp.injury is None:
        raise ValidationError("GURPS injury pool requires explicit migration")
    defense_derived, defense_item = defense_value(play, state, defender, selected, item_id)
    attack_target = (
        int(attack_value.value)
        - attacker_hp.injury.shock
        - max(0, weapon.minimum_st - fatigue_value(attacker_fp, attack_build.statistics.st))
    )
    attack_target -= (
        4 if attacker.posture == "prone" else 2 if attacker.posture == "kneeling" else 0
    )
    attack = success_roll(equipment.profile_id, attack_target, rng=play.rng)
    defense = None
    hit = attack.outcome.succeeded
    critical_dice: tuple[int, ...] = ()
    critical = 0
    blocked = None
    if (
        attack.outcome is Outcome.CRITICAL_FAILURE
        and equipment.profile_id == "gurps-basic-set-4e-2004"
    ):
        critical_dice = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        blocked = f"basic-critical-miss:{sum(critical_dice)}"
    if hit and attack.outcome is not Outcome.CRITICAL_SUCCESS and defense_derived is not None:
        defense = success_roll(equipment.profile_id, int(defense_derived.value), rng=play.rng)
        hit = not defense.outcome.succeeded
        if selected == "parry" and defense_item:
            defender = defender.model_copy(update={"parries": defender.parries + (defense_item,)})
        if selected == "block":
            defender = defender.model_copy(update={"block_used": True})
        if equipment.profile_id == "gurps-basic-set-4e-2004" and (
            defense.outcome is Outcome.CRITICAL_SUCCESS
            or (selected == "parry" and defense.outcome is Outcome.CRITICAL_FAILURE)
        ):
            critical_dice = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
            blocked = f"basic-critical-miss:{sum(critical_dice)}:{'attacker' if defense.outcome.succeeded else 'defender'}"
            hit = False
        elif defense.outcome is Outcome.CRITICAL_FAILURE:
            if selected == "dodge":
                defender = defender.model_copy(update={"posture": "prone"})
            elif selected == "block" and defense_item:
                state = state.model_copy(
                    update={
                        "resources": state.resources.model_copy(
                            update={
                                "items": tuple(
                                    i.model_copy(update={"ready": False})
                                    if i.id == defense_item
                                    else i
                                    for i in state.resources.items
                                )
                            }
                        )
                    }
                )
                defender = defender.model_copy(
                    update={
                        "ready_item_ids": tuple(
                            i for i in defender.ready_item_ids if i != defense_item
                        )
                    }
                )
    if (
        attack.outcome is Outcome.CRITICAL_SUCCESS
        and equipment.profile_id == "gurps-basic-set-4e-2004"
    ):
        critical_dice = tuple(play.rng.randbelow(6) + 1 for _ in range(3))
        critical = sum(critical_dice)
    expression = (
        attack_build.statistics.swing
        if weapon.damage.basis == "swing"
        else attack_build.statistics.thrust
    )
    dice_count = weapon.damage.dice or expression.dice
    adds = weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add)
    maximum = critical in (6, 15) or (
        equipment.profile_id == "gurps-lite-4e-2004" and sum(attack.dice) <= 4
    )
    dice = (
        tuple(play.rng.randbelow(6) + 1 for _ in range(dice_count)) if hit and not maximum else ()
    )
    basic = (
        max(
            0 if weapon.damage.damage_type == "cr" else 1,
            (6 * dice_count if maximum else sum(dice)) + adds,
        )
        if hit
        else 0
    )
    basic *= 3 if critical in (3, 18) else 2 if critical in (5, 16) else 1
    entries = {e.definition_id: e for e in equipment.entries}
    resistance = max(
        (
            e.armor.dr
            for i in state.resources.items
            if i.owner_id == pending.defender_id and i.equipped
            for e in (entries[i.definition_id],)
            if e.armor and "torso" in e.armor.locations
        ),
        default=0,
    )
    if critical in (4, 17):
        resistance //= 2
    injury = 0
    held = tuple(
        i.id
        for i in state.resources.items
        if i.id in defender.ready_item_ids
        and (entries[i.definition_id].modes or entries[i.definition_id].shield)
    )
    if hit:
        resources, result = apply_injury(
            state.resources,
            Wound(
                id=pending.id,
                actor_id=pending.defender_id,
                expected_revision=state.resources.revision,
                basic_damage=basic,
                resistance=resistance,
                damage_type=weapon.damage.damage_type,
            ),
            ht=defend_build.statistics.ht,
            rng=play.rng,
            system=True,
            held_item_ids=held,
            force_major_wound=critical in (7, 13, 14),
            double_shock=critical == 8,
        )
        injury = result.injury
        state = state.model_copy(update={"resources": resources})
    updated_hp = next(p for p in state.resources.pools if p.id == hp.id)
    status = updated_hp.injury
    assert status is not None
    drops = held if critical == 12 else ()
    if drops:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"ready": False, "equipped": False})
                            if i.id in drops
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
    defender = defender.model_copy(
        update={
            "posture": "prone" if status.prone else defender.posture,
            "ready_item_ids": tuple(
                sorted(
                    i.id
                    for i in state.resources.items
                    if i.owner_id == defender.actor_id and i.ready and i.equipped
                )
            ),
        }
    )
    state = state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(
                    update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                )
                if a.actor_id == defender.actor_id and status.incapacitated
                else a
                for a in state.actors
            )
        }
    )
    encounter = encounter.model_copy(
        update={
            "participants": tuple(
                defender if p.actor_id == defender.actor_id else p for p in encounter.participants
            ),
            "blocked_reason": blocked,
        }
    )
    if blocked:
        number = sum(critical_dice)
        subject = defender if blocked.endswith(":defender") else attacker
        affected_item = defense_item if subject.actor_id == defender.actor_id else pending.weapon_id
        if number in (7, 13):
            subject = subject.model_copy(update={"defense_penalty": -2})
            blocked = None
        elif number == 16:
            subject = subject.model_copy(update={"posture": "prone"})
            blocked = None
        elif number in (8, 9, 10, 11, 12) or (
            number == 14
            and (weapon.damage.basis != "swing" or subject.actor_id == defender.actor_id)
        ):
            state = state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                i.model_copy(
                                    update={
                                        "ready": False,
                                        "equipped": False
                                        if number in (9, 10, 11, 14)
                                        else i.equipped,
                                    }
                                )
                                if i.id == affected_item
                                else i
                                for i in state.resources.items
                            )
                        }
                    )
                }
            )
            subject = subject.model_copy(
                update={
                    "ready_item_ids": tuple(i for i in subject.ready_item_ids if i != affected_item)
                }
            )
            blocked = None
        encounter = encounter.model_copy(
            update={
                "participants": tuple(
                    subject if p.actor_id == subject.actor_id else p for p in encounter.participants
                ),
                "blocked_reason": blocked,
            }
        )
    trace = InjuryTrace(
        attack=attack,
        defense=defense,
        attack_value=attack_value,
        defense_value=defense_derived,
        damage_dice=dice,
        basic_damage=basic,
        resistance=resistance,
        injury=injury,
        hp_before=hp.current,
        hp_after=updated_hp.current,
        incapacitated=status.incapacitated,
        profile_id=equipment.profile_id,
        rules_version="2004",
        critical_table=critical_dice,
        adjudication_required=blocked,
    )
    return state, encounter, trace
