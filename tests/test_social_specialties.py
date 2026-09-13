"""B196 and B218 required social specialties (#366)."""

import json
from dataclasses import replace
from decimal import Decimal
from pathlib import Path
from typing import cast

import pytest

from wayfarer.engine.character.compiler import (
    CharacterCompiler,
    CharacterDraft,
    Purchase,
    ValidatedBuild,
)
from wayfarer.engine.character.traits.social import skill_conditions
from wayfarer.engine.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    PackagePin,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_characters import definitions as statistic_definitions
from wayfarer.engine.rules.profiles import GURPS_BASIC_PROFILE
from wayfarer.engine.rules.skills.mundane import PROFILE, inventory, source_index
from wayfarer.engine.rules.skills.mundane.social.attempts import SocialSkillContext, resolve
from wayfarer.engine.rules.skills.mundane.social.inventory import (
    FORTUNE_TELLING_SPECIALTIES,
    PROCEDURES,
    require_procedure,
)
from wayfarer.engine.rules.skills.mundane.social.specialties import (
    CampaignSocialSpecialties,
    CampaignSocialSubject,
)
from wayfarer.engine.rules.skills.mundane.specialties import CampaignSkillSpecialties
from wayfarer.engine.rules.skills.mundane.technology.specialties import (
    CampaignTechnologySpecialties,
)
from wayfarer.engine.rules.traits.mundane.runtime import Audience
from wayfarer.engine.simulation.campaign.npcs import NPCSocialTrigger
from wayfarer.errors import ValidationError

FIXTURE = Path("tests/fixtures/gurps/social_specialties.json")


def data() -> dict[str, object]:
    return cast(dict[str, object], json.loads(FIXTURE.read_text()))


def registry(subject: str = "High Society") -> CampaignSocialSpecialties:
    return CampaignSocialSpecialties((CampaignSocialSubject("skill:savoir-faire", subject),))


@pytest.mark.parametrize(
    "case",
    cast(list[dict[str, object]], data()["fortune_telling"]),
    ids=lambda case: str(case["specialty"]),
)
def test_each_fortune_telling_specialty_executes_its_sourced_result(
    case: dict[str, object],
) -> None:
    identifier = f"skill:fortune-telling-{case['specialty']}"
    trace = resolve(
        PROFILE,
        identifier,
        SocialSkillContext(cast(int, case["skill"]), cast(int, case["resistance"])),
        rng=RecordedDice(cast(list[int], case["dice"])),
    )
    assert trace.reference == "B196"
    assert trace.verdict.value == case["verdict"]
    assert trace.effect.id == case["effect"]


def test_fortune_telling_family_is_non_rollable_and_source_indexed() -> None:
    expected = tuple(f"skill:fortune-telling-{name}" for name, _ in FORTUNE_TELLING_SPECIALTIES)
    family = PROCEDURES["skill:fortune-telling"]
    assert family.implemented and not family.dispatchable and family.specialties == expected
    with pytest.raises(ValidationError, match="requires a concrete specialty"):
        require_procedure(PROFILE, family.id)
    rows = {entry.id: entry for entry in inventory()}
    indexed = {entry.id: entry for entry in source_index().entries}
    for identifier in expected:
        row = rows[identifier]
        assert row.available and row.definition is not None and row.definition.skill is not None
        assert row.definition.skill.specialty is not None
        assert indexed[identifier.removeprefix("skill:")].parent == "fortune-telling"


def test_campaign_savoir_faire_specialty_executes_only_in_its_milieu() -> None:
    case = cast(dict[str, object], data()["savoir_faire"])
    configured = registry(cast(str, case["subject"]))
    identifier = cast(str, case["definition_id"])
    trace = resolve(
        PROFILE,
        identifier,
        SocialSkillContext(
            cast(int, case["skill"]),
            cast(int, case["resistance"]),
            conditions=frozenset(cast(list[str], case["conditions"])),
        ),
        rng=RecordedDice(cast(list[int], case["dice"])),
        campaign_specialties=configured,
    )
    assert trace.reference == "B218"
    assert trace.verdict.value == case["verdict"]
    assert trace.effect.id == case["effect"]
    assert trace.reaction == case["reaction"]
    with pytest.raises(ValidationError, match="prerequisite is not met"):
        resolve(
            PROFILE,
            identifier,
            SocialSkillContext(cast(int, case["skill"]), cast(int, case["resistance"])),
            rng=RecordedDice([]),
            campaign_specialties=configured,
        )


def test_savoir_faire_subject_materializes_through_an_explicit_package_migration() -> None:
    configured = registry()
    catalog = {entry.id: entry.definition for entry in inventory() if entry.definition}
    definition = configured.definitions(cast(dict[str, object], catalog))[0]  # type: ignore[arg-type]
    parent = catalog["skill:savoir-faire"]
    assert parent is not None and definition.skill is not None and parent.skill is not None
    assert definition.id == "skill:savoir-faire-high-society"
    assert replace(definition.skill, specialty=None) == parent.skill

    source = GURPS_BASIC_PROFILE.catalog.package(GURPS_BASIC_PROFILE.rules.packages[0]).sources[0]
    package = RulesPackage(
        "package:test-social-specialty",
        "1.0.0",
        "gurps-4e-2004",
        (source,),
        statistic_definitions(PROFILE) + (parent,),
    )
    pin = PackagePin(package.id, package.version, package.digest)
    rules = CampaignRules("gurps-4e-2004", (pin,), "test-social-specialty", 1)
    policy = CampaignPolicy(
        "test-social-specialty",
        1,
        200,
        50,
        20,
        20,
        frozenset({source.id}),
    )
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        rules,
        policy,
        statistics_profile=PROFILE,
        campaign_skill_specialties=CampaignSkillSpecialties(
            (CampaignTechnologySpecialties(), configured)
        ),
    )
    draft = CharacterDraft(
        name="Courtier",
        purchases=tuple(
            Purchase(definition_id=f"attribute:{name}", amount=10)
            for name in ("st", "dx", "iq", "ht")
        )
        + (Purchase(definition_id=definition.id, amount=4),),
    )
    compiled = compiler.compile(draft)
    assert compiled.legal and compiled.build is not None
    levels = {value.target: value.value for value in compiled.build.sheet.values}
    assert levels[definition.id] == Decimal(12)
    assert "skill:savoir-faire" not in compiler.definitions
    assert compiler.social_skill_specialties is configured


def test_scenario_and_campaign_validators_fail_closed() -> None:
    configured = registry()
    identifier = "skill:savoir-faire-high-society"
    trigger = NPCSocialTrigger(kind="skill", subject_id="npc", skill_id=identifier)
    assert trigger.skill_id == identifier
    assert skill_conditions(
        cast(ValidatedBuild, object()),
        {},
        identifier,
        Audience(classes=("high-society",)),
        configured,
    ) == frozenset({"matching-milieu"})
    assert not skill_conditions(
        cast(ValidatedBuild, object()), {}, identifier, Audience(classes=("military",)), configured
    )
    with pytest.raises(ValueError, match="derived from the selected specialty"):
        NPCSocialTrigger(
            kind="skill",
            subject_id="npc",
            skill_id=identifier,
            conditions=("matching-milieu",),
        )
    with pytest.raises(ValidationError, match="Unknown social skill procedure"):
        require_procedure(PROFILE, identifier)
    with pytest.raises(ValidationError, match="Unknown social skill procedure"):
        require_procedure(PROFILE, "skill:savoir-faire-military", configured)
    with pytest.raises(ValidationError, match="open social family"):
        registry("High Society").__class__(
            (CampaignSocialSubject("skill:streetwise", "Underworld"),)
        )
