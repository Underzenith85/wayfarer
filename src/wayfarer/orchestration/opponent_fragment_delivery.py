"""Private provenance of a canonical B410 relocation of one armed grenade."""

import hashlib
import json
from dataclasses import dataclass

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import COMBAT_ADAPTER, ChooseDefense
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat.generations import features
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack
from wayfarer.orchestration.task_combat_generations import features as task_features
from wayfarer.orchestration.task_combat_generations import producer as task_producer
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import CommandInput, CommandRecord, payload_digest


@dataclass(frozen=True)
class FragmentDelivery:
    origin_attack_id: str
    attack_id: str
    producer_command_id: str


def _payload(row: CommandRecord) -> dict[str, object]:
    if (
        row.command_input is None
        or payload_digest({"input": row.command_input}) != row.payload_hash
    ):
        raise ValidationError("Grenade producer input differs from its accepted digest")
    return validation.mapping(replay_payload(row.command_input))


def _canonical_producer(row: CommandRecord, encounter_id: str) -> bool:
    payload = _payload(row)
    if payload.get("operation") == "combat":
        command = COMBAT_ADAPTER.validate_json(json.dumps(payload.get("command")))
        return isinstance(command, ChooseDefense) and command.encounter_id == encounter_id
    task_command = payload.get("command")
    return (
        payload.get("operation") == "task-host"
        and isinstance(task_command, dict)
        and task_command.get("kind")
        in ("choose-opponent-attack", "prepare-owner-damage", "choose-owner-damage")
    )


def latest_delivery(history: list[CommandRecord], blast_id: str) -> FragmentDelivery | None:
    latest = None
    birth_proved = False
    root, separator, packet = blast_id.rpartition(":blast:")
    for before_row, row in zip(history, history[1:], strict=False):
        before = PlayState.model_validate_json(before_row.state_after["play_json"])
        after = PlayState.model_validate_json(row.state_after["play_json"])
        prior = next((b for b in blasts(before.resources) if b.id == blast_id), None)
        if prior is None:
            born = next((b for b in blasts(after.resources) if b.id == blast_id), None)
            encounter = next(
                (e for e in before.encounters if born and e.id == born.encounter_id), None
            )
            pending = encounter.pending_defense if encounter is not None else None
            scheduled_id = (
                "weapon-blast:" + hashlib.sha256((blast_id + ":schedule").encode()).hexdigest()
            )
            old_ids = {event.id for event in before.resources.events}
            scheduled = next(
                (e for e in after.resources.events if e.id == scheduled_id and e.id not in old_ids),
                None,
            )
            birth_proved = bool(
                born is not None
                and pending is not None
                and scheduled is not None
                and pending.id == root
                and pending.weapon_id == born.source_item_id
                and scheduled.target_id == born.source_item_id
                and born.follow_item
                and born.destroy_source
                and not born.resolved
                and BlastRecord.model_validate_json(scheduled.kind) == born
                and _canonical_producer(row, born.encounter_id)
            )
            continue
        encounter = next((e for e in before.encounters if e.id == prior.encounter_id), None)
        pending = encounter.pending_defense if encounter is not None else None
        if pending is None or pending.weapon_id != prior.source_item_id:
            continue
        event_id = (
            "weapon-blast:" + hashlib.sha256((pending.id + ":grenade-rethrow").encode()).hexdigest()
        )
        old_ids = {event.id for event in before.resources.events}
        event = next(
            (e for e in after.resources.events if e.id == event_id and e.id not in old_ids), None
        )
        if event is None:
            continue
        record = CommandInput(payload_hash=row.payload_hash, text=row.command_input)
        payload = _payload(row)
        command: object
        if payload.get("operation") == "task-host":
            if "grenade-fuse" not in task_features(record):
                raise ValidationError(
                    "Grenade delivery lacks its trusted canonical task generation"
                )
            task = task_producer(record)
            if isinstance(task, BeginOpponentAttack):
                raise ValidationError("Grenade task delivery requires its actual defense producer")
            if task.id != row.command_id or payload.get("principal_id") != row.actor_id:
                raise ValidationError(
                    "Grenade task delivery differs from its accepted receipt identity"
                )
            command = task.response
            if command is None or command.actor_id != pending.defender_id:
                raise ValidationError("Grenade task delivery differs from its pending target")
        else:
            if payload.get("operation") != "combat" or "grenade-fuse" not in features(record):
                raise ValidationError("Grenade delivery lacks its trusted canonical generation")
            command = COMBAT_ADAPTER.validate_json(json.dumps(payload.get("command")))
        if not isinstance(command, ChooseDefense) or command.encounter_id != prior.encounter_id:
            raise ValidationError("Grenade delivery lacks its accepted defense producer")
        relocated = BlastRecord.model_validate_json(event.kind)
        current = next((b for b in blasts(after.resources) if b.id == blast_id), None)
        if (
            event.target_id != prior.source_item_id
            or current != relocated
            or (
                relocated.id,
                relocated.source_item_id,
                relocated.encounter_id,
                relocated.payload,
                relocated.due,
                relocated.fuse_dice,
                relocated.follow_item,
                relocated.destroy_source,
                relocated.deferred_ticks,
                relocated.evidence,
            )
            != (
                prior.id,
                prior.source_item_id,
                prior.encounter_id,
                prior.payload,
                prior.due,
                prior.fuse_dice,
                prior.follow_item,
                prior.destroy_source,
                prior.deferred_ticks,
                prior.evidence,
            )
            or prior.resolved
            or relocated.resolved
        ):
            raise ValidationError("Grenade delivery changed its original armed cause")
        if not birth_proved or not separator or not packet.isdecimal():
            raise ValidationError("Grenade delivery has no original canonical birth receipt")
        latest = FragmentDelivery(root, pending.id, row.command_id)
    return latest
