"""Recorded equipment-retrieval state and read projections."""

from typing import Literal

from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.models import Record


class RetrievalTask(Record):
    id: str
    actor_id: str
    item_id: str
    landing: GroundPosition
    location_id: str
    due: int
    status: Literal["pending", "completed", "cancelled"] = "pending"


def tasks(resources: ResourceState) -> tuple[RetrievalTask, ...]:
    """Fold the retrieval event ledger into its latest task states."""
    latest: dict[str, RetrievalTask] = {}
    for event in resources.events:
        if event.id.startswith("equipment-retrieval:"):
            task = RetrievalTask.model_validate_json(event.kind)
            latest[task.id] = task
    return tuple(latest.values())
