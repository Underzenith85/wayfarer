"""When a combatant may leave an encounter, and how much time a fight has settled.

Both are rules about the encounter, not about the transaction that records one.
Whether an actor can break away depends on who can reach or see them; how much
shared time a fight consumed depends on the initiative cycles it completed.
"""

from __future__ import annotations

from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.combat.engine import CombatEngine
from wayfarer.engine.simulation.combat.spatial import (
    BasicSpatialContext,
    ReachSpatialFact,
    RetreatSpatialFact,
    VisibilitySpatialFact,
)
from wayfarer.engine.simulation.combat.tactical import sight
from wayfarer.engine.simulation.hex_geometry import Hex, HexBattlefield, neighbor
from wayfarer.errors import ConflictError, ValidationError


def require_resolved_boundary(encounter: Encounter, actor_id: str) -> Combatant:
    """A withdrawal needs a settled encounter and an unrestrained actor who just moved."""
    if (
        encounter.status != "active"
        or encounter.pending_defense is not None
        or encounter.pending_unarmed is not None
        or encounter.wait_interrupt is not None
        or encounter.blocked_reason is not None
    ):
        raise ConflictError("Withdrawal requires a resolved combat boundary")
    actor = next((p for p in encounter.participants if p.actor_id == actor_id), None)
    if actor is None:
        raise ValidationError("Actor is not a combat participant")
    if actor.last_maneuver != "move":
        raise ValidationError("Withdraw after resolving a Move maneuver")
    if (
        actor.grappled
        or actor.pinned
        or actor.entangled is not None
        or any(actor_id in (grip.holder_id, grip.target_id) for grip in encounter.grips)
    ):
        raise ValidationError("Escape restraint before withdrawing")
    return actor


def require_basic_escape(
    spatial: BasicSpatialContext, actor_id: str, others: tuple[Combatant, ...]
) -> None:
    """Every other combatant must be separated, unable to see, and leave a way out."""
    for other in others:
        reach = spatial.active("reach", other.actor_id, actor_id)
        visible = spatial.active("visibility", other.actor_id, actor_id)
        retreat = spatial.active("retreat", actor_id, other.actor_id)
        if (
            not isinstance(reach, ReachSpatialFact)
            or reach.relation != "separated"
            or not isinstance(visible, VisibilitySpatialFact)
            or visible.visible
            or not isinstance(retreat, RetreatSpatialFact)
            or not retreat.feasible
        ):
            raise ValidationError("Basic withdrawal requires safe authoritative facts")


def require_hex_escape(
    encounter: Encounter,
    actor: Combatant,
    others: tuple[Combatant, ...],
    *,
    board: HexBattlefield,
) -> None:
    """Leaving a mapped field needs a boundary hex and nobody able to pursue."""
    cells = {cell.position for cell in board.cells}
    position = encounter.placement(actor.actor_id).position
    assert isinstance(position, Hex)
    if not any(neighbor(position, direction) not in cells for direction in range(6)):
        raise ValidationError("Hex withdrawal requires a battlefield boundary")
    if any(
        CombatEngine.distance(encounter.placement(other.actor_id).position, position) <= other.reach
        or sight(encounter, other, actor, board=board)
        for other in others
    ):
        raise ValidationError("A visible or reachable combatant can pursue")


def elapsed_seconds(prior: Encounter | None, encounter: Encounter) -> int:
    """Return newly settled shared seconds without flattening actor-relative turns.

    A completed initiative cycle settles one shared second.  If combat ends because
    a turn completed partway through a cycle, that final one-second turn must also
    settle; otherwise a decisive first action would consume no world time.  A GM
    ending combat without another turn remains a zero-time lifecycle operation.
    """
    if prior is None:
        return 0
    ticks = max(0, encounter.round - prior.round)
    completed_during_partial_cycle = (
        prior.status == "active"
        and encounter.status == "completed"
        and encounter.round == prior.round
        and encounter.turn_index != prior.turn_index
    )
    return ticks + int(completed_during_partial_cycle)
