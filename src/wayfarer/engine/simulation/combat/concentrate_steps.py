"""Trusted selected-Concentrate movement admission; legacy movement stays exact."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.models import Id, Record

_selected: ContextVar[bool] = ContextVar("selected_concentrate_step", default=False)


def selected() -> bool:
    return _selected.get()


@contextmanager
def selected_step() -> Iterator[None]:
    token = _selected.set(True)
    try:
        yield
    finally:
        _selected.reset(token)


class ConcentrationResolution(Record):
    actor_id: Id
    hp: int
    check: CheckTrace


_resolutions: ContextVar[tuple[ConcentrationResolution, ...] | None] = ContextVar(
    "selected_step_concentration_resolutions", default=None
)


def resolved(actor_id: str, hp: int, check: CheckTrace) -> None:
    current = _resolutions.get()
    if current is not None:
        _resolutions.set(
            current + (ConcentrationResolution(actor_id=actor_id, hp=hp, check=check),)
        )


def resolutions() -> tuple[ConcentrationResolution, ...]:
    return _resolutions.get() or ()


@contextmanager
def capture_resolutions(*, enabled: bool = True) -> Iterator[None]:
    token = _resolutions.set(() if enabled else None)
    try:
        yield
    finally:
        _resolutions.reset(token)


def observing() -> bool:
    return _resolutions.get() is not None
