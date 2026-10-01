"""Keep combat movement projections consistent with the persisted current body size."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import movement
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.traits.size_forms import effects, synchronize_modes


def checkpoint(runtime: RulesContext, state: PlayState) -> PlayState:
    identifiers = {e.actor_id for e in effects(state.resources)}
    if identifiers:
        resources = synchronize_modes(
            state.resources,
            frozenset(
                p.actor_id for e in state.encounters if e.status == "active" for p in e.participants
            ),
        )
        state = state.model_copy(update={"resources": resources})
    if (
        not identifiers
        or runtime.rules.combat is None
        or runtime.rules.combat.gurps_equipment is None
    ):
        return state
    return state.model_copy(
        update={
            "encounters": tuple(
                e.model_copy(
                    update={
                        "participants": tuple(
                            p.model_copy(
                                update={"movement_allowance": movement(runtime, state, p.actor_id)}
                            )
                            if p.actor_id in identifiers
                            else p
                            for p in e.participants
                        )
                    }
                )
                if e.status == "active"
                else e
                for e in state.encounters
            )
        }
    )
