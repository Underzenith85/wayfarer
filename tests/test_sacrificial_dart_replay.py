"""Actual server seeds replay the protector or retained friend's carrier and payload."""

import secrets
from pathlib import Path

import pytest
from test_sacrificial_dart_fixture import pending_dart

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.persistence.replay import verify_commands


def chosen_seed(protects: bool) -> str:
    for n in range(10000):
        seed = f"{n:064x}"
        rng = SeededRandom(seed)
        attack = sum(rng.randbelow(6) + 1 for _ in range(3))
        dodge = sum(rng.randbelow(6) + 1 for _ in range(3))
        damage = rng.randbelow(6) + 1
        ht = sum(rng.randbelow(6) + 1 for _ in range(3))
        if 7 <= attack <= 13 and (
            (5 <= dodge <= 7 and damage == 6 and ht == 12) if protects else dodge == 12
        ):
            return seed
    raise AssertionError("No deterministic interposition oracle")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("protects", [False, True])
async def test_carrier_and_payload_seed_reexecution(
    tmp_path: Path, backend: str, protects: bool
) -> None:
    cid, play, command = await pending_dart(tmp_path, backend)
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, lambda: chosen_seed(protects)
    result = await CombatService(play).execute(cid, command, principal_id="c")
    if not protects:
        assert result.code == "combat.sacrificial_failed"
        for n in range(10000):
            seed = f"{n:064x}"
            rng = SeededRandom(seed)
            if rng.randbelow(6) + 1 == 6 and sum(rng.randbelow(6) + 1 for _ in range(3)) == 12:
                break
        else:
            raise AssertionError("No deterministic friend payload oracle")
        play.seeds = lambda: seed
        current = play._load(await play.store.read(cid))
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="friend",
                actor_id="b",
                expected_revision=current.revision,
                encounter_id="fight",
                defense="none",
            ),
            principal_id="b",
        )
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert len(checks) == (1 if protects else 2) and all(c.folded and c.reexecuted for c in checks)
    assert {k: v for k, v in replayed.items() if k != "play_json"} == {
        k: v for k, v in final.items() if k != "play_json"
    }
    assert play._load(replayed) == play._load(final)
    victim = "c" if protects else "b"
    current = play._load(final)
    assert next(
        p.injury.unconscious for p in current.resources.pools if p.id == "hp:" + victim and p.injury
    )
    assert len([e for e in current.resources.events if ":follow-up:" in e.id]) == 1
