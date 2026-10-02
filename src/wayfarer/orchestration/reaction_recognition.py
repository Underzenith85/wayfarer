"""Bind legacy recognition attribution to immutable, replayable source commands."""

import json

from wayfarer import validation
from wayfarer.engine.simulation.social.social import SocialCommand
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.reaction_records import AuthoredSocialReaction, PrepareReaction
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.social_generations import replay_payload
from wayfarer.orchestration.task_records import TaskCommand
from wayfarer.persistence.events import payload_digest


def _payload(text: str, digest: str) -> dict[str, object]:
    if payload_digest({"input": text}) != digest:
        raise ValidationError("Recognition source input does not match its recorded digest")
    value = replay_payload(text)
    return validation.mapping(value) if isinstance(value, dict) else {}


async def bind_recognition_sources(
    play: PlayService, cid: str, command: TaskCommand
) -> TaskCommand:
    if not isinstance(command, PrepareReaction):
        return command
    current = recorded_command.get()
    prior = await play.store.command_input(cid, command.id) if current is None else None
    recorded_text = current.command_input if current is not None else prior.text if prior else None
    recorded_hash = (
        current.payload_hash if current is not None else prior.payload_hash if prior else None
    )
    if recorded_text is not None and recorded_hash is not None:
        payload = _payload(recorded_text, recorded_hash)
        if payload.get("operation") == "task-host" and isinstance(payload.get("command"), dict):
            old = validation.mapping(payload["command"])
            if old.get("kind") == "prepare-reaction":
                stored = PrepareReaction.model_validate_json(json.dumps(old))
                if (
                    command.recognition_sources
                    and command.recognition_sources != stored.recognition_sources
                ):
                    raise ConflictError("Recorded reaction recognition attribution changed")
                # Caller supplied source changes are still rejected by the ordinary payload lock.
                return command.model_copy(
                    update={"recognition_sources": stored.recognition_sources}
                )
    if command.recognition_sources:
        return command
    subject_id = (
        command.source.subject_id if isinstance(command.source, AuthoredSocialReaction) else None
    )
    sources = []
    for row in await play.store.history(cid):
        if row.command_input is None:
            continue
        payload = _payload(row.command_input, row.payload_hash)
        if payload.get("operation") != "gurps-social" or not isinstance(
            payload.get("command"), dict
        ):
            continue
        source = SocialCommand.model_validate_json(json.dumps(payload["command"]))
        if source.kind in ("reaction", "influence", "skill") and (
            subject_id is None or source.subject_id == subject_id
        ):
            sources.append(source)
    return command.model_copy(update={"recognition_sources": tuple(sources)})
