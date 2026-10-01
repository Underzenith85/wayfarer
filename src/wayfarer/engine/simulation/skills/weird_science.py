"""B228 Weird Science aid consumed by actual invention and analysis checks."""

import hashlib
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.simulation.projects.inventions import InventionBlueprint, WeirdScienceAid
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.engine.simulation.skills.cinematic import (
    CinematicSkillCommand,
    CinematicSkillOutcome,
    apply_cinematic_skill,
)
from wayfarer.engine.world import World
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.models import Id, Record


class WeirdScienceCommand(CinematicSkillCommand):
    skill_id: Literal["skill:weird-science"] = "skill:weird-science"
    project_id: Id
    purpose: Literal["invention", "investigation"] = "invention"


def apply_weird_science(
    state: ResourceState,
    world: World,
    build: ValidatedBuild,
    command: WeirdScienceCommand,
    blueprint: InventionBlueprint,
    *,
    authorized_actor_id: str,
    rng: RandomSource,
) -> tuple[ResourceState, CinematicSkillOutcome]:
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Weird Science actor lacks authority")
    if any(r.command_id == command.id for r in state.receipts):
        return apply_cinematic_skill(
            state, world, build, command, authorized_actor_id=authorized_actor_id, rng=rng
        )
    project = next((p for p in state.inventions if p.id == command.project_id), None)
    if (
        project is None
        or project.owner_id != command.actor_id
        or project.blueprint_id != blueprint.id
    ):
        raise ValidationError("Weird Science requires the actor's authored invention project")
    if project.status != "active" or project.phase not in {
        "concept-design",
        "prototype",
        "testing",
    }:
        raise ValidationError("Weird Science requires an active invention or investigation attempt")
    expected_activity = "invention" if command.purpose == "invention" else "analysis"
    if blueprint.activity != expected_activity:
        raise ValidationError("Weird Science purpose does not match the authored project")
    work_id = project.active_work.id if project.active_work else None
    aid = project.weird_science
    if aid is not None and aid.phase == project.phase and aid.work_id in (None, work_id):
        raise ConflictError("This invention attempt already has a Weird Science roll")
    updated, outcome = apply_cinematic_skill(
        state, world, build, command, authorized_actor_id=authorized_actor_id, rng=rng
    )
    successful = outcome.outcome in {"success", "critical-success"}
    bonus: Literal[0, 1, 2, 5] = (
        (2 if command.purpose == "investigation" else 5 if project.method == "ordinary" else 1)
        if successful
        else 0
    )
    aid = WeirdScienceAid(
        command_id=command.id,
        phase=project.phase,
        work_id=work_id,
        bonus=bonus,
        insight_pending=outcome.outcome == "critical-success",
        adjudication_required=outcome.outcome == "critical-failure",
    )
    projected = project.model_copy(update={"weird_science": aid})
    return updated.model_copy(
        update={
            "inventions": tuple(projected if p.id == project.id else p for p in updated.inventions)
        }
    ), outcome


class WeirdScienceAdjudication(Record):
    id: Id
    expected_revision: int = Field(ge=0)
    project_id: Id
    resolution: str = Field(min_length=1, max_length=2000)
    consequence_event_ids: tuple[Id, ...] = ()


def acknowledge_weird_science_failure(
    state: ResourceState, command: WeirdScienceAdjudication, *, authorized_gm: bool
) -> ResourceState:
    """Record the GM's explicit resolution of the source's unquantified failure."""
    if not authorized_gm:
        raise AuthorizationError("Only the GM can acknowledge Weird Science failure consequences")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    if receipt is not None:
        if receipt.digest != digest:
            raise ConflictError("Weird Science adjudication ID was reused with different intent")
        return state
    if command.expected_revision != state.revision:
        raise ConflictError("Weird Science adjudication revision conflict")
    project = next((p for p in state.inventions if p.id == command.project_id), None)
    if (
        project is None
        or project.weird_science is None
        or not project.weird_science.adjudication_required
    ):
        raise ValidationError("Project has no pending Weird Science failure")
    if not set(command.consequence_event_ids) <= {e.id for e in state.events}:
        raise ValidationError("Weird Science resolution references an unavailable consequence")
    aid = project.weird_science.model_copy(update={"adjudication_required": False})
    return state.model_copy(
        update={
            "revision": state.revision + 1,
            "inventions": tuple(
                p.model_copy(update={"weird_science": aid}) if p.id == project.id else p
                for p in state.inventions
            ),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id="weird-science-adjudication:" + command.id,
                    at=state.game_time,
                    kind=command.model_dump_json(),
                    target_id=project.owner_id,
                ),
            ),
        }
    )
