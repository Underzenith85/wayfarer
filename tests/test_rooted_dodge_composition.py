"""Real ChooseDefense uses health score reductions then Rooted floor and CR roll bonus."""

from pathlib import Path

import pytest
from support.rooted_dodge_composition import fixture, prepare
from support.rooted_feet import revision
from support.runtime import build_play

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.orchestration.combat import ChooseDefense, CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "dodge,hp,fp,cr,expected",
    [
        (8, 12, 12, True, 5),
        (10, 3, 12, False, 2),
        (10, 12, 3, False, 2),
        (10, 3, 3, False, 1),
        (10, 4, 4, False, 5),
        (10, 3, 4, False, 2),
        (10, 4, 3, False, 2),
        (10, 12, 1, False, 2),
        (10, 3, 3, True, 2),
    ],
)
async def test_actual_rooted_dodge_health_and_approved_reflexes(
    tmp_path: Path, backend: str, dodge: int, hp: int, fp: int, cr: bool, expected: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, dodge=dodge, hp=hp, fp=fp, cr=cr)
    original = play._load(await play.store.read(cid))
    before_hp = next(p for p in original.resources.pools if p.id == "hp:b")
    before_fp = next(p for p in original.resources.pools if p.id == "fp:b")
    assert (before_hp.maximum, before_hp.current, before_fp.maximum, before_fp.current) == (
        12,
        hp,
        12,
        fp,
    )
    assert before_hp.injury is not None and before_hp.injury.physical_traits.combat_reflexes is cr
    await prepare(play, cid)
    play.rng = RecordedDice((3, 3, 3, 1, 1, 1, 3, 3, 3))
    command = ChooseDefense(
        id="defense",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="dodge",
    )
    result = await CombatService(play).execute(cid, command, principal_id="b")
    assert play.rng.exhausted()
    assert result.injury is not None and result.injury.defense is not None
    assert result.injury.defense.effective_target == expected
    final = await play.store.read(cid)
    assert next(p for p in play._load(final).resources.pools if p.id == "fp:b").current == fp
    restart = build_play(tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(()))
    assert await CombatService(restart).execute(cid, command, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("hp,fp", [(0, 12), (12, 0)])
async def test_nonpositive_initial_health_defense_refuses_before_rng(
    tmp_path: Path, backend: str, hp: int, fp: int
) -> None:
    from wayfarer.errors import ValidationError

    cid, play, _ = await fixture(tmp_path, backend, hp=hp, fp=fp)
    await prepare(play, cid)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="Selected defense is unavailable"):
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="forbidden",
                actor_id="b",
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                defense="dodge",
            ),
            principal_id="b",
        )
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("variant", ["legacy-health", "drop", "retreat"])
async def test_composition_keeps_unsupported_options_atomic(
    tmp_path: Path, backend: str, variant: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    from wayfarer.errors import ConflictError, ValidationError
    from wayfarer.orchestration.combat import generations

    cid, play, _ = await fixture(tmp_path, backend, hp=3)
    await prepare(play, cid)
    if variant == "legacy-health":
        monkeypatch.setattr(
            generations, "ACTIVE", generations.ACTIVE - {"rooted-dodge-health-trait-composition"}
        )
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    play.rng = RecordedDice(())
    command = ChooseDefense(
        id="unsupported",
        actor_id="b",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        defense="dodge",
        dodge_and_drop=variant == "drop",
        basic_retreat=variant == "retreat",
    )
    with pytest.raises(
        (ValidationError, ConflictError), match="Rooted Feet|Selected defense is unavailable"
    ):
        await CombatService(play).execute(cid, command, principal_id="b")
    assert play.rng.exhausted()
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
