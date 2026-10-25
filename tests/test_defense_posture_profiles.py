"""Basic Set posture corrections must not change the separately bound Lite profile."""

from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_actions import world
from test_basic_combat import start_basic
from test_gurps_maneuvers import turn
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.errors import ValidationError
from wayfarer.orchestration import defense_posture_generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations

Profile = Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"]
PROFILES: tuple[Profile, ...] = ("gurps-lite-4e-2004", "gurps-basic-set-4e-2004")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("posture", ["sitting", "crawling"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_actual_profile_controls_defense_source_policy(
    tmp_path: Path,
    backend: str,
    profile: Profile,
    posture: str,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play = await setup(tmp_path, profile, human=True)
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture=posture)
    await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="thrust", target_id="b")
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", enabled)
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="profile-defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
    )
    corrected = enabled and profile == "gurps-basic-set-4e-2004"
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3] + ([3] if corrected else []))
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == (
        (7 if posture == "sitting" else 6) if corrected else 9
    )
    assert result.injury.defense.outcome.succeeded == (not corrected)
    assert result.injury.injury == (2 if corrected else 0)
    assert play.rng.exhausted()
    final = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("profile", PROFILES)
@pytest.mark.parametrize("enabled", [False, True])
async def test_actual_mapless_retreat_correction_is_bound_to_basic_set(
    tmp_path: Path,
    backend: str,
    profile: Profile,
    enabled: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cid, play = await setup(
        tmp_path,
        profile,
        human=True,
        start_encounter=False,
        scene_bound=True,
        aware_of=("a", "b"),
        runtime_world=world().learn("a", "promise"),
    )
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    await CombatService(play).execute(cid, start_basic(1), principal_id="gm")
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="sitting")
    await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="thrust", target_id="b")
    features = generations.ACTIVE
    monkeypatch.setattr(
        generations, "ACTIVE", features if enabled else features - {"retreat-postures"}
    )
    # Isolate the independent B377 admission rule from defense scoring policy.
    monkeypatch.setattr(defense_posture_generations, "ACTIVE", False)
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="profile-retreat",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="dodge",
        basic_retreat=True,
    )
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    if enabled and profile == "gurps-basic-set-4e-2004":
        with pytest.raises(ValidationError, match="Retreat is unavailable"):
            await CombatService(play).execute(cid, command, principal_id="b")
        assert await play.store.read(cid) == before and await play.store.history(cid) == history
        assert play.rng.exhausted()
        return
    play.rng = RecordedDice([3, 3, 3, 2, 3, 3])
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == 12
    assert result.injury.defense.outcome.succeeded and result.injury.injury == 0
    assert play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)
