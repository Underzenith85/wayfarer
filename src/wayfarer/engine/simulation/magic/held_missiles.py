"""B241 injury to a caster holding a missile, inside the existing transaction."""

from wayfarer.engine.rules.checks import draw_dice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.melee import build
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.area_fire import armor
from wayfarer.engine.simulation.magic.spells import (
    PROFILE,
    SpellEvent,
    SpellResult,
    active_spells,
    event_id,
)
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.engine.simulation.rules_context import RulesContext


def checkpoint(runtime: RulesContext, state: PlayState, before: PlayState) -> PlayState:
    held_before = {e.cast_id for e in active_spells(before.resources) if e.spell_id == "fireball"}
    previous = {p.id: p for p in before.resources.pools}
    resources = state.resources
    for effect in active_spells(resources):
        if (
            not effect.execute_effects
            or effect.spell_id != "fireball"
            or effect.cast_id not in held_before
        ):
            continue
        hp = next(p for p in resources.pools if p.id == "hp:" + effect.actor_id)
        old = previous.get(hp.id)
        if (
            old is None
            or hp.injury is None
            or (hp.current >= old.current and not hp.injury.incapacitated)
        ):
            continue
        identifier = event_id(f"held-injury:{effect.cast_id}:{state.revision}")
        if any(e.id == identifier for e in resources.events):
            continue
        compiled = build(runtime, state, effect.actor_id)
        assert compiled.statistics
        check = (
            None
            if hp.injury.incapacitated
            else success_roll(
                PROFILE,
                compiled.statistics.will,
                check_modifiers(resources, effect.actor_id, "will"),
                rng=runtime.rng,
            )
        )
        lost = check is None or not check.outcome.succeeded
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id=identifier,
                        at=resources.game_time,
                        target_id=effect.actor_id,
                        kind=SpellEvent(
                            effect=effect.model_copy(update={"phase": "ended"}) if lost else effect,
                            result=SpellResult(
                                outcome="interrupted" if lost else "active",
                                checks=(check,) if check else (),
                            ),
                        ).model_dump_json(),
                    ),
                )
            }
        )
        if lost:
            damage = sum(draw_dice(runtime.rng, effect.energy))
            resources, _ = apply_injury(
                resources,
                Wound(
                    id=identifier + ":impact",
                    actor_id=effect.actor_id,
                    expected_revision=resources.revision,
                    basic_damage=damage,
                    resistance=armor(runtime, state, effect.actor_id),
                    damage_type="burn",
                ),
                ht=compiled.statistics.ht,
                rng=runtime.rng,
                system=True,
            )
    alive = {
        p.id.removeprefix("hp:") for p in resources.pools if p.injury and not p.injury.incapacitated
    }
    encounters = tuple(
        e.model_copy(
            update={
                "status": "completed",
                "completion_reason": "incapacitation",
                "pending_defense": None,
                "pending_unarmed": None,
                "wait_interrupt": None,
            }
        )
        if e.status == "active"
        and resources != state.resources
        and len(alive.intersection(e.turn_order)) < 2
        else e
        for e in state.encounters
    )
    return state.model_copy(
        update={
            "resources": resources.model_copy(update={"revision": state.revision}),
            "encounters": encounters,
        }
    )


def concentration_checkpoint(
    runtime: RulesContext, state: PlayState, before: PlayState
) -> PlayState:
    """B238: losing manipulation freezes an effect; critical failure ends it."""
    from wayfarer.engine.rules.checks import Outcome

    resources = state.resources
    for effect in active_spells(resources):
        if not effect.execute_effects or not effect.concentrating:
            continue
        hp = next(p for p in resources.pools if p.id == "hp:" + effect.actor_id)
        old = next(p for p in before.resources.pools if p.id == hp.id)
        defended = any(
            e.pending_defense
            and e.pending_defense.defender_id == effect.actor_id
            and any(
                n.id == e.id and n.pending_defense != e.pending_defense for n in state.encounters
            )
            for e in before.encounters
        )
        if hp.current >= old.current and not defended and not (hp.injury and hp.injury.stunned):
            continue
        identifier = event_id(f"manipulation:{effect.cast_id}:{state.revision}")
        if any(e.id == identifier for e in resources.events):
            continue
        compiled = build(runtime, state, effect.actor_id)
        assert compiled.statistics
        check = success_roll(
            PROFILE,
            compiled.statistics.will - 3,
            check_modifiers(resources, effect.actor_id, "will"),
            rng=runtime.rng,
        )
        effect = effect.model_copy(
            update={
                "concentrating": check.outcome.succeeded,
                "phase": "ended" if check.outcome is Outcome.CRITICAL_FAILURE else effect.phase,
            }
        )
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id=identifier,
                        at=resources.game_time,
                        target_id=effect.actor_id,
                        kind=SpellEvent(
                            effect=effect,
                            result=SpellResult(
                                outcome="interrupted" if not check.outcome.succeeded else "active",
                                checks=(check,),
                            ),
                        ).model_dump_json(),
                    ),
                )
            }
        )
    return state.model_copy(update={"resources": resources})
