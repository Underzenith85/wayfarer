"""Campaign-authored Biology, Disguise, Geography and Geology subjects (#390).

Expected rolls are transcribed independently from Basic Set Characters B168-B169,
B180, B187 and B198 into ``fixtures/gurps/open_technology_specialties.json``.
"""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.skills import DefaultContext, SkillCompiler
from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    PackagePin,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.gurps_characters import definitions as statistic_definitions
from wayfarer.engine.rules.profiles import GURPS_BASIC_PROFILE
from wayfarer.engine.rules.skills.mundane import PROFILE, inventory
from wayfarer.engine.rules.skills.mundane.technology.attempts import (
    Operator,
    Situation,
    attempt,
    replay,
)
from wayfarer.engine.rules.skills.mundane.technology.inventory import PROCEDURES
from wayfarer.engine.rules.skills.mundane.technology.specialties import (
    CampaignTechnologySpecialties,
    CampaignTechnologySubject,
)
from wayfarer.engine.rules.types.skill import ControllingAttribute
from wayfarer.errors import ValidationError

FIXTURE = Path("tests/fixtures/gurps/open_technology_specialties.json")
Case = dict[str, object]
OPEN = {"skill:biology", "skill:disguise", "skill:geography", "skill:geology"}


def load_cases() -> list[Case]:
    data = json.loads(FIXTURE.read_text())
    assert data["baseline_id"] == BASELINE_ID
    assert data["profile"] == PROFILE
    assert data["owner"] == 390
    return cast(list[Case], data["cases"])


def registry(*pairs: tuple[str, str]) -> CampaignTechnologySpecialties:
    return CampaignTechnologySpecialties(
        tuple(CampaignTechnologySubject(family, subject) for family, subject in pairs)
    )


@pytest.mark.parametrize("case", load_cases(), ids=lambda case: str(case["id"]))
def test_each_open_family_executes_its_sourced_result(case: Case) -> None:
    configured = registry((cast(str, case["family"]), cast(str, case["subject"])))
    given = cast(dict[str, object], case["input"])
    expected = cast(dict[str, object], case["expected"])
    result = attempt(
        Operator(
            cast(str, expected["definition_id"]),
            cast(int, given["level"]),
            cast(int, given["operator_tl"]),
        ),
        Situation(cast(int, given["task_tl"]), cast(bool, given["familiar"])),
        rng=RecordedDice(cast(list[int], given["dice"])),
        campaign_specialties=configured,
    )
    assert result.reference == case["reference"]
    assert result.procedure_id == expected["definition_id"]
    assert result.check.effective_target == expected["target"]
    assert result.check.total == expected["total"]
    assert result.check.margin == expected["margin"]
    assert result.check.outcome.value == expected["outcome"]
    assert result.dispatch.value == expected["dispatch"]
    assert result.effect.value == expected["effect"]
    assert result.units == expected["units"]
    assert result.unit == expected["unit"]
    assert replay(result, configured) == result


def test_campaign_registry_derives_identity_and_catalog_owned_mechanics() -> None:
    configured = registry(
        ("skill:biology", "Earthlike"),
        ("skill:disguise", "Human Culture"),
        ("skill:geography", "The Inner Sea"),
        ("skill:geology", "Earthlike"),
    )
    catalog = {entry.id: entry.definition for entry in inventory() if entry.definition}
    definitions = {entry.id: entry for entry in configured.definitions(catalog)}
    assert set(definitions) == {
        "skill:biology-earthlike",
        "skill:disguise-human-culture",
        "skill:geography-the-inner-sea",
        "skill:geology-earthlike",
    }
    for selection in configured.selections():
        definition = definitions[selection.definition_id]
        parent = catalog[selection.family]
        assert definition.name == selection.name
        assert definition.skill is not None and parent.skill is not None
        assert replace(definition.skill, specialty=None) == parent.skill
        task = PROCEDURES[selection.family].task
        assert task is not None
        assert definition.hooks[1] == task.dispatch.value
    assert not set(definitions) & PROCEDURES.keys()
    biology = catalog["skill:biology"]
    assert biology is not None
    with pytest.raises(ValidationError, match="exact catalog family"):
        configured.definitions(catalog | {biology.id: replace(biology, point_cost=1)})


def test_compiler_uses_campaign_defaults_between_materialized_specialties() -> None:
    configured = registry(
        ("skill:biology", "Earthlike"),
        ("skill:biology", "Gas Giant"),
        ("skill:geography", "Earthlike"),
        ("skill:geology", "Earthlike"),
    )
    catalog = {entry.id: entry.definition for entry in inventory() if entry.definition}
    definitions = configured.definitions(catalog)
    compiler = SkillCompiler(PROFILE, {entry.id: entry for entry in definitions})
    levels = {
        result.target: result
        for result in compiler.compile(
            {"skill:biology-gas-giant": 4, "skill:geography-earthlike": 4},
            {attribute.value: Decimal(10) for attribute in ControllingAttribute},
            default_context=DefaultContext(
                {
                    "skill:biology-gas-giant": 8,
                    "skill:geography-earthlike": 8,
                    "skill:geology-earthlike": 8,
                },
                frozenset(),
                campaign_technology_level=8,
                campaign_defaults=configured.default_selections(),
            ),
        )
    }
    assert levels["skill:biology-earthlike"].level == 5
    assert levels["skill:biology-earthlike"].default_from == "skill:biology-gas-giant"
    assert levels["skill:geology-earthlike"].level == 6
    assert levels["skill:geology-earthlike"].default_from == "skill:geography-earthlike"


def test_character_sheet_accepts_only_explicitly_migrated_campaign_subjects() -> None:
    configured = registry(("skill:biology", "Earthlike"))
    parents = tuple(
        entry.definition for entry in inventory() if entry.id in OPEN and entry.definition
    )
    assert len(parents) == 4
    source = GURPS_BASIC_PROFILE.catalog.package(GURPS_BASIC_PROFILE.rules.packages[0]).sources[0]
    package = RulesPackage(
        "package:test-open-specialty",
        "1.0.0",
        "gurps-4e-2004",
        (source,),
        statistic_definitions(PROFILE) + parents,
    )
    pin = PackagePin(package.id, package.version, package.digest)
    rules = CampaignRules("gurps-4e-2004", (pin,), "test-open-specialty", 1)
    policy = CampaignPolicy(
        "test-open-specialty",
        1,
        200,
        50,
        20,
        20,
        frozenset({source.id}),
        technology_level=8,
    )
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        rules,
        policy,
        statistics_profile=PROFILE,
        campaign_skill_specialties=configured,
    )
    draft = CharacterDraft(
        name="Xenobiologist",
        purchases=tuple(
            Purchase(definition_id=f"attribute:{name}", amount=10)
            for name in ("st", "dx", "iq", "ht")
        )
        + (Purchase(definition_id="skill:biology-earthlike", amount=4, technology_level=8),),
    )
    result = compiler.compile(draft)
    assert result.legal and result.build is not None
    assert {value.target for value in result.build.sheet.values} >= {"skill:biology-earthlike"}
    unknown = draft.model_copy(
        update={
            "purchases": draft.purchases[:-1]
            + (Purchase(definition_id="skill:biology-oceanic", amount=4, technology_level=8),)
        }
    )
    rejected = compiler.compile(unknown)
    assert not rejected.legal
    assert rejected.diagnostics[-1].code == "definition.unknown"


def test_undeclared_and_invalid_subjects_fail_closed() -> None:
    configured = registry(("skill:biology", "Earthlike"))
    with pytest.raises(ValidationError, match="outside the campaign's declared subjects"):
        attempt(
            Operator("skill:biology-oceanic", 12, 8),
            Situation(8),
            rng=RecordedDice([3, 3, 3]),
            campaign_specialties=configured,
        )
    with pytest.raises(ValidationError, match="open technology family"):
        registry(("skill:engineering", "Civil"))
    with pytest.raises(ValidationError, match="trimmed"):
        registry(("skill:biology", " Earthlike"))
    with pytest.raises(ValidationError, match="Duplicate"):
        registry(("skill:biology", "Earthlike"), ("skill:biology", "EARTHLIKE"))
