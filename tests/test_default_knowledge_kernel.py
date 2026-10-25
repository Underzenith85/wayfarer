"""B173 societal permission restricts defaults independently from acquisition."""

from dataclasses import replace
from decimal import Decimal

import pytest
from test_mundane_traits import combined_package
from test_unusual_background_admission import decision

from wayfarer.engine.character.skills import DefaultContext, SkillCompiler
from wayfarer.engine.rules.catalog import ImplementationStatus
from wayfarer.engine.rules.skills.mundane.melee import definitions, revise_package_defaults
from wayfarer.engine.rules.traits.unusual_background import bind_unusual_background
from wayfarer.engine.rules.types.background_admission import UNUSUAL_BACKGROUND_ID
from wayfarer.engine.rules.types.skill import ControllingAttribute as A

PROFILE = "gurps-basic-set-4e-2004"
ATTRIBUTES = {a.value: Decimal(12) for a in A}


def test_unknown_denies_unpaid_defaults_but_never_rewrites_learned_points_or_credit() -> None:
    selected = {
        d.id: d
        for d in definitions(default_revision=2)
        if d.id in ("skill:staff", "skill:spear", "skill:polearm")
    }
    compiler = SkillCompiler(PROFILE, selected)
    legacy = compiler.compile({}, ATTRIBUTES)
    assert next(level.level for level in legacy if level.target == "skill:staff") == 7
    unknown = DefaultContext({}, frozenset(), known_default_skills=frozenset())
    assert compiler.compile({}, ATTRIBUTES, default_context=unknown) == ()
    known = replace(unknown, known_default_skills=frozenset({"skill:staff"}))
    lawful = compiler.compile({}, ATTRIBUTES, default_context=known)
    assert len(lawful) == 1 and lawful[0].target == "skill:staff" and lawful[0].level == 7
    points = {"skill:staff": 1, "skill:spear": 4}
    trained = {v.target: v for v in compiler.compile(points, ATTRIBUTES)}
    denied = {v.target: v for v in compiler.compile(points, ATTRIBUTES, default_context=unknown)}
    assert denied.keys() == points.keys()
    assert all(denied[key] == trained[key] for key in points)
    assert denied["skill:staff"].default_credit > 0


@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("paid", [False, True])
def test_true_societal_fact_cannot_replace_background_permission_or_its_payment(
    allowed: bool, paid: bool
) -> None:
    base = combined_package()
    staff = next(d for d in definitions() if d.id == "skill:staff")
    base = replace(base, definitions=base.definitions + (staff,))
    gated = bind_unusual_background(
        base,
        decision(10, allowed=allowed).model_copy(update={"benefits": (staff.id,)}),
        package_id="package:societal-staff",
        version="1.0.0",
    )
    fresh = revise_package_defaults(gated, version="2.0.0")
    selected = {d.id: d for d in fresh.definitions if d.id in (staff.id, UNUSUAL_BACKGROUND_ID)}
    compiler = SkillCompiler(
        PROFILE,
        selected,
        available=frozenset(
            d.id for d in selected.values() if d.status is ImplementationStatus.IMPLEMENTED
        ),
    )
    context = DefaultContext(
        {},
        frozenset(),
        known_default_skills=frozenset({staff.id}),
        purchased_definition_ids=frozenset({UNUSUAL_BACKGROUND_ID}) if paid else frozenset(),
        purchased_definition_levels={UNUSUAL_BACKGROUND_ID: 1} if paid else {},
    )
    levels = compiler.compile({}, ATTRIBUTES, default_context=context)
    assert bool(levels) == (paid and allowed)


def test_gm_knowing_a_no_default_skill_cannot_create_a_skill_edge() -> None:
    selected = {
        d.id: d
        for d in definitions(default_revision=2)
        if d.id in ("skill:karate", "skill:brawling")
    }
    compiler = SkillCompiler(PROFILE, selected)
    context = DefaultContext({}, frozenset(), known_default_skills=frozenset(selected))
    assert compiler.compile({}, ATTRIBUTES, default_context=context) == ()
