"""B410: a rethrow relocates the same delayed grenade, without a new fuse."""

from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError


def armed_cause(
    resources: ResourceState, source_item_id: str, encounter_id: str
) -> BlastRecord | None:
    causes = tuple(
        cause
        for cause in blasts(resources)
        if cause.source_item_id == source_item_id
        and cause.follow_item
        and cause.destroy_source
        and not cause.resolved
    )
    if not causes:
        return None
    if len(causes) != 1:
        raise ValidationError("Live grenade has ambiguous armed blast causes")
    cause = causes[0]
    if cause.encounter_id != encounter_id or cause.due <= resources.game_time:
        raise ValidationError("Live grenade must retain its original encounter and unexpired fuse")
    return cause
