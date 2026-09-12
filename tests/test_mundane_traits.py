"""Independent construction values, Characters 4e third printing, B23-29/35-165.

Numeric metadata evidence does not certify runtime effects. Template totals use
the real CharacterCompiler, not fixture
values generated from the catalog under test.
"""

from dataclasses import replace
from typing import Literal

import pytest
from pydantic import ValidationError as SchemaError
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.templates import (
    Selection,
    Template,
    TemplateCatalog,
    TemplateChoice,
    TemplateOption,
    representative_templates,
)
from wayfarer.engine.rules.catalog import ImplementationStatus, RulesCatalog, RulesPackage
from wayfarer.engine.rules.traits.base import TraitOptions, cost
from wayfarer.engine.rules.traits.mundane import (
    PROFILE,
    Vocabulary,
    audit_report,
    candidate_package,
    inventory,
    validate_inventory,
)
from wayfarer.engine.rules.traits.mundane.runtime import (
    APPEARANCE_BINDINGS,
    REPUTATION_BINDINGS,
    SUPPORTED_HOOKS,
)
from wayfarer.errors import ValidationError


def combined_package() -> RulesPackage:
    package = candidate_package()
    base = profile_package(PROFILE)
    return replace(
        base,
        sources=base.sources + package.sources,
        definitions=base.definitions + package.definitions,
    )


def compiler() -> CharacterCompiler:
    return profile_compiler(PROFILE, package=combined_package())


def runtime_compiler() -> CharacterCompiler:
    """A campaign that supplies every bound hook; unbound effects stay off."""
    base = compiler()
    return CharacterCompiler(
        RulesCatalog((combined_package(),)),
        base.rules,
        base.policy,
        statistics_profile=PROFILE,
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )


@pytest.mark.parametrize(
    ("identifier", "levels", "expected", "page"),
    [
        ("appearance-hideous", 1, -16, 21),
        ("appearance-ugly", 1, -8, 21),
        ("appearance-unattractive", 1, -4, 21),
        ("appearance-average", 1, 0, 21),
        ("appearance-attractive", 1, 4, 21),
        ("appearance-handsome", 1, 12, 21),
        ("appearance-very-handsome", 1, 16, 21),
        ("reputation-bravery", 4, 20, 27),
        ("reputation-cruelty", 4, -20, 27),
        ("ambidexterity", 1, 5, 39),
        ("charisma", 3, 15, 41),
        ("combat-reflexes", 1, 15, 43),
        ("eidetic-memory", 1, 5, 51),
        ("photographic-memory", 1, 10, 51),
        ("fit", 1, 5, 55),
        ("very-fit", 1, 15, 55),
        ("high-pain-threshold", 1, 10, 59),
        ("night-vision", 9, 9, 71),
        ("acute-hearing", 2, 4, 35),
        ("status", 2, 10, 28),
        ("low-status", 2, -10, 28),
        ("wealth-dead-broke", 1, -25, 25),
        ("wealth-poor", 1, -15, 25),
        ("wealth-struggling", 1, -10, 25),
        ("wealth-average", 1, 0, 25),
        ("wealth-comfortable", 1, 10, 25),
        ("wealth-wealthy", 1, 20, 25),
        ("wealth-very-wealthy", 1, 30, 25),
        ("wealth-filthy-rich", 1, 50, 25),
        ("rank-watch", 2, 10, 29),
        ("rank-replaces-status-watch", 2, 20, 30),
        ("courtesy-rank-watch", 2, 2, 29),
        ("language-trade-spoken", 2, 2, 24),
        ("language-trade-written", 3, 3, 24),
        ("culture-foreign", 1, 1, 23),
        ("ally-associate", 1, 5, 36),
        ("contact-associate", 1, 2, 44),
        ("patron-associate", 1, 10, 72),
        ("dependent-associate", 1, -5, 131),
        ("enemy-associate", 1, -10, 135),
        ("sense-of-duty-small-group", 1, -5, 153),
        ("sense-of-duty-all-living", 1, -20, 153),
        ("perk-penetrating-voice", 1, 1, 101),
        ("quirk-careful", 1, -1, 163),
    ],
)
def test_independent_construction_costs(
    identifier: str, levels: int, expected: int, page: int
) -> None:
    entries = inventory()
    entry = next(e for e in entries if e.id == f"trait:{identifier}")
    definition = entry.definition(entries)
    assert entry.page == page
    assert definition.point_cost is not None and definition.trait_rules is not None
    assert cost(definition.point_cost, levels, TraitOptions(), definition.trait_rules) == expected


@pytest.mark.parametrize(("rating", "expected"), [(6, -10), (9, -7), (12, -5), (15, -2)])
def test_curiosity_uses_existing_self_control_cost(
    rating: Literal[6, 9, 12, 15], expected: int
) -> None:
    result = compiler().compile(
        gurps_draft(
            Purchase(definition_id="trait:curious", trait=TraitOptions(self_control=rating))
        )
    )
    assert result.spent == expected
    assert not result.legal and result.build is None
    assert "trait.runtime_unavailable" in {d.code for d in result.diagnostics}


@pytest.mark.parametrize(
    ("first", "second"),
    [
        ("appearance-handsome", "appearance-average"),
        ("appearance-attractive", "appearance-ugly"),
        ("fit", "very-fit"),
        ("eidetic-memory", "photographic-memory"),
        ("status", "low-status"),
        ("wealth-poor", "wealth-wealthy"),
        ("shyness-mild", "shyness-severe"),
        ("rank-watch", "rank-replaces-status-watch"),
        ("rank-replaces-status-watch", "status"),
    ],
)
def test_existing_compiler_rejects_trait_combinations(first: str, second: str) -> None:
    result = compiler().compile(
        gurps_draft(
            Purchase(definition_id=f"trait:{first}"), Purchase(definition_id=f"trait:{second}")
        )
    )
    assert "purchase.exclusion" in {d.code for d in result.diagnostics}


def test_inventory_package_and_audit_reconcile() -> None:
    entries = inventory()
    package = candidate_package()
    RulesCatalog((package,))
    report = audit_report()
    assert report["total"] == len(entries) == len(package.definitions)
    assert {e.category for e in entries} == {
        "advantage",
        "disadvantage",
        "perk",
        "quirk",
        "background",
    }
    assert {e.id for e in entries} == {d.id for d in package.definitions}
    assert all(e.followup_issues for e in entries)
    implemented = {
        d.id for d in package.definitions if d.status is ImplementationStatus.IMPLEMENTED
    }
    assert implemented == {
        "trait:appearance-hideous",
        "trait:appearance-ugly",
        "trait:appearance-unattractive",
        "trait:appearance-average",
        "trait:appearance-attractive",
        "trait:appearance-handsome",
        "trait:reputation-bravery",
        "trait:reputation-cruelty",
        "trait:ambidexterity",
        "trait:combat-reflexes",
        "trait:fit",
        "trait:very-fit",
        "trait:high-pain-threshold",
        "trait:night-vision",
        "trait:temperature-tolerance",
        "trait:rapid-healing",
        "trait:very-rapid-healing",
        "trait:acute-hearing",
        "trait:acute-taste-smell",
        "trait:acute-touch",
        "trait:acute-vision",
        "trait:bad-temper",
        "trait:charisma",
        "trait:curious",
        "trait:low-status",
        "trait:overconfidence",
        "trait:status",
        "trait:voice",
        "trait:eidetic-memory",
        "trait:photographic-memory",
        "trait:single-minded",
        "trait:versatile",
        "trait:shyness-mild",
        "trait:shyness-severe",
        "trait:shyness-crippling",
        "trait:perk-penetrating-voice",
        "trait:honesty",
        "trait:truthfulness",
        "trait:language-talent",
        "trait:wealth-dead-broke",
        "trait:wealth-poor",
        "trait:wealth-struggling",
        "trait:wealth-average",
        "trait:wealth-comfortable",
        "trait:wealth-wealthy",
        "trait:wealth-very-wealthy",
        "trait:wealth-filthy-rich",
        "trait:wealth-multimillionaire-1",
        "trait:wealth-multimillionaire-2",
        "trait:wealth-multimillionaire-3",
        "trait:language-trade-spoken",
        "trait:language-trade-written",
        "trait:culture-foreign",
        "trait:rank-watch",
        "trait:rank-replaces-status-watch",
        "trait:courtesy-rank-watch",
    } | set(APPEARANCE_BINDINGS) | set(REPUTATION_BINDINGS)
    assert report["available"] == len(implemented)
    assert all(
        d.status is ImplementationStatus.UNSUPPORTED
        for d in package.definitions
        if d.id not in implemented
    )
    unbound = report["unbound_effects"]
    assert isinstance(unbound, tuple)
    assert "trait.associated_npc" in unbound and "trait.rank" not in unbound
    bound = next(e for e in entries if e.id == "trait:voice")
    assert bound.blockers == ()
    assert any(e.obligations for e in entries)
    assert candidate_package().digest == package.digest
    with pytest.raises(ValidationError, match="Duplicate"):
        validate_inventory(entries + entries[:1])
    with pytest.raises(ValidationError, match="prerequisite"):
        validate_inventory((replace(entries[0], prerequisites=("trait:missing",)),))


def test_unbound_effects_never_activate_and_bound_ones_need_their_campaign_hooks() -> None:
    without_hooks, engine = compiler(), runtime_compiler()
    for entry in inventory():
        draft = gurps_draft(
            Purchase(
                definition_id=entry.id,
                trait=TraitOptions(self_control=12) if entry.self_control else None,
            )
        )
        unavailable = without_hooks.compile(draft)
        assert unavailable.build is None
        assert ("definition.not_implemented" in {d.code for d in unavailable.diagnostics}) is (
            not entry.implemented
        )
        assert "trait.runtime_unavailable" in {d.code for d in unavailable.diagnostics}
        result = engine.compile(draft)
        if entry.id == "trait:very-rapid-healing":
            assert result.build is None
            assert "trait.prerequisite_ht" in {d.code for d in result.diagnostics}
            continue
        assert (result.build is not None) is entry.implemented
        if not entry.implemented:
            assert "definition.not_implemented" in {d.code for d in result.diagnostics}


def test_background_identity_is_pinned_and_cannot_supply_costs() -> None:
    a = Vocabulary(languages=("trade", "diplomatic"), people=("friend",))
    b = Vocabulary(languages=("diplomatic", "trade"), people=("friend",))
    assert candidate_package(a).digest == candidate_package(b).digest
    assert candidate_package(a).digest != candidate_package().digest
    assert "trait:language-diplomatic-spoken" in {e.id for e in inventory(a)}
    for value in ({"languages": ["trade", "trade"]}, {"languages": ["../escape"]}, {"cost": 1}):
        with pytest.raises(SchemaError):
            Vocabulary.model_validate(value)
    with pytest.raises(SchemaError):
        inventory(a.model_copy(update={"languages": ("trade", "trade")}))


def test_templates_use_real_compiler_and_keep_unavailable_effects_blocked() -> None:
    templates = TemplateCatalog(representative_templates(), compiler())
    human = templates.preview(gurps_draft(), ("template:human",))
    assert human.compilation.spent == 0 and human.compilation.legal
    adapted = templates.preview(gurps_draft(), ("template:low-light-human",))
    assert adapted.compilation.spent == 4  # Night Vision 2 [2] + Acute Hearing 1 [2].
    assert adapted.compilation.build is None
    assert adapted.template_digest == human.template_digest
    scholar = templates.preview(
        gurps_draft(),
        ("template:human", "template:curious-scholar"),
        (Selection(template_id="template:scholar", choice_id="aptitude", option_ids=("memory",)),),
    )
    assert scholar.compilation.spent == 5  # Single-Minded 5 + Eidetic Memory 5 - Curious 5.
    assert not scholar.compilation.legal and scholar.compilation.build is None
    guard = templates.preview(gurps_draft(), ("template:guard",))
    assert guard.compilation.spent == 15  # Combat Reflexes 15 + Fit 5 - Sense of Duty 5.
    assert not guard.compilation.legal


def test_executable_template_composes_a_legal_build_only_where_hooks_exist() -> None:
    engine = runtime_compiler()
    templates = TemplateCatalog(representative_templates(), engine)
    envoy = templates.preview(gurps_draft(), ("template:envoy",))
    # Charisma 2 [10] + Status 1 [5] + Voice [10] - Overconfidence (12) [5].
    assert envoy.compilation.spent == 20
    assert envoy.compilation.legal and envoy.compilation.build is not None
    assert {p.definition_id for p in envoy.compilation.build.trait_purchases} == {
        "trait:charisma",
        "trait:overconfidence",
        "trait:status",
        "trait:voice",
    }
    with pytest.raises(ValidationError, match="taboo"):
        templates.preview(
            gurps_draft(Purchase(definition_id="trait:shyness-severe")), ("template:envoy",)
        )
    without_hooks = TemplateCatalog(representative_templates(), compiler())
    blocked = without_hooks.preview(gurps_draft(), ("template:envoy",))
    assert blocked.compilation.spent == 20
    assert not blocked.compilation.legal and blocked.compilation.build is None


def test_template_choices_reject_missing_extra_repeated_and_unknown_options() -> None:
    templates = TemplateCatalog(representative_templates(), compiler())
    with pytest.raises(ValidationError, match="selections"):
        templates.preview(gurps_draft(), ("template:scholar",))
    for options in (("unknown",), ("memory", "memory"), ("memory", "creativity")):
        with pytest.raises(ValidationError, match="choice"):
            templates.preview(
                gurps_draft(),
                ("template:scholar",),
                (
                    Selection(
                        template_id="template:scholar", choice_id="aptitude", option_ids=options
                    ),
                ),
            )
    with pytest.raises(ValidationError, match="Unknown"):
        templates.preview(gurps_draft(), ("template:invented",))


def test_template_duplicates_cycles_and_taboo_traits_fail_closed() -> None:
    engine = compiler()
    for templates in (
        (Template(id="a", kind="racial", includes=("b",)),),
        (Template(id="a", kind="racial", includes=("a",)),),
        (Template(id="a", kind="racial", purchases=(Purchase(definition_id="trait:missing"),)),),
    ):
        with pytest.raises(ValidationError):
            TemplateCatalog(templates, engine)
    taboo = TemplateCatalog((Template(id="a", kind="racial", taboo_traits=("trait:fit",)),), engine)
    with pytest.raises(ValidationError, match="taboo"):
        taboo.preview(gurps_draft(Purchase(definition_id="trait:fit")), ("a",))
    with pytest.raises(ValidationError, match="once"):
        taboo.preview(gurps_draft(), ("a", "a"))
    duplicate = TemplateCatalog(representative_templates(), engine)
    with pytest.raises(ValidationError, match="reconciliation"):
        duplicate.preview(gurps_draft(Purchase(definition_id="trait:fit")), ("template:guard",))


def test_racial_templates_cannot_make_mandatory_traits_optional() -> None:
    with pytest.raises(SchemaError, match="optional"):
        Template(
            id="a",
            kind="racial",
            choices=(
                TemplateChoice(
                    id="a",
                    options=(
                        TemplateOption(
                            id="a",
                            purchases=(Purchase(definition_id="trait:fit"),),
                        ),
                    ),
                ),
            ),
        )


def test_template_digest_changes_with_composition_and_exact_rules_pin() -> None:
    engine = compiler()
    original = Template(id="a", kind="racial")
    changed = Template(id="a", kind="racial", purchases=(Purchase(definition_id="trait:fit"),))
    assert TemplateCatalog((original,), engine).digest != TemplateCatalog((changed,), engine).digest
    with pytest.raises(ValidationError, match="profile"):
        TemplateCatalog((original,), profile_compiler("gurps-lite-4e-2004"))
