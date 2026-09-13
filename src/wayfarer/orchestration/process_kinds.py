"""Where the registered process kinds are collected (#640).

The runtime builds one registry and every kind registers into it here, so no
module holds a global worker and the composition root stays the only place that
knows the whole set.
"""

from __future__ import annotations

from wayfarer.orchestration.processes import ProcessRegistry
from wayfarer.orchestration.providers import PROVIDER_KINDS


def registered(registry: ProcessRegistry) -> ProcessRegistry:
    """Register every known kind on a fresh registry."""
    for kind in PROVIDER_KINDS:
        registry.register(kind)
    return registry
