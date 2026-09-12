"""Grapples in progress: holding, breaking free, locks and takedowns."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.unarmed.choke import start_choke
from wayfarer.engine.simulation.combat.unarmed.fighters import fighter, free_hands, strength
from wayfarer.engine.simulation.combat.unarmed.injury import hurt
from wayfarer.engine.simulation.combat.unarmed.records import BASIC, UnarmedTrace, contest
from wayfarer.engine.simulation.health.condition_checks import check_modifiers, retching_penalty
from wayfarer.engine.simulation.health.physical_traits import physical_traits

if TYPE_CHECKING:
    from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
    from wayfarer.engine.simulation.rules_context import RulesContext


def control(
    runtime: RulesContext, state: PlayState, encounter: Encounter, command: TakeUnarmedTurn
) -> tuple[PlayState, Encounter, UnarmedTrace]:
    grip = next(g for g in encounter.grips if g.id == command.grip_id)
    actor, target = fighter(encounter, command.actor_id), fighter(encounter, command.target_id)
    checks: tuple[CheckTrace, ...] = ()
    won = True
    damage = injury = 0
    if command.action == "release":
        remaining = tuple(h for h in grip.hands if h not in command.hands) if command.hands else ()
        encounter = encounter.model_copy(
            update={
                "grips": tuple(
                    g.model_copy(update={"hands": remaining}) if g.id == grip.id else g
                    for g in encounter.grips
                    if g.id != grip.id or remaining
                )
            }
        )
    elif command.action == "break_free":
        first = strength(runtime, state, actor.actor_id) - grip.escape_penalty
        target_hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
        assert target_hp.injury is not None
        second = (
            strength(runtime, state, target.actor_id)
            + (5 if len(grip.hands) == 2 else 0)
            + (5 if grip.pinned else 0)
            + (4 if grip.arm_lock else 0)
            - (4 if target_hp.injury.stunned else 0)
        )
        won, checks, _ = contest(
            BASIC,
            actor.actor_id,
            target.actor_id,
            first,
            second,
            first_modifiers=check_modifiers(state.resources, actor.actor_id, "st"),
            second_modifiers=check_modifiers(state.resources, target.actor_id, "st"),
            rng=runtime.rng,
        )
        encounter = encounter.model_copy(
            update={
                "grips": tuple(
                    g.model_copy(update={"escape_after_round": encounter.round + 10})
                    if g.id == grip.id and g.pinned and not won
                    else g
                    for g in encounter.grips
                    if not (g.id == grip.id and won)
                )
            }
        )
        if grip.arm_lock and not won:
            encounter = encounter.model_copy(
                update={
                    "grips": tuple(
                        g.model_copy(update={"escape_penalty": g.escape_penalty + 1})
                        if g.id == grip.id
                        else g
                        for g in encounter.grips
                    )
                }
            )
    elif command.action in ("strangle", "lock_damage"):
        compiled = build(runtime, state, target.actor_id)
        assert compiled.statistics is not None
        first = strength(runtime, state, actor.actor_id, trained=False)
        if command.action == "strangle":
            first -= 5 if len(grip.hands) == 1 else 0
        else:
            attacker = build(runtime, state, actor.actor_id)
            first = max(
                first,
                *(
                    int(v.value) + retching_penalty(state.resources, actor.actor_id)
                    for v in attacker.sheet.values
                    if v.target in {"skill:judo", "skill:wrestling"}
                ),
            )
        second = max(
            strength(runtime, state, target.actor_id, trained=False),
            compiled.statistics.ht + physical_traits(state.resources, target.actor_id).fitness,
        )
        won, checks, _ = contest(
            BASIC,
            actor.actor_id,
            target.actor_id,
            first,
            second,
            first_modifiers=check_modifiers(state.resources, actor.actor_id, "st"),
            second_modifiers=check_modifiers(state.resources, target.actor_id, "st"),
            rng=runtime.rng,
        )
        if won:
            damage = checks[0].margin - checks[1].margin
            target_hp = next(p for p in state.resources.pools if p.id == f"hp:{target.actor_id}")
            assert target_hp.injury is not None
            pain = command.action == "lock_damage" and any(
                w.location == grip.location
                and w.kind == "crippled"
                and w.active(
                    now=state.resources.game_time, full_hp=target_hp.current == target_hp.maximum
                )
                for w in target_hp.injury.lasting_injuries
            )
            state, encounter, injury = hurt(
                runtime,
                state,
                encounter,
                target.actor_id,
                "control-damage:" + hashlib.sha256(command.id.encode()).hexdigest(),
                damage,
                location=grip.location,
                rigid_only=command.action == "lock_damage",
                pain_only=pain,
            )
        if command.action == "strangle" and injury > 0 and grip.hazard_id is None:
            state, grip = start_choke(runtime, state, grip, command.id)
        grip = grip.model_copy(update={"last_damage_round": encounter.round})
        encounter = encounter.model_copy(
            update={"grips": tuple(grip if g.id == grip.id else g for g in encounter.grips)}
        )
    elif command.action == "takedown":

        def score(actor_id: str) -> int:
            compiled = build(runtime, state, actor_id)
            return max(
                strength(runtime, state, actor_id),
                *(
                    int(v.value) + retching_penalty(state.resources, actor_id)
                    for v in compiled.sheet.values
                    if v.target
                    in {"attribute:dx", "skill:judo", "skill:wrestling", "skill:sumo-wrestling"}
                ),
            )

        won, checks, decided = contest(
            BASIC,
            actor.actor_id,
            target.actor_id,
            score(actor.actor_id)
            - (4 if actor.posture == "prone" else 2 if actor.posture == "kneeling" else 0),
            score(target.actor_id),
            first_modifiers=check_modifiers(state.resources, actor.actor_id, "st"),
            second_modifiers=check_modifiers(state.resources, target.actor_id, "st"),
            rng=runtime.rng,
        )
        if decided:
            falling = target if won else actor
            encounter = CombatEngine._replace(
                encounter, falling.model_copy(update={"posture": "prone"})
            ).model_copy(
                update={
                    "grips": tuple(
                        g
                        for g in encounter.grips
                        if not (
                            g.holder_id == falling.actor_id
                            and g.target_id in (actor.actor_id, target.actor_id)
                        )
                    )
                }
            )
    else:
        # B370: compare free hands, counting hands already holding this grapple.
        hands_a = len(free_hands(state, encounter, actor.actor_id)) + len(grip.hands)
        hands_b = len(free_hands(state, encounter, target.actor_id))
        won, checks, _ = contest(
            BASIC,
            actor.actor_id,
            target.actor_id,
            strength(runtime, state, actor.actor_id) + (3 if hands_a > hands_b else 0),
            strength(runtime, state, target.actor_id) + (3 if hands_b > hands_a else 0),
            regular=True,
            first_modifiers=check_modifiers(state.resources, actor.actor_id, "st"),
            second_modifiers=check_modifiers(state.resources, target.actor_id, "st"),
            rng=runtime.rng,
        )
        if won:
            encounter = encounter.model_copy(
                update={
                    "grips": tuple(
                        g.model_copy(
                            update={"pinned": True, "escape_after_round": encounter.round + 10}
                        )
                        if g.id == grip.id
                        else g
                        for g in encounter.grips
                    )
                }
            )
    return (
        state,
        encounter,
        UnarmedTrace(
            action=command.action,
            actor_id=actor.actor_id,
            target_id=target.actor_id,
            checks=checks,
            won=won,
            grip_id=grip.id,
            basic_damage=damage,
            injury=injury,
        ),
    )
