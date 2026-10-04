"""Facts about one approved actor: their build, gear, fatigue and movement.

Every domain needs these, so they live beside the state rather than inside any
one mechanic. Resolving a mechanic is the caller's job; nothing here decides an
attack, a spell or a procedure.
"""

from __future__ import annotations

import hashlib

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.effects import DerivedValue
from wayfarer.engine.rules.types.recovery import interrupt_tasks
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.entangle import immobilized
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog, inventory_load
from wayfarer.engine.simulation.health.drug_state import drug_unconscious
from wayfarer.engine.simulation.health.fatigue import ContinueExertion, apply_fatigue, fatigue_value
from wayfarer.engine.simulation.health.hit_locations import disabled, part
from wayfarer.engine.simulation.health.injury import InjuryTurn, apply_injury, impaired_movement
from wayfarer.engine.simulation.health.symptom_state import projected_build
from wayfarer.engine.simulation.magic.backfires import clear_stun, mental_stun, refund_due
from wayfarer.engine.simulation.magic.haste_effects import bonus as haste_bonus
from wayfarer.engine.simulation.magic.rooted_feet_state import active_effect
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.size_forms import reduced_body_result
from wayfarer.errors import ValidationError


def fatigue_ready(state: PlayState, actor_id: str) -> bool:
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor_id}")
    if fp.fatigue is None:
        raise ValidationError("GURPS fatigue requires explicit migration")
    return not (
        drug_unconscious(state.resources, actor_id)
        or fp.fatigue.collapsed
        or fp.fatigue.unconscious
        or fp.fatigue.heart_attack
        or fp.current <= -fp.maximum
    )


def exertion(
    runtime: RulesContext, state: PlayState, actor_id: str, command_id: str
) -> tuple[PlayState, bool]:
    """Begin voluntary physical activity; persist a failed exertion result too."""
    compiled = build(runtime, state, actor_id)
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
        rng=runtime.rng,
        system=True,
    )
    return state.model_copy(update={"resources": resources}), result.allowed


def movement(runtime: RulesContext, state: PlayState, actor_id: str) -> int:
    if active_effect(state.resources, actor_id) is not None:
        return 0
    if any(part(p) in ("leg", "foot") for p in disabled(state.resources, actor_id)):
        # Supported combat movement is walking; crutches/crawling require an explicit mode.
        return 0
    # A binding that pins the legs stops movement outright until it is shed.
    if any(
        immobilized(p)
        for e in state.encounters
        if e.status == "active"
        for p in e.participants
        if p.actor_id == actor_id
    ):
        return 0
    compiled = build(runtime, state, actor_id)
    assert compiled.statistics is not None
    loaded = inventory_load(
        catalog(runtime), runtime.resources, state.resources, actor_id, compiled.statistics
    )
    if loaded.move is None:
        return 0
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    fp = next(p for p in state.resources.pools if p.id == f"fp:{actor_id}")
    return fatigue_value(
        fp,
        impaired_movement(
            hp,
            reduced_body_result(
                state.resources, actor_id, loaded.move + haste_bonus(state.resources, actor_id)
            ),
        ),
    )


def injury_turn(
    runtime: RulesContext,
    state: PlayState,
    actor_id: str,
    command_id: str,
    *,
    start: bool,
    do_nothing: bool,
    captured_end_build: ValidatedBuild | None = None,
) -> PlayState:
    if captured_end_build is not None and start:
        raise ValidationError("A captured build may only finish its already spent turn")
    compiled = (
        build(runtime, state, actor_id)
        if captured_end_build is None
        else projected_build(
            state.resources,
            actor_id,
            captured_end_build,
            runtime.reviewer.compiler.definitions,
            correct_attributes=runtime.correct_symptom_attributes,
        )
    )
    assert compiled.statistics is not None
    hp = next(p for p in state.resources.pools if p.id == f"hp:{actor_id}")
    if hp.injury is None or hp.injury.profile_id != compiled.statistics.profile_id:
        raise ValidationError("GURPS injury requires explicit migration")

    resources = state.resources
    if start:
        resources = refund_due(resources, actor_id, turn=hp.injury.turn + 1)
    was_mental = mental_stun(resources, actor_id)
    resources, _ = apply_injury(
        resources,
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
        stun_iq=compiled.statistics.iq if was_mental or hp.injury.surprise is not None else None,
        rng=runtime.rng,
        system=True,
    )
    after_hp = next(p for p in resources.pools if p.id == f"hp:{actor_id}")
    if was_mental and after_hp.injury and not after_hp.injury.stunned:
        resources = clear_stun(resources, actor_id, command_id)
    return state.model_copy(update={"resources": resources})


def catalog(runtime: RulesContext) -> EquipmentCatalog:
    rules = runtime.rules.combat
    if rules is None or rules.gurps_equipment is None:
        raise ValidationError("No GURPS equipment combat binding")
    return rules.gurps_equipment


def build(
    runtime: RulesContext, state: PlayState, actor_id: str, *, defensive: bool = False
) -> ValidatedBuild:
    if runtime.attack_source is not None and runtime.attack_source[0] == actor_id:
        compiled = runtime.attack_source[1]
    else:
        actor = next(a for a in state.actors if a.actor_id == actor_id)
        compiled, _ = runtime.reviewer.activate(
            actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor_id
        )
    if (
        compiled.statistics is None
        or compiled.statistics.profile_id != runtime.reviewer.compiler.statistics_profile
    ):
        raise ValidationError("Melee requires the campaign's exact statistics profile")
    return projected_build(
        state.resources,
        actor_id,
        compiled,
        runtime.reviewer.compiler.definitions,
        defensive=defensive,
        correct_attributes=runtime.correct_symptom_attributes,
    )


def level(compiled: ValidatedBuild, target: str) -> DerivedValue:
    value = next((v for v in compiled.sheet.values if v.target == target), None)
    if value is None:
        raise ValidationError("Weapon skill has no trained or legal default level")
    return value
