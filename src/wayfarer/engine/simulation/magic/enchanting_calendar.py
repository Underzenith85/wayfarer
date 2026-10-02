"""B481 daily enchanting work remains binding across project boundaries."""

from pydantic import Field

from wayfarer.engine.simulation.magic.enchanting import EnchantmentProject, EnchantmentWork
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

CALENDAR_DAY = 24 * 60 * 60
MAGE_DAY = 8 * 60 * 60
PREFIX = "enchantment-rest:"


class EnchantmentSchedule(Record):
    """Private daily-work facts; the public project schema remains unchanged."""

    first_shift_at: int = Field(ge=0)
    makeup_shifts: int = Field(default=0, ge=0)


def schedule(resources: ResourceState, work: EnchantmentWork) -> EnchantmentSchedule:
    event = next((e for e in resources.events if e.id == "enchantment-schedule:" + work.id), None)
    return (
        EnchantmentSchedule.model_validate_json(event.kind)
        if event
        else EnchantmentSchedule(first_shift_at=work.start)
    )


def enchanting_work_active(
    resources: ResourceState, project: EnchantmentProject, *, at: int | None = None
) -> bool:
    """Whether a mage is concentrating, excluding nightly rest and completed work."""
    now = resources.game_time if at is None else at
    work = project.active_work
    if work is None or project.status != "active" or now >= work.due:
        return False
    event = next((e for e in resources.events if e.id == "enchantment-schedule:" + work.id), None)
    if event is None:
        return True
    elapsed = now - EnchantmentSchedule.model_validate_json(event.kind).first_shift_at
    return elapsed >= 0 and elapsed % CALENDAR_DAY < MAGE_DAY


def require_enchanting_free(
    resources: ResourceState,
    actor_id: str,
    *,
    start: int | None = None,
    through: int | None = None,
) -> None:
    """Admit an idle instant or a half-open activity interval between work shifts.

    Finishing exactly when the next shift starts is permitted. Continuing into
    that shift requires the separate enchanting interruption procedure.
    """
    beginning = resources.game_time if start is None else start
    end = beginning if through is None else through
    if beginning < 0 or end < beginning:
        raise ValidationError("Invalid enchanting activity interval")
    for project in resources.enchantment_projects:
        work = project.active_work
        if work is None or project.status != "active" or actor_id not in project.enchanter_ids:
            continue
        if enchanting_work_active(resources, project, at=beginning):
            raise ConflictError("Actor is committed to enchanting work")
        first = schedule(resources, work).first_shift_at
        next_shift = first + max(0, (beginning - first) // CALENDAR_DAY + 1) * CALENDAR_DAY
        if beginning < next_shift < min(end, work.due):
            raise ConflictError("Activity overlaps the next enchanting work shift")


def require_pending_cast_time(resources: ResourceState, through: int) -> None:
    """A shared clock must leave an admitted night cast able to finish or cancel."""
    for effect in latest(resources).values():
        if effect.phase != "casting":
            continue
        require_enchanting_free(resources, effect.actor_id, through=through)
        if effect.ready_at != through and any(
            effect.actor_id in project.enchanter_ids
            and enchanting_work_active(resources, project, at=through)
            for project in resources.enchantment_projects
        ):
            raise ConflictError("Complete or cancel the pending cast before enchanting work")


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
