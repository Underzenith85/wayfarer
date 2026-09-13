"""Authoritative creature relationships, training and command permission."""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING, Annotated, Literal

from pydantic import Field

from wayfarer.engine.rules.creatures import command_training_days, training_days
from wayfarer.engine.rules.types.creature import (
    Creature,
    CreatureTraining,
    LearnedCommand,
)
from wayfarer.engine.simulation.resources import Command, Receipt, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError

if TYPE_CHECKING:
    from wayfarer.engine.simulation.resource_engine import ResourceEngine


class SetCreatureRelationship(Command):
    kind: Literal["creature-set-relationship"] = "creature-set-relationship"
    creature_id: str
    owner_id: str | None = None
    handler_id: str | None = None


class StartCreatureTraining(Command):
    kind: Literal["creature-start-training"] = "creature-start-training"
    creature_id: str
    training_id: str
    training_kind: Literal["general", "command", "war-mount"]
    handler_skill_id: str
    competence: int = Field(ge=1, le=100)
    target_level: int | None = Field(default=None, ge=2, le=5)
    command_id: str | None = None


class CompleteCreatureTraining(Command):
    kind: Literal["creature-complete-training"] = "creature-complete-training"
    creature_id: str
    training_id: str


class DirectCreature(Command):
    kind: Literal["creature-direct"] = "creature-direct"
    creature_id: str
    command_id: str


CreatureCommand = Annotated[
    SetCreatureRelationship | StartCreatureTraining | CompleteCreatureTraining | DirectCreature,
    Field(discriminator="kind"),
]


def _training(
    creature: Creature, state: ResourceState, command: StartCreatureTraining
) -> CreatureTraining:
    if creature.handler_id != command.actor_id:
        raise ValidationError("Only the current handler can train this creature")
    if creature.training is not None:
        raise ConflictError("Creature already has active training")
    if not command.handler_skill_id.startswith("skill:animal-handling"):
        raise ValidationError("Creature training requires an Animal Handling specialty")
    if command.training_kind == "general":
        if command.target_level is None or command.command_id is not None:
            raise ValidationError("General training requires only a target level")
        if creature.training_level is not None and command.target_level <= creature.training_level:
            raise ValidationError("Creature already has this general training")
        days = training_days(creature.statistics.iq, command.target_level)
    elif command.training_kind == "command":
        if command.command_id is None or command.target_level is not None:
            raise ValidationError("Command training requires only a command identifier")
        spec = next((entry for entry in creature.commands if entry.id == command.command_id), None)
        if spec is None:
            raise ValidationError("Command is not supported by this creature template")
        if (
            creature.training_level is None
            or creature.training_level < spec.required_training_level
        ):
            raise ValidationError("Creature lacks the general training required for this command")
        if any(entry.id == command.command_id for entry in creature.learned_commands):
            raise ValidationError("Creature already learned this command")
        days = command_training_days(creature.statistics.iq)
    else:
        if command.target_level is not None or command.command_id is not None:
            raise ValidationError("War-mount training has no level or command selector")
        if creature.mount is None or not creature.mount.riding:
            raise ValidationError("War training requires a riding mount")
        if creature.mount.war_trained:
            raise ValidationError("Mount is already war-trained")
        if creature.training_level is None or creature.training_level < 3:
            raise ValidationError("War training requires completed basic mount training")
        days = 365
    return CreatureTraining(
        id=command.training_id,
        kind=command.training_kind,
        handler_id=command.actor_id,
        handler_skill_id=command.handler_skill_id,
        competence=command.competence,
        handling_modifier=-5 if creature.mentality == "wild" else 0,
        started_at=state.game_time,
        due_at=state.game_time + days * 86400,
        required_days=days,
        target_level=command.target_level,
        command_id=command.command_id,
    )


def _complete(
    creature: Creature, state: ResourceState, command: CompleteCreatureTraining
) -> Creature:
    task = creature.training
    if task is None or task.id != command.training_id:
        raise ValidationError("Unknown creature training program")
    if task.handler_id != command.actor_id or creature.handler_id != command.actor_id:
        raise ValidationError("Only the program handler can complete creature training")
    if state.game_time < task.due_at:
        raise ConflictError("Creature training has not reached its shared-clock deadline")
    changes: dict[str, object] = {"training": None}
    if task.kind == "general":
        assert task.target_level is not None
        changes["training_level"] = task.target_level
        learned = {entry.id: entry for entry in creature.learned_commands}
        for spec in creature.commands:
            if (
                spec.included_in_general_training
                and spec.required_training_level <= task.target_level
            ):
                learned[spec.id] = LearnedCommand(
                    id=spec.id,
                    required_training_level=spec.required_training_level,
                    competence=task.competence,
                    learned_from_handler_id=task.handler_id,
                )
        changes["learned_commands"] = tuple(sorted(learned.values(), key=lambda entry: entry.id))
    elif task.kind == "command":
        assert task.command_id is not None
        spec = next(entry for entry in creature.commands if entry.id == task.command_id)
        changes["learned_commands"] = creature.learned_commands + (
            LearnedCommand(
                id=task.command_id,
                required_training_level=spec.required_training_level,
                competence=task.competence,
                learned_from_handler_id=task.handler_id,
            ),
        )
    else:
        assert creature.mount is not None
        changes["mount"] = creature.mount.model_copy(
            update={"war_trained": True, "combat_training_years": 1}
        )
    return creature.model_copy(update=changes)


def apply_creature_command(
    engine: ResourceEngine,
    state: ResourceState,
    command: CreatureCommand,
    *,
    system: bool = False,
) -> ResourceState:
    """Apply one CAS-protected transition; narration never grants a command."""
    engine.validate(state)
    if command.actor_id not in engine.actors:
        raise ValidationError("Creature command actor is not a world actor")
    if (
        isinstance(
            command, (SetCreatureRelationship, StartCreatureTraining, CompleteCreatureTraining)
        )
        and not system
    ):
        raise ValidationError("Creature administration and training require engine authority")
    digest = hashlib.sha256(command.model_dump_json().encode()).hexdigest()
    previous = next((entry for entry in state.receipts if entry.command_id == command.id), None)
    if previous is not None:
        if previous.digest != digest:
            raise ConflictError("Creature command ID reused with a different payload")
        return state
    if command.expected_revision != state.revision:
        raise ConflictError("Resource revision changed")
    creatures = {entry.actor_id: entry for entry in state.creatures}
    creature = creatures.get(command.creature_id)
    if creature is None:
        raise ValidationError("Unknown creature")
    event_kind: str
    if isinstance(command, SetCreatureRelationship):
        if creature.training is not None and command.handler_id != creature.handler_id:
            raise ConflictError("Finish active training before replacing the handler")
        if command.owner_id is not None and command.owner_id not in engine.actors:
            raise ValidationError("Unknown creature owner")
        if command.handler_id is not None and command.handler_id not in engine.actors:
            raise ValidationError("Unknown creature handler")
        creature = creature.model_copy(
            update={"owner_id": command.owner_id, "handler_id": command.handler_id}
        )
        event_kind = "creature-relationship"
    elif isinstance(command, StartCreatureTraining):
        task = _training(creature, state, command)
        creature = creature.model_copy(update={"training": task})
        event_kind = "creature-training-started"
    elif isinstance(command, CompleteCreatureTraining):
        creature = _complete(creature, state, command)
        event_kind = "creature-training-completed"
    else:
        if creature.handler_id != command.actor_id:
            raise ValidationError("Only the current handler can direct this creature")
        learned = next(
            (entry for entry in creature.learned_commands if entry.id == command.command_id), None
        )
        if learned is None:
            raise ValidationError("Creature has not learned that command")
        creature = creature.model_copy(update={"last_command_id": command.command_id})
        event_kind = f"creature-command:{command.command_id}:{learned.competence}"
    creatures[creature.actor_id] = creature
    updated = state.model_copy(
        update={
            "revision": state.revision + 1,
            "creatures": tuple(sorted(creatures.values(), key=lambda entry: entry.actor_id)),
            "receipts": state.receipts + (Receipt(command_id=command.id, digest=digest),),
            "events": state.events
            + (
                ResourceEvent(
                    id="creature:" + hashlib.sha256(command.id.encode()).hexdigest(),
                    at=state.game_time,
                    kind=event_kind,
                    target_id=creature.actor_id,
                ),
            ),
        }
    )
    engine.validate(updated)
    return updated


def replay_creature_commands(
    engine: ResourceEngine,
    seed: ResourceState,
    commands: tuple[CreatureCommand, ...],
    *,
    system: bool = False,
) -> ResourceState:
    state = seed
    for command in commands:
        state = apply_creature_command(engine, state, command, system=system)
    return state
