"""Literal B344 arithmetic through real definitions, plus native default checks.

These examples do not certify whole Lockpicking/Interrogation procedures or
unadopted campaigns. B173 eligibility is explicit, never inferred from TL.
"""

import secrets
from collections.abc import Callable
from dataclasses import replace
from decimal import Decimal
from pathlib import Path

import pytest
from support.default_roll_evidence import record_native_default
from support.runtime import build_play, build_runtime, seed_campaign
from test_actions import campaign, world
from test_default_knowledge_hosts import adopt
from test_live_skill_prerequisite_context import reexecute, revision
from test_statistics import profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.character.skills import DefaultContext, SkillCompiler
from wayfarer.engine.rules.catalog import PackagePin, RuleDefinition, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.gurps_magic import definitions as spell_definitions
from wayfarer.engine.rules.skills.gurps_skills import definitions as core_definitions
from wayfarer.engine.rules.skills.magic_craft import package as magic_craft_package
from wayfarer.engine.rules.skills.mundane.arts import definitions as arts_definitions
from wayfarer.engine.rules.skills.mundane.medicine import definitions as medicine_definitions
from wayfarer.engine.rules.skills.mundane.social.inventory import definitions as social_definitions
from wayfarer.engine.rules.types.skill import ControllingAttribute as A
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, CheckRule, Inspect
from wayfarer.engine.simulation.campaign.default_knowledge import DeclareDefaultKnowledge
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService

PROFILE = "gurps-basic-set-4e-2004"


def source_definitions() -> dict[str, RuleDefinition]:
    wanted = {
        "skill:lockpicking",
        "skill:interrogation",
        "skill:intimidation",
        "skill:psychology",
        "skill:karate",
        "skill:alchemy",
        "spell:light",
    }
    return {
        definition.id: definition
        for definition in (
            *arts_definitions(),
            *medicine_definitions(),
            *social_definitions(),
            *core_definitions(PROFILE),
            *magic_craft_package().definitions,
            *spell_definitions(),
        )
        if definition.id in wanted
    }


def attributes(iq: int) -> dict[str, Decimal]:
    return {a.value: Decimal(iq if a in (A.IQ, A.WILL, A.PER) else 10) for a in A}


@pytest.mark.parametrize("iq,expected", [(10, 5), (25, 15)])
def test_literal_lockpicking_attribute_default_and_rule_of_20(iq: int, expected: int) -> None:
    compiler = SkillCompiler(PROFILE, source_definitions())
    context = DefaultContext({}, frozenset(), known_default_skills=frozenset({"skill:lockpicking"}))
    results = compiler.compile({}, attributes(iq), default_context=context)
    assert len(results) == 1
    assert results[0].target == "skill:lockpicking" and results[0].level == expected
    assert results[0].default_from == A.IQ


@pytest.mark.parametrize("intimidation_points,expected", [(8, 11), (48, 21)])
def test_literal_interrogation_chooses_best_trained_source_without_attribute_cap(
    intimidation_points: int, expected: int
) -> None:
    # IQ/Will12: 8 points in Will/A gives Intimidation14, 8 in IQ/H Psychology13.
    # 48 points gives Intimidation24: skill-source24-3=21 is not Rule-of-20 capped.
    compiler = SkillCompiler(PROFILE, source_definitions())
    context = DefaultContext(
        {}, frozenset(), known_default_skills=frozenset({"skill:interrogation"})
    )
    results = {
        row.target: row
        for row in compiler.compile(
            {"skill:intimidation": intimidation_points, "skill:psychology": 8},
            attributes(12),
            default_context=context,
        )
    }
    assert results["skill:psychology"].level == 13
    assert results["skill:intimidation"].level == expected + 3
    assert results["skill:interrogation"].level == expected
    assert results["skill:interrogation"].default_from == "skill:intimidation"
    assert results["skill:interrogation"].points == 0


def test_literal_no_default_examples_stay_unavailable_even_with_positive_societal_context() -> None:
    definitions = source_definitions()
    compiler = SkillCompiler(PROFILE, definitions)
    known = frozenset({"skill:karate", "skill:alchemy", "spell:light"})
    context = DefaultContext({}, frozenset(), known_default_skills=known)
    assert compiler.compile({}, attributes(25), default_context=context) == ()
    # Knowing the society does not rewrite an actual trained level or its points.
    learned = {"skill:karate": 4, "skill:alchemy": 1, "spell:light": 1}
    assert compiler.compile(learned, attributes(12), default_context=context) == compiler.compile(
        learned, attributes(12), default_context=replace(context, known_default_skills=frozenset())
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("iq,expected", [(10, 5), (25, 15)])
async def test_literal_lockpicking_default_reaches_native_success_roll_and_seed_replay(
    tmp_path: Path,
    backend: str,
    iq: int,
    expected: int,
    record_property: Callable[[str, object], None],
) -> None:
    selected = source_definitions()["skill:lockpicking"]
    package = profile_package(PROFILE, *source_definitions().values())
    base = profile_compiler(PROFILE)
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog,
        replace(base.rules, packages=(PackagePin(package.id, package.version, package.digest),)),
        replace(
            base.policy,
            point_budget=1000,
            attribute_ceiling=25,
            technology_level=8,
            allow_supernatural=True,
        ),
        statistics_profile=PROFILE,
    )
    reviewer = PowerReviewer(
        compiler, PowerPolicy(id="source-defaults", version=1), frozenset({"gm"})
    )
    engine = ActionEngine(
        reviewer,
        ResourceEngine(world(), catalog, compiler.rules, compiler.policy, ()),
        ActionRules(
            id="literal-default-check",
            version=1,
            checks=(
                CheckRule(
                    id="inspect",
                    action="inspect",
                    target_id="chest",
                    definition_id=selected.id,
                    package_id=package.id,
                    package_version=package.version,
                    duration=1,
                    reveal_fact_ids=("clue",),
                ),
            ),
        ),
    )
    play = build_play(tmp_path, engine, backend=backend, seeds=lambda: f"{11:064x}")
    initial = campaign(engine)
    proposal = CharacterProposal(
        draft=CharacterDraft(
            name="Source example",
            purchases=tuple(
                Purchase(definition_id=f"attribute:{name}", amount=iq if name == "iq" else 10)
                for name in ("st", "dx", "iq", "ht")
            ),
        )
    )
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=proposal, aware_of=("chest",)),),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    await adopt(play, cid)
    before = await play.store.read(cid)
    denied = Inspect(
        id="unknown", actor_id="a", expected_revision=await revision(play, cid), target_id="chest"
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="GM-known default"):
        await play.execute(cid, denied, principal_id="a")
    assert await play.store.read(cid) == before and play.rng.exhausted()
    # Adopt is sticky; use the real GM declaration to supply this one fact.
    play.rng = secrets
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="lockpicking-known",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            performer_id="a",
            skill_ids=(selected.id, "skill:karate", "skill:alchemy", "spell:light"),
            society_known=True,
            reason="The GM confirms lockpicking is known in this actor's society",
        ),
        principal_id="gm",
    )
    view = await build_runtime(play).read(cid, principal_id="a")
    characters = view["characters"]
    assert isinstance(characters, tuple)
    usable = {value["target"] for value in characters[0]["values"]}
    assert selected.id in usable
    assert not usable.intersection({"skill:karate", "skill:alchemy", "spell:light"})
    command = denied.model_copy(
        update={"id": "literal-roll", "expected_revision": await revision(play, cid)}
    )
    play.rng = secrets
    result = await play.execute(cid, command, principal_id="a")
    assert result.check and result.check.base_target == result.check.effective_target == expected
    assert result.check.total == 5 and result.check.outcome.succeeded
    assert result.revealed_fact_ids == ("clue",)
    assert await play.execute(cid, command, principal_id="a") == result
    assert play._load(await play.store.read(cid)).resources.game_time == 1
    await reexecute(initial, play, tmp_path / "source-seed-replay")
    record_native_default(
        f"basic-b344-native-lockpicking-iq-{iq}",
        {"skill": selected.id, "iq": iq, "paid_points": 0, "gm_society_known": True},
        {
            "base_target": result.check.base_target,
            "effective_target": result.check.effective_target,
            "ordinary_success": result.check.rule_id == "check:success",
            "total": result.check.total,
            "success": result.check.outcome.succeeded,
            "revealed_fact_ids": list(result.revealed_fact_ids),
            "elapsed_seconds": play._load(await play.store.read(cid)).resources.game_time,
        },
        record_property,
    )
