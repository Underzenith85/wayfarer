"""B241-242/B251/B253 object consequences shared by lock-spell hosts."""

from typing import TYPE_CHECKING

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource
from wayfarer.engine.rules.gurps_checks import Contestant, resolve_quick_contest, success_roll
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.magic.lock_state import (
    destroyed,
    latest,
    magelocks,
    save,
)
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEffect,
    RuntimeSpellEvent,
    SpellResult,
    active_spells,
    event_id,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError

PROFILE = "gurps-basic-set-4e-2004"

if TYPE_CHECKING:
    from wayfarer.engine.simulation.magic.spells import SpellContext


def lock_scale(state: ResourceState, spell_id: str, target_id: str) -> int:
    if spell_id not in ("lockmaster", "magelock"):
        return 1
    return 1 + max(0, latest(state)[target_id].fixture.size_modifier)


def finalize_lock_skill(
    state: ResourceState, effect: RuntimeSpellEffect, context: SpellContext
) -> RuntimeSpellEffect:
    if effect.spell_id not in ("lockmaster", "magelock"):
        return effect
    hp = next(p for p in state.pools if p.id == "hp:" + effect.actor_id)
    assert hp.injury is not None
    spells_on = sum(
        3 if e.concentrating else 1 for e in active_spells(state) if e.actor_id == effect.actor_id
    )
    # B239 measures range when rolling. B236/B238 use current shock and spells
    # on; stored casting duration/energy remain the original commitment.
    skill = (
        context.skill
        - 5 * int(context.mana == "low")
        - hp.injury.shock
        - spells_on
        - effect.hp_energy
        - context.distance
        - 5 * int(context.unseen)
        + lock_difficulty(state, effect.spell_id, effect.target_id)
    )
    # B345: a nondefense success roll is unavailable below effective skill 3.
    # Apply the same live condition modifiers that the actual casting roll uses.
    if skill + sum(m.value for m in check_modifiers(state, effect.actor_id, "iq")) < 3:
        raise ValidationError("Effective lock spell skill must be at least 3")
    return effect.model_copy(update={"skill": skill})


def validate_lock_target(state: ResourceState, spell_id: str, target_id: str) -> None:
    if spell_id not in {"lockmaster", "magelock"}:
        return
    value = latest(state).get(target_id)
    if value is None or destroyed(state, value):
        raise ValidationError("Lock magic requires an intact authored lock fixture")
    if spell_id == "magelock" and (value.fixture.kind != "door" or not value.closed):
        raise ValidationError("Magelock requires a closed door")
    if spell_id == "lockmaster" and not (
        value.fixture.mechanical_lock or magelocks(state, target_id)
    ):
        raise ValidationError("Lockmaster requires a mechanical or magical lock")


def lock_difficulty(state: ResourceState, spell_id: str, target_id: str) -> int:
    if spell_id != "lockmaster":
        return 0
    return latest(state)[target_id].fixture.difficulty_modifier


def resist_lockmaster(
    state: ResourceState, target_id: str, casting: CheckTrace, rng: RandomSource
) -> tuple[bool, tuple[CheckTrace, ...]]:
    if casting.outcome is Outcome.CRITICAL_SUCCESS:
        return False, ()
    traces = tuple(
        success_roll(PROFILE, effect.skill, rng=rng) for effect in magelocks(state, target_id)
    )
    # B242 compares margins directly; ties resist. B241 explicitly excludes
    # spell subjects from the Rule of 16. The one casting roll is not rerolled.
    return any(
        resolve_quick_contest(
            PROFILE,
            Contestant("caster", casting.effective_target),
            Contestant("magelock", trace.effective_target),
            first_dice=casting.dice,
            second_dice=trace.dice,
        ).winner
        != "caster"
        for trace in traces
    ), traces


def apply_lock_effect(
    state: ResourceState, effect: RuntimeSpellEffect, command_id: str
) -> tuple[ResourceState, RuntimeSpellEffect]:
    if effect.spell_id not in {"lockmaster", "magelock"}:
        return state, effect
    value = latest(state)[effect.target_id]
    if effect.spell_id == "magelock":
        # The active spell itself is the magical closure. Never duplicate a
        # persistent lock flag that could survive cancellation or expiry.
        return state, effect
    for ward in magelocks(state, effect.target_id):
        state = state.model_copy(
            update={
                "events": state.events
                + (
                    ResourceEvent(
                        id=event_id(command_id + ":counter:" + ward.cast_id, ward.spell_id),
                        at=state.game_time,
                        target_id=ward.target_id,
                        kind=RuntimeSpellEvent(
                            effect=ward.model_copy(update={"phase": "ended"}),
                            result=SpellResult(outcome="cancelled"),
                        ).model_dump_json(),
                    ),
                )
            }
        )
    # Opening the lock is lasting. Opening the door is a separate physical act.
    value = value.model_copy(
        update={"locked": False, "closed": value.closed if value.fixture.kind == "door" else False}
    )
    return save(state, value, command_id), effect.model_copy(update={"phase": "ended"})


def validate_lock_operation(
    state: ResourceState,
    spell_id: str,
    target_id: str,
    kind: str,
    *,
    execution_version: int,
    execute_effects: bool,
) -> None:
    if spell_id in ("lockmaster", "magelock") and (execution_version != 2 or not execute_effects):
        raise ValidationError(
            "Lock spells require the source-derived execution and effect protocol"
        )
    if kind in ("start", "complete"):
        validate_lock_target(state, spell_id, target_id)


def resolve_lock_cast(
    state: ResourceState, effect: RuntimeSpellEffect, check: CheckTrace, rng: RandomSource
) -> tuple[RuntimeSpellEffect, bool, tuple[CheckTrace, ...]]:
    if effect.spell_id == "lockmaster":
        resisted, traces = resist_lockmaster(state, effect.target_id, check, rng)
        return effect, resisted, traces
    if effect.spell_id == "magelock":
        # B242 resistance uses effective skill when the spell was actually cast.
        effect = effect.model_copy(update={"skill": check.effective_target})
    return effect, False, ()


def complete_lock_effect(
    state: ResourceState, effect: RuntimeSpellEffect, command_id: str, kind: str, outcome: str
) -> tuple[ResourceState, RuntimeSpellEffect]:
    if kind == "complete" and outcome == "active":
        return apply_lock_effect(state, effect, command_id)
    return state, effect
