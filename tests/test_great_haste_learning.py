"""B251 Great Haste construction uses actual IQ and the opted-in source pin."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.magic.colleges import college_package
from wayfarer.engine.rules.magic.movement import BINDINGS, COLLEGE, ISSUE, package
from wayfarer.engine.rules.types.skill import Difficulty, PrerequisiteKind
from wayfarer.engine.simulation.magic.spells import PROFILE


@pytest.mark.parametrize(
    "iq,magery,haste,legal",
    [
        (12, 1, True, True),
        (11, 2, True, False),
        (11, 3, True, False),
        (12, 0, True, False),
        (12, 1, False, False),
    ],
)
def test_source_prerequisites_use_actual_iq(iq: int, magery: int, haste: bool, legal: bool) -> None:
    spells = package(great_haste=True)
    compiled = profile_compiler(PROFILE, package=profile_package(PROFILE, *spells.definitions))
    compiled = CharacterCompiler(
        RulesCatalog((profile_package(PROFILE, *spells.definitions),)),
        compiled.rules,
        replace(compiled.policy, point_budget=1000, allow_supernatural=True),
        statistics_profile=PROFILE,
    )
    purchases = [
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="spell:great-haste", amount=4),
    ]
    if magery:
        purchases.append(Purchase(definition_id="trait:magery", amount=magery))
    if haste:
        purchases.append(Purchase(definition_id="spell:haste", amount=4))
    draft = gurps_draft(*purchases)
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": iq}) if p.definition_id == "attribute:iq" else p
                for p in draft.purchases
            )
        }
    )
    result = compiled.compile(draft)
    assert (result.build is not None) == legal, result.diagnostics
    definition = next(d for d in spells.definitions if d.id == "spell:great-haste")
    assert definition.skill is not None and definition.skill.difficulty is Difficulty.VERY_HARD


def test_default_movement_package_retains_legacy_pin_and_definitions() -> None:
    assert package() == college_package(ISSUE, COLLEGE, BINDINGS)
    assert package().version == "1.0.0"
    assert package(great_haste=True).version == "1.1.0"


def test_iq_minimum_uses_existing_raw_purchase_metadata_without_public_enum_growth() -> None:
    assert tuple(kind.value for kind in PrerequisiteKind) == (
        "trained-skill",
        "purchased-definition",
        "capability",
    )
    definition = next(
        d for d in package(great_haste=True).definitions if d.id == "spell:great-haste"
    )
    assert definition.skill is not None
    iq = next(p for p in definition.skill.prerequisites if p.target == "attribute:iq")
    assert iq.kind is PrerequisiteKind.PURCHASED_DEFINITION
    assert iq.minimum == 12
