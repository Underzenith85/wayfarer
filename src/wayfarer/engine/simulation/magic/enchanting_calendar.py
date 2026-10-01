"""B481 daily enchanting work remains binding across project boundaries."""

from pydantic import Field

from wayfarer.engine.simulation.magic.enchanting import EnchantmentWork
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.models import Id, Record

CALENDAR_DAY = 24 * 60 * 60
PREFIX = "enchantment-rest:"


class EnchantmentRest(Record):
    enchanter_ids: tuple[Id, ...] = Field(min_length=1)
    next_shift_at: int = Field(ge=0)


def next_shift_at(resources: ResourceState, enchanter_ids: tuple[str, ...]) -> int:
    """Each participating mage must have finished the preceding daily shift."""
    actors = set(enchanter_ids)
    available = resources.game_time
    for event in resources.events:
        if event.id.startswith(PREFIX):
            rest = EnchantmentRest.model_validate_json(event.kind)
            if actors.intersection(rest.enchanter_ids):
                available = max(available, rest.next_shift_at)
    return available


def record_rest(
    resources: ResourceState,
    work: EnchantmentWork,
    command_id: str,
    *,
    first_shift_at: int,
) -> ResourceState:
    """Remember the last started workday, bounded by actual work completion.

    Late settlement does not invent additional shifts. Abandoning before the
    scheduled first shift has begun does not consume a workday.
    """
    ended_at = min(resources.game_time, work.due)
    if ended_at <= first_shift_at:
        return resources
    last_shift = first_shift_at + (ended_at - first_shift_at - 1) // CALENDAR_DAY * CALENDAR_DAY
    rest = EnchantmentRest(
        enchanter_ids=work.enchanter_ids, next_shift_at=last_shift + CALENDAR_DAY
    )
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=PREFIX + command_id,
                    at=resources.game_time,
                    target_id=work.enchanter_ids[0],
                    kind=rest.model_dump_json(),
                ),
            )
        }
    )
