"""The native initiating concentration, not a later writer, owns B348 policy."""

import json

import pytest

from wayfarer.engine.simulation.abilities import internal_id, interrupt_concentration
from wayfarer.engine.simulation.ability_resistance_origin import current, identifier, record
from wayfarer.engine.simulation.ability_types import (
    AbilityCommand,
    AbilityEffect,
    AbilityEvent,
    AbilityOutcome,
)
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError


def command(kind: str = "activate") -> AbilityCommand:
    return AbilityCommand.model_validate(
        {
            "id": "begin" if kind == "activate" else "resolve",
            "actor_id": "a",
            "expected_revision": 0,
            "ability_id": "trait:ability",
            "channel_id": "read",
            "kind": kind,
        }
    )


def native() -> tuple[ResourceState, AbilityEffect]:
    effect = AbilityEffect(
        actor_id="a",
        ability_id="trait:ability",
        target_id="b",
        kind="mind-reading",
        started_at=0,
        ready_at=1,
        level=1,
        concentrating=True,
        build_revision="approved-original",
        channel_id="read",
        hp_at_start=10,
    )
    event = AbilityEvent(
        actor_id="a",
        ability_id="trait:ability",
        target_id="b",
        effect=effect,
        outcome=AbilityOutcome(outcome="concentrating"),
    )
    return ResourceState(
        events=(
            ResourceEvent(
                id=internal_id("begin"),
                at=0,
                target_id="a",
                kind=event.model_dump_json(),
            ),
        )
    ), effect


def test_old_absence_stays_old_and_current_matches_exact_native_initiation() -> None:
    resources, _ = native()
    assert not current(resources, command("resolve"))
    marked = record(resources, command())
    assert current(marked, command("resolve"))
    assert not current(marked, command("resolve").model_copy(update={"channel_id": "different"}))
    distracted = interrupt_concentration(marked, "a", "distraction", distraction=True)
    assert current(distracted, command("resolve"))


def test_later_unmarked_activation_does_not_inherit_an_older_marked_origin() -> None:
    resources, effect = native()
    marked = record(resources, command())
    events = []
    for key, active, concentrating in (("cancel", False, True), ("new-old-producer", True, True)):
        value = effect.model_copy(update={"active": active, "concentrating": concentrating})
        event = AbilityEvent(
            actor_id="a",
            ability_id="trait:ability",
            target_id="b",
            effect=value,
            outcome=AbilityOutcome(outcome="concentrating" if active else "cancelled"),
        )
        events.append(
            ResourceEvent(id=internal_id(key), at=0, target_id="a", kind=event.model_dump_json())
        )
    replaced = marked.model_copy(update={"events": marked.events + tuple(events)})
    assert not current(replaced, command("resolve"))


@pytest.mark.parametrize(
    "change", ["generation", "actor", "interval", "native", "duplicate", "duplicate-key"]
)
def test_invalid_private_origin_never_enrolls_or_silently_falls_back(change: str) -> None:
    resources, _ = native()
    marked = record(resources, command())
    event = marked.events[-1]
    data = json.loads(event.kind)
    if change == "generation":
        data["generation"] = True
    elif change == "actor":
        data["effect"]["actor_id"] = "other"
    elif change == "interval":
        data["effect"]["ready_at"] = 2
    elif change == "native":
        data["command_id"] = "other-native"
    encoded = json.dumps(data, separators=(",", ":"), ensure_ascii=False)
    if change == "duplicate-key":
        encoded = '{"generation":2,' + event.kind[1:]
    events = marked.events[:-1] + (event.model_copy(update={"kind": encoded}),)
    if change == "duplicate":
        events += (event,)
    with pytest.raises(ValidationError, match="resistance|Resistance"):
        current(marked.model_copy(update={"events": events}), command("resolve"))
    assert event.id == identifier("begin")
