"""Independent fold and deterministic re-execution checks for retained commands."""

from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.randomness import RNG_ALGORITHM
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.events import EngineEvent, document
from wayfarer.errors import ValidationError
from wayfarer.persistence.events import CommandRecord, StoredEvent, fold, payload_digest
from wayfarer.persistence.upcasters import EVENT_UPCASTERS


@dataclass(frozen=True)
class ReplayCheck:
    command_id: str
    folded: bool
    reexecuted: bool
    reason: str | None = None


ExecuteRecorded = Callable[[Campaign, CommandRecord], Awaitable[tuple[Campaign, list[EngineEvent]]]]


def require_configuration(state: Campaign, expected: str) -> None:
    if (
        "play_json" not in state
        or PlayState.model_validate_json(state["play_json"]).configuration_digest != expected
    ):
        raise ValidationError("Replay configuration digest differs from pinned rules")


def command_text(record: CommandRecord) -> str:
    # Some pre-418 receipts retained their complete input in the transcript. Only
    # recover it when its original digest proves that these are exactly the bytes.
    if record.command_input is None:
        raise ValidationError("Legacy command has no recoverable input")
    value = record.command_input
    if payload_digest({"input": value}) != record.payload_hash:
        raise ValidationError("Recorded command input does not match its digest")
    return value


def unavailable_reason(record: CommandRecord) -> str | None:
    if record.entropy_seed is None:
        return "legacy command has no entropy seed"
    if record.rng_algorithm != RNG_ALGORITHM:
        return f"unsupported RNG algorithm {record.rng_algorithm!r}"
    if record.recorded_at_us is None:
        return "legacy command has no recorded instant"
    if record.command_input is not None:
        command_text(record)
        return None
    try:
        command_text(record)
    except ValidationError:
        return "legacy command has no recoverable input"
    return None


def fold_command(
    before: Campaign,
    record: CommandRecord,
    events: Sequence[StoredEvent],
    *,
    configuration_digest: str,
) -> Campaign:
    require_configuration(before, configuration_digest)
    if before["id"] != record.campaign_id or before["revision"] != record.expected_revision:
        raise ValidationError("Command log revision or campaign mismatch")
    if not events or any(
        e.campaign_id != record.campaign_id
        or e.command_id != record.command_id
        or e.revision != record.resulting_revision
        or e.schema_version != EVENT_UPCASTERS.current[e.event.kind]
        for e in events
    ):
        raise ValidationError("Missing or inconsistent command events")
    if [e.ordinal for e in events] != list(
        range(events[0].ordinal, events[0].ordinal + len(events))
    ):
        raise ValidationError("Event order has a gap")
    after = fold(before, [e.event for e in events])
    require_configuration(after, configuration_digest)
    if after["revision"] != record.resulting_revision or document(after) != document(
        record.state_after
    ):
        raise ValidationError("Snapshot diverges from event fold")
    return after


async def verify_commands(
    initial: Campaign,
    commands: Sequence[CommandRecord],
    events: Sequence[StoredEvent],
    *,
    configuration_digest: str,
    execute: ExecuteRecorded | None = None,
) -> tuple[Campaign, tuple[ReplayCheck, ...]]:
    """Fold retained events; report unrepeatable commands explicitly.

    The executor must build an isolated state from the supplied *before* image.
    Neither its expected snapshot nor provider origins are execution inputs.
    """
    require_configuration(initial, configuration_digest)
    state = initial
    checks: list[ReplayCheck] = []
    known = {c.command_id for c in commands}
    if any(e.command_id not in known for e in events):
        raise ValidationError("Event stream has no matching command")
    for command in commands:
        rows = [e for e in events if e.command_id == command.command_id]
        before = state
        state = fold_command(before, command, rows, configuration_digest=configuration_digest)
        reason = unavailable_reason(command)
        reexecuted = False
        if reason is None and execute is not None:
            actual, emitted = await execute(before, command)
            if document(actual) != document(state) or emitted != [e.event for e in rows]:
                raise ValidationError("Re-execution events or dice diverge from recorded facts")
            reexecuted = True
        elif reason is None:
            reason = "fold-only verification requested"
        checks.append(ReplayCheck(command.command_id, True, reexecuted, reason))
    return state, tuple(checks)
