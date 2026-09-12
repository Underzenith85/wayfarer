"""B366/B374-375: ordered fallback defenses, with durable combat receipts.

Expectations reuse the existing Basic Set Double Defense contract; exact
source-artifact audit and unarmed critical consequences remain outstanding.
"""

from pathlib import Path

import pytest
from test_unarmed import action, setup, state_of

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.vocabulary import Defense
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def pending_attack(tmp_path: Path, *, double: bool = True) -> tuple[str, PlayService]:
    cid, play = await setup(tmp_path, third_actor=True)
    state = await state_of(cid, play)
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="prepare-defense",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="all_out_defense" if double else "do_nothing",
            defense_option="double" if double else None,
        ),
        authenticated_actor_id="a",
    )
    await action(cid, play, "b", "kick")
    return cid, play


@pytest.mark.parametrize(
    ("first", "second", "rolls", "check_count", "parries"),
    [
        ("dodge", "parry", (2, 2, 2, 4, 4, 4, 2, 2, 2), 3, ("right-hand",)),
        ("parry", "dodge", (2, 2, 2, 4, 4, 4, 2, 2, 2), 3, ("left-hand",)),
        ("parry", "parry", (2, 2, 2, 4, 4, 4, 2, 2, 2), 3, ("left-hand", "right-hand")),
        ("dodge", "parry", (2, 2, 2, 2, 2, 2), 2, ()),
        ("parry", "parry", (2, 2, 2, 2, 2, 2), 2, ("left-hand",)),
    ],
)
async def test_ordered_double_defense_restart_and_replay(
    tmp_path: Path,
    first: Defense,
    second: Defense,
    rolls: tuple[int, ...],
    check_count: int,
    parries: tuple[str, ...],
) -> None:
    cid, play = await pending_attack(tmp_path)
    before = await state_of(cid, play)
    play = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    play.rng = RecordedDice(rolls)
    command = ChooseDefense(
        id="double-defense",
        actor_id="a",
        expected_revision=before.revision,
        encounter_id="fight",
        defense=first,
        item_id="left-hand" if first == "parry" else None,
        second_defense=second,
        second_item_id="right-hand" if second == "parry" else None,
    )
    service = CombatService(play)
    result = await service.execute(cid, command, authenticated_actor_id="a")
    assert play.rng.exhausted()
    after = await state_of(cid, play)
    trace = after.encounters[0].unarmed_history[-1]
    assert len(trace.checks) == check_count
    assert not trace.won and trace.injury == 0 and trace.blocked_reason is None
    assert trace.defenses == ((first, command.item_id), (second, command.second_item_id))
    assert next(p for p in after.encounters[0].participants if p.actor_id == "a").parries == parries
    assert after.encounters[0].pending_unarmed is None
    assert after.encounters[0].current_actor_id == "c"
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice(())
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="a") == result
    )
    with pytest.raises(ConflictError):
        await CombatService(restarted).execute(
            cid,
            command.model_copy(update={"second_item_id": "left-hand"}),
            authenticated_actor_id="a",
        )
    assert await state_of(cid, restarted) == after
    assert restarted.rng.exhausted()


@pytest.mark.parametrize(
    ("double", "first", "item", "second", "second_item"),
    [
        (False, "dodge", None, "parry", "right-hand"),
        (True, "dodge", None, "dodge", None),
        (True, "parry", "left-hand", "parry", "left-hand"),
        (True, "parry", None, "parry", None),
        (True, "none", None, "parry", "right-hand"),
        (True, "dodge", None, "none", None),
        (True, "dodge", None, None, "right-hand"),
        (True, "dodge", None, "parry", "sword-a"),
        (True, "dodge", None, "block", None),
    ],
)
async def test_invalid_fallback_rejected_before_dice(
    tmp_path: Path,
    double: bool,
    first: Defense,
    item: str | None,
    second: Defense | None,
    second_item: str | None,
) -> None:
    cid, play = await pending_attack(tmp_path, double=double)
    before = await state_of(cid, play)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await CombatService(play).execute(
            cid,
            ChooseDefense(
                id="invalid-fallback",
                actor_id="a",
                expected_revision=before.revision,
                encounter_id="fight",
                defense=first,
                item_id=item,
                second_defense=second,
                second_item_id=second_item,
            ),
            authenticated_actor_id="a",
        )
    assert await state_of(cid, play) == before
    assert play.rng.exhausted()


@pytest.mark.parametrize(
    ("rolls", "check_count", "blocked", "won", "parries"),
    [
        ((2, 2, 2, 4, 4, 4, 4, 4, 4, 1), 3, False, True, ("left-hand", "right-hand")),
        ((2, 2, 2, 6, 6, 6, 3, 3, 3, 1), 2, False, True, ("left-hand",)),
        ((2, 2, 2, 4, 4, 4, 6, 6, 6, 3, 3, 3, 1), 3, False, True, ("left-hand", "right-hand")),
        ((3, 3, 3, 2, 2, 2), 2, False, False, ()),
        ((1, 1, 1, 3, 3, 3, 1), 1, False, True, ()),
    ],
)
async def test_double_defense_damage_miss_and_critical_boundaries(
    tmp_path: Path,
    rolls: tuple[int, ...],
    check_count: int,
    blocked: bool,
    won: bool,
    parries: tuple[str, ...],
) -> None:
    cid, play = await pending_attack(tmp_path)
    before = await state_of(cid, play)
    play.rng = RecordedDice(rolls)
    command = ChooseDefense(
        id="boundary",
        actor_id="a",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="parry",
        item_id="left-hand",
        second_defense="parry",
        second_item_id="right-hand",
    )
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    assert play.rng.exhausted()
    after = await state_of(cid, play)
    encounter = after.encounters[0]
    trace = encounter.unarmed_history[-1]
    assert len(trace.checks) == check_count and trace.won == won
    assert bool(encounter.blocked_reason) == blocked
    assert trace.table_dice == (
        (3, 3, 3) if any(c.outcome.value.startswith("critical") for c in trace.checks) else ()
    )
    assert trace.defenses == (("parry", "left-hand"), ("parry", "right-hand"))
    assert next(p for p in encounter.participants if p.actor_id == "a").parries == parries
    assert encounter.current_actor_id == ("b" if blocked else "c")
    restarted = PlayService(AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine)
    restarted.rng = RecordedDice(())
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="a") == result
    )
    assert await state_of(cid, restarted) == after
