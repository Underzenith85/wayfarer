"""Audit integrity and negative certification evidence; no source-completeness claims."""

import json
from pathlib import Path

import pytest

from wayfarer.errors import ValidationError
from wayfarer.simulation.hex_geometry import Hex, distance
from wayfarer.source_audit import blockers, inventory, load, report, validate

ROOT = Path(__file__).resolve().parents[1]


def test_audit_integrity_and_unresolved_sources_block_certification() -> None:
    result = report(ROOT)
    assert result["audit_complete"] is False
    manifest = load(ROOT)
    assert sum(f.status == "compared" for f in manifest.fixtures) == 54
    assert not any(f.status == "reviewed" for f in manifest.fixtures)
    assert "source:sjg:gurps-lite-4e-2004" in blockers(manifest)
    assert any(b.startswith("scope:") for b in blockers(manifest))


def test_owner_inventory_exclusions_are_not_profile_exclusions() -> None:
    rows = inventory()
    assert {112, 113, 119, 180} <= {r.owner for r in rows}
    supernatural = [r for r in rows if r.scope == "supernatural-skills"]
    assert supernatural and all(r.owner == 119 for r in supernatural)
    assert all(r.source_review == "pending" for r in rows)


def test_missing_fixture_review_rejected() -> None:
    manifest = load(ROOT)
    with pytest.raises(ValidationError, match="Every fixture"):
        validate(ROOT, manifest.model_copy(update={"fixtures": manifest.fixtures[1:]}))


def test_stale_review_and_nonexistent_test_rejected() -> None:
    manifest = load(ROOT)
    mutations: list[tuple[dict[str, object], str]] = [
        ({"sha256": "0" * 64}, "Stale fixture"),
        ({"tests": ("tests/test_source_audit.py::test_invented",)}, "Missing fixture test"),
        ({"status": "reviewed"}, "independent review"),
    ]
    for change, message in mutations:
        fixture = manifest.fixtures[0].model_copy(update=change)
        with pytest.raises(ValidationError, match=message):
            validate(
                ROOT, manifest.model_copy(update={"fixtures": (fixture, *manifest.fixtures[1:])})
            )


def test_missing_source_and_invalid_scope_rejected() -> None:
    manifest = load(ROOT)
    with pytest.raises(ValidationError, match="Missing profile source"):
        validate(ROOT, manifest.model_copy(update={"sources": manifest.sources[1:]}))
    scope = manifest.scopes[0].model_copy(update={"source_id": "invented"})
    with pytest.raises(ValidationError, match="scope reference"):
        validate(ROOT, manifest.model_copy(update={"scopes": (scope, *manifest.scopes[1:])}))


def test_source_review_cannot_hide_blockers_or_omit_artifact_digest() -> None:
    manifest = load(ROOT)
    source = manifest.sources[0].model_copy(update={"status": "reviewed"})
    with pytest.raises(ValidationError, match="digest and resolved"):
        validate(ROOT, manifest.model_copy(update={"sources": (source, *manifest.sources[1:])}))


def test_documentation_drift_rejected(tmp_path: Path) -> None:
    (tmp_path / "tests").symlink_to(ROOT / "tests", target_is_directory=True)
    (tmp_path / "docs").mkdir()
    docs = (ROOT / "docs/gurps-conformance.md").read_text()
    (tmp_path / "docs/gurps-conformance.md").write_text(
        docs.replace(
            "| `gurps.combat.aim` | yes | yes | partial",
            "| `gurps.combat.aim` | yes | yes | absent",
        )
    )
    with pytest.raises(ValidationError, match="coverage drift"):
        validate(tmp_path, load(ROOT))


def test_step_fixture_executes() -> None:
    ledger = json.loads((ROOT / "tests/fixtures/gurps/conformance.json").read_text())
    case = next(c for c in ledger["cases"] if c["id"] == "basic-hex-step-unit")
    assert (
        distance(Hex(q=0, r=0), Hex(q=case["input"]["hexes"], r=0)) == case["expected"]["distance"]
    )
    assert case["expected"]["unit"] == "yard"


def test_removed_required_scope_rejected() -> None:
    manifest = load(ROOT)
    with pytest.raises(ValidationError, match="Missing required audit scope"):
        validate(ROOT, manifest.model_copy(update={"scopes": manifest.scopes[1:]}))


def test_compared_fixture_cannot_be_promoted_without_source_reconciliation() -> None:
    manifest = load(ROOT)
    index = next(i for i, f in enumerate(manifest.fixtures) if f.status == "compared")
    ledger = json.loads((ROOT / "tests/fixtures/gurps/conformance.json").read_text())
    case = next(c for c in ledger["cases"] if c["id"] == manifest.fixtures[index].id)
    source_index = next(i for i, s in enumerate(manifest.sources) if s.id == case["source_id"])
    sources = list(manifest.sources)
    sources[source_index] = sources[source_index].model_copy(
        update={"status": "compared", "blockers": (191,)}
    )
    fixtures = list(manifest.fixtures)
    fixtures[index] = fixtures[index].model_copy(update={"status": "reviewed"})
    with pytest.raises(ValidationError, match="requires reconciled source"):
        validate(
            ROOT,
            manifest.model_copy(update={"fixtures": tuple(fixtures), "sources": tuple(sources)}),
        )


def test_mundane_skill_rows_carry_item_level_owners_and_certification_state() -> None:
    """#112 item-level blockers reach certification; no family-level rollup."""
    from wayfarer.rules.mundane_skills import PROFILE, coverage_blockers

    rows = [r for r in inventory() if r.scope == "mundane-skills"]
    assert len(rows) == 504
    assert all(r.owner == 112 and r.blockers for r in rows)
    assert {b for r in rows for b in r.blockers} == set(coverage_blockers(PROFILE))
    assert {r.implementation for r in rows} == {"implemented", "unsupported", "contextual"}
    # #336 records a technique template or an open family for each of these.
    assert sum(r.implementation == "contextual" for r in rows) == 28
    # #344 (with #354, #355, #357, #359), #345, #346, #356: a bound procedure
    # reaches certification
    # as implemented, and a transferred one reaches it naming the concrete open
    # child that owns it.
    assert sum(r.implementation == "implemented" for r in rows) == 233
    assert next(r for r in rows if r.id == "skill:acting").blockers == (112, 336, 345)
    assert next(r for r in rows if r.id == "skill:savoir-faire").blockers == (
        111,
        112,
        336,
        345,
        366,
        383,
        385,
    )
    assert next(r for r in rows if r.id == "skill:bow").blockers == (112, 336, 344)
    assert next(r for r in rows if r.id == "skill:net").blockers == (112, 336, 344, 362, 383)
    assert next(r for r in rows if r.id == "skill:broadsword").blockers == (103, 112, 336, 339)
