"""Pure, catalog-backed resolution of a persisted attack and defense choice."""

from __future__ import annotations

from wayfarer.engine.rules.checks import Modifier, Outcome, draw_dice, success_check
from wayfarer.engine.rules.effects import DerivedValue, EffectEvaluator, MechanicalTarget
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.combat import Defense, Encounter, InjuryTrace
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ValidationError


def combat_value(runtime: RulesContext, state: PlayState, actor_id: str) -> DerivedValue:
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    build, _ = runtime.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
    )
    base = next(v.value for v in build.sheet.values if v.target == "attribute:dx")
    return EffectEvaluator((MechanicalTarget("attribute:dx"),)).evaluate(
        "attribute:dx",
        base,
        runtime.resources.equipment_effects(state.resources, actor_id),
        context={"actor_id": actor_id},
        at=state.resources.game_time,
    )


def resolve_injury(
    runtime: RulesContext, state: PlayState, encounter: Encounter, selected: Defense
) -> tuple[PlayState, InjuryTrace]:
    rules = runtime.rules.combat
    pending = encounter.pending_defense
    if rules is None or pending is None:
        raise ValidationError("No pending attack")
    weapon = next(i for i in state.resources.items if i.id == pending.weapon_id)
    profile = next((p for p in rules.attacks if p.definition_id == weapon.definition_id), None)
    if profile is None:
        raise ValidationError("Unsupported combat weapon or attack mode")
    if selected not in ("dodge", "parry", "none"):
        raise ValidationError("Unsupported active defense")
    target = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    if selected == "parry" and not any(
        i.id in target.ready_item_ids
        and any(p.definition_id == i.definition_id for p in rules.attacks)
        for i in state.resources.items
    ):
        raise ValidationError("Parry requires a supported ready weapon")
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    hp = next(p for p in state.resources.pools if p.id == f"hp:{pending.defender_id}")
    if hp.injury is not None:
        raise ValidationError("GURPS attack and defense dispatch requires the GURPS combat adapter")
    attack_value = combat_value(runtime, state, pending.attacker_id)
    attack = success_check(
        int(attack_value.value),
        (
            Modifier(
                profile.attack_modifier, "attack profile", profile.definition_id, str(rules.version)
            ),
            Modifier(
                0 if attacker.posture == "standing" else -2, "posture", rules.id, str(rules.version)
            ),
        ),
        rng=runtime.rng,
        rules_package=rules.id,
        rules_version=str(rules.version),
    )
    defense_value = None
    defense = None
    hit = attack.outcome in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS)
    # The declared subset gives critical hits ordinary damage but bypasses defense.
    if hit and attack.outcome != Outcome.CRITICAL_SUCCESS and selected != "none":
        defense_value = combat_value(runtime, state, pending.defender_id)
        defense = success_check(
            int(defense_value.value) // 2 + 3,
            (
                Modifier(
                    0 if target.posture == "standing" else -2,
                    "posture",
                    rules.id,
                    str(rules.version),
                ),
            ),
            rng=runtime.rng,
            rules_package=rules.id,
            rules_version=str(rules.version),
        )
        hit = defense.outcome not in (Outcome.SUCCESS, Outcome.CRITICAL_SUCCESS)
    dice = draw_dice(runtime.rng, profile.damage_dice) if hit else ()
    basic = max(0, sum(dice) + profile.damage_bonus) if hit else 0
    # Only the strongest equipped torso armor applies: duplicate armor cannot stack.
    resistance = max(
        (
            p.resistance
            for p in rules.protection
            for i in state.resources.items
            if i.owner_id == pending.defender_id
            and i.equipped
            and i.definition_id == p.definition_id
        ),
        default=0,
    )
    injury = max(0, basic - resistance) * profile.injury_multiplier if hit else 0
    remaining = max(0, hp.current - injury)
    stunned_until = (
        state.resources.game_time + profile.stun_ticks
        if injury > 0 and remaining > 0 and profile.stun_ticks
        else None
    )
    trace = InjuryTrace(
        attack=attack,
        defense=defense,
        attack_value=attack_value,
        defense_value=defense_value,
        damage_dice=dice,
        basic_damage=basic,
        resistance=resistance,
        injury=injury,
        hp_before=hp.current,
        hp_after=remaining,
        incapacitated=remaining == 0,
        stunned_until=stunned_until,
        profile_id=profile.definition_id,
        rules_version=str(rules.version),
    )
    resources = state.resources.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": remaining}) if p.id == hp.id else p
                for p in state.resources.pools
            )
        }
    )
    actors = tuple(
        a.model_copy(
            update={
                "conditions": tuple(dict.fromkeys((*a.conditions, "unconscious")))
                if remaining == 0
                else a.conditions,
                "available_at": max(a.available_at, stunned_until or 0),
            }
        )
        if a.actor_id == pending.defender_id
        else a
        for a in state.actors
    )
    return state.model_copy(update={"resources": resources, "actors": actors}), trace
