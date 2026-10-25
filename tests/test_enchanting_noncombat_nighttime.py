"""#1013: an authored timed approach fits the existing B481 nightly calendar."""

import asyncio
import json
import secrets
from dataclasses import replace
from decimal import Decimal
from fractions import Fraction
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_enchanting_nighttime import advance_work, light
from test_enchanting_projects import setup
from test_enchanting_source import source_recipe
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_statistics import profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import CampaignRules, PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.effects import Effect, Operation
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.skills.gurps_skills import definitions
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, CheckRule, PlayState
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.magic.bindings import SpellChannel
from wayfarer.engine.simulation.magic.enchanting_calendar import enchanting_work_active
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    BeginEnchanting,
    CreateEnchantment,
)
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.social.noncombat import Approach, NoncombatRule, NoncombatRules
from wayfarer.engine.world import Fact
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.noncombat import NoncombatCommand, NoncombatService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.replay import verify_commands

PROFILE = "gurps-basic-set-4e-2004"


async def configured(
    path: Path,
    backend: str = "sqlite",
    *,
    enroll: bool = True,
    retain_source_spells: bool = False,
    grouped: bool = False,
    equipment_modifier: int = 0,
    check_modifier: int = 0,
) -> tuple[Campaign, PlayService, PlayState]:
    original, _, foundation = setup(
        path, learn_source_spells=True, hold_target=bool(equipment_modifier)
    )
    observation = next(d for d in definitions(PROFILE) if d.id == "skill:observation")
    base = profile_package(PROFILE)
    package = replace(
        base,
        sources=base.sources
        + tuple(
            s for s in enchantment_package().sources if s.id not in {p.id for p in base.sources}
        ),
        definitions=(*original.reviewer.compiler.definitions.values(), observation),
    )
    catalog = RulesCatalog((package,))
    old = original.reviewer.compiler
    compiler = CharacterCompiler(
        catalog,
        CampaignRules(
            package.edition,
            (PackagePin(package.id, package.version, package.digest),),
            old.policy.id,
            old.policy.version,
        ),
        replace(old.policy, point_budget=250),
        statistics_profile=PROFILE,
    )
    reviewer = PowerReviewer(compiler, original.reviewer.policy, original.reviewer.gm_ids)
    world = replace(
        foundation.world,
        facts=(
            *foundation.world.facts,
            Fact("night-clue", "forge", "finding", "source of the scrape"),
        ),
    )
    resources = ResourceEngine(
        world,
        catalog,
        compiler.rules,
        compiler.policy,
        tuple(
            spec.model_copy(
                update={
                    "effects": (
                        Effect(
                            "observation-tool",
                            observation.id,
                            Operation.ADD,
                            Decimal(equipment_modifier),
                            spec.definition_id,
                            package.version,
                        ),
                    )
                }
            )
            if spec.definition_id == "equipment:sword" and equipment_modifier
            else spec
            for spec in original.resources.specs.values()
        ),
    )
    enchanting, spells = original.rules.enchanting, original.rules.spells
    assert enchanting is not None and spells is not None
    rules = original.rules.model_copy(
        update={
            "party": PartyRules(id="forge-party", version=1) if grouped else original.rules.party,
            "enchanting": enchanting.model_copy(update={"recipes": (source_recipe("staff"),)}),
            "spells": spells.model_copy(
                update={
                    "execution_version": 2,
                    "channels": (
                        SpellChannel(
                            id="personal-light",
                            actor_id="a",
                            target_id="a",
                            location_id="forge",
                            spell_id="light",
                        ),
                    ),
                }
            ),
            "scenes": SceneRules(
                id="forge",
                version=1,
                scenes=(
                    Scene(id="forge-scene", version=1, location_id="forge", title="The Forge"),
                ),
            ),
            "checks": (
                CheckRule(
                    id="night-observation",
                    action="inspect",
                    target_id="forge",
                    definition_id=observation.id,
                    package_id=package.id,
                    package_version=package.version,
                    duration=10,
                    modifier=check_modifier,
                    reveal_fact_ids=("night-clue",),
                ),
            ),
            "noncombat": NoncombatRules(
                id="night-encounters",
                version=1,
                encounters=(
                    NoncombatRule(
                        id="investigate-noise",
                        scene_id="forge-scene",
                        category="investigation",
                        stakes="Identify the noise before resuming work",
                        required_progress=2,
                        maximum_failures=1,
                        approaches=(
                            Approach(
                                id="look",
                                check_rule_id="night-observation",
                                fatigue_cost=1,
                                failure_hp_cost=1,
                                success_fact_ids=("night-clue",),
                            ),
                        ),
                    ),
                ),
            ),
        }
    )
    engine = ActionEngine(reviewer, resources, rules)
    play = build_play(path, engine, backend=backend, rng=secrets, seeds=lambda: f"{1:064x}")
    initial = campaign(engine)
    actors = tuple(
        ActorSetup(
            actor_id=a.actor_id,
            proposal=a.proposal.model_copy(
                update={
                    "draft": a.proposal.draft.model_copy(
                        update={
                            "purchases": (
                                *(
                                    p
                                    for p in a.proposal.draft.purchases
                                    if enroll
                                    or retain_source_spells
                                    or not p.definition_id.startswith("spell:")
                                ),
                                Purchase(definition_id=observation.id, amount=4),
                            )
                        }
                    )
                }
            ),
        )
        for a in foundation.actors
    )
    genesis = play.initial_state(initial, world, foundation.resources, actors)
    initial["play_json"] = genesis.model_dump_json()
    await seed_campaign(play.store, initial)
    if not enroll:
        return initial, play, genesis
    enchanting_service = EnchantmentService(play)
    cid = initial["id"]
    await enchanting_service.declare_staff(
        cid,
        DeclareStaffConstruction(
            id="staff-fact",
            actor_id="a",
            expected_revision=0,
            construction=StaffConstruction(
                item_id="blade",
                definition_id="equipment:sword",
                form="wand",
                length_yards=Fraction(1),
                material="reed",
                once_living=True,
            ),
        ),
        principal_id="gm",
    )
    await enchanting_service.execute(
        cid,
        CreateEnchantment(
            id="create-staff",
            actor_id="a",
            expected_revision=1,
            project_id="staff-project",
            recipe_id=source_recipe("staff").id,
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        principal_id="gm",
    )
    await enchanting_service.execute(
        cid,
        BeginEnchanting(
            id="work-staff", actor_id="a", expected_revision=2, project_id="staff-project"
        ),
        principal_id="gm",
    )
    return initial, play, genesis


async def start_choice(play: PlayService, cid: str) -> NoncombatCommand:
    state = play._load(await play.store.read(cid))
    await NoncombatService(play).execute(
        cid,
        NoncombatCommand(
            id="start-choice",
            actor_id="a",
            expected_revision=state.revision,
            kind="start_noncombat",
            encounter_id="night-investigation",
            selection_id="investigate-noise",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    return NoncombatCommand(
        id="approach",
        actor_id="a",
        expected_revision=state.revision,
        kind="approach_noncombat",
        encounter_id="night-investigation",
        selection_id="look",
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("at", [28800, 86390])
async def test_night_approach_spends_once_reveals_fact_and_preserves_real_work(
    tmp_path: Path, backend: str, at: int
) -> None:
    initial, play, genesis = await configured(tmp_path, backend)
    cid = initial["id"]
    await advance_work(play, cid, at)
    command = await start_choice(play, cid)
    before = play._load(await play.store.read(cid))
    project = before.resources.enchantment_projects[0]
    assert not enchanting_work_active(before.resources, project)
    first, duplicate = await asyncio.gather(
        *(NoncombatService(play).execute(cid, command, principal_id="a") for _ in range(2))
    )
    assert first == duplicate and first.status == "success"
    assert first.checks[0].effective_target == 17 and first.checks[0].total == 9
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == at + 10
    assert state.resources.enchantment_projects == before.resources.enchantment_projects
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert "night-clue" in {f.id for f in state.world.perspective("a").facts}
    assert project.active_work is not None and project.active_work.due == 1238400
    stored = await play.store.command_input(cid, command.id)
    assert stored is not None and stored.text is not None
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict) and payload["enchanting_rest_generation"] == 1
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await NoncombatService(restarted).execute(cid, command, principal_id="a") == first
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=genesis.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("at", [0, 86391, 86400])
async def test_work_and_crossing_intervals_refuse_before_dice_cost_or_time(
    tmp_path: Path, at: int
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    if at:
        await advance_work(play, cid, at)
    command = await start_choice(play, cid)
    before = await play.store.read(cid)
    dice = RecordedDice(())
    play.rng = dice
    with pytest.raises(ConflictError, match="enchanting work|enchanting work shift"):
        await NoncombatService(play).execute(cid, command, principal_id="a")
    assert dice.exhausted() and await play.store.read(cid) == before


async def test_night_refuses_current_pending_cast_and_unauthorized_or_stale_approach(
    tmp_path: Path,
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    command = await start_choice(play, cid)
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await NoncombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await NoncombatService(play).execute(
            cid,
            command.model_copy(update={"expected_revision": command.expected_revision - 1}),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
    await SpellService(play).execute(
        cid, light("pending-light", command.expected_revision), principal_id="a"
    )
    state = play._load(await play.store.read(cid))
    command = command.model_copy(update={"expected_revision": state.revision})
    before = await play.store.read(cid)
    dice = RecordedDice(())
    play.rng = dice
    with pytest.raises(ConflictError, match="concentrating"):
        await NoncombatService(play).execute(cid, command, principal_id="a")
    assert dice.exhausted() and await play.store.read(cid) == before


async def test_failed_commit_rolls_back_approach_cost_time_and_discovery(tmp_path: Path) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    command = await start_choice(play, cid)
    before = await play.store.read(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
    with pytest.raises(RuntimeError):
        await NoncombatService(failing).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before
    result = await NoncombatService(play).execute(cid, command, principal_id="a")
    assert result.status == "success"


async def test_historical_unmarked_approach_retains_exact_input_on_retry(tmp_path: Path) -> None:
    initial, play, genesis = await configured(tmp_path, enroll=False)
    cid = initial["id"]
    command = await start_choice(play, cid)
    service = NoncombatService(play)
    first = await submit(play, cid, service.plan(command), principal_id="a")
    stored = await play.store.command_input(cid, command.id)
    assert (
        stored is not None
        and stored.text is not None
        and "enchanting_rest_generation" not in json.dumps(replay_payload(stored.text))
    )
    assert await service.execute(cid, command, principal_id="a") == first
    assert await play.store.command_input(cid, command.id) == stored
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=genesis.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "old-reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)


async def test_night_approach_retains_equipment_and_authored_check_modifiers(
    tmp_path: Path,
) -> None:
    initial, play, _ = await configured(tmp_path, equipment_modifier=1, check_modifier=-2)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    command = await start_choice(play, cid)
    result = await NoncombatService(play).execute(cid, command, principal_id="a")
    assert result.checks[0].base_target == 18
    assert result.checks[0].effective_target == 16
    assert result.checks[0].modifiers[0].value == -2


async def test_failed_night_approach_spends_authored_costs_without_revealing_success(
    tmp_path: Path,
) -> None:
    initial, play, _ = await configured(tmp_path)
    cid = initial["id"]
    await advance_work(play, cid, 28800)
    command = await start_choice(play, cid)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice((6, 6, 6))
    result = await NoncombatService(play).execute(cid, command, principal_id="a")
    assert result.status == "failure" and result.failures == 1
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 28810
    assert state.resources.enchantment_projects == before.resources.enchantment_projects
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 9
    assert "night-clue" not in {f.id for f in state.world.perspective("a").facts}
