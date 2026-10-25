"""Selected Campaigns B551: target posture changes actual ranged hit and injury."""

import json
import secrets
from functools import cache
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.tables.combat import Posture
from wayfarer.engine.rules.types.location import HitLocation
from wayfarer.engine.simulation.campaign.default_knowledge import AdoptDefaultKnowledge
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.equipment.catalog import Damage
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration import ranged_target_posture_generations as generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn
from wayfarer.orchestration.default_knowledge import DefaultKnowledgeService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view.preview import preview
from wayfarer.persistence.command_inputs import RANGED_TARGET_POSTURE_KEY, replay_payload
from wayfarer.persistence.replay import verify_commands

Kind = Literal["gun", "throw"]
Profile = Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"]


@cache
def consequence_seed() -> str:
    for number in range(100000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        dice = tuple(rng.randbelow(6) + 1 for _ in range(3))
        if dice[0] == 3 and sum(dice) == 12 and rng.randbelow(6) + 1 == 3:
            return seed
    raise AssertionError("No source consequence seed")


async def ready(
    path: Path,
    backend: str,
    kind: Kind,
    posture: Posture = "sitting",
    *,
    profile: Profile = "gurps-basic-set-4e-2004",
    penetrating: bool = False,
) -> tuple[str, PlayService]:
    selected = weapon(thrown=kind == "throw")
    if penetrating:
        selected = selected.model_copy(
            update={"damage": Damage(basis="fixed", dice=1, damage_type="imp")}
        )
    cid, play = await setup(
        path,
        profile,
        human=True,
        ranged_fixture=True,
        ranged_mode=selected,
        ranged_scene=scene(),
    )
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    if kind == "gun":
        await load(cid, play)
    await turn(cid, play, "a", "do_nothing")
    if posture == "crouching":
        await turn(cid, play, "b", "do_nothing", crouch="before")
    else:
        await turn(cid, play, "b", "change_posture", posture=posture)
    return cid, play


async def shoot(cid: str, play: PlayService, *, location: HitLocation | None = None) -> None:
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        mode_id="ranged",
        target_id="b",
        hit_location=location,
    )


def response(play: PlayService, campaign: Campaign) -> ChooseDefense:
    state = play._load(campaign)
    return ChooseDefense(
        id="posture-response",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["gun", "throw"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_same_controlled_seed_changes_real_hit_and_preserves_launched_policy(
    tmp_path: Path, backend: str, kind: Kind, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await ready(tmp_path, backend, kind)
    monkeypatch.setattr(generations, "ACTIVE", enabled)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, consequence_seed
    await shoot(cid, play)
    launch = (await play.store.history(cid))[-1]
    assert launch.command_input
    assert (RANGED_TARGET_POSTURE_KEY in json.loads(launch.command_input)) == enabled
    monkeypatch.setattr(generations, "ACTIVE", not enabled)
    command = response(play, await play.store.read(cid))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury
    # Same roll12 and damage3: native target13 hits; B551 sitting target11 misses.
    assert result.injury.attack.total == 12
    assert result.injury.attack.effective_target == (11 if enabled else 13)
    assert result.injury.attack.outcome.succeeded == (not enabled)
    assert result.injury.injury == (0 if enabled else 3)
    assert result.injury.hp_after == (10 if enabled else 7)
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    state = play._load(initial)
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert replayed == final and len(checks) == 2
    assert all(check.folded and check.reexecuted for check in checks)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    caller = replay_payload(launch.command_input)
    assert isinstance(caller, dict) and RANGED_TARGET_POSTURE_KEY not in caller


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture", ["crouching", "kneeling", "sitting"])
@pytest.mark.parametrize(
    "location,target",
    [
        ("torso", 11),
        ("groin", 8),
        ("left-leg", 9),
        ("right-leg", 9),
        ("right-arm", 11),
        ("right-hand", 9),
        ("face", 8),
    ],
)
async def test_actual_target_locations_get_only_the_literal_source_modifier(
    tmp_path: Path, backend: str, posture: Posture, location: HitLocation, target: int
) -> None:
    cid, play = await ready(tmp_path, backend, "throw", posture)
    await shoot(cid, play, location=location)
    command = response(play, await play.store.read(cid))
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(
                update={"id": "stale", "expected_revision": command.expected_revision - 1}
            ),
            principal_id="b",
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    play.rng = RecordedDice([3, 3, 4] + ([3] if target >= 10 else []))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury and result.injury.attack.effective_target == target
    assert result.injury.attack.outcome.succeeded == (target >= 10)
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "posture,location",
    [("prone", "torso"), ("crawling", "left-leg"), ("sitting", "random"), ("kneeling", "vitals")],
)
async def test_unresolved_source_lanes_refuse_before_entropy_or_throw_commitment(
    tmp_path: Path, backend: str, posture: Posture, location: HitLocation
) -> None:
    cid, play = await ready(tmp_path, backend, "throw", posture, penetrating=location == "vitals")
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="require"):
        state = play._load(before)
        preview(
            play,
            state,
            state.encounters[0],
            TakeCombatTurn(
                id="preview",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="ranged",
                target_id="b",
                hit_location=location,
            ),
        )
    with pytest.raises(ValidationError, match="require"):
        await shoot(cid, play, location=location)
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_lite_profile_keeps_native_target_even_with_fresh_generation(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await ready(tmp_path, backend, "throw", profile="gurps-lite-4e-2004")
    await shoot(cid, play)
    play.rng = RecordedDice([4, 4, 4, 3])
    result = await CombatService(play).execute(
        cid, response(play, await play.store.read(cid)), principal_id="b"
    )
    assert result.injury and result.injury.attack.effective_target == 13
    assert result.injury.injury == 3 and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("distance,native", [(5, 3), (2, 5)])
@pytest.mark.parametrize("enabled", [False, True])
async def test_literal_source_target_one_refuses_and_target_three_remains_lawful(
    tmp_path: Path,
    backend: str,
    distance: int,
    native: int,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        trained=False,
        ranged_fixture=True,
        ranged_mode=weapon(),
        ranged_scene=scene(distance),
        battlefield=Battlefield(id="range", location_id="dock", width=8, height=3),
        placements=(
            Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
            Placement(actor_id="b", position=GridPoint(x=distance, y=0), facing="west"),
        ),
    )
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    await DefaultKnowledgeService(play).execute(
        cid,
        AdoptDefaultKnowledge(
            id="posture-society-knowledge",
            actor_id="gm",
            expected_revision=(await play.store.read(cid))["revision"],
            performer_id="a",
            known_skill_ids=("skill:broadsword",),
            reason="The GM establishes this explicit test weapon skill for its fresh posture scenario",
        ),
        principal_id="gm",
    )
    await load(cid, play)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="sitting")
    monkeypatch.setattr(generations, "ACTIVE", enabled)
    target = native - (2 if enabled else 0)
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    if target < 3:
        assert target == 1  # Literal native3 -> source1, without changing actor DX.
        with pytest.raises(ValidationError, match="effective skill at least 3"):
            await shoot(cid, play)
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        assert play.rng.exhausted()
        return
    await shoot(cid, play)
    play.rng = RecordedDice([2, 2, 3])
    result = await CombatService(play).execute(
        cid, response(play, await play.store.read(cid)), principal_id="b"
    )
    assert result.injury and result.injury.attack.effective_target == target
    assert result.injury.attack.total == 7 and not result.injury.attack.outcome.succeeded
    assert play.rng.exhausted()
