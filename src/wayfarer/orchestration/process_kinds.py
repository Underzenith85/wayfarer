"""Where the registered process kinds are collected (#640).

The runtime builds one registry and every kind registers into it here, so no
module holds a global worker and the composition root stays the only place that
knows the whole set.
"""

from __future__ import annotations

from wayfarer.orchestration.director import DIRECTOR_TURN
from wayfarer.orchestration.processes import ProcessKind, ProcessRegistry
from wayfarer.orchestration.providers import PROVIDER_KINDS

KINDS: tuple[ProcessKind, ...] = (*PROVIDER_KINDS, DIRECTOR_TURN)


def registered(registry: ProcessRegistry) -> ProcessRegistry:
    """Register every known kind on a fresh registry."""
    for kind in KINDS:
        registry.register(kind)
    return registry
