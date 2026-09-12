"""Replayable communicator, sensor, and computer procedures (B471-472)."""

from __future__ import annotations

import hashlib
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter

from wayfarer.engine.rules.checks import (
    NO_RANDOM,
    Modifier,
    ModifierKind,
    RandomSource,
    draw_dice,
    evaluate_success,
)
from wayfarer.engine.rules.types.electronics import ComputerProgram, ElectronicsSuite
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import (
    Command,
    Item,
    Receipt,
    ResourceEvent,
    ResourceState,
)
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

PREFIX = "electronics-use:"
PROFILE = "gurps-basic-set-4e-2004"
VERSION = "1"


class OperatorSkill(Record):
    id: Id
    level: int = Field(ge=0)


class CommunicationLink(Record):
    id: Id
    device_item_id: Id
    recipient_ids: tuple[Id, ...] = Field(min_length=1)
    distance_yards: int | None = Field(default=None, ge=0)
    available: bool = True
    addressed: bool = True
    interceptor_ids: tuple[Id, ...] = ()


class DetectionTarget(Record):
    id: Id
    device_item_id: Id
    target_id: Id
    fact_ids: tuple[Id, ...]
    distance_yards: int = Field(ge=0)
    environment_available: bool = True


class ElectronicsContext(Record):
    skills: tuple[OperatorSkill, ...]
    links: tuple[CommunicationLink, ...] = ()
    targets: tuple[DetectionTarget, ...] = ()
    programs: tuple[ComputerProgram, ...] = ()

    def skill(self, skill_id: str) -> int | None:
        return next((skill.level for skill in self.skills if skill.id == skill_id), None)


class Communicate(Command):
    kind: Literal["communicate"] = "communicate"
    item_id: Id
    link_id: Id
    fact_ids: tuple[Id, ...]
    medium: Literal["code", "voice", "text", "video", "data"]
    duration_seconds: int = Field(default=1, ge=1)


class Detect(Command):
    kind: Literal["detect"] = "detect"
    item_id: Id
    target_context_id: Id
    duration_seconds: int = Field(default=1, ge=1)
    perception_level: int | None = Field(default=None, ge=0)


class RunComputerTask(Command):
    kind: Literal["run_computer_task"] = "run_computer_task"
    item_id: Id
    program_id: Id
    task_skill_id: Id
    active_program_complexities: tuple[int, ...] = ()
    duration_seconds: int = Field(default=1, ge=1)


ElectronicsCommand = Annotated[Communicate | Detect | RunComputerTask, Field(discriminator="kind")]
COMMAND_ADAPTER: TypeAdapter[ElectronicsCommand] = TypeAdapter(ElectronicsCommand)


class ElectronicsOutcome(Record):
    command_id: Id
    kind: Literal["communicate", "detect", "run_computer_task"]
    status: Literal["succeeded", "failed", "unavailable", "supported"]
    revealed_fact_ids: tuple[Id, ...] = ()
    recipient_ids: tuple[Id, ...] = ()
    interceptor_ids: tuple[Id, ...] = ()
    dice: tuple[int, int, int] | None = None
    effective_target: int | None = None
    task_modifier: int | None = None
    effective_technology_level: int | None = None


class ElectronicsEvent(Record):
    digest: str
    outcome: ElectronicsOutcome


def _event_id(command_id: str) -> str:
    return PREFIX + hashlib.sha256(command_id.encode()).hexdigest()


def history(state: ResourceState) -> tuple[ElectronicsEvent, ...]:
    return tuple(
        ElectronicsEvent.model_validate_json(event.kind)
        for event in state.events
        if event.id.startswith(PREFIX)
    )


def _item_and_suite(
    engine: ResourceEngine, state: ResourceState, command: ElectronicsCommand
) -> tuple[Item, ElectronicsSuite]:
    item = next((candidate for candidate in state.items if candidate.id == command.item_id), None)
    if item is None or item.owner_id != command.actor_id or item.container_id is not None:
        raise ValidationError("Electronic device must be accessible to its operator")
    suite = engine.specs[item.definition_id].electronics
    if suite is None:
        raise ValidationError("Equipment has no executable electronics profile")
    if suite.power_capacity_seconds is not None:
        if item.charges is None or item.charges < command.duration_seconds:
            raise ValidationError("Electronic device has insufficient power")
    return item, suite


def _operator_level(context: ElectronicsContext, skill_id: str) -> int:
    level = context.skill(skill_id)
    if level is None:
        raise ValidationError("Required electronic operator procedure is unavailable")
    return level


def _commit(
    engine: ResourceEngine,
    state: ResourceState,
    command: ElectronicsCommand,
    digest: str,
    outcome: ElectronicsOutcome,
    *,
    consume_power: bool,
) -> ResourceState:
    items = state.items
    if consume_power:
        items = tuple(
            item.model_copy(update={"charges": item.charges - command.duration_seconds})
            if item.id == command.item_id and item.charges is not None
            else item
            for item in items
        )
    event = ElectronicsEvent(digest=digest, outcome=outcome)
    updated = state.model_copy(
        update={
            "revision": state.revision + 1,
            "items": items,
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id=_event_id(command.id),
                    at=state.game_time,
                    kind=event.model_dump_json(),
                    target_id=command.actor_id,
                ),
            ),
        }
    )
    engine.validate(updated)
    return updated


def _communicate(
    engine: ResourceEngine,
    state: ResourceState,
    world: World,
    command: Communicate,
    context: ElectronicsContext,
    suite: ElectronicsSuite,
    digest: str,
    rng: RandomSource,
) -> tuple[ResourceState, World, ElectronicsOutcome]:
    communicator = suite.communicator
    if communicator is None or command.medium not in communicator.media:
        raise ValidationError("Communicator does not support the requested medium")
    level = _operator_level(context, communicator.operator_skill_id)
    link = next((link for link in context.links if link.id == command.link_id), None)
    if link is None or link.device_item_id != command.item_id:
        raise ValidationError("Authored communication link is unavailable")
    entities = {entity.id for entity in world.entities}
    facts = {fact.id for fact in world.facts}
    known = {fact for actor, fact in world.knowledge if actor == command.actor_id}
    if not set(link.recipient_ids) <= entities or not set(command.fact_ids) <= facts & known:
        raise ValidationError("Communication cannot disclose unknown actors or facts")
    if communicator.requires_address and not link.addressed:
        raise ValidationError("Communicator requires the recipient address")
    if not link.available:
        outcome = ElectronicsOutcome(command_id=command.id, kind=command.kind, status="unavailable")
        return (
            _commit(engine, state, command, digest, outcome, consume_power=False),
            world,
            outcome,
        )
    modifiers: tuple[Modifier, ...] = ()
    if communicator.range_yards is not None:
        distance = link.distance_yards
        if distance is None or distance > communicator.range_yards * 2:
            raise ValidationError("Communication target is outside maximum extended range")
        excess = max(0, distance - communicator.range_yards)
        penalty = -((excess * 10 + communicator.range_yards - 1) // communicator.range_yards)
        if penalty:
            modifiers = (
                Modifier(
                    penalty,
                    "extended communicator range",
                    "sjg:basic-set-campaigns-4e-2004",
                    "B471",
                    ModifierKind.EQUIPMENT,
                ),
            )
    dice = draw_dice(rng) if modifiers else None
    succeeded = (
        dice is None
        or evaluate_success(
            level,
            modifiers,
            dice,
            rules_package=PROFILE,
            rules_version=VERSION,
            rule_id="electronics:communication",
        ).outcome.succeeded
    )
    recipients = link.recipient_ids if succeeded else ()
    interceptors = () if communicator.secure or not succeeded else link.interceptor_ids
    updated_world = world
    for actor_id in (*recipients, *interceptors):
        for fact_id in command.fact_ids:
            updated_world = updated_world.learn(actor_id, fact_id)
    outcome = ElectronicsOutcome(
        command_id=command.id,
        kind=command.kind,
        status="succeeded" if succeeded else "failed",
        revealed_fact_ids=command.fact_ids if succeeded else (),
        recipient_ids=recipients,
        interceptor_ids=interceptors,
        dice=dice,
        effective_target=level + sum(modifier.value for modifier in modifiers)
        if dice is not None
        else None,
    )
    return (
        _commit(engine, state, command, digest, outcome, consume_power=True),
        updated_world,
        outcome,
    )


def _detect(
    engine: ResourceEngine,
    state: ResourceState,
    world: World,
    command: Detect,
    context: ElectronicsContext,
    suite: ElectronicsSuite,
    digest: str,
    rng: RandomSource,
) -> tuple[ResourceState, World, ElectronicsOutcome]:
    sensor = suite.sensor
    if sensor is None:
        raise ValidationError("Equipment has no executable sensor")
    sensor_level = (
        _operator_level(context, sensor.operator_skill_id)
        if sensor.new_sense
        else command.perception_level
    )
    if sensor_level is None:
        raise ValidationError("Augmenting sensor requires the operator's sense level")
    target = next(
        (target for target in context.targets if target.id == command.target_context_id), None
    )
    facts_by_id = {fact.id: fact for fact in world.facts}
    if (
        target is None
        or target.device_item_id != command.item_id
        or target.target_id not in {entity.id for entity in world.entities}
        or any(
            facts_by_id.get(fact_id) is None or facts_by_id[fact_id].subject_id != target.target_id
            for fact_id in target.fact_ids
        )
    ):
        raise ValidationError("Authored sensor target is unavailable")
    if not target.environment_available:
        outcome = ElectronicsOutcome(command_id=command.id, kind=command.kind, status="unavailable")
        return (
            _commit(engine, state, command, digest, outcome, consume_power=False),
            world,
            outcome,
        )
    penalties = 0
    if sensor.range_yards is not None and target.distance_yards > sensor.range_yards:
        boundary = sensor.range_yards
        while boundary < target.distance_yards:
            boundary *= 2
            penalties -= 2
    modifiers = (
        (
            Modifier(
                penalties,
                "active sensor range doubling",
                "sjg:basic-set-campaigns-4e-2004",
                "B472",
                ModifierKind.EQUIPMENT,
            ),
        )
        if penalties
        else ()
    )
    dice = draw_dice(rng)
    trace = evaluate_success(
        sensor_level,
        modifiers,
        dice,
        rules_package=PROFILE,
        rules_version=VERSION,
        rule_id="electronics:detection",
    )
    updated_world = world
    if trace.outcome.succeeded:
        for fact_id in target.fact_ids:
            updated_world = updated_world.learn(command.actor_id, fact_id)
    outcome = ElectronicsOutcome(
        command_id=command.id,
        kind=command.kind,
        status="succeeded" if trace.outcome.succeeded else "failed",
        revealed_fact_ids=target.fact_ids if trace.outcome.succeeded else (),
        dice=dice,
        effective_target=trace.effective_target,
    )
    return (
        _commit(engine, state, command, digest, outcome, consume_power=True),
        updated_world,
        outcome,
    )


def _run_computer(
    engine: ResourceEngine,
    state: ResourceState,
    world: World,
    command: RunComputerTask,
    context: ElectronicsContext,
    suite: ElectronicsSuite,
    digest: str,
) -> tuple[ResourceState, World, ElectronicsOutcome]:
    computer = suite.computer
    if computer is None:
        raise ValidationError("Equipment has no executable computer")
    _operator_level(context, computer.operator_skill_id)
    if not computer.has_terminal:
        raise ValidationError("Computer task requires an available terminal")
    program = next(
        (program for program in context.programs if program.id == command.program_id), None
    )
    if program is None or program.task_skill_id != command.task_skill_id:
        raise ValidationError("Required task program is unavailable")
    complexities = command.active_program_complexities + (program.complexity,)
    for complexity in set(complexities):
        if complexities.count(complexity) > computer.program_capacity(complexity):
            raise ValidationError("Computer program load exceeds Complexity capacity")
    if program.storage_megabytes > computer.storage_megabytes:
        raise ValidationError("Computer lacks program storage capacity")
    if program.complexity > computer.complexity:
        raise ValidationError("Computer Complexity is too low for the program")
    outcome = ElectronicsOutcome(
        command_id=command.id,
        kind=command.kind,
        status="supported",
        task_modifier=program.bonus if program.mode == "bonus" else None,
        effective_technology_level=program.native_technology_level,
    )
    return _commit(engine, state, command, digest, outcome, consume_power=True), world, outcome


def apply_electronics(
    engine: ResourceEngine,
    state: ResourceState,
    world: World,
    command: ElectronicsCommand,
    context: ElectronicsContext,
    *,
    system: bool = False,
    rng: RandomSource = NO_RANDOM,
) -> tuple[ResourceState, World, ElectronicsOutcome]:
    """Execute one trusted electronics action and retain its exact result."""
    if not system:
        raise ValidationError("Electronic use requires engine authority")
    engine.validate(state)
    world.validate()
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    previous_receipt = next((r for r in state.receipts if r.command_id == command.id), None)
    previous_event = next(
        (event for event in history(state) if event.outcome.command_id == command.id), None
    )
    if previous_receipt is not None:
        if (
            previous_receipt.digest != digest
            or previous_event is None
            or previous_event.digest != digest
        ):
            raise ConflictError("Electronics command ID reused with different payload")
        return state, world, previous_event.outcome
    if command.expected_revision != state.revision:
        raise ConflictError("Electronic resource revision changed")
    _, suite = _item_and_suite(engine, state, command)
    if isinstance(command, Communicate):
        return _communicate(engine, state, world, command, context, suite, digest, rng)
    if isinstance(command, Detect):
        return _detect(engine, state, world, command, context, suite, digest, rng)
    return _run_computer(engine, state, world, command, context, suite, digest)
