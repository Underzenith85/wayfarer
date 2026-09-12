"""Damage from unarmed blows, parries and critical misses."""

from __future__ import annotations

from decimal import Decimal
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import CheckTrace, draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.tables.unarmed import unarmed_critical_miss
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, catalog
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter, skill_value
from wayfarer.engine.simulation.combat.unarmed_records import BASIC, PendingUnarmed
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.injury import Wound, apply_injury

if TYPE_CHECKING:
    from wayfarer.engine.simulation.rules_context import RulesContext


def armor_dr(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    location: HumanLocation,
    *,
    rigid_only: bool = False,
) -> int:
    from wayfarer.engine.simulation.health.hit_locations import armor_resistance

    entries = {e.definition_id: e for e in catalog(runtime).entries}
    resistance = armor_resistance(
        (
            e.armor
            for i in state.resources.items
            if i.owner_id == actor_id and i.equipped
            for e in (entries[i.definition_id],)
            if e.armor
        ),
        location,
        rigid_only=rigid_only,
    )
    if runtime.rules.abilities is not None:
        from wayfarer.engine.simulation.abilities import damage_resistance

        resistance += damage_resistance(
            state.resources, actor_id, build_revision=build(runtime, state, actor_id).revision
        )
    return resistance


def hurt(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    actor_id: str,
    command_id: str,
    basic: int,
    *,
    location: HumanLocation = "torso",
    rigid_only: bool = False,
    critical: int = 0,
    damage_type: DamageType = "cr",
    ignore_dr: bool = False,
    armor_divisor: Decimal = Decimal(1),
    tight_beam: bool = False,
    pain_only: bool = False,
) -> tuple[PlayState, Encounter, int]:
    target = fighter(encounter, actor_id)
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    resistance = armor_dr(runtime, state, actor_id, location, rigid_only=rigid_only)
    resources, result = apply_injury(
        state.resources,
        Wound(
            id=command_id,
            actor_id=actor_id,
            expected_revision=state.resources.revision,
            basic_damage=basic,
            resistance=resistance,
            damage_type=damage_type,
            location=location,
            armor_divisor=armor_divisor,
            tight_beam=tight_beam,
        ),
        ht=compiled.statistics.ht,
        dx=compiled.statistics.dx,
        rng=runtime.rng,
        system=True,
        held_item_ids=target.ready_item_ids,
        held_item_locations=target.hand_bindings,
        force_major_wound=critical in (7, 13, 14),
        double_shock=critical == 8,
        funny_bone=critical == 8,
        halve_dr="down" if critical in (4, 17) else None,
        ignore_dr=ignore_dr,
        pain_only=pain_only,
    )
    state = state.model_copy(update={"resources": resources})
    hp = next(p for p in resources.pools if p.id == f"hp:{actor_id}")
    assert hp.injury is not None
    if hp.injury.incapacitated:
        state = state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(
                        update={"conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))}
                    )
                    if a.actor_id == actor_id
                    else a
                    for a in state.actors
                )
            }
        )
    target = target.model_copy(
        update={
            "posture": "prone" if hp.injury.prone else target.posture,
            "ready_item_ids": tuple(
                sorted(
                    i.id
                    for i in resources.items
                    if i.owner_id == actor_id and i.ready and i.equipped
                )
            ),
        }
    )
    encounter = CombatEngine._replace(encounter, target)
    from wayfarer.engine.simulation.combat.maneuver_transitions import distracted

    encounter = distracted(
        runtime,
        state,
        encounter,
        actor_id,
        defended=False,
        injured=result.injury > 0 or (pain_only and result.penetration > 0),
    )
    return state, encounter, result.injury


def critical_miss(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    pending: PendingUnarmed,
    actor_id: str,
    table: tuple[int, ...],
    hand: str | None,
    parry_mode_id: str | None = None,
) -> tuple[PlayState, Encounter, tuple[CheckTrace, ...], tuple[int, ...], bool]:
    """B557 consequences that fit the current injury and tactical contracts.

    Remaining contextual outcomes retain their table roll and halt the transaction's
    continuation; a bare limb never selects the armed weapon-break/drop table.

    """
    if hand not in (None, "left-hand", "right-hand"):
        from wayfarer.engine.simulation.combat.encounter import PendingDefense
        from wayfarer.engine.simulation.combat.ranged_misses import resolve_miss

        # The shared weapon reducer needs the pending transaction identity, not
        # an invented weapon for the bare-limbed attack. This adapter is local;
        # the durable pause remains PendingUnarmed throughout the transaction.
        context = PendingDefense(
            id=pending.id,
            attacker_id=pending.actor_id,
            defender_id=pending.target_id,
            weapon_id=hand,
            allowed=("parry",),
            opened_round=encounter.round,
            opened_turn=encounter.turn_index,
        )
        original = encounter.pending_defense
        state, encounter, result, blocker = resolve_miss(
            runtime,
            state,
            encounter.model_copy(update={"pending_defense": context}),
            table,
            parry_item=hand,
            parry_mode_id=parry_mode_id,
        )
        encounter = encounter.model_copy(update={"pending_defense": original})
        return (
            state,
            encounter,
            (),
            tuple(d for roll in result.table_rolls[1:] for d in roll)
            + result.location_dice
            + result.damage_dice,
            blocker is None,
        )
    actor = fighter(encounter, actor_id)
    number = sum(table)
    rule = unarmed_critical_miss(number)
    checks: tuple[CheckTrace, ...] = ()
    dice: tuple[int, ...] = ()
    if rule.effect in ("strain", "self-hit"):
        from wayfarer.engine.simulation.equipment.catalog import MeleeMode
        from wayfarer.engine.simulation.health.injury import DisableLocation, apply_location_effect

        if rule.effect == "self-hit" and actor_id == pending.actor_id:
            opponent = fighter(encounter, pending.target_id)
            impaling_modes = tuple(
                m
                for i in state.resources.items
                if i.id in opponent.ready_item_ids
                and i.ready
                and i.equipped
                and (i.condition is None or not i.condition.disabled)
                for e in catalog(runtime).entries
                if e.definition_id == i.definition_id
                for m in e.modes
                if isinstance(m, MeleeMode) and m.damage.damage_type == "imp"
            )
            if len(impaling_modes) > 1:
                return state, encounter, (), (), False
            if impaling_modes:
                weapon = impaling_modes[0]
                compiled = build(runtime, state, actor_id)
                assert compiled.statistics is not None
                expression = (
                    compiled.statistics.swing
                    if weapon.damage.basis == "swing"
                    else compiled.statistics.thrust
                )
                dice = draw_dice(runtime.rng, weapon.damage.dice or expression.dice)
                damage = max(
                    1,
                    sum(dice)
                    + weapon.damage.adds
                    + (0 if weapon.damage.basis == "fixed" else expression.add),
                )
                state, encounter, _ = hurt(
                    runtime,
                    state,
                    encounter,
                    actor_id,
                    pending.id + ":impaling-fall",
                    damage // 2 if rule.half_damage else damage,
                    damage_type="imp",
                    armor_divisor=weapon.damage.armor_divisor,
                    tight_beam=weapon.damage.tight_beam,
                )
                actor = fighter(encounter, actor_id).model_copy(update={"posture": "prone"})
                return state, CombatEngine._replace(encounter, actor), (), dice, True

        kicking = actor_id == pending.actor_id and pending.action == "kick"
        selected_hand = hand
        if not kicking and selected_hand is None:
            if len(pending.hands) == 2:
                dice = (draw_dice(runtime.rng, 1)[0],)
            selected_hand = pending.hands[1 if dice and dice[0] > 3 else 0]
        limb: HumanLocation = (
            ("left-leg" if pending.foot == "left-foot" else "right-leg")
            if kicking
            else ("left-arm" if selected_hand == "left-hand" else "right-arm")
        )
        if rule.effect == "self-hit":
            compiled = build(runtime, state, actor_id)
            assert compiled.statistics is not None
            expression = compiled.statistics.thrust
            damage_dice = draw_dice(runtime.rng, expression.dice)
            damage = max(0, sum(damage_dice) + expression.add)
            state, encounter, _ = hurt(
                runtime,
                state,
                encounter,
                actor_id,
                pending.id + ":self-hit",
                damage // 2 if rule.half_damage else damage,
                location=limb,
            )
            return state, encounter, (), dice + damage_dice, True
        state, encounter, _ = hurt(
            runtime,
            state,
            encounter,
            actor_id,
            pending.id + ":strain-wound",
            1,
            location=limb,
            ignore_dr=True,
        )
        resources, _ = apply_location_effect(
            state.resources,
            DisableLocation.model_validate(
                {
                    "id": pending.id + ":strain",
                    "actor_id": actor_id,
                    "expected_revision": state.resources.revision,
                    "location": limb,
                    "duration_seconds": rule.strain_seconds,
                }
            ),
            system=True,
        )
        actor = fighter(encounter, actor_id)
        if kicking:
            actor = actor.model_copy(update={"posture": "prone"})
        return (
            state.model_copy(update={"resources": resources}),
            CombatEngine._replace(encounter, actor),
            (),
            dice,
            True,
        )
    fall = rule.effect == "fall" or (rule.effect == "stumble" and actor_id == pending.target_id)
    if rule.effect == "trip":
        score = skill_value(runtime, state, actor_id, "attribute:dx")
        score += (
            rule.trip_kick_penalty
            if actor_id == pending.actor_id and pending.action == "kick"
            else 0
        )
        check = success_roll(
            BASIC, score, check_modifiers(state.resources, actor_id, "dx"), rng=runtime.rng
        )
        checks = (check,)
        fall = not check.outcome.succeeded
    elif rule.effect == "balance":
        actor = actor.model_copy(
            update={
                "defense_penalty": actor.defense_penalty + rule.defense_penalty,
                "unarmed_balance_lost": True,
            }
        )
    elif rule.effect == "guard":
        actor = actor.model_copy(
            update={
                "defense_penalty": actor.defense_penalty + rule.defense_penalty,
                "unarmed_guard_dropped": True,
            }
        )
    elif not fall:
        return state, encounter, (), (), False
    if fall:
        if actor.posture == "prone":
            dice = (draw_dice(runtime.rng, 1)[0],)
            # Already-grounded subjects take general injury, bypassing armor.
            state, encounter, _ = hurt(
                runtime,
                state,
                encounter,
                actor_id,
                pending.id + ":fall",
                max(0, dice[0] - 3),
                ignore_dr=True,
            )
            actor = fighter(encounter, actor_id)
        else:
            actor = actor.model_copy(update={"posture": "prone"})
    return state, CombatEngine._replace(encounter, actor), checks, dice, True


def armed_parry_injury(
    runtime: RulesContext,
    state: PlayState,
    encounter: Encounter,
    pending: PendingUnarmed,
    item_id: str,
    mode_id: str | None,
) -> tuple[PlayState, Encounter, tuple[CheckTrace, ...], tuple[int, ...]]:
    """B376: a separate weapon-skill check, never a second defended attack."""
    from wayfarer.engine.simulation.combat.melee.modes import mode
    from wayfarer.engine.simulation.equipment.catalog import MeleeMode

    weapon = mode(runtime, state, pending.target_id, item_id, mode_id)
    assert isinstance(weapon, MeleeMode)
    score = skill_value(runtime, state, pending.target_id, weapon.skill_id)
    score -= 4 if pending.skill in ("skill:judo", "skill:karate") else 0
    check = success_roll(
        BASIC,
        score,
        check_modifiers(state.resources, pending.target_id, "dx", defensive=True),
        rng=runtime.rng,
    )
    if not check.outcome.succeeded:
        return state, encounter, (check,), ()
    compiled = build(runtime, state, pending.target_id)
    assert compiled.statistics is not None
    expression = (
        compiled.statistics.swing if weapon.damage.basis == "swing" else compiled.statistics.thrust
    )
    dice = draw_dice(runtime.rng, weapon.damage.dice or expression.dice)
    damage = max(
        0 if weapon.damage.damage_type == "cr" else 1,
        sum(dice) + weapon.damage.adds + (0 if weapon.damage.basis == "fixed" else expression.add),
    )
    limb: HumanLocation = (
        ("left-leg" if pending.foot == "left-foot" else "right-leg")
        if pending.action == "kick"
        else ("left-arm" if pending.hands[0] == "left-hand" else "right-arm")
    )
    state, encounter, _ = hurt(
        runtime,
        state,
        encounter,
        pending.actor_id,
        pending.id + ":armed-parry",
        damage,
        location=limb,
        damage_type=weapon.damage.damage_type,
        armor_divisor=weapon.damage.armor_divisor,
        tight_beam=weapon.damage.tight_beam,
    )
    return state, encounter, (check,), dice


def drop_held(state: PlayState, encounter: Encounter, actor_id: str) -> tuple[PlayState, Encounter]:
    """B556 result 12 includes every held item, even through armor."""
    actor = fighter(encounter, actor_id)
    held = {i for i, _ in actor.hand_bindings}
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"ready": False, "equipped": False}) if i.id in held else i
                for i in state.resources.items
            )
        }
    )
    actor = actor.model_copy(
        update={
            "ready_item_ids": tuple(i for i in actor.ready_item_ids if i not in held),
            "hand_bindings": (),
        }
    )
    return state.model_copy(update={"resources": resources}), CombatEngine._replace(
        encounter, actor
    )
