"""Trusted per-command combat semantics; absent recorded features stay legacy."""

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar

_current: ContextVar[frozenset[str]] = ContextVar("combat_protocol_features", default=frozenset())


def preserve_grenade_fuse() -> bool:
    return "grenade-fuse" in _current.get()


def maneuver_budget_enabled() -> bool:
    return "maneuver-budget" in _current.get()


@contextmanager
def combat_generation(features: frozenset[str]) -> Iterator[None]:
    token = _current.set(features)
    try:
        yield
    finally:
        _current.reset(token)
