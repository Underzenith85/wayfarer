"""B66 family completeness is independent of supported Luck purchases."""

from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest
from test_mundane_traits import runtime_compiler
from test_statistics import gurps_draft

from wayfarer.certification import source_audit
from wayfarer.certification.basic_set_certification import evaluate
from wayfarer.certification.source_ledgers import load_source_ledgers, validate_source_ledgers
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import ImplementationStatus
from wayfarer.engine.rules.conformance import CAPABILITIES
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane import audit_report, inventory
from wayfarer.errors import ValidationError

ROOT = Path(__file__).resolve().parents[1]
LUCK_ID = "trait:advantage:luck"


def test_luck_family_has_matching_partial_inventory_and_live_source_ownership() -> None:
    entry = next(entry for entry in inventory() if entry.id == LUCK_ID)
    runtime = next(row for row in source_audit.inventory(ROOT) if row.id == LUCK_ID)
    bundle = load_source_ledgers(ROOT)
    source = next(row for row in bundle.rows if row.id == LUCK_ID)
    assert entry.implemented and entry.status is ImplementationStatus.IMPLEMENTED
    assert (
        entry.certification_status == runtime.implementation == source.implementation == "partial"
    )
    assert runtime.reference == entry.reference == "B66"
    assert runtime.source_review == source.source_review == "reviewed"
    assert source.completion_owner == source.consequence_owner == 854
    assert next(issue.state for issue in bundle.owners.issues if issue.issue == 854) == "open"
    # Ordinary and secret task evidence do not certify the remaining named consumers.
    assert entry.completion_issues == (855, 867, 868, 869, 870)
    assert runtime.blockers == entry.followup_issues == (113, 854, 855, 867, 868, 869, 870)
    assert 680 in source.historical_owners and 680 not in runtime.blockers
    assert 821 not in runtime.blockers  # Audit partition, not an implementation dependency.
    reported_entries = cast(list[dict[str, object]], audit_report()["entries"])
    reported = next(row for row in reported_entries if row["id"] == LUCK_ID)
    assert reported["status"] == "implemented"
    assert reported["certification_status"] == "partial"
    assert {
        (blocker.kind, blocker.owner_issue)
        for blocker in evaluate(ROOT).blockers
        if blocker.identifier == LUCK_ID and blocker.kind in {"inventory", "ledger"}
    } == {("inventory", 854), ("ledger", 854)}


@pytest.mark.parametrize("points", [15, 30, 60])
@pytest.mark.parametrize(
    ("modifiers", "percent"),
    [
        ((), 100),
        (("active",), 60),
        (("defensive",), 80),
        (("aspected-athletics",), 80),
        (("aspected-social",), 80),
        (("aspected-job",), 80),
        (("aspected-combat",), 80),
    ],
)
def test_partial_luck_family_preserves_supported_purchase_availability(
    points: int, modifiers: tuple[str, ...], percent: int
) -> None:
    result = runtime_compiler().compile(
        gurps_draft(
            Purchase(
                definition_id=LUCK_ID,
                trait=TraitOptions(parameters=(("point-cost", points),), modifiers=modifiers),
            )
        )
    )
    assert result.build is not None, result.diagnostics
    assert result.spent == points * percent // 100


def test_task_completion_alone_cannot_promote_the_whole_luck_family() -> None:
    entry = next(entry for entry in inventory() if entry.id == LUCK_ID)
    runtime = next(row for row in source_audit.inventory(ROOT) if row.id == LUCK_ID)
    source = next(row for row in load_source_ledgers(ROOT).rows if row.id == LUCK_ID)
    assert not {866, 871} & set(entry.completion_issues)
    assert {
        "tests/test_task_host.py",
        "tests/test_task_host_boundaries.py",
        "tests/test_task_host_work.py",
        "tests/test_secret_task_host.py",
        "tests/test_secret_task_boundaries.py",
    } <= set(source.evidence_paths)
    assert runtime.implementation == source.implementation == "partial"
    assert source.completion_owner == source.consequence_owner == 854
    assert runtime.blockers == (113, 854, 855, 867, 868, 869, 870)


def test_luck_source_cannot_claim_completion_over_the_canonical_partial_inventory() -> None:
    bundle = load_source_ledgers(ROOT)
    promoted = tuple(
        row.model_copy(update={"implementation": "implemented", "completion_owner": None})
        if row.id == LUCK_ID
        else row
        for row in bundle.rows
    )
    with pytest.raises(ValidationError, match="Runtime inventory implementation disagrees"):
        validate_source_ledgers(
            replace(bundle, rows=promoted),
            source_audit.inventory(ROOT),
            frozenset(CAPABILITIES),
            ROOT,
        )
