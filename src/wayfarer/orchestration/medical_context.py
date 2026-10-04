"""Private trusted care snapshots; provisional capture is revalidated under CAS."""

import hashlib
import json
from dataclasses import asdict
from typing import Literal

from pydantic import field_validator

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.medical_environment import CareEnvironment
from wayfarer.persistence.command_inputs import intent_input
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "medical_context"
ORIGINAL = "medical_original_input"
SourceKind = Literal["configuration-default", "trusted-scenario-snapshot"]


class CareSnapshot(Record):
    technology_level: int
    food: bool
    water: bool
    sleep: bool
    physician_id: str | None
    surgical_facility: bool
    anesthetic: bool
    surgical_modifier: int
    life_support: bool
    sterile: bool
    equipment_quality_modifier: int
    infection_risk: bool
    infection_modifier: int
    resuscitation_first_aid_penalty: int
    drug_item_id: str | None
    drug_form: Literal["pill", "contact", "aerosol", "injection"] | None
    drug_hp: int
    drug_fp: int
    mana: Literal["none", "low", "normal", "high", "very-high"] | None


class MedicalCapture(Record):
    generation: Literal[1]

    @field_validator("generation", mode="before")
    @classmethod
    def exact_generation(cls, value: object) -> Literal[1]:
        if type(value) is not int or value != 1:
            raise ValueError("Medical context generation must be exact integer 1")
        return 1

    source_kind: SourceKind
    command_id: str
    actor_id: str
    target_id: str
    expected_revision: int
    profile_id: str
    configuration_digest: str
    prestate_digest: str
    environment: CareSnapshot
    snapshot_digest: str


def canonical(value: object) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def digest(value: object) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def prestate_digest(state: PlayState) -> str:
    return digest(state.model_dump(mode="json"))


def snapshot(environment: CareEnvironment) -> CareSnapshot:
    return CareSnapshot.model_validate(asdict(environment))


def capture(
    state: PlayState,
    command: BeginRecovery,
    profile_id: str,
    environment: CareEnvironment,
    *,
    source_kind: SourceKind,
) -> MedicalCapture:
    values = {
        "generation": 1,
        "source_kind": source_kind,
        "command_id": command.id,
        "actor_id": command.actor_id,
        "target_id": command.target_id,
        "expected_revision": command.expected_revision,
        "profile_id": profile_id,
        "configuration_digest": state.configuration_digest,
        "prestate_digest": prestate_digest(state),
        "environment": snapshot(environment).model_dump(mode="json"),
    }
    return MedicalCapture.model_validate(values | {"snapshot_digest": digest(values)})


def validate(
    captured: MedicalCapture, state: PlayState, command: BeginRecovery, profile_id: str
) -> None:
    if (
        digest(captured.model_dump(mode="json", exclude={"snapshot_digest"}))
        != captured.snapshot_digest
    ):
        raise ValidationError("Medical context snapshot digest mismatch")
    if (
        captured.command_id != command.id
        or captured.actor_id != command.actor_id
        or captured.target_id != command.target_id
        or captured.expected_revision != command.expected_revision
        or captured.profile_id != profile_id
        or captured.configuration_digest != state.configuration_digest
        or captured.prestate_digest != prestate_digest(state)
    ):
        raise ValidationError("Medical context does not match its original command and prestate")


def admitted_intent(text: str) -> str:
    """Strict private-envelope validation before returning exact original public intent."""
    payload = validation.mapping(validation.decode(text))
    if KEY not in payload and ORIGINAL not in payload:
        return text
    if (
        set(payload) != {"operation", "command", KEY, ORIGINAL}
        or payload.get("operation") != "gurps-recovery"
    ):
        raise ValidationError("Invalid medical context envelope")
    if text != canonical(payload):
        raise ValidationError("Medical context envelope is not canonical")
    captured = MedicalCapture.model_validate(payload[KEY])
    if (
        digest(captured.model_dump(mode="json", exclude={"snapshot_digest"}))
        != captured.snapshot_digest
    ):
        raise ValidationError("Medical context snapshot digest mismatch")
    original = validation.string(payload[ORIGINAL])
    raw = validation.mapping(validation.decode(original))
    if (
        set(raw) != {"operation", "command"}
        or raw.get("operation") != "gurps-recovery"
        or raw["command"] != payload["command"]
    ):
        raise ValidationError("Medical context original intent mismatch")
    command = BeginRecovery.model_validate_json(canonical(raw["command"]))
    if (captured.command_id, captured.actor_id, captured.target_id, captured.expected_revision) != (
        command.id,
        command.actor_id,
        command.target_id,
        command.expected_revision,
    ):
        raise ValidationError("Medical context original command identity mismatch")
    return original


def recorded(record: CommandInput) -> tuple[str, MedicalCapture | None]:
    if record.text is None or payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Medical input does not match its recorded digest")
    text = intent_input(record.text)
    payload = validation.mapping(validation.decode(text))
    if payload.get("operation") != "gurps-recovery":
        raise ValidationError("Recorded command is not the medical recovery family")
    if KEY not in payload and ORIGINAL not in payload and set(payload) != {"operation", "command"}:
        raise ValidationError("Invalid legacy medical intent")
    original = admitted_intent(text)
    captured = MedicalCapture.model_validate(payload[KEY]) if KEY in payload else None
    return original, captured


def envelope(original: str, captured: MedicalCapture) -> str:
    raw = validation.mapping(validation.decode(original))
    return canonical(raw | {KEY: captured.model_dump(mode="json"), ORIGINAL: original})


def environment(captured: MedicalCapture) -> CareEnvironment:
    return CareEnvironment(**captured.environment.model_dump())
