"""Per-mode Basic Set vehicle coverage audit (#358).

Independent expectations: Basic Set Campaigns, Fourth Edition, B394-B395 planar
and high-speed movement, B397 the mounted loss check, B430-B432 the collision
exchange and its occupants, and B466-B470 the per-mode control-loss table. The
audit records what the adapter carries per mode; it implements no mechanic, so
its evidence is that the declared capability rows cannot drift from it.
"""

import pytest

from wayfarer.errors import ValidationError
from wayfarer.rules.conformance import CAPABILITIES, CoverageStatus, capability
from wayfarer.rules.mundane_skills import inventory
from wayfarer.rules.mundane_skills.technology import PROCEDURES, unsupported_scope
from wayfarer.rules.vehicle_capabilities import VEHICLE_OPERATIONS
from wayfarer.rules.vehicle_coverage import (
    ALL_CONCERNS,
    COMBAT,
    COMBAT_RESIDUALS,
    MODES,
    MOVEMENT,
    OWNER,
    SUPERSEDED,
    Concern,
    ModeCoverage,
    audit_report,
    combat_status,
    movement_status,
    residual_owners,
    validate_coverage,
)

# The children this audit split its residual scope into, transcribed from #358.
RESIDUAL_OWNERS = (395, 396, 397)


def test_every_declared_mode_is_audited_against_the_adapter_itself() -> None:
    assert set(MODES) == set(VEHICLE_OPERATIONS)
    for mode, entry in MODES.items():
        assert entry.operations is VEHICLE_OPERATIONS[mode]
        assert entry.reference.startswith("B")


def test_completed_ground_modes_are_verified_and_residuals_have_live_owners() -> None:
    """#358 acceptance: verified with evidence, or carrying a concrete open child."""
    for mode, entry in MODES.items():
        assert entry.verified == (
            mode in ("air", "water", "underwater")
            or mode.startswith("ground-")
            and mode != "ground-mount"
        )
        assert bool(entry.residuals) != entry.verified
        for detail, owner in entry.residuals.items():
            assert owner in RESIDUAL_OWNERS, f"{mode}: {detail}"
            # A closed issue cannot hold a blocker, which is the whole point.
            assert owner not in (OWNER, *SUPERSEDED), f"{mode}: {detail}"


def test_the_capability_rows_are_owned_by_this_audit_and_derived_from_it() -> None:
    """The registry stops naming the closed #120 and stops being hand-set."""
    for identifier, derived in ((MOVEMENT, movement_status()), (COMBAT, combat_status())):
        declared = CAPABILITIES[identifier]
        assert declared.owner_issue == OWNER
        assert declared.status is derived is CoverageStatus.PARTIAL
    assert capability(MOVEMENT).status is CoverageStatus.PARTIAL
    validate_coverage()


def test_a_mode_that_carries_no_operation_resolves_no_concern() -> None:
    """`ground-mount` rejects every version-two path by name, so it claims nothing."""
    mount = MODES["ground-mount"]
    assert not mount.operations and mount.concerns == ()
    assert set(mount.owners) == {396}
    # A spacecraft can lose control and can collide, but it cannot travel.
    assert "vehicle-maneuver" not in MODES["space"].operations
    assert Concern.COLLISION in MODES["space"].concerns


def test_vehicle_combat_has_no_implementation_behind_its_partial_row() -> None:
    assert combat_status() is CoverageStatus.PARTIAL
    assert set(COMBAT_RESIDUALS.values()) == {397}
    assert any("ramming" in detail for detail in COMBAT_RESIDUALS)


def replace(monkeypatch: pytest.MonkeyPatch, entry: ModeCoverage) -> None:
    """Swap one mode, keeping the declared set intact so its own rule is reached."""
    import wayfarer.rules.vehicle_coverage as module

    rows = tuple(entry if row.mode == entry.mode else row for row in module._MODES)
    monkeypatch.setattr(module, "_MODES", rows)
    monkeypatch.setattr(module, "MODES", {row.mode: row for row in rows})


def test_a_residual_cannot_be_resolved_into_a_closed_owner(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """#120 and #207 are closed, so neither can hold what is still missing."""
    import wayfarer.rules.vehicle_coverage as module

    replace(monkeypatch, ModeCoverage("water", "B469", (), {"sinking": 120}))
    with pytest.raises(ValidationError, match="names no live owner"):
        module.validate_coverage()


def test_a_mode_cannot_claim_a_concern_it_has_no_operation_for(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wayfarer.rules.vehicle_coverage as module

    replace(monkeypatch, ModeCoverage("ground-mount", "B397", (Concern.COLLISION,)))
    with pytest.raises(ValidationError, match="no operation"):
        module.validate_coverage()


def test_a_mode_that_stops_owing_anything_raises_the_declared_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Verification is derived: clearing every residual is what moves the status."""
    import wayfarer.rules.vehicle_coverage as module

    assert movement_status() is CoverageStatus.PARTIAL
    raised = tuple(
        ModeCoverage(row.mode, row.reference, ALL_CONCERNS if row.operations else ())
        for row in module._MODES
    )
    monkeypatch.setattr(module, "_MODES", raised)
    # `ground-mount` carries no operation, so the row cannot rise on the others.
    assert module.movement_status() is CoverageStatus.PARTIAL
    monkeypatch.setattr(module, "_MODES", tuple(r for r in raised if r.operations))
    assert module.movement_status() is CoverageStatus.VERIFIED
    monkeypatch.setattr(module, "COMBAT_RESIDUALS", {})
    assert module.combat_status() is CoverageStatus.VERIFIED


def test_the_registry_cannot_quietly_disagree_with_the_audit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Raising a mode is the only way to raise a row; the reverse is rejected."""
    import wayfarer.rules.vehicle_coverage as module

    monkeypatch.setattr(module, "movement_status", lambda: CoverageStatus.VERIFIED)
    with pytest.raises(ValidationError, match="disagrees with the audit"):
        module.validate_coverage()


def test_the_report_publishes_every_residual_and_its_owner() -> None:
    report = audit_report()
    assert report["owner"] == OWNER
    assert report["supersedes"] == [120, 207]
    assert report["verified_modes"] == 8
    assert report["total_modes"] == len(VEHICLE_OPERATIONS)
    assert report["residual_owners"] == list(RESIDUAL_OWNERS)
    assert residual_owners() == RESIDUAL_OWNERS
    modes = report["modes"]
    assert isinstance(modes, list)
    assert {str(row["mode"]) for row in modes} == set(VEHICLE_OPERATIONS)


def test_the_bound_vehicle_rows_still_publish_their_activation_blocker() -> None:
    """#346's procedures execute; live play may not offer them until this verifies."""
    scope = dict(unsupported_scope())
    assert scope["skill:driving-automobile"].id == MOVEMENT
    assert scope["skill:driving-automobile"].owner_issue == OWNER
    assert "skill:research" not in scope
    # Repairing a machine is not operating one, so #356's Mechanic rows are clear.
    assert "skill:mechanic-automobile" not in scope
    rows = {entry.id: entry for entry in inventory()}
    assert OWNER in rows["skill:driving-automobile"].followup_issues
    assert PROCEDURES["skill:driving-automobile"].activation_blockers == (MOVEMENT,)
