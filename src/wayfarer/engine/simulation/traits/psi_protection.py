"""Persisted direct-target Psi Static protection consumed by actual power attempts."""

from pydantic import Field

from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.models import Record

PREFIX = "psi-static-state:"


class PsiStaticState(Record):
    owner_id: str
    effect_id: str
    active: bool
    expires_at: int | None = Field(default=None, ge=0)


def protects(resources: ResourceState, target_id: str, *, purchased: bool | None = None) -> bool:
    if purchased is False:
        return False
    owners = {target_id} | {
        item.owner_id for item in resources.items if item.id == target_id and item.ground is None
    }
    latest: dict[str, PsiStaticState] = {}
    for event in resources.events:
        if event.id.startswith(PREFIX):
            state = PsiStaticState.model_validate_json(event.kind)
            latest[state.owner_id] = state
    return any(
        value.active
        and value.effect_id in resources.active_effect_ids
        and (value.expires_at is None or resources.game_time < value.expires_at)
        for owner, value in latest.items()
        if owner in owners
    ) or (purchased is True and target_id not in latest)
