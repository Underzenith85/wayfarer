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


def acrobatic_trait_bonuses_enabled() -> bool:
    return "acrobatic-trait-bonuses" in _current.get()


def ground_dive_step_enabled() -> bool:
    return "ground-dive-step" in _current.get()
