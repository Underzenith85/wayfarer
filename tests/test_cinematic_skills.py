"""Independent expectations for Basic Set cinematic skills, B180-228."""

from dataclasses import replace

import pytest
from test_statistics import gurps_draft, profile_package

from wayfarer.character.compiler import CharacterCompiler, Purchase
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.rules.catalog import (
    CampaignPolicy,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.cinematic_skills import BINDINGS, PROFILE, package
from wayfarer.rules.recovery_types import FatigueStatus
from wayfarer.rules.skill_types import ControllingAttribute as A
from wayfarer.rules.skill_types import Difficulty as D
from wayfarer.rules.skill_types import SkillSpec
from wayfarer.rules.supernatural import inventory
from wayfarer.simulation.cinematic_skills import (
    CinematicSkillCommand,
    apply_cinematic_skill,
    visible_history,
)
from wayfarer.simulation.resources import Pool, ResourceState
from wayfarer.world import Entity, EntityKind, World

SKILLS = {
    "blind-fighting",
    "body-control",
    "breaking-blow",
    "captivate",
    "computer-hacking",
    "enthrallment",
    "flying-leap",
    "immovable-stance",
    "invisibility-art",
    "kiai",
    "light-walk",
    "mental-strength",
    "musical-influence",
    "persuade",
    "power-blow",
    "pressure-points",
    "pressure-secrets",
    "push",
    "suggest",
    "sway-emotions",
    "throwing-art",
    "weird-science",
    "zen-archery",
}


def compiler() -> CharacterCompiler:
    base = profile_package(PROFILE)
    cinematic = package()
    existing = {definition.id for definition in base.definitions + cinematic.definitions}
    dependencies = tuple(
        RuleDefinition(
            "skill:" + key,
            DefinitionKind.SKILL,
            key.replace("-", " ").title(),
            cinematic.sources[0].id,
            None,
            ImplementationStatus.IMPLEMENTED,
            hooks=("character.gurps-skill", "check.target"),
            skill=SkillSpec(attribute, difficulty, "B168-228"),
        )
        for key, attribute, difficulty in (
            ("breath-control", A.HT, D.HARD),
            ("meditation", A.WILL, D.HARD),
            ("public-speaking", A.IQ, D.AVERAGE),
            ("jumping", A.DX, D.EASY),
            ("acrobatics", A.DX, D.HARD),
            ("stealth", A.DX, D.AVERAGE),
            ("bow", A.DX, D.AVERAGE),
            ("musical-instrument", A.IQ, D.HARD),
            ("singing", A.HT, D.EASY),
        )
        if "skill:" + key not in existing
    )
    combined = replace(
        base,
        id="package:test-cinematic-skills",
        definitions=base.definitions + cinematic.definitions + dependencies,
        sources=base.sources + cinematic.sources,
    )
    policy = CampaignPolicy(
        "policy:cinematic-skills",
        1,
        1000,
        1000,
        20,
        20,
        frozenset(source.id for source in combined.sources),
        technology_level=8,
        allow_supernatural=True,
    )
    rules = CampaignRules(
        combined.edition,
        (PackagePin(combined.id, combined.version, combined.digest),),
        policy.id,
        policy.version,
    )
    return CharacterCompiler(RulesCatalog((combined,)), rules, policy, statistics_profile=PROFILE)


def approved_weird_science():
    engine = compiler()
    result = engine.compile(gurps_draft(Purchase(definition_id="skill:weird-science", amount=4)))
    assert result.build is not None, result.diagnostics
    return result.build


def world() -> World:
    return World(
        entities=(
            Entity("room", EntityKind.LOCATION, "Room"),
            Entity("inventor", EntityKind.ACTOR, "Inventor", "room"),
            Entity("observer", EntityKind.ACTOR, "Observer", "room"),
        )
    )


def test_exact_skill_set_metadata_and_inventory() -> None:
    assert {binding.key for binding in BINDINGS} == SKILLS
    assert len(package().definitions) == 23
    computer = next(value for value in BINDINGS if value.key == "computer-hacking")
    assert computer.technology_level_required
    rows = {row.id: row for row in inventory().entries if row.id.removeprefix("skill:") in SKILLS}
    assert set(rows) == {"skill:" + value for value in SKILLS}
    assert all(row.blockers == (191,) for row in rows.values())
    assert all(row.evidence == ("tests/test_cinematic_skills.py",) for row in rows.values())


def test_learning_uses_existing_skill_compiler() -> None:
    build = approved_weird_science()
    assert {value.target: value.value for value in build.sheet.values}["skill:weird-science"] == 9
    assert not compiler().compile(gurps_draft(Purchase(definition_id="skill:blind-fighting"))).legal


def test_attempt_is_authorized_private_exactly_once_and_restart_safe() -> None:
    build = approved_weird_science()
    state = ResourceState(
        pools=(
            Pool(
                id="fp:inventor",
                current=10,
                maximum=10,
                fatigue=FatigueStatus(profile_id=PROFILE),
            ),
        )
    )
    command = CinematicSkillCommand(
        id="invent",
        actor_id="inventor",
        expected_revision=0,
        build_revision=build.revision,
        skill_id="skill:weird-science",
        target_actor_id="observer",
    )
    changed, outcome = apply_cinematic_skill(
        state,
        world(),
        build,
        command,
        authorized_actor_id="inventor",
        rng=RecordedDice([3, 3, 3]),
    )
    assert outcome.outcome == "success" and changed.revision == 1
    restarted = ResourceState.model_validate_json(changed.model_dump_json())
    assert apply_cinematic_skill(
        restarted,
        world(),
        build,
        command,
        authorized_actor_id="inventor",
        rng=RecordedDice([]),
    ) == (restarted, outcome)
    assert visible_history(restarted, viewer_actor_id="inventor") == (outcome,)
    assert visible_history(restarted, viewer_actor_id="observer") == ()
    assert visible_history(restarted, viewer_actor_id="observer", gm=True) == (outcome,)
    with pytest.raises(AuthorizationError):
        apply_cinematic_skill(
            state,
            world(),
            build,
            command,
            authorized_actor_id="observer",
            rng=RecordedDice([3, 3, 3]),
        )
    with pytest.raises(ConflictError, match="revision"):
        apply_cinematic_skill(
            state.model_copy(update={"revision": 1}),
            world(),
            build,
            command,
            authorized_actor_id="inventor",
            rng=RecordedDice([3, 3, 3]),
        )


def test_unsupported_focus_and_interruption_fail_before_dice() -> None:
    build = approved_weird_science()
    base = CinematicSkillCommand(
        id="bad",
        actor_id="inventor",
        expected_revision=0,
        build_revision=build.revision,
        skill_id="skill:weird-science",
    )
    with pytest.raises(ValidationError, match="no concentration"):
        apply_cinematic_skill(
            ResourceState(),
            world(),
            build,
            base.model_copy(update={"concentration_turns": 1}),
            authorized_actor_id="inventor",
            rng=RecordedDice([]),
        )
    with pytest.raises(ConflictError, match="interrupted"):
        apply_cinematic_skill(
            ResourceState(),
            world(),
            build,
            base.model_copy(update={"interrupted": True}),
            authorized_actor_id="inventor",
            rng=RecordedDice([]),
        )
