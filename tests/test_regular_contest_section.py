"""B349 closure: source-pinned rounds cause a real B370 pin on both stores."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from support.runtime import build_play, played
from test_gurps_conformance import load_cases
from test_regular_contest_source import prepare
from test_unarmed import action, defend, state_of, wait

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.certification.executable_evidence import provenance
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.commands import TakeUnarmedTurn
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands

CASE_ID = "basic-regular-contest-live-pin-rounds"
ROOT = Path(__file__).resolve().parents[1]


async def grapple_and_takedown(cid: str, play: PlayService, *, seeded: bool) -> str:
    if seeded:
        play.rng = secrets
        play.seeds = lambda: f"{2:064x}"
    await action(cid, play, "a", "grapple", hands=("left-hand", "right-hand"), enter=True)
    if not seeded:
        play.rng = RecordedDice((3, 3, 3))
    await defend(cid, play)
    state = await state_of(cid, play)
    assert len(state.encounters[0].grips) == 1
    grip = state.encounters[0].grips[0]
    await wait(cid, play, "b")
    if not seeded:
        play.rng = RecordedDice((2, 2, 2, 4, 4, 4))
    await action(cid, play, "a", "takedown", grip=grip.id)
    state = await state_of(cid, play)
    assert next(p for p in state.encounters[0].participants if p.actor_id == "b").posture == "prone"
    await wait(cid, play, "b")
    return grip.id


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_live_pin_matches_source_rounds_and_reexecutes(
    tmp_path: Path, backend: str, record_property: Callable[[str, object], None]
) -> None:
    case = next(c for c in load_cases() if c["id"] == CASE_ID)
    dice = (4, 4, 4, 4, 4, 3, 3, 3, 3, 3, 3, 3, 3, 3, 3, 4, 4, 3)
    assert case["input"] == {"first_target": 15, "second_target": 15, "roll": list(dice)}
    assert case["expected"] == {
        "rounds": 3,
        "winner": "first",
        "adjusted_targets": [10, 10],
        "pinned": True,
    }
    cid, play, _ = await prepare(tmp_path / "numeric", backend)
    grip_id = await grapple_and_takedown(cid, play, seeded=False)
    before = await play.store.read(cid)
    initial_state = play._load(before)
    initial_grip = initial_state.encounters[0].grips[0]
    pools = {p.id: p.current for p in initial_state.resources.pools}
    command = TakeUnarmedTurn(
        id="pin-round-1",
        actor_id="a",
        expected_revision=initial_state.revision,
        encounter_id="fight",
        action="pin",
        target_id="b",
        grip_id=grip_id,
    )
    forbidden = RecordedDice(())
    play.rng = forbidden
    with pytest.raises((AuthorizationError, ValidationError)):
        await CombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert await play.store.read(cid) == before and forbidden.exhausted()
    scripted = RecordedDice(dice)
    play.rng = scripted
    for index in range(3):
        state = await state_of(cid, play)
        command = command.model_copy(
            update={"id": f"pin-round-{index + 1}", "expected_revision": state.revision}
        )
        result = await CombatService(play).execute(cid, command, principal_id="a")
        state = await state_of(cid, play)
        if index < 2:
            assert state.encounters[0].grips[0] == initial_grip
            await wait(cid, play, "b")
    assert scripted.exhausted()
    state = await state_of(cid, play)
    pins = [h for h in state.encounters[0].unarmed_history if h.action == "pin"]
    assert len(pins) == 3 and [h.won for h in pins] == [False, False, True]
    assert state.encounters[0].grips[0].pinned
    assert [c.base_target for h in pins for c in h.checks] == [15] * 6
    assert [c.effective_target for h in pins for c in h.checks] == [10] * 6
    assert tuple(d for h in pins for c in h.checks for d in c.dice) == dice
    assert [[c.outcome.succeeded for c in h.checks] for h in pins] == [
        [False, False],
        [True, True],
        [True, False],
    ]
    assert {p.id: p.current for p in state.resources.pools} == pools
    final = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, command, principal_id="a") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)

    # The numeric source oracle above and this independent seeded campaign prove
    # different things: every scripted round, then original-genesis reexecution.
    seeded_id, seeded_play, genesis = await prepare(tmp_path / "seeded", backend)
    seeded_grip = await grapple_and_takedown(seeded_id, seeded_play, seeded=True)
    await action(seeded_id, seeded_play, "a", "pin", grip=seeded_grip)
    seeded_state = await state_of(seeded_id, seeded_play)
    pin = seeded_state.encounters[0].unarmed_history[-1]
    assert pin.action == "pin" and [c.effective_target for c in pin.checks] == [10, 10]
    replayed, checks = await verify_commands(
        genesis,
        await played(seeded_play.store, seeded_id),
        await seeded_play.store.stream(seeded_id),
        configuration_digest=seeded_state.configuration_digest,
        execute=FixtureExecutor(seeded_play.engine, tmp_path / "reexecute"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await seeded_play.store.read(seeded_id)
    for name, value in provenance(ROOT, case).items():
        record_property(name, value)
