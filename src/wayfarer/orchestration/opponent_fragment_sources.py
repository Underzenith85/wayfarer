"""Verify a blast's launch against its accepted command, never current custody."""

import json

from pydantic import ValidationError as PydanticValidationError

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.commands import (
    COMBAT_ADAPTER,
    ChooseDefense,
    ResumeInterruptedTurn,
    TakeCombatTurn,
    TypedCombatCommand,
)
from wayfarer.engine.simulation.combat.encounter import PendingDefense
from wayfarer.engine.simulation.combat.explosions import BlastRecord, blasts
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.opponent_fragment_records import (
    PrepareOpponentFragment,
    RecordedFragmentLaunch,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import CommandInput, CommandRecord, payload_digest


def _payload(record: CommandInput) -> dict[str, object]:
    if record.text is None or payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Fragment launch input is missing or differs from its digest")
    value = replay_payload(record.text)
    return validation.mapping(value) if isinstance(value, dict) else {}


def _combat_command(payload: dict[str, object]) -> TypedCombatCommand | None:
    command = payload.get("command")
    if payload.get("operation") != "combat" or not isinstance(command, dict):
        return None
    try:
        return COMBAT_ADAPTER.validate_json(json.dumps(command))
    except PydanticValidationError:
        return None


async def bind_fragment_source(
    play: PlayService, cid: str, command: PrepareOpponentFragment
) -> PrepareOpponentFragment:
    replayed = recorded_command.get()
    prior = (
        CommandInput(payload_hash=replayed.payload_hash, text=replayed.command_input)
        if replayed is not None
        else await play.store.command_input(cid, command.id)
    )
    if prior is not None:
        value = _payload(prior)
        original = value.get("command")
        if (
            value.get("operation") != "task-host"
            or not isinstance(original, dict)
            or original.get("kind") != command.kind
        ):
            raise ConflictError("Fragment preparation identity belongs to another operation")
        captured = PrepareOpponentFragment.model_validate_json(json.dumps(original))
        if command.launch is not None and command.launch != captured.launch:
            raise ConflictError("Recorded fragment launch changed")
        return command.model_copy(
            update={"launch": captured.launch, "incendiary_objects": captured.incendiary_objects}
        )
    source_input = await play.store.command_input(cid, command.launch_command_id)
    if source_input is None:
        raise ValidationError("Fragment Luck requires its recorded original launch command")
    value = _payload(source_input)
    accepted = _combat_command(value)
    if not isinstance(accepted, TakeCombatTurn):
        raise ValidationError("Fragment Luck requires an actual canonical weapon launch")
    if accepted.id != command.launch_command_id:
        raise ValidationError("Recorded launch input does not match its command identity")
    launch = await _accepted_producer(
        play, cid, accepted, source_input.payload_hash, command.resolution.blast_id
    )
    if command.launch is not None and command.launch != launch:
        raise ConflictError("Supplied fragment source differs from its recorded launch")
    resolution = command.resolution.model_copy(
        update={
            "object_cover": dict(sorted(command.resolution.object_cover.items())),
            "object_sizes": dict(sorted(command.resolution.object_sizes.items())),
        }
    )
    return command.model_copy(
        update={"launch": launch, "resolution": resolution, "incendiary_objects": True}
    )


def validate_fragment_launch(
    state: PlayState,
    blast: BlastRecord,
    owner_ids: tuple[str, ...],
    launch: RecordedFragmentLaunch | None,
) -> RecordedFragmentLaunch:
    if launch is None or launch.campaign_id != state.campaign_id:
        raise ValidationError("Fragment continuation requires its recorded campaign launch")
    command = launch.command
    root, separator, packet = blast.id.rpartition(":blast:")
    if (
        not separator
        or not packet.isdecimal()
        or root != launch.attack_id
        or command.encounter_id != blast.encounter_id
        or launch.source_item_id != blast.source_item_id
        or blast.source_item_id not in (command.item_id, command.second_item_id)
        or command.maneuver not in ("attack", "all_out_attack", "move_and_attack")
        or command.actor_id in owner_ids
    ):
        raise ValidationError("Fragment blast does not match that opponent's recorded launch")
    return launch


def _pending(state: PlayState, encounter_id: str) -> PendingDefense | None:
    encounter = next((e for e in state.encounters if e.id == encounter_id), None)
    return encounter.pending_defense if encounter is not None else None


def _source_pending(pending: PendingDefense | None, source: TakeCombatTurn) -> bool:
    return (
        pending is not None
        and pending.attacker_id == source.actor_id
        and pending.weapon_id in (source.item_id, source.second_item_id)
    )


def _accepted_resume(
    state: PlayState, row: CommandRecord, source: TakeCombatTurn, command_ids: set[str]
) -> bool:
    encounter = next((e for e in state.encounters if e.id == source.encounter_id), None)
    interrupt = encounter.wait_interrupt if encounter is not None else None
    if interrupt is None or row.command_input is None or not row.command_input.startswith("{"):
        return False
    paused = json.loads(interrupt.command_json)
    if (
        not isinstance(paused, dict)
        or paused.get("id") not in command_ids
        or paused.get("actor_id") != source.actor_id
    ):
        return False
    payload = _payload(CommandInput(payload_hash=row.payload_hash, text=row.command_input))
    command = _combat_command(payload)
    return (
        isinstance(command, ResumeInterruptedTurn)
        and not command.cancel
        and command.actor_id == source.actor_id
        and command.encounter_id == source.encounter_id
    )


async def _accepted_producer(
    play: PlayService, cid: str, source: TakeCombatTurn, source_hash: str, blast_id: str
) -> RecordedFragmentLaunch:
    history = await play.store.history(cid)
    start = next((index for index, row in enumerate(history) if row.command_id == source.id), None)
    if start is None:
        raise ValidationError("Fragment launch has no accepted campaign history")
    state = PlayState.model_validate_json(history[start].state_after["play_json"])
    if any(blast.id == blast_id for blast in blasts(state.resources)):
        raise ValidationError("The selected blast predates its claimed launch")
    encounter = next((e for e in state.encounters if e.id == source.encounter_id), None)
    if encounter is None:
        raise ValidationError("Fragment launch has no admitted encounter")
    previous_zones: set[str] = set()
    if start:
        earlier = PlayState.model_validate_json(history[start - 1].state_after["play_json"])
        previous_zones = {zone.id for e in earlier.encounters for zone in e.suppression_zones}
    zones = {
        zone.id
        for zone in encounter.suppression_zones
        if zone.id not in previous_zones
        and zone.attacker_id == source.actor_id
        and zone.weapon_id == source.item_id
    }
    initial = _pending(state, source.encounter_id)
    known = {initial.id} if _source_pending(initial, source) and initial is not None else set()
    command_ids = {source.id}
    for row in history[start + 1 :]:
        pending = _pending(state, source.encounter_id)
        if (
            _source_pending(pending, source)
            and pending is not None
            and pending.suppression_zone_id in zones
        ):
            known.add(pending.id)
        after = PlayState.model_validate_json(row.state_after["play_json"])
        produced = next((blast for blast in blasts(after.resources) if blast.id == blast_id), None)
        if produced is not None:
            if (
                pending is None
                or pending.id not in known
                or not _source_pending(pending, source)
                or produced.source_item_id != pending.weapon_id
                or not produced.id.startswith(pending.id + ":blast:")
            ):
                raise ValidationError("Blast producer does not continue that accepted launch")
            producer = _payload(CommandInput(payload_hash=row.payload_hash, text=row.command_input))
            producer_command = producer.get("command")
            if not isinstance(producer_command, dict) or not (
                isinstance(_combat_command(producer), ChooseDefense)
                or producer.get("operation") == "task-host"
                and producer_command.get("kind")
                in ("choose-opponent-attack", "prepare-owner-damage", "choose-owner-damage")
            ):
                raise ValidationError(
                    "Blast was not produced by an admitted canonical attack continuation"
                )
            return RecordedFragmentLaunch(
                campaign_id=cid,
                command=source,
                payload_hash=source_hash,
                attack_id=pending.id,
                source_item_id=pending.weapon_id,
                producer_command_id=row.command_id,
                producer_payload_hash=row.payload_hash,
            )
        following = _pending(after, source.encounter_id)
        if _accepted_resume(state, row, source, command_ids):
            command_ids.add(row.command_id)
            if _source_pending(following, source) and following is not None:
                known.add(following.id)
            prior_zone_ids = {zone.id for e in state.encounters for zone in e.suppression_zones}
            zones.update(
                zone.id
                for e in after.encounters
                if e.id == source.encounter_id
                for zone in e.suppression_zones
                if zone.id not in prior_zone_ids
                and zone.attacker_id == source.actor_id
                and zone.weapon_id == source.item_id
            )
        if (
            pending is not None
            and pending.id in known
            and _source_pending(following, source)
            and following is not None
        ):
            known.add(following.id)
        state = after
    raise ValidationError("Fragment blast has no reconstructible accepted launch producer")
