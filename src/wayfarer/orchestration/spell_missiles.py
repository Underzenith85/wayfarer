"""Held Fireball release through the existing defense pause and injury service."""

from wayfarer.errors import ValidationError
from wayfarer.orchestration.gurps_melee import build, defense_value, level
from wayfarer.orchestration.gurps_ranged import range_penalty
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spell_effects import armor
from wayfarer.rules.checks import Outcome
from wayfarer.rules.conformance import BASELINE_ID
from wayfarer.rules.gurps_checks import success_roll
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.combat import CombatEngine, Defense, Encounter, InjuryTrace
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.resources import ResourceEvent
from wayfarer.simulation.spells import PROFILE, SpellEvent, SpellResult, event_id, latest


def resolve(
    play: PlayService,
    state: PlayState,
    encounter: Encounter,
    selected: Defense,
    item_id: str | None,
    second_defense: Defense | None,
    second_item_id: str | None,
) -> tuple[PlayState, Encounter, InjuryTrace]:
    pending = encounter.pending_defense
    assert pending is not None and pending.spell_cast_id is not None
    effect = latest(state.resources).get(pending.spell_cast_id)
    if (
        effect is None
        or effect.phase != "active"
        or effect.spell_id != "fireball"
        or not effect.execute_effects
    ):
        raise ValidationError("Missile is no longer held")
    attacker = next(p for p in encounter.participants if p.actor_id == pending.attacker_id)
    defender = next(p for p in encounter.participants if p.actor_id == pending.defender_id)
    attack_build = build(play, state, attacker.actor_id)
    defend_build = build(play, state, defender.actor_id)
    assert defend_build.statistics
    if attack_build.revision != effect.build_revision:
        raise ValidationError("Missile build changed")
    value = level(attack_build, "skill:innate-attack-projectile")
    distance = CombatEngine.distance(attacker.position, defender.position)
    if distance > 50:
        raise ValidationError("Fireball exceeds its maximum range")
    hp = next(p for p in state.resources.pools if p.id == "hp:" + defender.actor_id)
    actor_hp = next(p for p in state.resources.pools if p.id == "hp:" + attacker.actor_id)
    defense, used_item = defense_value(play, state, defender, selected, item_id)
    second, second_item = defense_value(
        play, state, defender, second_defense or "none", second_item_id
    )
    attack = success_roll(
        PROFILE,
        int(value.value)
        + range_penalty(distance)
        - (actor_hp.injury.shock if actor_hp.injury else 0),
        rng=play.rng,
    )
    defended = None
    second_roll = None
    hit = attack.outcome.succeeded
    if hit and attack.outcome is not Outcome.CRITICAL_SUCCESS and defense is not None:
        defended = success_roll(PROFILE, int(defense.value), rng=play.rng)
        hit = not defended.outcome.succeeded
        if selected == "block":
            defender = defender.model_copy(update={"block_used": True})
        if hit and second is not None:
            second_roll = success_roll(PROFILE, int(second.value), rng=play.rng)
            hit = not second_roll.outcome.succeeded
            if second_defense == "block":
                defender = defender.model_copy(update={"block_used": True})
    dropped: set[str] = set()
    for choice, roll, equipment_id in (
        (selected, defended, used_item),
        (second_defense, second_roll, second_item),
    ):
        if roll and roll.outcome is Outcome.CRITICAL_FAILURE:
            if choice == "dodge":
                defender = defender.model_copy(update={"posture": "prone"})
            elif choice == "block" and equipment_id:
                dropped.add(equipment_id)
    resources = state.resources
    # Same fail-closed critical-table boundary as the Basic ranged service.
    blocked = attack.outcome in (Outcome.CRITICAL_SUCCESS, Outcome.CRITICAL_FAILURE)
    critical = tuple(play.rng.randbelow(6) + 1 for _ in range(3)) if blocked else ()
    dice = (
        tuple(play.rng.randbelow(6) + 1 for _ in range(effect.energy))
        if hit and not blocked
        else ()
    )
    damage = sum(dice) // (2 if distance > 25 else 1)
    dr = armor(play, state, defender.actor_id)
    lost = 0
    if damage:
        held = tuple(
            i.id
            for i in resources.items
            if i.id in {item for item, _ in defender.hand_bindings} and i.ready and i.equipped
        )
        resources, injury = apply_injury(
            resources,
            Wound(
                id=event_id(pending.id) + ":impact",
                actor_id=defender.actor_id,
                expected_revision=resources.revision,
                basic_damage=damage,
                resistance=dr,
                damage_type="burn",
            ),
            ht=defend_build.statistics.ht,
            rng=play.rng,
            system=True,
            held_item_ids=held,
        )
        lost = injury.injury
    resources = resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"ready": False}) if i.id in dropped else i
                for i in resources.items
            ),
            "events": resources.events
            + (
                ResourceEvent(
                    id=event_id(pending.id) + ":released",
                    at=resources.game_time,
                    target_id=attacker.actor_id,
                    kind=SpellEvent(
                        effect=effect.model_copy(update={"phase": "ended"}),
                        result=SpellResult(outcome="released"),
                    ).model_dump_json(),
                ),
            ),
        }
    )
    ready = {i.id for i in resources.items if i.ready and i.equipped}
    defender = defender.model_copy(
        update={"ready_item_ids": tuple(i for i in defender.ready_item_ids if i in ready)}
    )
    encounter = CombatEngine._replace(encounter, defender)
    from wayfarer.orchestration.gurps_maneuvers import distracted

    encounter = distracted(
        play,
        state.model_copy(update={"resources": resources}),
        encounter,
        defender.actor_id,
        defended=defended is not None,
        injured=lost > 0,
    )
    if blocked:
        encounter = encounter.model_copy(update={"blocked_reason": "ranged-critical-table"})
    updated_hp = next(p for p in resources.pools if p.id == hp.id)
    return (
        state.model_copy(update={"resources": resources}),
        encounter,
        InjuryTrace(
            attack=attack,
            defense=defended,
            second_defense=second_roll,
            attack_value=value,
            defense_value=defense,
            damage_dice=dice,
            basic_damage=damage,
            resistance=dr,
            injury=lost,
            hp_before=hp.current,
            hp_after=updated_hp.current,
            incapacitated=bool(updated_hp.injury and updated_hp.injury.incapacitated),
            profile_id=PROFILE,
            rules_version=BASELINE_ID,
            critical_table=critical,
            adjudication_required="ranged-critical-table" if blocked else None,
            shots_fired=1,
            hits=int(hit and not blocked),
            per_hit_damage=(damage,) if dice else (),
            per_hit_injury=(lost,) if dice else (),
        ),
    )
