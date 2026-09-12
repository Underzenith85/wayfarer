"""Whole-entry combat skill procedures (#339)."""

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus
from wayfarer.engine.rules.skills.mundane import inventory
from wayfarer.engine.rules.skills.mundane.melee import (
    PROCEDURES,
    definitions,
    require_mode,
    require_shield,
)
from wayfarer.engine.rules.skills.mundane.procedures import (
    Performer,
    Resolution,
    Situation,
    attempt,
    replay,
)
from wayfarer.errors import ValidationError

PROFILE = "gurps-basic-set-4e-2004"
FAMILIES = {
    "skill:fast-draw": 8,
    "skill:shield": 3,
    "skill:strategy": 3,
}


def test_every_owned_row_is_bound_with_no_remaining_mechanics_blocker() -> None:
    rows = [row for row in inventory() if row.procedure_owner == 339]
    assert len(rows) == 56
    assert all(row.bound for row in rows)
    assert all(not row.blockers for row in rows)
    assert {row.implementation for row in rows} == {"implemented", "contextual"}
    assert {row.id for row in rows if row.implementation == "contextual"} == {
        "skill:combat-art",
        "skill:combat-sport",
        "skill:melee-weapon",
    }


def test_finite_families_require_a_recorded_specialty() -> None:
    for identifier, count in FAMILIES.items():
        procedure = PROCEDURES[identifier]
        assert len(procedure.specialties) == count
        assert procedure.implemented and not procedure.dispatchable
        with pytest.raises(ValidationError, match="concrete specialty"):
            attempt(PROCEDURES, Performer(identifier, 12), Situation(), rng=RecordedDice([3] * 3))


@pytest.mark.parametrize(
    ("identifier", "reference", "difficulty", "dispatch", "effect"),
    (
        ("skill:broadsword", "B208", "average", "combat.melee-attack", "strike-with-broadsword"),
        ("skill:judo", "B203", "hard", "combat.unarmed", "throw-grapple-or-parry"),
        ("skill:shield-standard", "B220", "easy", "combat.active-defense", "block-with-shield"),
        ("skill:fast-draw-knife", "B194", "easy", "combat.ready", "ready-knife"),
        ("skill:tactics", "B224", "hard", "combat.tactical", "gain-tactical-advantage"),
        ("skill:soldier", "B221", "average", "noncombat.approach", "perform-routine-military-duty"),
    ),
)
def test_selected_source_rows_have_distinct_executable_contracts(
    identifier: str,
    reference: str,
    difficulty: str,
    dispatch: str,
    effect: str,
) -> None:
    procedure = PROCEDURES[identifier]
    spec = procedure.spec()
    task = procedure.task
    assert spec is not None and task is not None
    assert (spec.reference, spec.difficulty.value) == (reference, difficulty)
    assert (procedure.dispatch, task.effect) == (dispatch, effect)


def test_every_concrete_procedure_executes_and_replays() -> None:
    for procedure in PROCEDURES.values():
        if not procedure.dispatchable:
            continue
        task = procedure.task
        assert task is not None
        conditions = set(task.required_context)
        if procedure.subject:
            conditions.add("subject-selected")
        rolls = [3, 3, 3, 6, 6, 6] if task.resolution is Resolution.QUICK_CONTEST else [3] * 3
        result = attempt(
            PROCEDURES,
            Performer(procedure.id, 12),
            Situation(frozenset(conditions), resistance=10),
            rng=RecordedDice(rolls),
        )
        assert result.succeeded and result.effect == task.effect
        assert replay(result) == result


def test_melee_mode_and_shield_authoring_fail_closed() -> None:
    assert (
        require_mode(
            PROFILE,
            "skill:broadsword",
            ranged=False,
            hands=1,
            parry=True,
            fencing=False,
            unbalanced=True,
        )
        is PROCEDURES["skill:broadsword"]
    )
    assert require_shield(PROFILE, "skill:shield") is PROCEDURES["skill:shield-standard"]
    with pytest.raises(ValidationError, match="exact Basic Set profile"):
        require_mode(
            "gurps-lite-4e-2004",
            "skill:broadsword",
            ranged=False,
            hands=1,
            parry=True,
            fencing=False,
            unbalanced=False,
        )
    with pytest.raises(ValidationError, match="grip"):
        require_mode(
            PROFILE,
            "skill:two-handed-sword",
            ranged=False,
            hands=1,
            parry=True,
            fencing=False,
            unbalanced=False,
        )
    with pytest.raises(ValidationError, match="Fencing"):
        require_mode(
            PROFILE,
            "skill:rapier",
            ranged=False,
            hands=1,
            parry=True,
            fencing=False,
            unbalanced=False,
        )
    with pytest.raises(ValidationError, match="melee weapon mode"):
        require_mode(
            PROFILE,
            "skill:boxing",
            ranged=False,
            hands=1,
            parry=True,
            fencing=False,
            unbalanced=False,
        )


def test_only_concrete_rows_publish_definitions_and_capability_is_partial() -> None:
    published = {definition.id for definition in definitions()}
    assert published
    assert not set(FAMILIES) & published
    assert not {"skill:combat-art", "skill:combat-sport", "skill:melee-weapon"} & published
    capability = CAPABILITIES["gurps.combat.melee_weapon_skills"]
    assert capability.status is CoverageStatus.PARTIAL
    assert capability.owner_issue == 339
