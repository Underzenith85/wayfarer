"""B376/B381/B383 thrown instance and critical catch regression fixtures."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import scene, weapon

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.thrown.flight import position
from wayfarer.engine.simulation.combat.thrown.items import record
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, DeclareThrownLanding, TakeCombatTurn
from wayfarer.orchestration.equipment_view import equipment_view
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


@pytest.mark.parametrize("roll,caught", [([3, 3, 3, 1, 1, 1], True), ([3, 3, 3, 2, 2, 2], False)])
async def test_catch_requires_critical_success(
    tmp_path: Path, roll: list[int], caught: bool
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        free_defender_hand=True,
        ranged_mode=weapon(thrown=True).model_copy(update={"catchable": True}),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice(roll)
    result = await defend(cid, play, "b", "parry", item_id="left-hand", catch_thrown=True)
    assert result.injury is not None and result.injury.hits == 0
    state = play._load(await play.store.read(cid))
    history = record(state.resources, "sword-a")
    assert history is not None and history.caught_by == ("b" if caught else None)
    assert (
        sum(i.id == "sword-a" for i in (*state.resources.items, *state.resources.expended_items))
        == 1
    )
    if caught:
        item = next(i for i in state.resources.items if i.id == "sword-a")
        assert item.owner_id == "b" and item.ready
        b = next(p for p in state.encounters[0].participants if p.actor_id == "b")
        assert (item.id, "left-hand") in b.hand_bindings
    else:
        assert next(i for i in state.resources.expended_items if i.id == "sword-a").ground is None
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_unresolved_landing_gm_declaration_and_recovery_receipt(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        free_defender_hand=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([5, 5, 5])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    history = record(state.resources, "sword-a")
    assert history is not None and history.landing is None
    view = next(i for i in equipment_view(play, state, "a") if i.id == "sword-a")
    assert view.work == "Landing unresolved" and not view.choices
    a = next(p for p in state.encounters[0].participants if p.actor_id == "a")
    command = DeclareThrownLanding(
        id="landing",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        item_id="sword-a",
        landing=position(state.encounters[0], a),
    )
    await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    await turn(cid, play, "b", "do_nothing")
    with pytest.raises(ValidationError, match="Ground recovery"):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            recover_thrown_item=True,
            ready_hand="right-hand",
        )
    await turn(cid, play, "a", "change_posture", posture="kneeling")
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    original = next(i for i in state.resources.expended_items if i.id == "sword-a")
    recovery = TakeCombatTurn(
        id="recover",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="ready",
        item_id="sword-a",
        recover_thrown_item=True,
        ready_hand="right-hand",
    )
    result = await CombatService(play).execute(cid, recovery, authenticated_actor_id="a")
    # The actual fixture store path is retained for a genuine connection restart.
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine, rng=RecordedDice([])
    )
    assert (
        await CombatService(restarted).execute(cid, recovery, authenticated_actor_id="a") == result
    )
    state = play._load(await play.store.read(cid))
    item = next(i for i in state.resources.items if i.id == original.id)
    assert (
        item.condition == original.condition and item.quantity == original.quantity and item.ready
    )
    assert not any(i.id == item.id for i in state.resources.expended_items)
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_occupied_hand_rejects_without_consuming_dice(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=True).model_copy(update={"catchable": True}),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="free usable"):
        await defend(cid, play, "b", "parry", item_id="left-hand", catch_thrown=True)
    assert await play.store.read(cid) == before


async def test_hit_records_target_and_remote_recovery_rejects(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 3, 1])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    history = record(state.resources, "sword-a")
    target = next(p for p in state.encounters[0].participants if p.actor_id == "b")
    assert history is not None and history.landing == position(state.encounters[0], target)
    await turn(cid, play, "b", "do_nothing")
    with pytest.raises(ValidationError, match="Move to"):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            recover_thrown_item=True,
            ready_hand="right-hand",
        )


async def test_barehand_critical_failure_uses_unarmed_table(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        free_defender_hand=True,
        ranged_mode=weapon(thrown=True).model_copy(update={"catchable": True}),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    # B557 8: fall down; incoming projectile still hits for 1 damage.
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 2, 3, 3, 1])
    result = await defend(cid, play, "b", "parry", item_id="left-hand", catch_thrown=True)
    assert result.injury is not None and result.injury.critical_table == (2, 3, 3)
    state = play._load(await play.store.read(cid))
    target = next(p for p in state.encounters[0].participants if p.actor_id == "b")
    assert target.posture == "prone"
    history = record(state.resources, "sword-a")
    assert history is not None and history.caught_by is None


async def test_recovery_rejects_broken_items_and_hidden_observers(tmp_path: Path) -> None:
    from wayfarer.engine.rules.types.object import ObjectCondition
    from wayfarer.engine.simulation.combat.thrown.items import recover

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([5, 5, 5])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    actor = next(p for p in encounter.participants if p.actor_id == "a")
    landing = position(encounter, actor)
    await CombatService(play).execute(
        cid,
        DeclareThrownLanding(
            id="land",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id="sword-a",
            landing=landing,
        ),
        authenticated_actor_id="gm",
    )
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0].model_copy(
        update={
            "participants": tuple(
                p.model_copy(update={"posture": "kneeling"}) if p.actor_id == "a" else p
                for p in state.encounters[0].participants
            )
        }
    )
    item = state.resources.expended_items[0]
    broken = item.model_copy(update={"condition": ObjectCondition(hp=-100, disabled=True)})
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"expended_items": (broken,)})}
    )
    command = TakeCombatTurn(
        id="recover",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="ready",
        item_id=item.id,
        recover_thrown_item=True,
        ready_hand="right-hand",
    )
    with pytest.raises(ValidationError, match="broken"):
        recover(play.rules_context, state, encounter, command)
    assert not equipment_view(play, state, "unrelated-observer")
