"""B232 Karate Kicking levels reach actual attacks under a private generation."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_gurps_melee import setup as melee_setup
from test_mundane_traits import combined_package
from test_statistics import BASIC, profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.gurps_skills import definitions
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.traits.unusual_background import bind_unusual_background
from wayfarer.engine.rules.types.background_admission import (
    UNUSUAL_BACKGROUND_ID,
    UnusualBackgroundDecision,
)
from wayfarer.engine.rules.types.skill import Difficulty
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.combat.unarmed.fighters import attack_skill_value
from wayfarer.engine.simulation.combat.unarmed.records import UnarmedSkill
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, StartEncounter, generations
from wayfarer.orchestration.play import ApproveCharacter, PlayService
from wayfarer.persistence.replay import verify_commands

FEATURE = "karate-kicking-technique"
TECHNIQUE = "skill:kicking-karate"


async def prepare(
    path: Path,
    backend: str,
    points: int = 2,
    *,
    background: str | None = None,
    owned: bool = False,
    altered: str | None = None,
    configured: bool = True,
) -> tuple[str, PlayService, Campaign]:
    source_cid, original = await melee_setup(
        path / "source",
        "gurps-basic-set-4e-2004",
        human=True,
        unarmed_fixture=True,
        start_encounter=False,
    )
    source = original._load(await original.store.read(source_cid))
    old = original.engine.reviewer.compiler
    base = profile_package(BASIC)
    selected = {d.id: d for d in definitions(BASIC) if d.id in ("skill:karate", TECHNIQUE)}
    if altered:
        row = selected["skill:karate" if altered == "parent-difficulty" else TECHNIQUE]
        assert row.skill
        if altered == "parent-difficulty":
            selected[row.id] = replace(row, skill=replace(row.skill, difficulty=Difficulty.AVERAGE))
        elif altered == "difficulty":
            selected[row.id] = replace(row, skill=replace(row.skill, difficulty=Difficulty.AVERAGE))
        else:
            assert row.skill.technique
            technique = (
                replace(row.skill.technique, parent="skill:wrestling")
                if altered == "parent"
                else replace(row.skill.technique, maximum_modifier=1)
            )
            selected[row.id] = replace(row, skill=replace(row.skill, technique=technique))
    if not configured:
        selected.pop(TECHNIQUE)
    extra = next(d for d in combined_package().definitions if d.id == UNUSUAL_BACKGROUND_ID)
    package = replace(
        base,
        definitions=tuple(old.definitions.values()) + tuple(selected.values()) + (extra,),
        sources=tuple({s.id: s for s in (*base.sources, *combined_package().sources)}.values()),
    )
    if background is not None:
        decision = UnusualBackgroundDecision(
            id="rare-kicking",
            gm_id="gm",
            description="A rare Karate Kicking technique",
            point_cost=10 if background == "paid" else 0,
            benefits=(TECHNIQUE,),
            allowed=background != "forbidden",
        )
        package = bind_unusual_background(
            package, decision, package_id=package.id + ":kicking", version="2"
        )
    catalog = RulesCatalog((package,))
    rules = replace(old.rules, packages=(PackagePin(package.id, package.version, package.digest),))
    policy = replace(
        old.policy, point_budget=1000, permitted_sources=frozenset(s.id for s in package.sources)
    )
    compiler = CharacterCompiler(
        catalog, rules, policy, statistics_profile=BASIC, trait_runtime_hooks=SUPPORTED_HOOKS
    )
    reviewer = PowerReviewer(
        compiler, original.engine.reviewer.policy, original.engine.reviewer.gm_ids
    )
    resources = ResourceEngine(
        source.world, catalog, rules, policy, tuple(original.engine.resources.specs.values())
    )
    engine = ActionEngine(reviewer, resources, original.engine.rules)
    play = build_play(
        path / "actual", engine, backend=backend, rng=secrets, seeds=lambda: f"{1:064x}"
    )
    initial = campaign(engine)
    actors = []
    for actor in source.actors:
        purchases = actor.proposal.draft.purchases + (
            Purchase(definition_id="skill:karate", amount=4),
        )
        if actor.actor_id == "a":
            purchases += (Purchase(definition_id=TECHNIQUE, amount=points),) if points else ()
            purchases += (Purchase(definition_id=UNUSUAL_BACKGROUND_ID),) if owned else ()
        proposal = actor.proposal.model_copy(
            update={"draft": actor.proposal.draft.model_copy(update={"purchases": purchases})}
        )
        actors.append(ActorSetup(actor_id=actor.actor_id, proposal=proposal, body=actor.body))
    seed = source.resources.model_copy(
        update={
            "revision": 0,
            "items": tuple(
                i.model_copy(update={"equipped": False, "ready": False})
                for i in source.resources.items
            ),
        }
    )
    state = play.initial_state(initial, source.world, seed, tuple(actors), members=source.members)
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    if owned:
        await play.approve(
            cid,
            ApproveCharacter(
                id="background-approval",
                actor_id="gm",
                target_actor_id="a",
                expected_revision=state.revision,
                reason="Approve the paid technique admission",
            ),
            principal_id="gm",
        )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        StartEncounter(
            id="start",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                Placement(actor_id="b", position=GridPoint(x=1, y=2)),
            ),
        ),
        principal_id="gm",
    )
    return cid, play, initial


def kick(state: PlayState, skill: UnarmedSkill = "skill:karate") -> TakeUnarmedTurn:
    return TakeUnarmedTurn(
        id="kick",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        action="kick",
        target_id="b",
        skill=skill,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("points,target", [(0, 8), (2, 9), (3, 10)])
async def test_actual_karate_kick_uses_bought_technique_once(
    tmp_path: Path, backend: str, points: int, target: int
) -> None:
    cid, play, _ = await prepare(tmp_path, backend, points)
    state = play._load(await play.store.read(cid))
    command = kick(state)
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError), match="authorized"):
        await CombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(update={"expected_revision": state.revision - 1}),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
    await CombatService(play).execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    defense = ChooseDefense(
        id="defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    recorded = RecordedDice((2, 2, 2, 4))
    play.rng = recorded
    result = await CombatService(play).execute(cid, defense, principal_id="b")
    assert (
        result.unarmed
        and result.unarmed.checks[0]
        and result.unarmed.checks[0].effective_target == target
    )
    assert result.unarmed.injury == 3 and recorded.exhausted()
    final = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, defense, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize(
    "background,owned,allowed",
    [
        ("paid", False, False),
        ("paid", True, True),
        ("forbidden", False, False),
        ("common", False, True),
    ],
)
async def test_live_karate_kick_obeys_the_exact_background_admission(
    tmp_path: Path, background: str, owned: bool, allowed: bool
) -> None:
    cid, play, _ = await prepare(tmp_path, "sqlite", 0, background=background, owned=owned)
    state = play._load(await play.store.read(cid))
    compiled = build(play.rules_context, state, "a")
    assert (any(v.target == TECHNIQUE for v in compiled.sheet.values)) == allowed
    if allowed:
        await CombatService(play).execute(cid, kick(state), principal_id="a")
    else:
        before = await play.store.read(cid)
        with pytest.raises(ValidationError, match="admitted technique"):
            await CombatService(play).execute(cid, kick(state), principal_id="a")
        assert await play.store.read(cid) == before
        # The GM named only Karate Kicking, not the separate ordinary DX kick.
        await CombatService(play).execute(cid, kick(state, "attribute:dx"), principal_id="a")


@pytest.mark.parametrize("alteration", ["cap", "parent", "difficulty", "parent-difficulty"])
async def test_altered_metadata_is_not_a_canonical_technique(
    tmp_path: Path, alteration: str
) -> None:
    cid, play, _ = await prepare(tmp_path, "sqlite", 0, altered=alteration)
    state = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="canonical B232"):
        await CombatService(play).execute(cid, kick(state), principal_id="a")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("enabled", [False, True])
async def test_feature_capture_preserves_old_kick_history_on_retry_and_seed_replay(
    tmp_path: Path, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    features = generations.ACTIVE
    monkeypatch.setattr(generations, "ACTIVE", features if enabled else features - {FEATURE})
    cid, play, initial = await prepare(tmp_path, "sqlite", 2)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(cid, kick(state), principal_id="a")
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert (
        result.unarmed
        and result.unarmed.checks[0]
        and result.unarmed.checks[0].effective_target == (9 if enabled else 8)
    )
    final = await play.store.read(cid)
    monkeypatch.setattr(generations, "ACTIVE", features if not enabled else features - {FEATURE})
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, command, principal_id="b") == result
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final


async def test_catalogs_without_the_technique_keep_the_parent_default(tmp_path: Path) -> None:
    cid, play, _ = await prepare(tmp_path, "sqlite", 0, configured=False)
    state = play._load(await play.store.read(cid))
    with combat_generation(frozenset({FEATURE})):
        assert attack_skill_value(play.rules_context, state, "a", "kick", "skill:karate") == 8
    await CombatService(play).execute(cid, kick(state), principal_id="a")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((2, 2, 2, 4))
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="defense",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert result.unarmed and result.unarmed.checks[0].effective_target == 8


async def test_attack_source_snapshot_retains_its_original_background_admission(
    tmp_path: Path,
) -> None:
    cid, play, _ = await prepare(tmp_path, "sqlite", 0, background="paid", owned=True)
    state = play._load(await play.store.read(cid))
    actor = next(a for a in state.actors if a.actor_id == "a")
    source, _ = play.engine.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=cid, actor_id="a"
    )
    proposal = actor.proposal.model_copy(
        update={
            "draft": actor.proposal.draft.model_copy(
                update={
                    "purchases": tuple(
                        p
                        for p in actor.proposal.draft.purchases
                        if p.definition_id != UNUSUAL_BACKGROUND_ID
                    )
                }
            )
        }
    )
    approval = play.engine.reviewer.approve(
        proposal, campaign_id=cid, actor_id="a", revision=state.revision
    )
    current = state.model_copy(
        update={
            "actors": tuple(
                a.model_copy(update={"proposal": proposal, "approval": approval})
                if a.actor_id == "a"
                else a
                for a in state.actors
            )
        }
    )
    with combat_generation(frozenset({FEATURE})):
        with pytest.raises(ValidationError, match="admitted technique"):
            attack_skill_value(play.rules_context, current, "a", "kick", "skill:karate")
        captured = replace(play.rules_context, attack_source=("a", source))
        assert attack_skill_value(captured, current, "a", "kick", "skill:karate") == 8
