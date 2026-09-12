"""Durable context for critical consequences whose capability is unavailable."""

from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from test_gurps_melee import attack, choice, setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.critical import CriticalMiss, load_critical, save_critical
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


@pytest.mark.parametrize("table", [(1, 1, 1), (2, 2, 1), (2, 2, 2), (5, 5, 5)])
async def test_blocked_context_survives_restart_and_exact_retry(
    tmp_path: Path, table: tuple[int, int, int]
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    play.rng = RecordedDice([6, 6, 6, *table])
    result = await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    events = tuple(e for e in state.resources.events if e.id.startswith("critical:"))
    assert len(events) == 1
    record = CriticalMiss.model_validate_json(events[0].kind)
    assert record.subject_id == "a" and record.item_id == "sword-a"
    assert record.action == "attack" and record.incoming is None
    assert record.table_rolls == (table,)
    assert record.table_total == sum(table)
    assert record.weapons[0].mode.id == "swing"
    assert record.blocker == state.encounters[0].blocked_reason
    assert load_critical(state.resources, record.id) == record
    assert save_critical(state.resources, record) is state.resources
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert (
        await CombatService(restarted).execute(cid, choice(), authenticated_actor_id="b") == result
    )
    restored = restarted._load(await restarted.store.read(cid))
    assert restored.resources == state.resources
    with pytest.raises(ConflictError, match="cannot be replaced"):
        save_critical(state.resources, record.model_copy(update={"ht": record.ht + 1}))
    bad_event = events[0].model_copy(update={"target_id": "b"})
    with pytest.raises(ValidationError, match="identity"):
        load_critical(state.resources.model_copy(update={"events": (bad_event,)}), record.id)
    with pytest.raises(SchemaError, match="incoming wound"):
        CriticalMiss.model_validate(record.model_copy(update={"action": "parry"}))


async def test_critical_parry_captures_deferred_incoming_damage(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 2, 2, 1])
    result = await CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith("critical:"))
    record = CriticalMiss.model_validate_json(event.kind)
    assert record.action == "parry" and record.subject_id == "b"
    assert record.item_id == "sword-b"
    assert record.incoming is not None and record.incoming.actor_id == "b"
    assert record.incoming.damage_type == "cut"
    assert result.injury is not None and result.injury.damage_dice == ()
    assert result.injury.hp_before == result.injury.hp_after
    assert record.blocker == "basic-critical-miss:5:defender"
    with pytest.raises(SchemaError, match="attacking"):
        CriticalMiss.model_validate(record.model_copy(update={"action": "attack"}))


@pytest.mark.parametrize("table", [(2, 2, 3), (2, 3, 3), (3, 3, 3), (5, 5, 6)])
async def test_ordinary_critical_parry_does_not_cancel_incoming_hit(
    tmp_path: Path, table: tuple[int, int, int]
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, *table, 3, 3, 3, 3])
    result = await CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="b")
    assert result.injury is not None
    assert result.injury.adjudication_required is None
    assert result.injury.basic_damage == 4 and result.injury.hp_after == 4
    state = play._load(await play.store.read(cid))
    assert not any(e.id.startswith("critical:") for e in state.resources.events)
    play.rng = RecordedDice([])
    assert (
        await CombatService(play).execute(cid, choice("parry"), authenticated_actor_id="b")
        == result
    )
