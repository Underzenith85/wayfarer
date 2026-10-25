"""Literal B344 examples at the current ordinary roll boundary.

These are approved real catalog skills at authored success-roll hosts. They do
not certify B202's timed/resisted interrogation procedure or Alchemy analysis.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.default_roll_evidence import record_native_default
from support.runtime import build_play, build_runtime, seed_campaign
from test_actions import campaign, world
from test_default_knowledge_hosts import adopt
from test_default_roll_source_examples import source_definitions
from test_live_skill_prerequisite_context import reexecute, revision
from test_statistics import profile_compiler, profile_package

from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, CheckRule, Inspect
from wayfarer.engine.simulation.campaign.default_knowledge import DeclareDefaultKnowledge
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.command_inputs import default_knowledge_version

PROFILE = "gurps-basic-set-4e-2004"


async def fixture(
    path: Path,
    backend: str,
    *,
    intimidation_points: int = 0,
    psychology_points: int = 0,
    interrogation_points: int = 0,
    alchemy: bool = False,
) -> tuple[Campaign, PlayService]:
    selected = source_definitions()
    package = profile_package(PROFILE, *selected.values())
    original = profile_compiler(PROFILE)
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog,
        replace(
            original.rules, packages=(PackagePin(package.id, package.version, package.digest),)
        ),
        replace(
            original.policy,
            point_budget=1000,
            skill_ceiling=30,
            technology_level=8,
            allow_supernatural=True,
        ),
        statistics_profile=PROFILE,
    )
    reviewer = PowerReviewer(compiler, PowerPolicy(id="b344-current", version=1), frozenset({"gm"}))
    engine = ActionEngine(
        reviewer,
        ResourceEngine(world(), catalog, compiler.rules, compiler.policy, ()),
        ActionRules(
            id="source-ordinary-skill-roll",
            version=1,
            checks=(
                CheckRule(
                    id="authored-skill-check",
                    action="inspect",
                    target_id="chest",
                    definition_id="skill:alchemy" if alchemy else "skill:interrogation",
                    package_id=package.id,
                    package_version=package.version,
                    duration=1,
                    reveal_fact_ids=("clue",),
                ),
            ),
        ),
    )
    play = build_play(path, engine, backend=backend, seeds=lambda: f"{11:064x}")
    initial = campaign(engine)
    purchases = tuple(
        Purchase(definition_id=f"attribute:{name}", amount=12 if name == "iq" else 10)
        for name in ("st", "dx", "iq", "ht")
    ) + tuple(
        Purchase(definition_id=key, amount=points)
        for key, points in (
            ("skill:intimidation", intimidation_points),
            ("skill:psychology", psychology_points),
            ("skill:interrogation", interrogation_points),
        )
        if points
    )
    proposal = CharacterProposal(
        draft=CharacterDraft(name="Literal B344 example", purchases=purchases)
    )
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=proposal, aware_of=("b", "chest")),),
    )
    actor = state.actors[0]
    assert actor.approval is not None
    build, _ = reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id="a"
    )
    values = {v.target: int(v.value) for v in build.sheet.values}
    assert values["attribute:iq"] == values["secondary:will"] == 12
    if intimidation_points:
        assert values["skill:intimidation"] == (14 if intimidation_points == 8 else 24)
    if psychology_points:
        assert values["skill:psychology"] == 13
    if interrogation_points:
        assert values["skill:interrogation"] == 13
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return initial, play


async def declare(play: PlayService, cid: str, skill: str, *, known: bool = True) -> None:
    await DefaultKnowledgeService(play).execute(
        cid,
        DeclareDefaultKnowledge(
            id="gm-source-fact",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            performer_id="a",
            skill_ids=(skill,),
            society_known=known,
            reason="The GM independently determines whether this real skill is known in the actor society",
        ),
        principal_id="gm",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("intimidation_points,expected", [(8, 11), (48, 21)])
async def test_literal_best_default_and_uncapped_learned_source_reach_current_native_roll(
    tmp_path: Path,
    backend: str,
    intimidation_points: int,
    expected: int,
    record_property: Callable[[str, object], None],
) -> None:
    initial, play = await fixture(
        tmp_path, backend, intimidation_points=intimidation_points, psychology_points=8
    )
    cid = initial["id"]
    # Native B202 definition has IQ-5, trained Intimidation-3, Psychology-4.
    # Literal candidates are max(7,11,9)=11; trained24-3=21 remains uncapped.
    for adopted in (False, True):
        if adopted:
            play.rng = secrets
            await adopt(play, cid)
        before = await play.store.read(cid)
        command = Inspect(
            id="refused-" + str(adopted),
            actor_id="a",
            expected_revision=await revision(play, cid),
            target_id="chest",
        )
        dice = RecordedDice(())
        play.rng = dice
        with pytest.raises(ValidationError, match="default|knowledge|adopt"):
            await play.execute(cid, command, principal_id="a")
        assert await play.store.read(cid) == before and dice.exhausted()
        denied_view = await build_runtime(play).read(cid, principal_id="a")
        denied_characters = denied_view["characters"]
        assert isinstance(denied_characters, tuple)
        assert "skill:interrogation" not in {v["target"] for v in denied_characters[0]["values"]}
    play.rng = secrets
    await declare(play, cid, "skill:interrogation")
    view = await build_runtime(play).read(cid, principal_id="a")
    characters = view["characters"]
    assert isinstance(characters, tuple)
    projected = {v["target"]: v["value"] for v in characters[0]["values"]}
    assert projected["skill:interrogation"] == str(expected)
    assert ("a", "clue") not in play._load(await play.store.read(cid)).world.knowledge
    command = Inspect(
        id="actual-roll",
        actor_id="a",
        expected_revision=await revision(play, cid),
        target_id="chest",
    )
    assert (await play.preview(cid, command, principal_id="a")).status == "feasible"
    result = await play.execute(cid, command, principal_id="a")
    assert result.check and result.check.base_target == result.check.effective_target == expected
    assert result.check.total == 5 and result.check.outcome.succeeded
    assert result.check.rule_id == "check:success"
    assert result.revealed_fact_ids == ("clue",)
    assert await play.execute(cid, command, principal_id="a") == result
    after = play._load(await play.store.read(cid))
    assert after.resources.game_time == 1
    assert ("a", "clue") in after.world.knowledge
    saved = await play.store.command_input(cid, command.id)
    assert saved is not None
    assert default_knowledge_version(saved) == 2
    await reexecute(initial, play, tmp_path / "literal-current-genesis")
    record_native_default(
        f"basic-b344-native-interrogation-source-{14 if intimidation_points == 8 else 24}",
        {
            "skill": "skill:interrogation",
            "iq": 12,
            "intimidation_points": intimidation_points,
            "psychology_points": 8,
            "paid_points": 0,
            "gm_society_known": True,
        },
        {
            "base_target": result.check.base_target,
            "effective_target": result.check.effective_target,
            "ordinary_success": result.check.rule_id == "check:success",
            "total": result.check.total,
            "success": result.check.outcome.succeeded,
            "revealed_fact_ids": list(result.revealed_fact_ids),
            "elapsed_seconds": after.resources.game_time,
        },
        record_property,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("knowledge", ["unadopted", "unknown", "known"])
async def test_actual_learned_interrogation_level_is_invariant_under_society_facts(
    tmp_path: Path,
    backend: str,
    knowledge: Literal["unadopted", "unknown", "known"],
) -> None:
    initial, play = await fixture(tmp_path, backend, interrogation_points=4)
    cid = initial["id"]
    if knowledge != "unadopted":
        await adopt(play, cid)
        await declare(play, cid, "skill:interrogation", known=knowledge == "known")
    before = play._load(await play.store.read(cid))
    actor = before.actors[0]
    view = await build_runtime(play).read(cid, principal_id="a")
    characters = view["characters"]
    assert isinstance(characters, tuple)
    assert {v["target"]: v["value"] for v in characters[0]["values"]}["skill:interrogation"] == "13"
    command = Inspect(
        id="learned-roll", actor_id="a", expected_revision=before.revision, target_id="chest"
    )
    result = await play.execute(cid, command, principal_id="a")
    assert result.check and result.check.base_target == result.check.effective_target == 13
    assert result.check.total == 5 and result.check.outcome.succeeded
    assert result.check.rule_id == "check:success"
    assert result.revealed_fact_ids == ("clue",)
    assert await play.execute(cid, command, principal_id="a") == result
    assert play._load(await play.store.read(cid)).actors[0] == actor
    await reexecute(initial, play, tmp_path / "learned-current-genesis")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_true_alchemy_no_default_is_not_created_by_positive_gm_fact_at_ordinary_host(
    tmp_path: Path,
    backend: str,
    record_property: Callable[[str, object], None],
) -> None:
    initial, play = await fixture(tmp_path, backend, alchemy=True)
    cid = initial["id"]
    await adopt(play, cid)
    await declare(play, cid, "skill:alchemy")
    before = await play.store.read(cid)
    dice = RecordedDice(())
    play.rng = dice
    command = Inspect(
        id="no-invented-default",
        actor_id="a",
        expected_revision=await revision(play, cid),
        target_id="chest",
    )
    result = await play.execute(cid, command, principal_id="a")
    assert result.status == "rejected" and result.code == "check.skill_required"
    assert result.check is None and result.revealed_fact_ids == ()
    assert await play.store.command_input(cid, command.id) is None
    assert await play.store.read(cid) == before and dice.exhausted()
    view = await build_runtime(play).read(cid, principal_id="a")
    characters = view["characters"]
    assert isinstance(characters, tuple)
    assert "skill:alchemy" not in {v["target"] for v in characters[0]["values"]}
    play.rng = secrets
    await reexecute(initial, play, tmp_path / "no-default-positive-fact-genesis")
    record_native_default(
        "basic-b344-native-alchemy-no-default",
        {"skill": "skill:alchemy", "paid_points": 0, "gm_society_known": True},
        {
            "status": result.status,
            "code": result.code,
            "check_absent": result.check is None,
            "revealed_fact_ids": list(result.revealed_fact_ids),
            "state_unchanged": await play.store.read(cid) == before,
            "receipt_absent": await play.store.command_input(cid, command.id) is None,
        },
        record_property,
    )
