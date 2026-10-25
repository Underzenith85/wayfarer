"""Independent B345 care policies and authoritative context capture."""

from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING

from wayfarer import validation
from wayfarer.engine.rules.types.hazard_care import HazardCare, captured
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.persistence.command_inputs import intent_input, replay_payload, same_input
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.hazard_care_inputs import CONTEXT, KEY, intent, stamp

if TYPE_CHECKING:
    from wayfarer.orchestration.play import PlayService


DIAGNOSIS_MINIMUM_ACTIVE = True


@dataclass(frozen=True)
class CareAdmission:
    current: bool
    saved: bool
    context: HazardCare | None = None
    diagnosis_minimum: bool = False


def read(saved: CommandInput) -> CareAdmission:
    if saved.text is None:
        return CareAdmission(False, True)
    if payload_digest({"input": saved.text}) != saved.payload_hash:
        raise ValidationError("Recorded hazard care input changed")
    payload = validation.mapping(replay_payload(saved.text))
    intent(intent_input(saved.text))
    if KEY not in payload:
        return CareAdmission(False, True)
    return CareAdmission(
        True, True, captured(payload[CONTEXT]), diagnosis_minimum=payload[KEY] == 2
    )


async def capture(play: PlayService, cid: str, command_id: str, public: str) -> CareAdmission:
    prior = recorded_command.get()
    saved = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await play.store.command_input(cid, command_id)
    )
    if saved is None:
        return CareAdmission(True, False, diagnosis_minimum=DIAGNOSIS_MINIMUM_ACTIVE)
    admission = read(saved)
    if not same_input(saved, public):
        raise ConflictError("Hazard care command ID reused with different input")
    return admission


def payload(
    public: str, context: HazardCare | None, *, current: bool, diagnosis_minimum: bool = False
) -> str:
    if diagnosis_minimum and (not current or context is None or context.kind != "diagnosis"):
        raise ValidationError("Diagnosis minimum policy requires its captured exact context")
    if not current:
        return public
    if context is None:
        raise ValidationError("Current hazard care requires its captured trusted context")
    return stamp(public, asdict(context), version=2 if diagnosis_minimum else 1)
