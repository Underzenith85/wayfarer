"""Player-facing recovery exposes only choices the authoritative GURPS state allows."""

from pathlib import Path
from typing import cast

import pytest
from test_actions import campaign, world
from test_actions import engine as prototype_engine
from test_actions import seed as prototype_seed
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.injury_types import InjuryStatus
from wayfarer.engine.rules.recovery_types import FatigueStatus, ProfileId
from wayfarer.engine.simulation.access import CampaignMember
from wayfarer.engine.simulation.action_engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, PlayState
from wayfarer.engine.simulation.resources import Owner, ResourceEngine, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.medical import CareEnvironment
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.player_medical import choices
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

PROFILE: ProfileId = "gurps-basic-set-4e-2004"


def environment(_play: PlayService, _state: PlayState, _target: str) -> CareEnvironment:
    return CareEnvironment(technology_level=8, food=True, water=True, sleep=True)


def projected_list(projection: dict[str, object], key: str) -> list[dict[str, object]]:
    return cast(list[dict[str, object]], projection[key])


async def setup(tmp_path: Path) -> tuple[str, PlayService, CampaignAccess]:
    compiler = profile_compiler(PROFILE)
    catalog = RulesCatalog((profile_package(PROFILE),))
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="test", version=1), frozenset({"gm"})),
        ResourceEngine(world(), catalog, compiler.rules, compiler.policy, ()),
        ActionRules(id="player-medical-test", version=1, fatigue_cost=0, maximum_wait=10000),
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "player-medical.sqlite", 10),
        engine,
        rng=RecordedDice([]),
    )
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (
            ActorSetup(
                actor_id="a",
                proposal=CharacterProposal(draft=gurps_draft()),
                aware_of=("alley",),
            ),
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    pools = tuple(
        pool.model_copy(
            update={
                "current": 5,
                "injury": InjuryStatus(profile_id=PROFILE),
            }
        )
        if pool.id == "hp:a"
        else pool.model_copy(update={"current": 5, "fatigue": FatigueStatus(profile_id=PROFILE)})
        for pool in state.resources.pools
    )
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"pools": pools})}
    )
    engine.validate(state)
    initial["play_json"] = state.model_dump_json()
    await play.store.insert(initial)
    return initial["id"], play, CampaignAccess(play, environment)


async def test_non_gurps_profile_exposes_no_medical_choices(tmp_path: Path) -> None:
    reducer = prototype_engine()
    play = PlayService(AsyncSQLiteStore(tmp_path / "prototype.sqlite"), reducer)
    public, tasks, private = choices(play, prototype_seed(reducer), ("a",))
    assert public == tasks == [] and private == {}


async def test_player_can_submit_only_server_authorized_opaque_choice(tmp_path: Path) -> None:
    cid, play, access = await setup(tmp_path)
    projection = await access.read(cid, principal_id="alice")
    offered = {
        choice["kind"]: choice for choice in projected_list(projection, "gurps_recovery_choices")
    }
    assert {"rest", "natural"} <= set(offered)
    assert "resuscitate" not in offered and "stabilize" not in offered

    rest = offered["rest"]
    with pytest.raises(ValidationError, match="Invalid typed campaign command"):
        await access.execute(
            cid,
            {
                "id": "forged-values",
                "actor_id": "a",
                "expected_revision": 0,
                "kind": "gurps_recovery",
                "choice_id": rest["id"],
                "skill": 20,
                "technology_level": 12,
                "healing": 100,
            },
            principal_id="alice",
        )
    with pytest.raises(ValidationError, match="no longer authorized"):
        await access.execute(
            cid,
            {
                "id": "forged-choice",
                "actor_id": "a",
                "expected_revision": 0,
                "kind": "gurps_recovery",
                "choice_id": "gurps:stabilize:a:a:forged",
            },
            principal_id="alice",
        )
    assert (await play.store.read(cid))["revision"] == 0


async def test_pending_task_reconnect_and_finish_retry_are_exact_once(tmp_path: Path) -> None:
    cid, play, access = await setup(tmp_path)
    projection = await access.read(cid, principal_id="alice")
    rest = next(
        choice
        for choice in projected_list(projection, "gurps_recovery_choices")
        if choice["kind"] == "rest"
    )
    start = {
        "id": "start-rest",
        "actor_id": "a",
        "expected_revision": 0,
        "kind": "gurps_recovery",
        "choice_id": rest["id"],
    }
    started = await access.execute(cid, start, principal_id="alice")
    assert started["revision"] == 1
    assert started["gurps_recovery_tasks"] == [
        {
            "id": "start-rest",
            "actor_id": "a",
            "target_actor_id": "a",
            "kind": "rest",
            "status": "pending",
            "due": 600,
            "settled": False,
            "can_finish": False,
        }
    ]

    replayed = await access.execute(cid, start, principal_id="alice")
    assert replayed["revision"] == 1
    state = play._load(await play.store.read(cid))
    assert len(state.resources.recovery_tasks) == 1

    restarted = CampaignAccess(
        PlayService(play.store, play.engine, rng=RecordedDice([])), environment
    )
    restored = await restarted.read(cid, principal_id="alice")
    assert restored["gurps_recovery_tasks"] == started["gurps_recovery_tasks"]

    after_wait = await restarted.execute(
        cid,
        {
            "id": "wait-rest",
            "actor_id": "a",
            "expected_revision": 1,
            "kind": "wait",
            "ticks": 600,
        },
        principal_id="alice",
    )
    finish = next(
        choice
        for choice in projected_list(after_wait, "gurps_recovery_choices")
        if choice["kind"] == "finish-recovery"
    )
    finish_command = {
        "id": "finish-rest",
        "actor_id": "a",
        "expected_revision": 2,
        "kind": "gurps_recovery",
        "choice_id": finish["id"],
    }
    completed = await restarted.execute(cid, finish_command, principal_id="alice")
    assert completed["revision"] == 3
    fp = next(pool for pool in projected_list(completed, "pools") if pool["id"] == "fp:a")
    assert fp["current"] == 6

    retried = await restarted.execute(cid, finish_command, principal_id="alice")
    assert retried["revision"] == 3
    fp = next(pool for pool in projected_list(retried, "pools") if pool["id"] == "fp:a")
    assert fp["current"] == 6
