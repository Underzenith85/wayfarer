"""Capture real time at orchestration entry; downstream code receives a value."""

import time
from dataclasses import dataclass
from datetime import UTC, datetime

from wayfarer.errors import ValidationError


@dataclass(frozen=True)
class CommandInstant:
    unix_microseconds: int

    def __post_init__(self) -> None:
        if type(self.unix_microseconds) is not int or self.unix_microseconds < 0:
            raise ValidationError("Invalid recorded command instant")

    @property
    def seconds(self) -> float:
        return self.unix_microseconds / 1_000_000

    def isoformat(self) -> str:
        return (
            datetime.fromtimestamp(self.seconds, UTC)
            .isoformat(timespec="milliseconds")
            .replace("+00:00", "Z")
        )


def capture_instant() -> CommandInstant:
    return CommandInstant(time.time_ns() // 1000)
