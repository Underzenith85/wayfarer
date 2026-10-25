"""Deterministic start-of-opportunity facts needed by no-dice attack admission."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.backfires import refund_due


def project_refund(state: PlayState, actor_id: str, *, starting: bool) -> PlayState:
    if not starting:
        return state
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    assert hp.injury is not None
    # Continuing health-check branches preserve attack attributes and shock;
    # unsuccessful branches already commit an unavailable/Do Nothing result.
    # This refund is the deterministic exception: FP can restore usable ST.
    return state.model_copy(
        update={"resources": refund_due(state.resources, actor_id, turn=hp.injury.turn + 1)}
    )
