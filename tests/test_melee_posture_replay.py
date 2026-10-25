"""B551 producer semantics survive fresh defenders, exact retries and seed replay."""

import secrets
from functools import cache
from pathlib import Path

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_gurps_maneuvers import turn
from test_gurps_melee import setup

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.orchestration.combat import ChooseDefense, CombatService, generations
from wayfarer.orchestration.opponent_attack_records import BeginOpponentAttack, ChooseOpponentAttack
from wayfarer.orchestration.task_records import SetRealPlayClock, TaskResult
from wayfarer.orchestration.tasks import TaskService
from wayfarer.persistence.replay import verify_commands

FEATURE = "melee-attack-postures"


@cache
def posture_seed() -> str:
    for number in range(100000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        attack_dice = tuple(rng.randbelow(6) + 1 for _ in range(3))
        damage = rng.randbelow(6) + 1
        if attack_dice[0] == 3 and sum(attack_dice) == 12 and damage == 3:
            return seed
    raise AssertionError("No source posture consequence seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("typed", [False, True])
async def test_old_pending_posture_cannot_be_rewritten_by_fresh_defender(
    tmp_path: Path, backend: str, enabled: bool, typed: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    if backend == "postgres":
        store = open_store(tmp_path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(tmp_path, play.engine, store=store)
    await turn(cid, play, "a", "change_posture", posture="sitting")
    await turn(cid, play, "b", "do_nothing")
    if typed:
        current = play._load(await play.store.read(cid))
        await TaskService(play).execute(
            cid,
            SetRealPlayClock(
                id="clock",
                actor_id="gm",
                expected_revision=current.revision,
                running=True,
            ),
            principal_id="gm",
        )
    features = generations.ACTIVE
    old = features - {FEATURE, "voluntary-attack-minimum"}
    monkeypatch.setattr(generations, "ACTIVE", features if enabled else old)
    play.rng, play.seeds = secrets, posture_seed
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    await turn(cid, play, "a", "attack", item_id="sword-a", mode_id="thrust", target_id="b")
    monkeypatch.setattr(generations, "ACTIVE", old if enabled else features)
    state = play._load(await play.store.read(cid))
    original_id = None
    if typed:
        incoming = state.encounters[0].pending_defense
        assert incoming
        opened = await TaskService(play).execute(
            cid,
            BeginOpponentAttack(
                id="original",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                attack_id=incoming.id,
            ),
            principal_id="gm",
        )
        assert opened.pending_id
        original_id = opened.pending_id
        state = play._load(await play.store.read(cid))
    response = ChooseDefense(
        id="producer-posture-defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    command = (
        ChooseOpponentAttack(
            id=response.id,
            actor_id="b",
            expected_revision=state.revision,
            pending_id=original_id,
            choice="accept",
            response=response,
        )
        if original_id
        else response
    )
    result: CombatResult | TaskResult
    if isinstance(command, ChooseOpponentAttack):
        result = await TaskService(play).execute(cid, command, principal_id="b")
        assert result.combat_json
        combat = CombatResult.model_validate_json(result.combat_json)
    else:
        result = combat = await CombatService(play).execute(cid, command, principal_id="b")
    assert combat.injury
    assert combat.injury.attack.effective_target == (11 if enabled else 13)
    assert combat.injury.attack.outcome.succeeded == (not enabled)
    assert combat.injury.injury == (0 if enabled else 2)
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "attack-posture-seed"),
    )
    assert len(checks) == (3 if typed else 2)
    assert all(check.folded and check.reexecuted for check in checks)
    assert replayed == final
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice([]))
    if isinstance(command, ChooseOpponentAttack):
        assert await TaskService(restarted).execute(cid, command, principal_id="b") == result
    else:
        assert await CombatService(restarted).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
