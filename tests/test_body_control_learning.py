"""B235/B244–245 actual purchased learning, with an acyclic five-spell witness."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Compilation, Purchase
from wayfarer.engine.rules.catalog import ImplementationStatus, RuleDefinition, RulesCatalog
from wayfarer.engine.rules.magic.body_control import package
from wayfarer.engine.rules.magic.body_control_learning import prerequisite_failures
from wayfarer.engine.rules.magic.colleges import PROFILE
from wayfarer.engine.rules.magic.movement import package as movement_package

LOWER = ("itch", "spasm", "pain", "clumsiness", "hinder")
HIGHER = ("paralyze-limb", "wither-limb", "deathtouch")


def compile_spells(
    spells: tuple[str, ...], *, magery: int = 2, override: RuleDefinition | None = None
) -> Compilation:
    sources = (package(), movement_package())
    definitions = {d.id: d for p in sources for d in p.definitions}
    if override is not None:
        definitions[override.id] = override
    base = profile_package(PROFILE, *definitions.values())
    combined = replace(
        base, sources=tuple({s.id: s for p in (base, *sources) for s in p.sources}.values())
    )
    original = profile_compiler(PROFILE, package=combined)
    compiler = CharacterCompiler(
        RulesCatalog((combined,)),
        original.rules,
        replace(original.policy, point_budget=1000, skill_ceiling=40, allow_supernatural=True),
        statistics_profile=PROFILE,
    )
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=magery),
        *(Purchase(definition_id="spell:" + key) for key in spells),
    )
    return compiler.compile(draft)


@pytest.mark.parametrize(
    "lower", [LOWER, ("itch", "spasm", "pain", "haste", "hinder", "rooted-feet")]
)
def test_full_deathtouch_learning_has_approved_acyclic_source_route(lower: tuple[str, ...]) -> None:
    result = compile_spells((*lower, *HIGHER))
    assert result.legal and result.build is not None, result.diagnostics
    learned = {v.target for v in result.build.sheet.values}
    assert all("spell:" + key in learned for key in (*lower, *HIGHER))
    # IQ 10 + Magery 2, one point in each IQ/Hard spell, gives skill 10.
    assert all(
        v.value == 10
        for v in result.build.sheet.values
        if v.target in {"spell:" + key for key in (*lower, *HIGHER)}
    )


@pytest.mark.parametrize("higher", [("paralyze-limb",), HIGHER])
def test_four_lower_spells_cannot_count_self_or_dependent_higher_purchases(
    higher: tuple[str, ...],
) -> None:
    result = compile_spells((*LOWER[:-1], *higher))
    assert not result.legal and result.build is None
    assert any(d.code == "spell.prerequisite" for d in result.diagnostics)


def test_five_other_purchases_without_pain_do_not_satisfy_paralyze() -> None:
    result = compile_spells(
        ("itch", "spasm", "clumsiness", "hinder", "rooted-feet", "paralyze-limb")
    )
    assert not result.legal and result.build is None


def test_haste_is_hinder_alternative_but_not_a_body_control_count() -> None:
    result = compile_spells(("itch", "spasm", "pain", "haste", "hinder", "paralyze-limb"))
    assert not result.legal and result.build is None


def test_wither_requires_actual_magery_two() -> None:
    result = compile_spells((*LOWER, *HIGHER), magery=1)
    assert not result.legal and result.build is None


@pytest.mark.parametrize("change", ["manual", "no-skill", "wrong-college", "foreign-source"])
def test_counterfeit_lower_metadata_does_not_supply_a_count_witness(change: str) -> None:
    definition = next(d for d in package().definitions if d.id == "spell:hinder")
    match change:
        case "manual":
            definition = replace(definition, status=ImplementationStatus.MANUAL)
        case "no-skill":
            definition = replace(definition, skill=None)
        case "wrong-college":
            definition = replace(
                definition,
                hooks=tuple(
                    "spell-college:fire" if h.startswith("spell-college:") else h
                    for h in definition.hooks
                ),
            )
        case _:
            definition = replace(definition, source_id="foreign:unreviewed")
    definitions = {d.id: d for d in package().definitions}
    definitions[definition.id] = definition
    purchases = {"spell:" + key: 100 for key in (*LOWER, *HIGHER)}
    assert prerequisite_failures(definitions, purchases) == ("spell:paralyze-limb",)


def test_large_point_purchases_do_not_multiply_four_distinct_witnesses() -> None:
    definitions = {d.id: d for d in package().definitions}
    purchases = {"spell:" + key: 100 for key in (*LOWER[:-1], *HIGHER)}
    assert prerequisite_failures(definitions, purchases) == ("spell:paralyze-limb",)


def test_foreign_paralyze_definition_does_not_activate_printed_private_policy() -> None:
    definitions = {d.id: d for d in package().definitions}
    definitions["spell:paralyze-limb"] = replace(
        definitions["spell:paralyze-limb"], source_id="foreign:other-game"
    )
    assert prerequisite_failures(definitions, {"spell:paralyze-limb": 1}) == ()
