"""Private source-current mental provider origin; no player policy field."""

import hashlib
import json

from pydantic import Field

from wayfarer.engine.simulation.resources import Command, ResourceEvent
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class MentalControlSource(Record):
    event_id: str = Field(min_length=1)
    event_digest: str = Field(pattern=r"^[0-9a-f]{64}$")


def source_digest(event: ResourceEvent) -> str:
    return hashlib.sha256(event.model_dump_json().encode()).hexdigest()


def control_source(event: ResourceEvent) -> MentalControlSource:
    return MentalControlSource(event_id=event.id, event_digest=source_digest(event))


class MentalResistanceOrigin(Record):
    generation: int = Field(ge=1, le=1)
    command_json: str
    command_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    channel_json: str
    prepared_channel_json: str | None = Field(default=None, exclude_if=lambda value: value is None)
    control_sources: tuple[MentalControlSource, ...] = Field(
        default=(), exclude_if=lambda value: not value
    )
    build_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    at: int = Field(ge=0)


def capture(
    command: Command, channel: Record, build_revision: str, at: int
) -> MentalResistanceOrigin:
    encoded = command.model_dump_json()
    return MentalResistanceOrigin(
        generation=1,
        command_json=encoded,
        command_digest=hashlib.sha256(encoded.encode()).hexdigest(),
        channel_json=channel.model_dump_json(),
        build_revision=build_revision,
        at=at,
    )


def validate_digest(origin: MentalResistanceOrigin) -> None:
    if hashlib.sha256(origin.command_json.encode()).hexdigest() != origin.command_digest:
        raise ValidationError("Recorded mental provider command digest changed")


_PRIVATE_FIELDS = frozenset(
    {
        "resistance_origin",
        "generation",
        "command_json",
        "command_digest",
        "channel_json",
        "prepared_channel_json",
        "control_sources",
        "build_revision",
        "at",
        "event_id",
        "event_digest",
    }
)


def _decoded_object(raw: str, *, event: bool) -> bool:
    """Detect duplicate claims before model decoding can collapse them."""
    duplicates: list[str] = []

    def pairs(items: list[tuple[str, object]]) -> dict[str, object]:
        result: dict[str, object] = {}
        for key, value in items:
            if key in result:
                if not event or key in _PRIVATE_FIELDS:
                    raise ValidationError("Duplicate private mental provider metadata")
                duplicates.append(key)
            result[key] = value
        return result

    try:
        decoded: object = json.loads(raw, object_pairs_hook=pairs)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValidationError("Invalid mental provider JSON") from exc
    if not isinstance(decoded, dict):
        raise ValidationError("Mental provider JSON requires an object")
    present = "resistance_origin" in decoded
    if present and duplicates:
        raise ValidationError("Duplicate current mental provider event fields")
    return present


def validate_event_json(raw: str) -> bool:
    """Old absence is distinct from an explicit current-origin field."""
    return _decoded_object(raw, event=True)


def validate_context_json(raw: str) -> None:
    _decoded_object(raw, event=False)
