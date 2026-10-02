"""B38 actual combat timing prerequisite for B251 Great Haste."""

from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_gurps_maneuvers import turn
from test_gurps_melee import setup

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.traits.movement_forms import PROFILE, RUNTIME_HOOKS, package
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.orchestration.play import PlayService


async def prepare(tmp_path: Path, backend: str) -> tuple[str, PlayService]:
    cid, play = await setup(
        tmp_path,
        PROFILE,
        allow_supernatural=True,
        extra_definitions=package().definitions,
        extra_purchases=(Purchase(definition_id="advantage:altered-time-rate", amount=1),),
        trait_runtime_hooks=RUNTIME_HOOKS,
    )
    if backend == "postgres":
        initial = await play.store.read(cid)
        play = build_play(tmp_path, play.engine, backend=backend)
        await seed_campaign(play.store, initial)
    return cid, play


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_altered_time_rate_two_actual_moves_keep_initiative_and_clock(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    initial = play._load(await play.store.read(cid))
    encounter = initial.encounters[0]
    assert encounter.current_actor_id == "a"
    await turn(cid, play, "a", "move", destination=GridPoint(x=1, y=2))
    first = play._load(await play.store.read(cid))
    assert first.encounters[0].current_actor_id == "a"
    assert first.resources.game_time == initial.resources.game_time
    await turn(cid, play, "a", "move", destination=GridPoint(x=1, y=3))
    second = play._load(await play.store.read(cid))
    assert second.encounters[0].current_actor_id == "b"
    assert second.encounters[0].turn_order == encounter.turn_order
    assert second.resources.game_time == initial.resources.game_time


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_multiple_concentration_maneuvers_are_subjective_seconds(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    initial = play._load(await play.store.read(cid))
    await turn(cid, play, "a", "concentrate")
    first = play._load(await play.store.read(cid))
    actor = next(p for p in first.encounters[0].participants if p.actor_id == "a")
    assert actor.maneuver_state.concentration_seconds == 1
    assert first.encounters[0].current_actor_id == "a"
    await turn(cid, play, "a", "concentrate")
    second = play._load(await play.store.read(cid))
    actor = next(p for p in second.encounters[0].participants if p.actor_id == "a")
    assert actor.maneuver_state.concentration_seconds == 2
    assert second.resources.game_time == initial.resources.game_time
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    third = play._load(await play.store.read(cid))
    assert third.encounters[0].current_actor_id == "a"
    assert third.resources.game_time == initial.resources.game_time + 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_two_armed_attack_responses_settle_opportunities_once(
    tmp_path: Path, backend: str
) -> None:
    from test_gurps_maneuvers import defend

    from wayfarer.engine.rules.checks import RecordedDice

    cid, play = await prepare(tmp_path, backend)
    for index in range(2):
        await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
        pending = play._load(await play.store.read(cid))
        assert pending.encounters[0].current_actor_id == "a"
        play.rng = RecordedDice((4, 4, 4, 3, 3, 3, 1))
        result = await defend(cid, play, "b", "parry", item_id="sword-b")
        assert result.injury is not None and result.injury.defense is not None
        assert result.injury.defense.effective_target == (10 if index == 0 else 6)
        settled = play._load(await play.store.read(cid))
        assert settled.encounters[0].current_actor_id == ("a" if index == 0 else "b")
        assert settled.resources.game_time == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_historical_unflagged_atr_move_stays_single_and_seeded_replays(
    tmp_path: Path,
    backend: str,
) -> None:
    import secrets

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
    from wayfarer.orchestration.pipeline import submit
    from wayfarer.persistence.replay import command_text, verify_commands

    cid, play = await prepare(tmp_path, backend)
    initial = await play.store.read(cid)
    play.rng = secrets
    state = play._load(initial)
    legacy = TakeCombatTurn(
        id="legacy-move",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="move",
        destination=GridPoint(x=1, y=2),
    )
    service = CombatService(play)
    result = await submit(play, cid, service.plan(cid, legacy), principal_id="a")
    after = await play.store.read(cid)
    assert play._load(after).encounters[0].current_actor_id == "b"
    assert await service.execute(cid, legacy, principal_id="a") == result
    assert await play.store.read(cid) == after
    for index in range(2):
        await turn(cid, play, "b", "do_nothing")
        assert play._load(await play.store.read(cid)).encounters[0].current_actor_id == (
            "b" if index == 0 else "a"
        )
    records = [
        r
        for r in await play.store.history(cid)
        if r.expected_revision >= initial["revision"] and r.command_id != "setup:seed"
    ]
    assert "combat_protocol_features" not in command_text(records[0])
    assert all("maneuver-budget" in command_text(r) for r in records[1:])
    identifiers = {r.command_id for r in records}
    final, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_wait_reaction_and_restart_preserve_original_maneuver_budget(
    tmp_path: Path, backend: str
) -> None:
    from test_gurps_maneuvers import defend

    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.orchestration.combat import CombatService, ResumeInterruptedTurn

    cid, play = await prepare(tmp_path, backend)
    await turn(cid, play, "a", "do_nothing")
    await turn(
        cid,
        play,
        "a",
        "wait",
        wait_trigger={
            "actor_id": "b",
            "action": "attack",
            "reaction": "attack",
            "item_id": "sword-a",
            "reaction_target_id": "b",
            "mode_id": "swing",
        },
    )
    paused = await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="swing")
    assert paused.code == "combat.wait_triggered"
    play = PlayService(play.store, play.engine, rng=RecordedDice(()))
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    budget = state.encounters[0].maneuver_budget
    assert budget is not None and budget.actor_id == "b" and budget.remaining == 2
    resume = ResumeInterruptedTurn(
        id="resume", actor_id="b", expected_revision=state.revision, encounter_id="fight"
    )
    result = await CombatService(play).execute(cid, resume, principal_id="b")
    assert await CombatService(play).execute(cid, resume, principal_id="b") == result
    play.rng = RecordedDice((5, 5, 5))
    await defend(cid, play, "a")
    state = play._load(await play.store.read(cid))
    budget = state.encounters[0].maneuver_budget
    assert budget is not None and budget.actor_id == "b" and budget.remaining == 1
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "a"
    assert state.resources.game_time == 1
    assert await play.store.read(cid) == await play.store.replay(cid)
