"""B208 explicit package revision preserves source inheritance and old pins."""

from dataclasses import replace
from decimal import Decimal

import pytest
from test_statistics import profile_package

from wayfarer.engine.character.skills import DefaultContext, SkillCompiler
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog, RulesPackage
from wayfarer.engine.rules.skills.gurps_skills import definitions as core_definitions
from wayfarer.engine.rules.skills.mundane.melee import definitions, revise_package_defaults
from wayfarer.engine.rules.skills.mundane.melee_defaults import MELEE_WEAPON_SKILLS
from wayfarer.engine.rules.types.skill import ControllingAttribute as A
from wayfarer.engine.rules.types.skill import (
    Difficulty,
    PrerequisiteKind,
    SkillDefault,
    SkillPrerequisite,
)
from wayfarer.errors import ValidationError

PROFILE = "gurps-basic-set-4e-2004"


def test_source_declared_weapon_family_inheritance_leaves_no_default_unarmed_untouched() -> None:
    legacy = {d.id: d for d in definitions()}
    fresh = {d.id: d for d in definitions(default_revision=2)}
    assert len(MELEE_WEAPON_SKILLS) == 22
    assert not {"skill:brawling", "skill:lance"} & MELEE_WEAPON_SKILLS
    for key in MELEE_WEAPON_SKILLS:
        spec = fresh[key].skill
        old = legacy[key].skill
        assert spec and old
        penalty = {Difficulty.EASY: -4, Difficulty.AVERAGE: -5, Difficulty.HARD: -6}[
            spec.difficulty
        ]
        expected = SkillDefault(A.DX, penalty)
        assert spec.defaults.count(expected) == 1
        assert all(edge in spec.defaults for edge in old.defaults)
    for key in ("skill:brawling", "skill:karate", "skill:judo"):
        current = fresh[key]
        assert current == legacy[key] and current.skill is not None
        assert not current.skill.defaults
    # B204 expressly excludes Lance from Melee Weapon; its own printed defaults
    # and Riding prerequisite stay byte-for-byte independent of B208 inheritance.
    lance = fresh["skill:lance"]
    assert lance == legacy[lance.id] and lance.skill
    assert lance.skill.defaults == (SkillDefault(A.DX, -5), SkillDefault("skill:spear", -3))
    assert lance.skill.prerequisites == (SkillPrerequisite("skill:riding"),)
    assert definitions() == definitions(default_revision=1)


@pytest.mark.parametrize(
    "key,expected", [("skill:knife", 8), ("skill:staff", 7), ("skill:flail", 6)]
)
def test_actual_weapon_easy_average_hard_defaults_use_source_dx(key: str, expected: int) -> None:
    definition = next(d for d in definitions(default_revision=2) if d.id == key)
    compiler = SkillCompiler(PROFILE, {key: definition})
    levels = compiler.compile({}, {a.value: Decimal(12) for a in A})
    assert next(v for v in levels if v.target == key).level == expected


def package() -> RulesPackage:
    return replace(profile_package(PROFILE), definitions=definitions())


def test_new_package_pin_is_explicit_and_legacy_and_core_definitions_remain_exact() -> None:
    old = package()
    old_json = old.canonical_json()
    new = revise_package_defaults(old, version="2.0.0")
    assert new.id == old.id and new.version != old.version and new.digest != old.digest
    catalog = RulesCatalog((old, new))
    assert catalog.package(PackagePin(old.id, old.version, old.digest)).canonical_json() == old_json
    assert catalog.package(PackagePin(new.id, new.version, new.digest)) == new
    assert old.canonical_json() == old_json
    core = next(d for d in core_definitions(PROFILE) if d.id == "skill:broadsword")
    mixed = replace(old, definitions=tuple(d for d in old.definitions if d.id != core.id) + (core,))
    corrected = revise_package_defaults(mixed, version="2.0.0")
    assert next(d for d in corrected.definitions if d.id == core.id) == core
    with pytest.raises(ValidationError, match="new package version"):
        revise_package_defaults(old, version=old.version)


def test_unknown_customized_default_permissions_cannot_be_bypassed_by_revision() -> None:
    old = package()
    staff = next(d for d in old.definitions if d.id == "skill:staff")
    assert staff.skill
    forbidden = replace(staff, skill=replace(staff.skill, defaults=()))
    customized = replace(
        old, definitions=tuple(forbidden if d.id == staff.id else d for d in old.definitions)
    )
    with pytest.raises(ValidationError, match="customized default permissions"):
        revise_package_defaults(customized, version="2.0.0")


@pytest.mark.parametrize("extra", [SkillDefault(A.IQ, 0), SkillDefault("skill:knife", 0)])
def test_correct_dx_edge_does_not_authenticate_unknown_extra_defaults(extra: SkillDefault) -> None:
    old = package()
    staff = next(d for d in old.definitions if d.id == "skill:staff")
    assert staff.skill
    customized = replace(
        old,
        definitions=(
            replace(staff, skill=replace(staff.skill, defaults=(SkillDefault(A.DX, -5), extra))),
        ),
    )
    with pytest.raises(ValidationError, match="customized default permissions"):
        revise_package_defaults(customized, version="2.0.0")


def test_unavailable_default_stays_unavailable_after_package_revision() -> None:
    old = package()
    fresh = revise_package_defaults(old, version="2.0.0")
    staff = next(d for d in fresh.definitions if d.id == "skill:staff")
    compiler = SkillCompiler(PROFILE, {staff.id: staff}, available=frozenset())
    assert (
        compiler.compile(
            {}, {a.value: Decimal(12) for a in A}, default_context=DefaultContext.empty()
        )
        == ()
    )


def test_added_default_retains_existing_authoritative_acquisition_qualification() -> None:
    old = package()
    staff = next(d for d in old.definitions if d.id == "skill:staff")
    assert staff.skill
    qualified = replace(
        staff,
        skill=replace(
            staff.skill,
            prerequisites=(SkillPrerequisite("staff-qualified", kind=PrerequisiteKind.CAPABILITY),),
        ),
    )
    old = replace(old, definitions=(qualified,))
    new = revise_package_defaults(old, version="2.0.0")
    compiler = SkillCompiler(PROFILE, {staff.id: new.definitions[0]})
    attributes = {a.value: Decimal(12) for a in A}
    assert compiler.compile({}, attributes, default_context=DefaultContext.empty()) == ()
    context = DefaultContext({}, frozenset(), capabilities=frozenset({"staff-qualified"}))
    assert compiler.compile({}, attributes, default_context=context)[0].level == 7


@pytest.mark.parametrize("allowed", [False, True])
@pytest.mark.parametrize("paid", [False, True])
def test_versioned_direct_default_retains_real_background_admission(
    allowed: bool, paid: bool
) -> None:
    from test_mundane_traits import combined_package
    from test_unusual_background_admission import decision

    from wayfarer.engine.rules.catalog import ImplementationStatus
    from wayfarer.engine.rules.traits.unusual_background import bind_unusual_background
    from wayfarer.engine.rules.types.background_admission import UNUSUAL_BACKGROUND_ID

    base = combined_package()
    staff = next(d for d in definitions() if d.id == "skill:staff")
    base = replace(
        base, definitions=tuple(d for d in base.definitions if d.id != staff.id) + (staff,)
    )
    gated = bind_unusual_background(
        base,
        decision(10, allowed=allowed).model_copy(update={"benefits": (staff.id,)}),
        package_id="package:staff-background",
        version="1.0.0",
    )
    fresh = revise_package_defaults(gated, version="2.0.0")
    selected = {d.id: d for d in fresh.definitions if d.id in (staff.id, UNUSUAL_BACKGROUND_ID)}
    available = frozenset(
        d.id for d in selected.values() if d.status is ImplementationStatus.IMPLEMENTED
    )
    compiler = SkillCompiler(PROFILE, selected, available=available)
    context = DefaultContext(
        {},
        frozenset(),
        purchased_definition_ids=frozenset({UNUSUAL_BACKGROUND_ID}) if paid else frozenset(),
        purchased_definition_levels={UNUSUAL_BACKGROUND_ID: 1} if paid else {},
    )
    compiled = compiler.compile({}, {a.value: Decimal(12) for a in A}, default_context=context)
    assert bool(compiled) == (paid and allowed)
    if compiled:
        assert compiled[0].target == staff.id and compiled[0].level == 7
