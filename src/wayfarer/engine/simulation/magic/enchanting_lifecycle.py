"""Durable B482 project loss, separate from temporary missed working days."""

from typing import Literal

from pydantic import Field

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.enchanting import (
    EnchantmentMethod,
    EnchantmentProject,
    EnchantmentWork,
)
from wayfarer.engine.simulation.magic.enchanting_calendar import record_rest, schedule
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record

PREFIX = "enchantment-lifecycle:"
LOSS_PREFIX = "enchantment-loss:"


class EnchantmentLifecycle(Record):
    project_id: Id
    method: Literal["quick-and-dirty", "slow-and-sure"]


class EnchantmentLoss(Record):
    project_id: Id
    enchanter_ids: tuple[Id, ...]
    work: EnchantmentWork | None
    energy_completed: int = Field(ge=0)


def needs_clock_checkpoints(resources: ResourceState) -> bool:
    """New Slow-and-Sure projects observe loss at each actual clock consequence."""
    active = {
        project.id
        for project in resources.enchantment_projects
        if project.status in ("active", "interrupted")
    }
    return any(
        event.target_id in active
        and EnchantmentLifecycle.model_validate_json(event.kind).method == "slow-and-sure"
        for event in resources.events
        if event.id.startswith(PREFIX)
    )


def enroll(
    resources: ResourceState, project: EnchantmentProject, method: EnchantmentMethod
) -> ResourceState:
    if any(e.id == PREFIX + project.id for e in resources.events):
        return resources
    record = EnchantmentLifecycle(project_id=project.id, method=method)
    return resources.model_copy(
        update={
            "events": resources.events
            + (
                ResourceEvent(
                    id=PREFIX + project.id,
                    at=resources.game_time,
                    target_id=project.id,
                    kind=record.model_dump_json(),
                ),
            )
        }
    )


def lost_enchanters(state: PlayState, project: EnchantmentProject) -> tuple[str, ...]:
    actors = {a.actor_id for a in state.actors}
    hp = {p.id.removeprefix("hp:"): p.injury for p in state.resources.pools if p.injury is not None}
    return tuple(
        actor
        for actor in project.enchanter_ids
        if actor not in actors or actor not in hp or hp[actor].dead
    )


def require_participants(state: PlayState, enchanter_ids: tuple[str, ...]) -> None:
    pools = {p.id: p for p in state.resources.pools}
    for actor in enchanter_ids:
        hp, fp = pools.get("hp:" + actor), pools.get("fp:" + actor)
        if (
            hp is None
            or hp.injury is None
            or hp.injury.incapacitated
            or hp.injury.stunned
            or fp is None
            or fp.fatigue is None
            or fp.fatigue.unconscious
        ):
            raise ValidationError("Every enchanter must be alive and able to participate")


def _end_work(resources: ResourceState, project: EnchantmentProject) -> ResourceState:
    work = project.active_work
    if work is None:
        return resources
    return record_rest(
        resources,
        work,
        LOSS_PREFIX + project.id,
        first_shift_at=schedule(resources, work).first_shift_at,
    )


def checkpoint(state: PlayState, *, before: PlayState | None = None) -> PlayState:
    """Persist death/removal once at its checkpoint; never undo injury or prior work.

    Temporary absence, unconsciousness, and skipped days are not permanent mage
    loss. Only enrolled projects use this generation, preserving historical logs.
    """
    registrations = (
        EnchantmentLifecycle.model_validate_json(e.kind)
        for e in state.resources.events
        if e.id.startswith(PREFIX)
    )
    enrolled = {entry.project_id for entry in registrations if entry.method == "slow-and-sure"}
    resources = state.resources
    projects = []
    for project in resources.enchantment_projects:
        if project.id not in enrolled or project.status not in ("active", "interrupted"):
            projects.append(project)
            continue
        lost = tuple(
            sorted(
                set(lost_enchanters(state, project))
                | (set(lost_enchanters(before, project)) if before is not None else set())
            )
        )
        if not lost:
            projects.append(project)
            continue
        loss = EnchantmentLoss(
            project_id=project.id,
            enchanter_ids=lost,
            work=project.active_work,
            energy_completed=project.energy_completed,
        )
        resources = _end_work(resources, project)
        resources = resources.model_copy(
            update={
                "events": resources.events
                + (
                    ResourceEvent(
                        id=LOSS_PREFIX + project.id,
                        at=resources.game_time,
                        target_id=project.id,
                        kind=loss.model_dump_json(),
                    ),
                )
            }
        )
        projects.append(project.model_copy(update={"status": "failed", "active_work": None}))
    return (
        state.model_copy(
            update={
                "resources": resources.model_copy(update={"enchantment_projects": tuple(projects)})
            }
        )
        if resources != state.resources
        else state
    )
