"""B248 alert intervals; B237 overlapping alertness is one non-stacking state."""

from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.models import Record

PREFIX = "awaken-alert:"


class Alert(Record):
    id: str
    actor_id: str
    ht: int
    due: int
    paid: bool = False


def alerts(state: ResourceState) -> tuple[Alert, ...]:
    found: dict[str, Alert] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            alert = Alert.model_validate_json(event.kind)
            found[alert.id] = alert
    return tuple(found.values())


def alert_until(state: ResourceState, actor_id: str) -> int:
    """A recast extends coverage, never adds alertness or erases incurred FP costs."""
    return max(
        (
            a.due
            for a in alerts(state)
            if a.actor_id == actor_id and not a.paid and a.due > state.game_time
        ),
        default=0,
    )
