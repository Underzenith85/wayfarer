"""Unrecorded ranged skill defaults (#362): the gap is published, never invented.

#112's frozen inventory records that the source states a conditional or
cross-skill default for these rows and that neither its source skill nor its
modifier is recorded. Verifying those values needs the frozen
first-printing/2007-errata artifact, which #336 and #191 own. Until then the
gap is published per row and nothing is reconstructed into a runnable roll.
"""

from wayfarer.rules.mundane_skills import audit_report, inventory
from wayfarer.rules.mundane_skills.ranged import (
    CONDITIONAL_DEFAULTS,
    PROCEDURES,
    ranged_scope,
)
from wayfarer.rules.skill_types import ControllingAttribute

ATTRIBUTES = {attribute.value for attribute in ControllingAttribute}


def test_no_ranged_row_invents_a_default_it_does_not_record() -> None:
    """The regression guard: every ranged default is an attribute default."""
    for identifier, procedure in PROCEDURES.items():
        for default in procedure.defaults:
            assert default.target in ATTRIBUTES, (identifier, default.target)
            assert default.modifier <= 0, identifier
    # The rows that record no default at all still record none.
    assert PROCEDURES["skill:bolas"].defaults == ()
    assert PROCEDURES["skill:net"].defaults == ()


def test_every_blocked_row_publishes_what_is_missing() -> None:
    blocked = {
        identifier
        for identifier, procedure in PROCEDURES.items()
        if CONDITIONAL_DEFAULTS in procedure.transferred
    }
    assert blocked
    published = {
        identifier for identifier, scope in ranged_scope() if scope.id == "unrecorded-default"
    }
    # Only a bound row publishes scope; an unbound one would still be a blocker.
    assert published == {i for i in blocked if PROCEDURES[i].implemented} == blocked
    for identifier, scope in ranged_scope():
        if scope.id != "unrecorded-default":
            continue
        assert scope.owner_issue == 362
        assert "Neither the skill it comes from nor its modifier is recorded" in scope.detail
        specialty = PROCEDURES[identifier].specialty
        if specialty is not None:
            # A cross-specialty default names the family it would run between.
            assert f"the {specialty.family} specialties" in scope.detail


def test_the_blocker_stays_and_keeps_its_owners() -> None:
    entries = {e.id: e for e in inventory()}
    for identifier, procedure in PROCEDURES.items():
        if CONDITIONAL_DEFAULTS not in procedure.transferred:
            continue
        entry = entries[identifier]
        assert CONDITIONAL_DEFAULTS in entry.blockers
        # #383 owns the source reconciliation; #362 owns these rows' share of it.
        assert entry.blocker_owners[CONDITIONAL_DEFAULTS] == (383, 362)
        assert {383, 362} <= set(entry.followup_issues)


def test_the_gap_reaches_the_certification_report() -> None:
    scope = audit_report()["transferred_procedure_scope"]
    assert isinstance(scope, list)
    gaps = [row for row in scope if row["id"] == "unrecorded-default"]
    assert len(gaps) == len(
        [p for p in PROCEDURES.values() if CONDITIONAL_DEFAULTS in p.transferred]
    )
    assert all(row["owner_issue"] == 362 and row["detail"] for row in gaps)
