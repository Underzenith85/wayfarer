"""Pure, ordered JSON migrations shared by every storage reader."""

import json
from collections.abc import Callable, Mapping
from copy import deepcopy

from wayfarer import validation
from wayfarer.engine.simulation.events import EVENT_ADAPTER, EngineEvent
from wayfarer.errors import StorageError

JsonRow = dict[str, object]
Upcaster = Callable[[JsonRow], JsonRow]


class UpcasterRegistry:
    def __init__(
        self, current: Mapping[str, int], steps: Mapping[tuple[str, int], Upcaster]
    ) -> None:
        self.current = dict(current)
        self.steps = dict(steps)

    def check(self, kind: str, version: int) -> None:
        target = self.current.get(kind)
        if target is None or version < 0 or version > target:
            raise StorageError(f"Unsupported retained schema {kind}@{version}")
        for previous in range(version, target):
            if (kind, previous) not in self.steps:
                raise StorageError(f"Missing upcaster for retained schema {kind}@{previous}")

    def read(self, kind: str, version: int, row: JsonRow) -> JsonRow:
        self.check(kind, version)
        result = deepcopy(row)
        for previous in range(version, self.current[kind]):
            result = self.steps[kind, previous](result)
        return result


# Version 1 is the first persisted engine-event shape. Future structural changes
# register a step beside the event definitions and increase that kind's version.
EVENT_UPCASTERS = UpcasterRegistry(
    {
        kind: 1
        for kind in (
            "state.patched",
            "command.applied",
            "projection.refresh",
            "action.resolved",
            "resource.changed",
            "scene.changed",
            "spell.resolved",
            "ability.resolved",
            "combat.resolved",
            "fright.resolved",
            "hazard.resolved",
            "injury.resolved",
        )
    },
    {},
)


def read_event(raw: str, version: int) -> EngineEvent:
    row = validation.mapping(validation.decode(raw))
    kind = validation.string(row.get("kind"))
    migrated = EVENT_UPCASTERS.read(kind, version, row)
    return EVENT_ADAPTER.validate_json(json.dumps(migrated))


def check_retention(registry: UpcasterRegistry, usage: list[JsonRow]) -> None:
    """A version can retire only after every campaign has a covering snapshot.

    Retained fixture versions are checked separately even when operational rows
    are covered: releasing a reader must preserve every promised fixture.
    """
    for row in usage:
        snapshot = row.get("snapshot_revision")
        if snapshot is None or validation.integer(snapshot) < validation.integer(
            row["last_revision"]
        ):
            registry.check(validation.string(row["kind"]), validation.integer(row["version"]))
