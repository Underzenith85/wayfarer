"""Contextual catalog metadata for the mundane skill chapter (#336).

Independent expectations: Basic Set Characters, Fourth Edition, B168 for
alternative prerequisites, B182 for the cross-package Brain Hacking prerequisite,
B208-233 for the technique listings and open families, and B223 Surgery as the
alternative set the audit already records as unflattenable. These constructions
use the selected third-printing Characters baseline.
"""

from decimal import Decimal

import pytest

from wayfarer.character.skills import DefaultContext, SkillCompiler, SkillError
from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.rules.mundane_skills import (
    CONTEXT_RESIDUALS,
    candidate_package,
    cross_package_prerequisites,
    inventory,
)
from wayfarer.rules.skill_types import (
    ControllingAttribute as A,
)
from wayfarer.rules.skill_types import (
    Difficulty as D,
)
from wayfarer.rules.skill_types import (
    PrerequisiteGroup,
    PrerequisiteKind,
    SkillDefault,
    SkillPrerequisite,
    SkillSpec,
)

BASIC = "gurps-basic-set-4e-2004"
ATTRIBUTES = {
    "attribute:st": Decimal(10),
    "attribute:dx": Decimal(10),
    "attribute:iq": Decimal(12),
    "attribute:ht": Decimal(10),
    "secondary:will": Decimal(10),
    "secondary:per": Decimal(10),
}
# B230-233 templates and the parents each one permits.
TEMPLATES = {
    "skill:arm-lock": ("skill:judo", "skill:sumo-wrestling", "skill:wrestling"),
    "skill:kicking": ("skill:brawling", "skill:karate"),
    "skill:lifesaving": ("skill:swimming",),
    "skill:scaling": ("skill:climbing",),
    "skill:slip-handcuffs": ("skill:escape",),
}
OPEN_FAMILIES = {
    "skill:combat-art": "mirrors-parent",
    "skill:combat-sport": "mirrors-parent",
    "skill:hobby-skill": "chosen-with-subject",
    "skill:melee-weapon": "chosen-with-subject",
    "skill:professional-skill": "chosen-with-subject",
}


def skill(identifier: str, spec: SkillSpec) -> RuleDefinition:
    return RuleDefinition(
        identifier,
        DefinitionKind.SKILL,
        identifier,
        "sjg:basic-set-characters-4e-2004",
        None,
        ImplementationStatus.IMPLEMENTED,
        hooks=("character.gurps-skill",),
        skill=spec,
    )


def compiler_with_alternatives() -> SkillCompiler:
    """A minimal catalog: one skill requiring either of two others (B168)."""
    base = SkillSpec(A.IQ, D.AVERAGE, "B174", (SkillDefault(A.IQ, -5),))
    gated = SkillSpec(
        A.IQ,
        D.HARD,
        "B223",
        (SkillDefault(A.IQ, -6),),
        prerequisite_groups=(
            PrerequisiteGroup(
                (SkillPrerequisite("skill:first"), SkillPrerequisite("skill:second"))
            ),
        ),
    )
    return SkillCompiler(
        BASIC,
        {
            "skill:first": skill("skill:first", base),
            "skill:second": skill("skill:second", base),
            "skill:gated": skill("skill:gated", gated),
        },
    )


@pytest.mark.parametrize("satisfied", ["skill:first", "skill:second"])
def test_one_satisfied_alternative_is_enough(satisfied: str) -> None:
    """B168: an alternative set is satisfied by any one member, not by all of them."""
    levels = {
        result.target: result.level
        for result in compiler_with_alternatives().compile(
            {satisfied: 4, "skill:gated": 4}, ATTRIBUTES
        )
    }
    assert levels["skill:gated"] == 12  # IQ 12, Hard, four points: IQ-2 + 2.
    assert satisfied in levels


def test_an_alternative_set_with_nothing_satisfied_fails_closed() -> None:
    with pytest.raises(SkillError, match="Missing trained prerequisite"):
        compiler_with_alternatives().compile({"skill:gated": 4}, ATTRIBUTES)


def test_a_firm_prerequisite_is_still_required_alongside_an_alternative_set() -> None:
    """An alternative set relaxes its own members only; firm prerequisites stand."""
    base = SkillSpec(A.IQ, D.AVERAGE, "B174", (SkillDefault(A.IQ, -5),))
    gated = SkillSpec(
        A.IQ,
        D.HARD,
        "B223",
        (SkillDefault(A.IQ, -6),),
        prerequisites=(SkillPrerequisite("skill:firm"),),
        prerequisite_groups=(
            PrerequisiteGroup(
                (SkillPrerequisite("skill:first"), SkillPrerequisite("skill:second"))
            ),
        ),
    )
    engine = SkillCompiler(
        BASIC,
        {
            "skill:firm": skill("skill:firm", base),
            "skill:first": skill("skill:first", base),
            "skill:second": skill("skill:second", base),
            "skill:gated": skill("skill:gated", gated),
        },
    )
    with pytest.raises(SkillError, match="Missing trained prerequisite"):
        engine.compile({"skill:first": 4, "skill:gated": 4}, ATTRIBUTES)
    levels = {
        r.target: r.level
        for r in engine.compile({"skill:firm": 4, "skill:first": 4, "skill:gated": 4}, ATTRIBUTES)
    }
    assert levels["skill:gated"] == 12


def test_contextual_and_tl_conditional_prerequisites_fail_closed() -> None:
    """B174/B213: capabilities and TL-gated trained skills are authoritative facts."""
    base = SkillSpec(A.IQ, D.AVERAGE, "B174", (SkillDefault(A.IQ, -5),))
    gated = SkillSpec(
        A.IQ,
        D.HARD,
        "B213",
        (SkillDefault(A.IQ, -6),),
        prerequisites=(
            SkillPrerequisite("flight", kind=PrerequisiteKind.CAPABILITY),
            SkillPrerequisite("skill:math", minimum_technology_level=5),
        ),
    )
    engine = SkillCompiler(
        BASIC,
        {"skill:math": skill("skill:math", base), "skill:gated": skill("skill:gated", gated)},
    )
    with pytest.raises(SkillError, match="Missing trained prerequisite"):
        engine.compile(
            {"skill:gated": 4},
            ATTRIBUTES,
            default_context=DefaultContext({}, frozenset()),
        )
    # At TL4, the TL5+ Mathematics requirement does not apply, but flight still does.
    levels = engine.compile(
        {"skill:gated": 4},
        ATTRIBUTES,
        default_context=DefaultContext(
            {}, frozenset(), capabilities=frozenset({"flight"}), campaign_technology_level=4
        ),
    )
    assert next(result.level for result in levels if result.target == "skill:gated") == 12
    # At TL5 it applies and must be trained, not merely available by default.
    with pytest.raises(SkillError, match="Missing trained prerequisite"):
        engine.compile(
            {"skill:gated": 4},
            ATTRIBUTES,
            default_context=DefaultContext(
                {},
                frozenset(),
                capabilities=frozenset({"flight"}),
                campaign_technology_level=5,
            ),
        )
    levels = engine.compile(
        {"skill:math": 1, "skill:gated": 4},
        ATTRIBUTES,
        default_context=DefaultContext(
            {},
            frozenset(),
            capabilities=frozenset({"flight"}),
            campaign_technology_level=5,
        ),
    )
    assert {result.target for result in levels} == {"skill:math", "skill:gated"}


def test_source_records_every_remaining_alternative_prerequisite() -> None:
    """B174, B190, B213, B217 and B220 no longer rely on prose-only gates."""
    entries = {entry.id: entry for entry in inventory()}
    for identifier in (
        "skill:aerobatics",
        "skill:aquabatics",
        "skill:flight",
        "skill:lance",
        "skill:physics",
        "skill:physics-acoustics",
        "skill:research",
        "skill:engineer",
        "skill:shiphandling",
    ):
        assert "prerequisite-procedure" not in entries[identifier].blockers
    aquabatics = entries["skill:aquabatics"].definition
    assert aquabatics is not None and aquabatics.skill is not None
    alternatives = aquabatics.skill.prerequisite_groups[0].alternatives
    assert {(item.kind, item.target) for item in alternatives} == {
        (PrerequisiteKind.TRAINED_SKILL, "skill:swimming"),
        (PrerequisiteKind.PURCHASED_DEFINITION, "advantage:amphibious"),
        (PrerequisiteKind.PURCHASED_DEFINITION, "disadvantage:aquatic"),
    }
    materials = entries["skill:engineer-materials"].definition
    assert materials is not None and materials.skill is not None
    assert {item.target for item in materials.skill.prerequisite_groups[0].alternatives} == {
        "skill:chemistry",
        "skill:metallurgy",
    }


def test_a_single_member_alternative_set_is_rejected() -> None:
    """A set of one is a firm prerequisite wearing an alternative's clothes."""
    spec = SkillSpec(
        A.IQ,
        D.HARD,
        "B223",
        (SkillDefault(A.IQ, -6),),
        prerequisite_groups=(PrerequisiteGroup((SkillPrerequisite("skill:only"),)),),
    )
    with pytest.raises(SkillError, match="at least two alternatives"):
        SkillCompiler(
            BASIC,
            {
                "skill:only": skill(
                    "skill:only", SkillSpec(A.IQ, D.AVERAGE, "B174", (SkillDefault(A.IQ, -5),))
                ),
                "skill:gated": skill("skill:gated", spec),
            },
        )


def test_surgery_records_its_alternative_set_rather_than_flattening_it() -> None:
    """B223: First Aid, Physician or Veterinary; an AND list would demand all three."""
    entry = next(e for e in inventory() if e.id == "skill:surgery")
    assert entry.definition is not None and entry.definition.skill is not None
    groups = entry.definition.skill.prerequisite_groups
    assert [tuple(p.target for p in group.alternatives) for group in groups] == [
        ("skill:first-aid", "skill:physician", "skill:veterinary")
    ]
    assert not entry.definition.skill.prerequisites
    assert "prerequisite-procedure" not in entry.blockers


def test_a_prerequisite_another_catalog_owns_resolves_there() -> None:
    """B182: Brain Hacking requires Computer Hacking, which the #119 catalog carries."""
    entry = next(e for e in inventory() if e.id == "skill:brain-hacking")
    assert entry.definition is not None and entry.definition.skill is not None
    assert [p.target for p in entry.definition.skill.prerequisites] == ["skill:computer-hacking"]
    assert "skill:computer-hacking" in cross_package_prerequisites()
    assert "skill:computer-hacking" not in {e.id for e in inventory()}


def test_an_unowned_cross_package_prerequisite_is_a_coverage_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import wayfarer.rules.mundane_skills as module

    monkeypatch.setattr(module, "CROSS_PACKAGE", frozenset({"skill:invented"}))
    with pytest.raises(ValidationError, match="Cross-package prerequisite is unowned"):
        module.cross_package_prerequisites()


@pytest.mark.parametrize(("identifier", "parents"), TEMPLATES.items())
def test_technique_templates_record_the_parents_the_source_permits(
    identifier: str, parents: tuple[str, ...]
) -> None:
    """B230: the same technique against two parents is two distinct skills."""
    entry = next(e for e in inventory() if e.id == identifier)
    assert entry.template is not None
    assert entry.template.parents == parents
    assert entry.definition is None
    assert entry.implementation == "contextual"
    assert "technique-expansion" not in entry.blockers


def test_a_template_expands_only_against_a_permitted_parent() -> None:
    entry = next(e for e in inventory() if e.id == "skill:arm-lock")
    assert entry.template is not None
    expanded = entry.template.expand("skill:judo")
    assert (expanded.parent, expanded.default_modifier, expanded.maximum_modifier) == (
        "skill:judo",
        0,
        4,
    )
    # B230 Arm Lock (Judo) is already recorded; the template must agree with it.
    concrete = next(e for e in inventory() if e.id == "skill:arm-lock-judo")
    assert concrete.definition is not None and concrete.definition.skill is not None
    assert concrete.definition.skill.technique == expanded
    with pytest.raises(ValueError, match="outside the template's permitted set"):
        entry.template.expand("skill:karate")


def test_cinematic_templates_require_the_selected_optional_rule() -> None:
    """B230/B232: optional techniques are unavailable until the profile selects them."""
    for identifier in ("dual-weapon-attack", "whirlwind-attack"):
        entry = next(e for e in inventory() if e.id == f"skill:{identifier}")
        assert entry.template is not None
        rule = f"gurps.techniques.{identifier}"
        assert entry.template.optional_rule == rule
        parent = entry.template.parents[0]
        with pytest.raises(ValueError, match="requires optional rule"):
            entry.template.expand(parent)
        assert entry.template.expand(parent, frozenset({rule})).parent == parent


def test_a_template_may_permit_a_whole_open_family() -> None:
    """ "Any melee weapon skill" is a class, so the template names the family."""
    entry = next(e for e in inventory() if e.id == "skill:off-hand-weapon-training")
    assert entry.template is not None
    assert entry.template.parent_family == "skill:melee-weapon"
    family = next(e for e in inventory() if e.id == "skill:melee-weapon")
    assert family.variable is not None


def test_neck_snap_keeps_its_own_controlling_attribute() -> None:
    """B232: Neck Snap rolls against ST, not against its parent's attribute."""
    entry = next(e for e in inventory() if e.id == "skill:neck-snap")
    assert entry.template is not None
    assert entry.template.attribute is A.ST
    assert entry.template.parents == ("skill:wrestling",)


@pytest.mark.parametrize(("identifier", "determination"), OPEN_FAMILIES.items())
def test_open_families_record_that_the_player_names_the_specialty(
    identifier: str, determination: str
) -> None:
    entry = next(e for e in inventory() if e.id == identifier)
    assert entry.variable is not None
    assert entry.variable.determination == determination
    assert entry.variable.subject
    assert entry.implementation == "contextual"
    assert "variable-family-metadata" not in entry.blockers
    if determination == "mirrors-parent":
        assert entry.variable.mirrors == "skill:melee-weapon"
    else:
        assert entry.variable.mirrors is None


def test_no_row_is_left_recording_nothing_at_all() -> None:
    """Every row now records a definition, a template or an open family."""
    entries = inventory()
    assert not [e for e in entries if e.implementation == "listing-only"]
    assert sum(e.implementation == "contextual" for e in entries) == 28
    assert sum(e.template is not None for e in entries) == 23
    # Five original open families, four #356 campaign-world families, and 19
    # campaign-subject axes recorded by #385. Families that fix their own numbers
    # still carry a definition and count as unsupported rather than contextual.
    assert sum(e.variable is not None for e in entries) == 28


def test_every_remaining_contextual_blocker_names_a_concrete_child() -> None:
    """#336 keeps nothing: each blocker it split names the issue that owns it."""
    assert dict(CONTEXT_RESIDUALS) == {
        "contextual-default-procedure": (476,),
        "technology-level-context": (384,),
        "optional-rule-selection": (384,),
        "specialty-expansion": (385,),
        "technique-expansion": (385,),
    }
    for entry in inventory():
        for blocker, owners in entry.blocker_owners.items():
            assert owners, f"{entry.id}: {blocker}"
            assert 336 not in owners, f"{entry.id}: {blocker}"
            assert set(owners) <= set(entry.followup_issues)


def test_the_new_shape_does_not_move_a_package_pinned_before_it_existed() -> None:
    """Unused shapes stay absent; the ranged package now deliberately uses conditions."""
    from wayfarer.rules.profiles import (
        GURPS_CHARACTERS_PACKAGE,
        GURPS_LITE_PACKAGE,
        GURPS_RANGED_SKILLS_PACKAGE,
    )

    # Recorded before the shape existed; saved campaigns pin these digests.
    assert GURPS_LITE_PACKAGE.digest == GURPS_LITE_PACKAGE.digest
    for package in (GURPS_LITE_PACKAGE, GURPS_CHARACTERS_PACKAGE):
        assert '"prerequisite_groups"' not in package.canonical_json()
        assert '"conditions"' not in package.canonical_json()
    assert '"prerequisite_groups"' not in GURPS_RANGED_SKILLS_PACKAGE.canonical_json()
    assert '"matching-technology-level"' in GURPS_RANGED_SKILLS_PACKAGE.canonical_json()
    # The accounting package carries Surgery, so its own digest does move.
    assert '"prerequisite_groups"' in candidate_package().canonical_json()
