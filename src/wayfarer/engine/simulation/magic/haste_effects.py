"""B237/B251 live Move and Dodge bonuses from the canonical spell ledger."""

from typing import TYPE_CHECKING

from wayfarer.engine.simulation.magic.spell_state import active_spells, latest
from wayfarer.engine.simulation.resources import ResourceState

if TYPE_CHECKING:
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.rules_context import RulesContext


def bonus(resources: ResourceState, actor_id: str) -> int:
    """Repeated variable spells use the strongest active effect, never their sum."""
    return max(
        (
            e.energy
            for e in active_spells(resources)
            if e.spell_id == "haste"
            and e.target_id == actor_id
            and e.execute_effects
            and not e.reversed
        ),
        default=0,
    )


def checkpoint(runtime: RulesContext, state: PlayState) -> PlayState:
    """Refresh only Haste-enrolled actors' live movement, including expiry."""
    # deferred: actors imports the pure Haste bonus consumer above.
    from wayfarer.engine.simulation.actors import movement

    targets = {e.target_id for e in latest(state.resources).values() if e.spell_id == "haste"}
    if not targets or runtime.rules.combat is None or runtime.rules.combat.gurps_equipment is None:
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
                            if p.actor_id in targets
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
