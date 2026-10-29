"""Capture bandage context independently of rolled minima and FP-rest policy."""

from dataclasses import dataclass

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, FinishRecovery
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import intent_input
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.medical_bandage_inputs import ENVIRONMENT, KEY, intent, validate_keys


@dataclass(frozen=True)
class BandageInputs:
    enabled: bool
    saved: bool
    environment: object = None


def read(
    saved: CommandInput, *, eligible: bool, command_id: str, actor_id: str, begin_only: bool = False
) -> BandageInputs:
    if saved.text is None:
        return BandageInputs(False, True)
    if payload_digest({"input": saved.text}) != saved.payload_hash:
        raise ValidationError("Recorded bandage context does not match its digest")
    validate_keys(saved.text)
    raw = intent_input(saved.text)
    intent(raw)
    payload = validation.mapping(validation.decode(raw))
    if KEY not in payload:
        return BandageInputs(False, True)
    command = validation.mapping(payload.get("command"))
    if (
        not eligible
        or command.get("id") != command_id
        or command.get("actor_id") != actor_id
        or begin_only
        and command.get("kind") != "bandage"
    ):
        raise ValidationError("Bandage capture does not match its producer")
    return BandageInputs(True, True, payload.get(ENVIRONMENT))


async def capture(
    store: Store,
    state: PlayState,
    command: BeginRecovery | FinishRecovery,
    *,
    opted_in: bool,
    eligible_profile: bool,
) -> BandageInputs:
    eligible = eligible_profile and (
        isinstance(command, BeginRecovery)
        and command.kind == "bandage"
        or isinstance(command, FinishRecovery)
    )
    prior = recorded_command.get()
    saved = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await store.command_input(state.campaign_id, command.id)
    )
    if saved is not None:
        return read(saved, eligible=eligible, command_id=command.id, actor_id=command.actor_id)
    if isinstance(command, BeginRecovery):
        return BandageInputs(opted_in and eligible, False)
    task = next(
        (task for task in state.resources.recovery_tasks if task.id == command.task_id), None
    )
    if task is None or task.kind != "bandage":
        return BandageInputs(False, False)
    # Task IDs are the accepted Begin command IDs, not inferred names. A new
    # deployment opt-in can never upgrade an old unmarked producer's finish.
    producer = await store.command_input(state.campaign_id, task.id)
    if producer is None:
        return BandageInputs(False, False)
    inherited = read(
        producer,
        eligible=eligible_profile,
        command_id=task.id,
        actor_id=task.actor_id,
        begin_only=True,
    )
    return BandageInputs(inherited.enabled, False)
