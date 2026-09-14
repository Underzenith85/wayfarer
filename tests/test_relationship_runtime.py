"""Source-derived associated-NPC execution for Characters B36/B44/B72/B131/B135."""

from typing import cast

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mental import Relationship, RelationshipKind
from wayfarer.engine.rules.traits.relationship_runtime import (
    RelationshipCommand,
    RelationshipContext,
    RelationshipOutcome,
    RelationshipState,
    resolve_relationship,
)
from wayfarer.errors import ConflictError, ValidationError


def relationship(kind: RelationshipKind) -> Relationship:
    values: dict[str, object] = {
        "id": f"{kind}:associate",
        "person_id": "associate",
        "kind": kind,
        "frequency": 9,
    }
    if kind in ("ally", "dependent"):
        values["character_points_percent"] = 100 if kind == "ally" else 50
    elif kind == "contact":
        values["contact_skill"] = 12
    elif kind == "patron":
        values["patron_power"] = "wealthy"
    return Relationship.model_validate(values)


def context(
    kind: RelationshipKind, *, event: str = "ordinary", **changes: object
) -> RelationshipContext:
    values: dict[str, object] = {
        "actor_id": "hero",
        "adventure_id": "adventure:one",
        "day": 1,
        "relationship": relationship(kind),
        "known_npc_ids": frozenset({"associate"}),
        "event": event,
    }
    values.update(changes)
    return RelationshipContext.model_validate(values)


def command(kind: RelationshipKind, revision: int = 0) -> RelationshipCommand:
    return RelationshipCommand(
        id=f"resolve:{kind}",
        actor_id="hero",
        definition_id=f"trait:{kind}-associate",
        relationship_id=f"{kind}:associate",
        expected_revision=revision,
    )


@pytest.mark.parametrize(
    ("kind", "event", "outcome"),
    (
        ("ally", "ordinary", "accompanies"),
        ("ally", "betrayal", "bonus-forfeited"),
        ("ally", "severe-betrayal", "relationship-broken"),
        ("ally", "death-no-fault", "replacement-permitted"),
        ("ally", "amicable-parting", "parted"),
        ("patron", "aid", "patron-aid"),
        ("patron", "assignment", "patron-assignment"),
        ("dependent", "ordinary", "causes-trouble"),
        ("dependent", "harmed", "dependent-harmed"),
        ("dependent", "lost", "dependent-lost"),
        ("enemy", "ordinary", "opposes"),
        ("enemy", "eliminated", "enemy-eliminated"),
    ),
)
def test_each_noncontact_trait_has_its_source_specific_appearing_outcome(
    kind: str, event: str, outcome: str
) -> None:
    selected = cast(RelationshipKind, kind)
    state, receipt = resolve_relationship(
        RelationshipState(),
        command(selected),
        context(selected, event=event),
        rng=RecordedDice([3, 3, 3]),
    )
    assert state.revision == 1 and receipt.appears
    assert receipt.appearance_dice == (3, 3, 3)
    assert receipt.outcome == cast(RelationshipOutcome, outcome)
    assert receipt.bonus_points_forfeited == (event in {"betrayal", "severe-betrayal"})
    assert receipt.relationship_ends == (
        event in {"severe-betrayal", "death-no-fault", "amicable-parting", "lost", "eliminated"}
    )
    assert receipt.settlement == (
        "purchased-points-lost"
        if event == "severe-betrayal"
        else "replacement-permitted"
        if event in {"death-no-fault", "amicable-parting"}
        else "buy-off-or-replace-disadvantage"
        if event in {"lost", "eliminated"}
        else "none"
    )


def test_contact_consumes_frequency_and_skill_rolls_and_records_delay() -> None:
    state, information = resolve_relationship(
        RelationshipState(),
        command("contact"),
        context("contact"),
        rng=RecordedDice([3, 3, 3, 3, 3, 3]),
    )
    assert information.outcome == "information"
    assert information.contact_check is not None
    assert information.contact_check.dice == (3, 3, 3)

    delayed_command = command("contact").model_copy(
        update={"id": "contact:day-two", "expected_revision": 1}
    )
    state, delayed = resolve_relationship(
        state,
        delayed_command,
        context("contact", day=2),
        rng=RecordedDice([3, 3, 3, 5, 5, 5, 4]),
    )
    assert delayed.outcome == "delayed" and delayed.delay_days == 4
    assert delayed.contact_check is not None and delayed.contact_check.dice == (5, 5, 5)

    lied_command = command("contact").model_copy(
        update={"id": "contact:day-three", "expected_revision": 2}
    )
    _, lied = resolve_relationship(
        state,
        lied_command,
        context("contact", day=3),
        rng=RecordedDice([3, 3, 3, 6, 6, 6]),
    )
    assert lied.outcome == "lies"


def test_unavailability_omission_and_closed_channels_do_not_invent_outcomes() -> None:
    _, rare = resolve_relationship(
        RelationshipState(), command("enemy"), context("enemy"), rng=RecordedDice([5, 5, 5])
    )
    assert not rare.appears and rare.outcome == "unavailable-adventure"

    _, unreachable = resolve_relationship(
        RelationshipState(),
        command("contact"),
        context("contact", channels_open=False),
        rng=RecordedDice([]),
    )
    assert unreachable.outcome == "unreachable" and unreachable.appearance_dice is None

    _, gone = resolve_relationship(
        RelationshipState(),
        command("contact"),
        context("contact"),
        rng=RecordedDice([6, 6, 5]),
    )
    assert gone.outcome == "unavailable-adventure"

    _, omitted = resolve_relationship(
        RelationshipState(),
        command("patron"),
        context("patron", event="aid", patron_applicable=False),
        rng=RecordedDice([]),
    )
    assert omitted.outcome == "omitted" and omitted.appearance_dice is None


def test_same_command_retries_do_not_reroll_and_replay_is_exact() -> None:
    value = command("ally")
    authored = context("ally")
    state, receipt = resolve_relationship(
        RelationshipState(), value, authored, rng=RecordedDice([3, 3, 3])
    )
    repeated, same = resolve_relationship(state, value, authored, rng=RecordedDice([]))
    assert repeated == state and same == receipt
    assert RelationshipState.model_validate_json(state.model_dump_json()) == state
    with pytest.raises(ConflictError, match="different facts"):
        resolve_relationship(
            state,
            value,
            context("ally", event="betrayal"),
            rng=RecordedDice([]),
        )
    with pytest.raises(ConflictError, match="already resolved"):
        resolve_relationship(
            state,
            value.model_copy(update={"id": "other", "expected_revision": 1}),
            authored,
            rng=RecordedDice([]),
        )


def test_relationship_execution_rejects_unpurchased_shapes_and_unknown_npcs() -> None:
    with pytest.raises(SchemaError, match="authoritative campaign NPC"):
        context("ally", known_npc_ids=frozenset({"someone-else"}))
    wrong = relationship("ally").model_copy(update={"frequency": 12})
    with pytest.raises(ValidationError, match="differ from the purchased"):
        resolve_relationship(
            RelationshipState(),
            command("ally"),
            context("ally").model_copy(update={"relationship": wrong}),
            rng=RecordedDice([]),
        )
    with pytest.raises(ValidationError, match="actor differs"):
        resolve_relationship(
            RelationshipState(),
            command("ally").model_copy(update={"actor_id": "intruder"}),
            context("ally"),
            rng=RecordedDice([]),
        )
