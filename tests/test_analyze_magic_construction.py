"""B235/B249 purchased IQ/Hard Knowledge chain, separate from runtime effects."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Compilation, Purchase
from wayfarer.engine.rules.catalog import ImplementationStatus, RulesCatalog
from wayfarer.engine.rules.magic.colleges import PROFILE
from wayfarer.engine.rules.magic.knowledge import package
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, PrerequisiteKind

CHAIN = ("detect-magic", "identify-spell", "analyze-magic")


def compiled(spells: tuple[tuple[str, int], ...], *, magery: int = 1) -> Compilation:
    source = package()
    base = profile_package(PROFILE, *source.definitions)
    sources = {s.id: s for p in (base, source) for s in p.sources}
    combined = replace(base, sources=tuple(sources.values()))
    original = profile_compiler(PROFILE, package=combined)
    compiler = CharacterCompiler(
        RulesCatalog((combined,)),
        original.rules,
        replace(original.policy, point_budget=1000, skill_ceiling=40, allow_supernatural=True),
        statistics_profile=PROFILE,
    )
    purchases: tuple[Purchase, ...] = (Purchase(definition_id="trait:magery-0"),)
    if magery:
        purchases += (Purchase(definition_id="trait:magery", amount=magery),)
    draft = gurps_draft(
        *purchases,
        *(Purchase(definition_id="spell:" + key, amount=points) for key, points in spells),
    )
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 11}) if p.definition_id == "attribute:iq" else p
                for p in draft.purchases
            )
        }
    )
    return compiler.compile(draft)


@pytest.mark.parametrize("points,expected", [(1, 10), (2, 11), (4, 12), (8, 13), (12, 14)])
def test_approved_analyze_magic_learning_has_actual_skill_10_to_14(
    points: int, expected: int
) -> None:
    result = compiled((("detect-magic", 1), ("identify-spell", 1), ("analyze-magic", points)))
    assert result.legal and result.build is not None, result.diagnostics
    assert (
        next(v.value for v in result.build.sheet.values if v.target == "spell:analyze-magic")
        == expected
    )


@pytest.mark.parametrize("index", range(3))
def test_each_knowledge_chain_spell_is_iq_hard_with_purchased_source_prerequisite(
    index: int,
) -> None:
    result = compiled(tuple((key, 1) for key in CHAIN[: index + 1]))
    assert result.build is not None, result.diagnostics
    definition = next(d for d in package().definitions if d.id == "spell:" + CHAIN[index])
    spec = definition.skill
    assert definition.status is ImplementationStatus.IMPLEMENTED and spec is not None
    assert spec.attribute is ControllingAttribute.IQ and spec.difficulty is Difficulty.HARD
    assert spec.reference == "B249"
    prerequisite = spec.prerequisites[0]
    assert prerequisite.kind is PrerequisiteKind.PURCHASED_DEFINITION
    assert prerequisite.target == ("trait:magery" if index == 0 else "spell:" + CHAIN[index - 1])


@pytest.mark.parametrize("missing", CHAIN[:2])
def test_analyze_requires_every_purchased_link_even_with_high_points(missing: str) -> None:
    result = compiled(tuple((key, 16) for key in CHAIN if key != missing), magery=3)
    assert not result.legal and result.build is None


@pytest.mark.parametrize("index", range(3))
def test_magery_zero_cannot_learn_detect_or_its_descendants(index: int) -> None:
    result = compiled(tuple((key, 16) for key in CHAIN[: index + 1]), magery=0)
    assert not result.legal and result.build is None


def test_other_knowledge_inventory_spells_are_not_promoted() -> None:
    definitions = {d.id: d for d in package().definitions}
    for key in ("aura", "seeker", "trace"):
        definition = definitions["spell:" + key]
        assert definition.skill is None and definition.status is ImplementationStatus.MANUAL
