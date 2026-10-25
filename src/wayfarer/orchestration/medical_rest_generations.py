"""Private captured inputs for the bounded FP-rest work-calendar consumer."""

from dataclasses import dataclass
from typing import TYPE_CHECKING

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService

KEY = "medical_rest_generation"
ENVIRONMENT = "rest_care_environment"


@dataclass(frozen=True)
class RestInputs:
    enabled: bool
    saved: bool
    environment: object = None


def read(saved: CommandInput, *, eligible: bool) -> RestInputs:
    if saved.text is None:
        return RestInputs(False, True)
    if payload_digest({"input": saved.text}) != saved.payload_hash:
        raise ValidationError("Recorded FP-rest input does not match its digest")
    payload = validation.mapping(replay_payload(saved.text))
    if KEY not in payload:
        if ENVIRONMENT in payload:
            raise ValidationError("Captured FP-rest facts require their generation")
        return RestInputs(False, True)
    command = validation.mapping(payload.get("command"))
    if (
        type(payload[KEY]) is not int
        or payload[KEY] != 1
        or not eligible
        or payload.get("operation") != "gurps-recovery"
        or command.get("kind") not in {"rest", "finish-recovery"}
    ):
        raise ValidationError("Unsupported recorded FP-rest generation")
    if command.get("kind") == "rest" and ENVIRONMENT not in payload:
        raise ValidationError("Recorded FP-rest start lacks its captured environment")
    if command.get("kind") == "finish-recovery" and ENVIRONMENT in payload:
        raise ValidationError("FP-rest completion reads its existing task, not a new environment")
    return RestInputs(True, True, payload.get(ENVIRONMENT))


async def capture(play: PlayService, cid: str, command_id: str, *, eligible: bool) -> RestInputs:
    prior = recorded_command.get()
    saved = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await play.store.command_input(cid, command_id)
    )
    return RestInputs(eligible, False) if saved is None else read(saved, eligible=eligible)
