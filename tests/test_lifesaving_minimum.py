"""B345/B355 native current rescue floors; old unmarked inputs stay historical."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign, world
from test_medical_service import setup
from test_statistics import gurps_draft, profile_package

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler
from wayfarer.engine.character.power import CharacterProposal, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.entropy import commit_command
from wayfarer.orchestration.hazard_care import HazardCare, HazardCareCommand, HazardCareService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.command_inputs import DEFAULT_KNOWLEDGE_KEY, replay_payload
from wayfarer.persistence.hazard_care_inputs import KEY
from wayfarer.persistence.replay import verify_commands


class NoDraw:
    def __init__(self) -> None:
        self.calls = 0

    def randbelow(self, upper: int) -> int:
        self.calls += 1
        raise AssertionError("Unexpected RNG before care refusal or on retry")

    def exhausted(self) -> bool:
        return self.calls == 0


def seed_three() -> str:
    for number in range(10000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        if sum(rng.randbelow(6) + 1 for _ in range(3)) == 3:
            return seed
    raise AssertionError("No exact source3 seed")


async def fixture(
    path: Path, backend: str, *, ht: int = 12, known: bool = True, no_edge: bool = False
) -> tuple[str, PlayService, Campaign]:
    _, original, _ = await setup(path / "foundation", pinned_skill_ids=("skill:swimming",))
    engine = original.engine
    if no_edge:
        # Deliberately missing source edge: a GM familiarity fact cannot create it.
        compiler = engine.reviewer.compiler
        package = profile_package("gurps-basic-set-4e-2004", compiler.definitions["skill:swimming"])
        definitions = tuple(
            replace(d, skill=replace(d.skill, defaults=()))
            if d.id == "skill:swimming" and d.skill
            else d
            for d in package.definitions
        )
        package = replace(package, definitions=definitions)
        compiler = CharacterCompiler(
            RulesCatalog((package,)),
            replace(
                compiler.rules, packages=(PackagePin(package.id, package.version, package.digest),)
            ),
            compiler.policy,
            statistics_profile="gurps-basic-set-4e-2004",
        )
        engine = ActionEngine(
            PowerReviewer(compiler, engine.reviewer.policy, engine.reviewer.gm_ids),
            ResourceEngine(
                world(),
                RulesCatalog((package,)),
                compiler.rules,
                compiler.policy,
                tuple(engine.resources.specs.values()),
            ),
            engine.rules,
        )
    play = build_play(path, engine, backend=backend, seeds=seed_three)
    initial = campaign(engine)
    drowning = HazardSchedule(
        id="exposure",
        actor_id="b",
        spec=HazardSpec(
            id="water", scene_id="dock", kind="drowning", interval=5, cycles=1000, reference="B355"
        ),
        started=0,
        due=5,
        remaining=1000,
        ht=10,
        will=10,
        swimming=6,
        stage="struggling",
    )
    actors = []
    for actor in ("a", "b"):
        draft = gurps_draft()
        if actor == "a":
            draft = draft.model_copy(
                update={
                    "purchases": tuple(
                        p.model_copy(update={"amount": ht})
                        if p.definition_id == "attribute:ht"
                        else p
                        for p in draft.purchases
                    )
                }
            )
        actors.append(ActorSetup(actor_id=actor, proposal=CharacterProposal(draft=draft)))
    state = play.initial_state(
        initial,
        world(),
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100), Owner(actor_id="b", capacity=100))
        ),
        tuple(actors),
        members=(
            CampaignMember(principal_id="a", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="b", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    # An authored initially struggling victim, not a claim about an unrecorded
    # public enter/exposure command. Rescue reads real current b/ST10 and health.
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"hazards": (drowning,)})}
    )
    engine.validate(state)
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    if known:
        await DefaultKnowledgeService(play).execute(
            initial["id"],
            AdoptDefaultKnowledge(
                id="swimming-fact",
                actor_id="gm",
                expected_revision=0,
                performer_id="a",
                known_skill_ids=("skill:swimming",),
                reason="The GM confirms Swimming for the rescuer's society",
            ),
            principal_id="gm",
        )
    return initial["id"], play, initial


def context(*_args: object) -> HazardCare:
    return HazardCare("lifesaving", "b", "exposure", safe_landing=True)


def unavailable(*_args: object) -> HazardCare:
    raise AssertionError("No fresh resolver or dice on exact retry")


async def command(play: PlayService, cid: str, *, identifier: str = "rescue") -> HazardCareCommand:
    return HazardCareCommand(
        id=identifier,
        actor_id="a",
        expected_revision=(await play.store.read(cid))["revision"],
        treatment_id="bank",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ht,target", [(10, 1), (11, 2)])
@pytest.mark.parametrize("empty_fp", [False, True])
async def test_current_one_two_refuse_without_health_skill_entropy_or_cost(
    tmp_path: Path, backend: str, ht: int, target: int, empty_fp: bool
) -> None:
    from test_combat_sensory_authority import change

    cid, play, _ = await fixture(tmp_path, backend, ht=ht)
    if empty_fp:

        def deplete(state: PlayState) -> PlayState:
            return state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "pools": tuple(
                                p.model_copy(update={"current": 0}) if p.id == "fp:a" else p
                                for p in state.resources.pools
                            )
                        }
                    )
                }
            )

        await change(play, cid, deplete)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    play.rng = dice = NoDraw()
    value = await command(play, cid)
    with pytest.raises(ValidationError, match="effective skill at least 3"):
        await HazardCareService(play, context).execute(cid, value, principal_id="a")
    assert ht - 4 - 5 == target
    assert await play.store.read(cid) == before and dice.calls == 0 and dice.exhausted()
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, value.id) is None


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_current_three_actual_rescue_retry_privacy_and_full_seed_replay(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import build_runtime

    cid, play, initial = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    value = await command(play, cid)
    result, duplicate = await asyncio.gather(
        *(HazardCareService(play, context).execute(cid, value, principal_id="a") for _ in range(2))
    )
    assert result == duplicate and result.check and result.succeeded
    assert result.check.effective_target == result.check.total == 3
    state = play._load(await play.store.read(cid))
    assert state.resources.hazards[0].stage == "rescued"
    assert state.resources.hazards[0].due == 1 and state.resources.game_time == 0
    assert state.resources.pools == before.resources.pools and result.fp_lost == 0
    stored = await play.store.command_input(cid, value.id)
    assert stored and stored.text
    payload = replay_payload(stored.text)
    assert isinstance(payload, dict) and payload[KEY] == 1
    assert json.loads(stored.text)[DEFAULT_KNOWLEDGE_KEY] == 2
    play.rng = dice = NoDraw()
    assert (
        await HazardCareService(play, unavailable).execute(cid, value, principal_id="a") == result
    )
    for actor in ("a", "b"):
        visible = json.dumps(await build_runtime(play).read(cid, principal_id=actor))
        assert KEY not in visible and "hazard_care_context" not in visible
    assert dice.calls == 0
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks), [
        (c.command_id, c.reason) for c in checks
    ]
    assert final == await play.store.read(cid) == await play.store.replay(cid)
    event = next(e for e in state.resources.events if e.id == "hazard-care:" + value.id)
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            initial,
            state.world,
            state.resources.model_copy(update={"revision": 0, "events": (event,), "receipts": ()}),
            tuple(
                ActorSetup.model_validate(a.model_dump(exclude={"approval"})) for a in state.actors
            ),
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_old_unmarked_producer_retains_literal_one_and_exact_original_bytes(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, ht=10)
    value = await command(play, cid)
    service = HazardCareService(play, context)
    plan = service.plan(play, value, principal_id="a")
    raw = plan.payload
    await commit_command(
        play, cid, value.id, value.expected_revision, raw, plan.resolve, actor_id="a", rng=play.rng
    )
    result = await plan.outcome(await play.store.read(cid))
    assert result.check and result.check.effective_target == 1 and result.succeeded
    saved = await play.store.command_input(cid, value.id)
    assert saved and saved.text == raw
    assert KEY not in raw and DEFAULT_KNOWLEDGE_KEY not in raw
    play.rng = dice = NoDraw()
    assert (
        await HazardCareService(play, unavailable).execute(cid, value, principal_id="a") == result
    )
    assert await play.store.command_input(cid, value.id) == saved and dice.calls == 0
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("no_edge", [False, True])
async def test_unknown_or_no_edge_cannot_be_manufactured_by_context(
    tmp_path: Path, backend: str, no_edge: bool
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, ht=12, known=no_edge, no_edge=no_edge)
    before = await play.store.read(cid)
    play.rng = dice = NoDraw()
    with pytest.raises(ValidationError, match="society-known"):
        await HazardCareService(play, context).execute(
            cid, await command(play, cid), principal_id="a"
        )
    assert await play.store.read(cid) == before and dice.calls == 0 and dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_wrong_actor_and_stale_refuse_before_external_resolver(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    value = await command(play, cid)
    before = await play.store.read(cid)
    service = HazardCareService(play, unavailable)
    play.rng = dice = NoDraw()
    with pytest.raises(ValidationError, match="authenticated actor"):
        await service.execute(cid, value, principal_id="b")
    with pytest.raises(ConflictError, match="revision"):
        await service.execute(
            cid, value.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert await play.store.read(cid) == before and dice.calls == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_three_failed_rescue_pays_once_without_changing_victim_or_clock(
    tmp_path: Path, backend: str
) -> None:
    cid, play, initial = await fixture(tmp_path, backend)
    before = play._load(await play.store.read(cid))
    value = await command(play, cid)
    play.seeds = lambda: f"{1:064x}"  # Exact native original9 fails final3.
    result = await HazardCareService(play, context).execute(cid, value, principal_id="a")
    assert not result.succeeded and result.check
    assert result.check.effective_target == 3 and result.check.total == 9
    assert result.fp_lost == 1
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 0 and state.resources.hazards == before.resources.hazards
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 11
    assert next(a.available_at for a in state.actors if a.actor_id == "a") == 60
    play.rng = dice = NoDraw()
    assert (
        await HazardCareService(play, unavailable).execute(cid, value, principal_id="a") == result
    )
    with pytest.raises(ConflictError, match="reused"):
        await HazardCareService(play, unavailable).execute(
            cid, value.model_copy(update={"treatment_id": "different"}), principal_id="a"
        )
    assert dice.calls == 0
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "failure-reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ht,allowed", [(13, False), (14, True)])
async def test_actual_coma_recovery_aftermath_crosses_the_final_two_three_boundary(
    tmp_path: Path, backend: str, ht: int, allowed: bool
) -> None:
    from test_combat_sensory_authority import change

    from wayfarer.engine.rules.fright import fright_effect
    from wayfarer.engine.simulation.actions import Wait
    from wayfarer.engine.simulation.health.fright import apply_effect, effects

    cid, play, _ = await fixture(tmp_path, backend, ht=ht)

    def coma(state: PlayState) -> PlayState:
        # Actual B361 row28 producer, followed below by the native Clock HT
        # recovery. This trusted fixture transition is not a public fear trigger.
        resources = apply_effect(
            state.resources,
            fright_effect(28, ht, rng=RecordedDice(())),
            actor_id="a",
            trigger_id="prior-coma",
            command_id="coma",
            ht=ht,
            will=10,
            modified_will=10,
            rng=RecordedDice(()),
        )
        # This case creates its struggling victim after coma recovery.
        return state.model_copy(update={"resources": resources.model_copy(update={"hazards": ()})})

    await change(play, cid, coma)
    play.rng = RecordedDice((3, 3, 3))
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="recover", actor_id="b", expected_revision=state.revision, ticks=1800),
        principal_id="b",
    )
    recovered = play._load(await play.store.read(cid))
    episode = effects(recovered.resources)[0]
    assert not episode.active and episode.aftermath_until == 23400
    assert recovered.resources.game_time == 1800

    # Real current hazard state; no source-approved exposure API claim here.
    def victim(state: PlayState) -> PlayState:
        hazard = HazardSchedule(
            id="exposure",
            actor_id="b",
            spec=HazardSpec(
                id="water",
                scene_id="dock",
                kind="drowning",
                interval=5,
                cycles=1000,
                reference="B355",
            ),
            started=1800,
            due=1805,
            remaining=1000,
            ht=10,
            will=10,
            swimming=6,
            stage="struggling",
        )
        return state.model_copy(
            update={"resources": state.resources.model_copy(update={"hazards": (hazard,)})}
        )

    await change(play, cid, victim)
    before = await play.store.read(cid)
    value = await command(play, cid)
    dice = NoDraw()
    play.rng = RecordedDice((1, 1, 1)) if allowed else dice
    if allowed:
        result = await HazardCareService(play, context).execute(cid, value, principal_id="a")
        assert result.check and result.check.base_target == 5
        assert result.check.effective_target == 3 and result.check.total == 3 and result.succeeded
        assert sum(m.value for m in result.check.modifiers) == -2
    else:
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            await HazardCareService(play, context).execute(cid, value, principal_id="a")
        assert await play.store.read(cid) == before and dice.calls == 0
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_native_result_commit_rollback_and_retry_do_not_spend_or_rescue_twice(
    tmp_path: Path, backend: str
) -> None:
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    cid, play, _ = await fixture(tmp_path, backend)
    value = await command(play, cid)
    before = await play.store.read(cid)
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((1, 1, 1)))
    with pytest.raises(RuntimeError, match="crash"):
        await HazardCareService(failing, context).execute(cid, value, principal_id="a")
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    assert await play.store.command_input(cid, value.id) is None
    result = await HazardCareService(play, context).execute(cid, value, principal_id="a")
    assert result.succeeded
    play.rng = dice = NoDraw()
    assert (
        await HazardCareService(play, unavailable).execute(cid, value, principal_id="a") == result
    )
    state = play._load(await play.store.read(cid))
    assert sum(e.id == "hazard-care:" + value.id for e in state.resources.events) == 1
    assert dice.calls == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_real_offshift_pause_fences_care_before_external_resolver(
    tmp_path: Path, backend: str
) -> None:
    from test_ability_offshift import command as ability_command
    from test_ability_offshift import night

    from wayfarer.orchestration.abilities import AbilityService

    initial, play = await night(tmp_path, backend)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await AbilityService(play).execute(cid, ability_command(state.revision), principal_id="a")
    before = await play.store.read(cid)
    play.rng = dice = NoDraw()
    with pytest.raises(ConflictError, match="off-shift"):
        await HazardCareService(play, unavailable).execute(
            cid, await command(play, cid), principal_id="a"
        )
    assert await play.store.read(cid) == before and dice.calls == 0 and dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("invalid", ["target", "schedule", "landing", "kind"])
async def test_captured_context_rechecks_current_actual_condition_before_dice(
    tmp_path: Path, backend: str, invalid: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    before = await play.store.read(cid)
    care = HazardCare(
        "diagnosis" if invalid == "kind" else "lifesaving",
        "a" if invalid == "target" else "b",
        "missing" if invalid == "schedule" else "exposure",
        safe_landing=invalid != "landing",
    )
    play.rng = dice = NoDraw()
    with pytest.raises(ValidationError):
        await HazardCareService(play, lambda *_: care).execute(
            cid, await command(play, cid), principal_id="a"
        )
    assert await play.store.read(cid) == before and dice.calls == 0 and dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_counted_rng_negative_control_reaches_the_real_original_draw(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    before = await play.store.read(cid)
    play.rng = dice = NoDraw()
    with pytest.raises(AssertionError, match="Unexpected RNG"):
        await HazardCareService(play, context).execute(
            cid, await command(play, cid), principal_id="a"
        )
    assert dice.calls == 1 and await play.store.read(cid) == before
