"""Trusted private timing and immutable origin for Great Haste combat casting."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from pydantic import Field

from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.models import Id, Record

PREFIX = "great-haste-casting-origin:"
_current: ContextVar[bool] = ContextVar("great_haste_subjective_casting", default=False)


class CastingOrigin(Record):
    cast_id: Id
    encounter_id: Id
    round: int = Field(ge=1)
    turn_index: int = Field(ge=0)
    game_time: int = Field(ge=0)


def origins(resources: ResourceState) -> dict[str, CastingOrigin]:
    return {
        origin.cast_id: origin
        for event in resources.events
        if event.id.startswith(PREFIX)
        for origin in (CastingOrigin.model_validate_json(event.kind),)
    }


def enabled() -> bool:
    return _current.get()


@contextmanager
def subjective_casting() -> Iterator[None]:
    token = _current.set(True)
    try:
        yield
    finally:
        _current.reset(token)
