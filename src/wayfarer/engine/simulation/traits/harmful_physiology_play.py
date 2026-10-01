"""Approved-body binding and settlement on the canonical play aggregate."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.harmful_physiology import (
    HarmfulContext,
    reconcile,
    save,
    settle,
)
from wayfarer.engine.simulation.traits.harmful_physiology_state import (
    HarmfulReceipt,
    conditions,
    living,
)


def context(runtime: RulesContext, state: PlayState, actors: tuple[str, ...]) -> HarmfulContext:
    dead = {
        p.id.removeprefix("hp:")
        for p in state.resources.pools
        if p.injury is not None and p.injury.dead
    }
    return HarmfulContext(
        builds={a: runtime.approved_build(state, a) for a in actors if a not in dead},
        definitions=runtime.reviewer.compiler.definitions,
        engine=runtime.resources.for_world(state.world),
        rng=runtime.rng,
    )


def reconcile_actor(
    runtime: RulesContext, state: PlayState, actor_id: str, command_id: str
) -> PlayState:
    entries = tuple(i for i in conditions(state.resources) if i.actor_id == actor_id)
    if not entries:
        return state
    current = context(runtime, state, (actor_id,))
    resources = state.resources
    for item in entries:
        changed = reconcile(resources, item, current, command_id)
        if changed != item:
            resources = save(
                resources,
                HarmfulReceipt(
                    command_id=command_id, conditions=(changed,), game_time=resources.game_time
                ),
                suffix="reconcile:" + actor_id + ":" + item.source + ":" + str(resources.game_time),
            )
    return state.model_copy(update={"resources": resources})


def settle_actor(
    runtime: RulesContext, state: PlayState, actor_id: str, command_id: str
) -> PlayState:
    """Settle before replacing a body, using its still-approved effective build."""
    for item in conditions(state.resources):
        if item.actor_id != actor_id or item.retired:
            continue
        if living(state.resources, item) and (
            item.deadline is None or item.deadline > state.resources.game_time
        ):
            continue
        current = context(runtime, state, (actor_id,))
        resources, changed, injuries = settle(state.resources, item, current, command_id)
        resources = save(
            resources,
            HarmfulReceipt(
                command_id=command_id,
                conditions=(changed,),
                injuries=injuries,
                game_time=resources.game_time,
            ),
            suffix="settle:" + item.interval_id,
        ).model_copy(update={"revision": state.resources.revision})
        state = state.model_copy(update={"resources": resources})
    return state
