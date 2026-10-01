"""Bounded #822 reconciliation; projection does not verify purchased runtime effects."""

import json
from pathlib import Path

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.mundane.complete import SPECS
from wayfarer.engine.simulation.health.toxins import DrinkCommand, apply_drinking
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError

OWNED = frozenset(
    "trait:advantage:" + name
    for name in ("alcohol-tolerance", "less-sleep", "reduced-consumption", "unusual-background")
) | frozenset(
    "trait:disadvantage:" + name
    for name in (
        "alcohol-intolerance",
        "alcoholism",
        "bad-back",
        "bad-grip",
        "cannot-float",
        "cannot-speak",
        "chronic-pain",
        "dwarfism",
        "epilepsy",
        "extra-sleep",
        "fat",
        "flashbacks",
        "gigantism",
        "hemophilia",
        "hunchback",
        "increased-consumption",
        "light-sleeper",
        "low-pain-threshold",
        "minor-handicaps",
        "missing-digit",
        "motion-sickness",
        "neurological-disorder",
        "numb",
        "one-arm",
        "one-hand",
        "skinny",
        "sleepwalker",
        "slow-healing",
        "space-sickness",
        "stuttering",
        "susceptible",
        "terminally-ill",
        "timesickness",
        "very-fat",
        "wounded",
    )
)
PARTITIONS = {
    819: {"behavior", "relationship"},
    820: {"social", "talent"},
    821: {"resources", "luck"},
    822: {"physiology", "recovery"},
    823: {"movement", "defense"},
    824: {"knowledge", "technology", "senses"},
}


def test_frozen_owned_rows_and_all_six_partitions_account_for_every_catalog_identity() -> None:
    identifiers = {row.id for row in SPECS}
    assert len(identifiers) == len(SPECS) == 267
    assigned: set[str] = set()
    frozen_owner = {"trait:advantage:unusual-background": 822}
    for owner, families in PARTITIONS.items():
        group = {
            row.id
            for row in SPECS
            if frozen_owner.get(row.id, owner if row.family in families else None) == owner
        }
        assert not assigned & group
        assigned |= group
    assert assigned == identifiers  # Unknown/unassigned families fail this assertion.
    owned = {
        row.id
        for row in SPECS
        if frozen_owner.get(row.id, 822 if row.family in PARTITIONS[822] else None) == 822
    }
    assert owned == OWNED and len(owned) == 39
    root = Path(__file__).resolve().parents[1]
    ledger = json.loads((root / "docs/gurps-mundane-physiology-reconciliation.json").read_text())
    rows = {row["id"]: row for row in ledger["rows"]}
    assert rows.keys() == OWNED and len(ledger["rows"]) == 39
    assert {row["follow_up"] for row in rows.values()} == {902, 903, 904, 905, 906}
    assert all(row["execution"] == "unverified" for row in rows.values())
    assert rows["trait:disadvantage:missing-digit"]["source"] == "Characters third printing B145"
    assert rows["trait:disadvantage:terminally-ill"]["source"] == "Characters third printing B158"
    assert rows["trait:advantage:unusual-background"]["family"] == "physiology"
    assert rows["trait:advantage:unusual-background"]["current_family"] == "resources"


@pytest.mark.parametrize(("tolerance", "target", "level"), [(2, 11, "sober"), (-2, 7, "tipsy")])
def test_existing_drinking_context_changes_actual_state_without_certifying_purchase_join(
    tolerance: int, target: int, level: str
) -> None:
    # B100/B165: +/-2 drinking HT; B439: ST10 allows two drinks, third gives -1.
    command = DrinkCommand(id="drink", actor_id="a", expected_revision=0, kind="drink", drinks=3)
    updated, result = apply_drinking(
        ResourceState(),
        command,
        rng=RecordedDice([3, 3, 4]),
        system=True,
        st=10,
        ht=10,
        tolerance=tolerance,
    )
    assert result.check is not None and result.check.effective_target == target
    assert result.level == level and updated.intoxications[0].level == level
    assert updated.intoxications[0].total_session_drinks == 3 and updated.revision == 1
    persisted = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_drinking(
        persisted, command, rng=RecordedDice([]), system=True, st=10, ht=10, tolerance=tolerance
    ) == (persisted, result)
    with pytest.raises(ValidationError, match="authoritative"):
        apply_drinking(ResourceState(), command, rng=RecordedDice([]), st=10, ht=10)
    with pytest.raises(ConflictError, match="revision"):
        apply_drinking(
            ResourceState(revision=1),
            command,
            rng=RecordedDice([]),
            system=True,
            st=10,
            ht=10,
            tolerance=tolerance,
        )
