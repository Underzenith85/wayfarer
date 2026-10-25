"""B551 target posture reaches actual composed and paid missile launch hosts."""

import secrets
from functools import cache
from pathlib import Path

import pytest
from test_composed_attack_host import declare, defense, fixture
from test_gurps_maneuvers import turn
from test_spell_bindings import command, idle, start_fight
from test_spell_bindings import setup as spell_setup

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.tables.combat import Posture
from wayfarer.engine.simulation.magic.spells import SpellCommand
from wayfarer.errors import ValidationError
from wayfarer.orchestration import ranged_target_posture_generations as generations
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.replay import verify_commands


@cache
def host_seed() -> str:
    for number in range(100000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        dice = tuple(rng.randbelow(6) + 1 for _ in range(3))
        if dice[0] == 3 and sum(dice) == 11 and rng.randbelow(6) + 1 == 3:
            return seed
    raise AssertionError("No host consequence seed")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_actual_composed_launch_pins_source_policy_across_fresh_defender_and_seed_replay(
    tmp_path: Path, backend: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, source = await fixture(tmp_path, backend, skill_points=4, levels=1)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="sitting")
    monkeypatch.setattr(generations, "ACTIVE", enabled)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, host_seed
    await declare(play, cid, source)
    monkeypatch.setattr(generations, "ACTIVE", not enabled)
    selected, result = await defense(play, cid)
    assert result.injury
    assert result.injury.attack.total == 11
    assert result.injury.attack.effective_target == (10 if enabled else 12)
    assert result.injury.attack.outcome.succeeded == (not enabled)
    assert result.injury.injury == (0 if enabled else 3)
    assert result.injury.hp_after == (10 if enabled else 7)
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "composed-seed"),
    )
    assert replayed == final and len(checks) == 2
    assert all(check.folded and check.reexecuted for check in checks)
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, selected, principal_id="bob") == result


async def charged(
    path: Path, backend: str, posture: Posture = "sitting", *, projectile_points: int = 4
) -> tuple[str, PlayService, SpellCommand]:
    cid, play = await spell_setup(
        path,
        combat=True,
        backend=backend,
        human_targets=True,
        extra_purchases=(
            Purchase(definition_id="skill:innate-attack-projectile", amount=projectile_points),
        ),
    )
    await start_fight(cid, play)
    await idle(cid, play, "a")
    if posture == "crouching":
        await turn(cid, play, "b", "do_nothing", crouch="before")
    else:
        await turn(cid, play, "b", "change_posture", posture=posture)
    state = play._load(await play.store.read(cid))
    start = command(state.revision).model_copy(
        update={"spell_id": "fireball", "channel_id": "fireball"}
    )
    service = SpellService(play)
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(
        cid,
        start.model_copy(
            update={"id": "finish", "kind": "complete", "expected_revision": state.revision}
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    release = start.model_copy(
        update={"id": "release", "kind": "release", "expected_revision": state.revision}
    )
    return cid, play, release


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("enabled", [False, True])
async def test_paid_missile_launch_original_includes_target_posture_once_and_replays(
    tmp_path: Path, backend: str, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play, release = await charged(tmp_path, backend)
    monkeypatch.setattr(generations, "ACTIVE", enabled)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, host_seed
    await SpellService(play).execute(cid, release, principal_id="a")
    state = play._load(await play.store.read(cid))
    incoming = state.encounters[0].pending_defense
    assert incoming and incoming.attack_roll
    assert incoming.attack_roll.total == 11
    assert incoming.attack_roll.effective_target == (10 if enabled else 12)
    monkeypatch.setattr(generations, "ACTIVE", not enabled)
    selected = ChooseDefense(
        id="missile-response",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    result = await CombatService(play).execute(cid, selected, principal_id="b")
    assert result.injury and result.injury.attack == incoming.attack_roll
    assert result.injury.injury == (0 if enabled else 3)
    final = await play.store.read(cid)
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [event for event in await play.store.stream(cid) if event.command_id in identifiers],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "missile-seed"),
    )
    assert replayed == final and len(checks) == 2
    assert all(check.folded and check.reexecuted for check in checks)
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, selected, principal_id="b") == result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("posture", ["prone", "crawling"])
async def test_unresolved_exposure_refuses_release_without_refunding_already_paid_charge(
    tmp_path: Path, backend: str, posture: Posture
) -> None:
    cid, play, release = await charged(tmp_path, backend, posture)
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="exposure requires"):
        await SpellService(play).execute(cid, release, principal_id="a")
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unresolved_composed_exposure_refuses_before_spending_or_entropy(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(tmp_path, backend, skill_points=4, levels=1)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "change_posture", posture="prone")
    before, history = await play.store.read(cid), await play.store.history(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="exposure requires"):
        await declare(play, cid, source)
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
