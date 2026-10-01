"""B184 cinematic intrusion with authored targets and actual world consequences."""

from dataclasses import replace
from typing import Literal

from pydantic import Field

from wayfarer.engine.character.compiler import ValidatedBuild
from wayfarer.engine.rules.checks import RandomSource
from wayfarer.engine.rules.skills.cinematic import computer_technology_modifier
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.skills.cinematic import (
    CinematicSkillCommand,
    CinematicSkillOutcome,
    apply_cinematic_skill,
)
from wayfarer.engine.world import EntityKind, Fact, World
from wayfarer.errors import AuthorizationError, ValidationError
from wayfarer.models import Id, Record


class ComputerSystem(Record):
    """Trusted GM-authored network reachability and information, never roll bonuses."""

    id: Id
    technology_level: int = Field(ge=0, le=12)
    reachable_actor_ids: tuple[Id, ...] = Field(min_length=1)
    information_fact_ids: tuple[Id, ...] = ()


class ComputerHackingCommand(CinematicSkillCommand):
    skill_id: Literal["skill:computer-hacking"] = "skill:computer-hacking"
    system_id: Id
    operation: Literal["access", "find", "change"] = "access"
    information_id: Id | None = None
    replacement_value: str | None = Field(default=None, max_length=2000)


def has_computer_access(world: World, actor_id: str, system_id: str) -> bool:
    return any(
        f.subject_id == actor_id and f.predicate == "computer-access" and f.value == system_id
        for f in world.facts
    )


def _validate(world: World, command: ComputerHackingCommand, system: ComputerSystem) -> None:
    if any(f.id == "intrusion-evidence:" + command.id for f in world.facts):
        raise ValidationError("Computer intrusion evidence ID already exists")
    target = next((e for e in world.entities if e.id == system.id), None)
    if command.system_id != system.id or target is None or target.kind is not EntityKind.OBJECT:
        raise ValidationError("Computer Hacking requires the authored computer system")
    if command.actor_id not in system.reachable_actor_ids:
        raise ValidationError("Computer system is not reachable by this actor")
    if command.operation == "access":
        if command.information_id is not None or command.replacement_value is not None:
            raise ValidationError("Access attempts cannot change or reveal information")
        return
    if not has_computer_access(world, command.actor_id, system.id):
        raise ValidationError("Finding or changing information requires prior system access")
    fact = next((f for f in world.facts if f.id == command.information_id), None)
    if fact is None or fact.id not in system.information_fact_ids or fact.subject_id != system.id:
        raise ValidationError("Information is outside the authored computer system")
    if (command.operation == "change") != (command.replacement_value is not None):
        raise ValidationError("Only a change operation supplies replacement information")


def _effect(world: World, command: ComputerHackingCommand, outcome: CinematicSkillOutcome) -> World:
    if outcome.outcome == "critical-failure":
        evidence = Fact(
            "intrusion-evidence:" + command.id,
            command.system_id,
            "intrusion-attempt",
            command.actor_id,
        )
        return replace(world, facts=world.facts + (evidence,))
    if outcome.outcome not in {"success", "critical-success"}:
        return world
    if command.operation == "access":
        if has_computer_access(world, command.actor_id, command.system_id):
            return world
        grant = Fact(
            "computer-access:" + command.system_id + ":" + command.actor_id,
            command.actor_id,
            "computer-access",
            command.system_id,
        )
        return replace(world, facts=world.facts + (grant,)).learn(command.actor_id, grant.id)
    assert command.information_id is not None
    if command.operation == "change":
        assert command.replacement_value is not None
        world = replace(
            world,
            facts=tuple(
                replace(f, value=command.replacement_value) if f.id == command.information_id else f
                for f in world.facts
            ),
        )
    return world.learn(command.actor_id, command.information_id)


def apply_computer_hacking(
    state: ResourceState,
    world: World,
    build: ValidatedBuild,
    command: ComputerHackingCommand,
    system: ComputerSystem,
    *,
    authorized_actor_id: str,
    rng: RandomSource,
) -> tuple[ResourceState, World, CinematicSkillOutcome]:
    """Resolve one actor-private cinematic intrusion under the canonical receipt/CAS gate."""
    if command.actor_id != authorized_actor_id:
        raise AuthorizationError("Computer Hacking actor lacks authority")
    replay = any(r.command_id == command.id for r in state.receipts)
    if not replay:
        _validate(world, command, system)
    purchase = next(
        (p for p in build.purchases if p.definition_id == "skill:computer-hacking"), None
    )
    if purchase is None or purchase.technology_level is None:
        raise ValidationError("Computer Hacking requires a learned technology-level specialty")
    modifiers = (
        ()
        if replay
        else computer_technology_modifier(purchase.technology_level, system.technology_level)
    )
    updated, outcome = apply_cinematic_skill(
        state,
        world,
        build,
        command,
        authorized_actor_id=authorized_actor_id,
        rng=rng,
        skill_modifiers=modifiers,
    )
    changed = world if replay else _effect(world, command, outcome)
    changed.validate()
    return updated, changed, outcome
