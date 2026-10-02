"""B251 living Will resistance and canonical Move-1 world consequences."""

from dataclasses import replace

from wayfarer.engine.rules.checks import Outcome
from wayfarer.engine.rules.gurps_checks import Contestant, resolve_quick_contest, success_roll
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.magic.apportation_bindings import energy_for_weight, weight
from wayfarer.engine.simulation.magic.apportation_state import ApportationChannel, ApportationRoute
from wayfarer.engine.simulation.magic.lock_state import passage_blocked
from wayfarer.engine.simulation.magic.spell_state import (
    RuntimeSpellEvent,
    SpellResult,
    event_id,
    latest,
)
from wayfarer.engine.simulation.magic.spells import PROFILE, RuntimeSpellCommand
from wayfarer.engine.simulation.resources import Advance
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ConflictError, ValidationError


def resistance(
    runtime: RulesContext, state: PlayState, command: RuntimeSpellCommand, result: SpellResult
) -> tuple[PlayState, SpellResult]:
    entity = next(
        e
        for e in state.world.entities
        if e.id == latest(state.resources)[command.cast_id].target_id
    )
    if (
        command.kind != "complete"
        or result.outcome != "active"
        or entity.kind is not EntityKind.ACTOR
    ):
        return state, result
    original = result.checks[0]
    if original.outcome is Outcome.CRITICAL_SUCCESS:
        return state, result
    target = runtime.approved_build(state, entity.id)
    will = next(int(v.value) for v in target.sheet.values if v.target == "secondary:will")
    check = success_roll(
        PROFILE, will, check_modifiers(state.resources, entity.id, "will"), rng=runtime.rng
    )
    contest = resolve_quick_contest(
        PROFILE,
        Contestant("caster", min(original.effective_target, max(16, check.effective_target))),
        Contestant("subject", check.effective_target),
        first_dice=original.dice,
        second_dice=check.dice,
    )
    resisted = contest.winner != "caster"
    result = result.model_copy(
        update={"checks": result.checks + (check,), "outcome": "resisted" if resisted else "active"}
    )
    effect = latest(state.resources)[command.cast_id]
    if resisted:
        effect = effect.model_copy(update={"phase": "ended", "expires_at": None})
    resources = state.resources.model_copy(
        update={
            "events": tuple(
                e.model_copy(
                    update={
                        "kind": RuntimeSpellEvent(effect=effect, result=result).model_dump_json()
                    }
                )
                if e.id == event_id(command.id, "apportation")
                else e
                for e in state.resources.events
            )
        }
    )
    return state.model_copy(update={"resources": resources}), result


def move(
    runtime: RulesContext,
    state: PlayState,
    channel: ApportationChannel,
    route: ApportationRoute,
    cast_id: str,
    command_id: str,
) -> PlayState:
    effect = latest(state.resources).get(cast_id)
    if effect is None or effect.phase != "active" or effect.reversed or not effect.execute_effects:
        raise ConflictError("Apportation movement requires its active accepted effect")
    if (effect.actor_id, effect.target_id, effect.spell_id) != (
        channel.actor_id,
        channel.target_id,
        "apportation",
    ):
        raise ConflictError("Apportation movement does not identify its accepted effect")
    if (
        effect.expires_at is None
        or state.resources.game_time + route.distance_yards > effect.expires_at
    ):
        raise ConflictError("Apportation expires before this route completes")
    entities = {e.id: e for e in state.world.entities}
    if any(s.actor_id == channel.target_id for s in state.actor_scenes):
        raise ValidationError("Apportation scene transfer requires its scene transition adapter")
    if (
        entities[channel.actor_id].location_id != route.source_id
        or entities[channel.target_id].location_id != route.source_id
    ):
        raise ConflictError("Apportation route no longer starts at caster and subject")
    if not any(
        c.source_id == route.source_id and c.destination_id == route.destination_id
        for c in state.world.connections
    ):
        raise ValidationError("Apportation route requires its current world connection")
    if passage_blocked(state.resources, route.source_id, route.destination_id):
        raise ConflictError("A closed or locked door blocks Apportation movement")
    advanced = runtime.advance(
        state,
        Advance(
            id=command_id + ":clock",
            actor_id=channel.actor_id,
            expected_revision=state.resources.revision,
            to=state.resources.game_time + route.distance_yards,
        ),
    )
    if energy_for_weight(weight(runtime, advanced, channel)) > effect.energy:
        raise ConflictError("Current subject exceeds accepted weight after clock consequences")
    resources = advanced.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"world_ground_location_id": route.destination_id})
                if item.id == channel.target_id
                else item
                for item in advanced.resources.items
            )
        }
    )
    runtime.resources.validate(resources)
    world = replace(
        advanced.world,
        entities=tuple(
            replace(e, location_id=route.destination_id) if e.id == channel.target_id else e
            for e in advanced.world.entities
        ),
    )
    world.validate()
    return advanced.model_copy(update={"world": world, "resources": resources})
