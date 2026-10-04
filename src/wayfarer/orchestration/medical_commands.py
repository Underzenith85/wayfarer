"""Explicit private medical command family, separate from authored recovery."""

from pydantic import TypeAdapter

from wayfarer import validation
from wayfarer.engine.simulation.health.medical.commands import BeginRecovery, FinishRecovery
from wayfarer.errors import ValidationError
from wayfarer.orchestration.medical_context import recorded
from wayfarer.persistence.events import CommandInput

ADAPTER: TypeAdapter[BeginRecovery | FinishRecovery] = TypeAdapter(BeginRecovery | FinishRecovery)


def recorded_command(record: CommandInput) -> BeginRecovery | FinishRecovery:
    """Require the original accepted family and digest before inspecting intent."""
    original, _captured = recorded(record)
    payload = validation.mapping(validation.decode(original))
    if payload.get("operation") != "gurps-recovery":
        raise ValidationError("Recorded command is not the medical recovery family")
    return ADAPTER.validate_python(payload.get("command"))
