"""Parent-specific combat technique procedures (#340)."""

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.skills.mundane.melee import WEAPON_CLASSES
from wayfarer.engine.rules.skills.mundane.procedures import Performer, Situation, attempt, replay
from wayfarer.engine.rules.skills.mundane.techniques import PROCEDURES, definitions
from wayfarer.errors import ValidationError


def performer(identifier: str) -> Performer:
    procedure = PROCEDURES[identifier]
    template = procedure.template
    spec = procedure.spec()
    technique = spec.technique if spec else None
    if template is not None:
        parent = template.parents[0] if template.parents else next(iter(WEAPON_CLASSES))
        return Performer(identifier, 12 + template.default_modifier, 12, parent)
    assert technique is not None
    return Performer(identifier, 12 + technique.default_modifier, 12, technique.parent)


def situation(identifier: str) -> Situation:
    task = PROCEDURES[identifier].task
    assert task is not None
    return Situation(frozenset(task.required_context), resistance=10)


def test_all_twenty_rows_are_bound_without_erasing_template_context() -> None:
    rows = [row for row in inventory() if row.procedure_owner == 340]
    assert len(rows) == 20
    assert all(row.bound and not row.blockers for row in rows)
    assert sum(row.implementation == "implemented" for row in rows) == 2
    assert sum(row.implementation == "contextual" for row in rows) == 18
    assert {definition.id for definition in definitions()} == {
        "skill:arm-lock-judo",
        "skill:kicking-karate",
    }


@pytest.mark.parametrize(
    ("identifier", "dispatch", "effect", "reference"),
    (
        ("skill:arm-lock", "combat.unarmed", "apply-arm-lock", "B230-233"),
        ("skill:disarming", "combat.melee-attack", "disarm-opponent", "B230-233"),
        ("skill:feint", "combat.tactical", "impose-defense-penalty", "B230-233"),
        ("skill:horse-archery", "combat.ranged-attack", "shoot-from-mount", "B230-233"),
        ("skill:retain-weapon", "combat.active-defense", "retain-weapon", "B230-233"),
        ("skill:kicking-karate", "combat.unarmed", "karate-kick", "B231"),
    ),
)
def test_selected_source_rows_have_distinct_contracts(
    identifier: str,
    dispatch: str,
    effect: str,
    reference: str,
) -> None:
    procedure = PROCEDURES[identifier]
    assert procedure.task is not None
    assert (procedure.dispatch, procedure.task.effect, procedure.reference) == (
        dispatch,
        effect,
        reference,
    )


def test_every_nonoptional_technique_executes_and_replays() -> None:
    for identifier, procedure in PROCEDURES.items():
        template = procedure.template
        if template is not None and template.optional_rule is not None:
            continue
        task = procedure.task
        assert task is not None
        dice = [1, 1, 1, 6, 6, 6]
        result = attempt(
            PROCEDURES,
            performer(identifier),
            situation(identifier),
            rng=RecordedDice(dice),
        )
        assert result.succeeded and result.effect == task.effect
        assert replay(result) == result


@pytest.mark.parametrize("name", ("dual-weapon-attack", "whirlwind-attack"))
def test_optional_techniques_require_explicit_profile_selection(name: str) -> None:
    identifier = "skill:" + name
    rule = "gurps.techniques." + name
    with pytest.raises(ValidationError, match="requires optional rule"):
        attempt(
            PROCEDURES,
            performer(identifier),
            situation(identifier),
            rng=RecordedDice([1, 1, 1]),
        )
    result = attempt(
        PROCEDURES,
        performer(identifier),
        situation(identifier),
        rng=RecordedDice([1, 1, 1]),
        optional_rules=frozenset({rule}),
    )
    assert result.succeeded


def test_parent_level_context_and_family_membership_fail_closed() -> None:
    with pytest.raises(ValidationError, match="explicit parent"):
        attempt(
            PROCEDURES,
            Performer("skill:kicking", 10),
            situation("skill:kicking"),
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="does not match"):
        attempt(
            PROCEDURES,
            Performer("skill:off-hand-weapon-training", 8, 12, "skill:accounting"),
            situation("skill:off-hand-weapon-training"),
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ValidationError, match="outside its parent-specific range"):
        attempt(
            PROCEDURES,
            Performer("skill:kicking", 13, 12, "skill:karate"),
            situation("skill:kicking"),
            rng=RecordedDice([3, 3, 3]),
        )


def test_capability_is_registered_as_partial() -> None:
    declared = CAPABILITIES["gurps.combat.technique_procedures"]
    assert declared.status is CoverageStatus.PARTIAL and declared.owner_issue == 340
