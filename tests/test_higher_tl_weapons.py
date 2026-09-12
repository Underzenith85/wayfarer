"""Characters third-printing B278/B280 representative higher-TL behavior."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.combat import RangedSituation
from wayfarer.engine.simulation.equipment.basic.catalog import BASIC_EQUIPMENT
from wayfarer.engine.simulation.equipment.basic.ultratech import ULTRATECH_INDEX
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.engine.simulation.resources import Item, RechargePowerCell
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import PlayService


def selected_mode(definition_id: str) -> RangedMode:
    entry = next(e for e in BASIC_EQUIPMENT.entries if e.definition_id == definition_id)
    mode = entry.modes[0]
    assert isinstance(mode, RangedMode)
    return mode


async def load_weapon(cid: str, play: PlayService, *, mode_id: str, seconds: int) -> None:
    for _ in range(seconds):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id=mode_id,
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")


def test_selected_gyroc_and_laser_rows_are_exact_and_executable() -> None:
    entries = {entry.definition_id: entry for entry in BASIC_EQUIPMENT.entries}
    gyroc = entries["equipment:gyroc-pistol-15mm"]
    laser = entries["equipment:laser-pistol"]
    cell = entries["equipment:laser-pistol-cell"]
    assert (gyroc.price, gyroc.weight_millipounds, gyroc.technology_level) == (200, 600, 9)
    assert (laser.price, laser.weight_millipounds, laser.technology_level) == (2800, 2800, 10)
    assert (cell.price, cell.weight_millipounds, cell.power_cell_capacity) == (10, 500, 400)
    assert (
        gyroc.weight_millipounds
        + 4 * entries["equipment:gyroc-pistol-15mm-round"].weight_millipounds
        == 1000
    )
    assert laser.weight_millipounds + cell.weight_millipounds == 3300
    assert not {"equipment:laser-pistol"} & {entry.definition_id for entry in ULTRATECH_INDEX}


async def test_gyroc_acceleration_divides_rolled_damage_by_range(tmp_path: Path) -> None:
    mode = selected_mode("equipment:gyroc-pistol-15mm").model_copy(
        update={"skill_id": "skill:broadsword"}
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=mode,
        ranged_scene=(RangedSituation(attacker_id="a", defender_id="b", distance_yards=2),),
    )
    await load_weapon(cid, play, mode_id="shot", seconds=3)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="shot",
    )
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 6, 6, 6] + [3] * 12)
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.per_hit_damage == (12,)
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_smartgun_laser_beam_cell_environment_and_recharge(tmp_path: Path) -> None:
    mode = selected_mode("equipment:laser-pistol").model_copy(
        update={"skill_id": "skill:broadsword"}
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=mode,
        ranged_scene=(
            RangedSituation(
                attacker_id="a",
                defender_id="b",
                distance_yards=2,
                beam_environment_dr=4,
                laser_visible_to_firer=True,
                laser_visible_to_target=True,
            ),
        ),
        power_cell_capacity=400,
        extra_items=(
            Item(
                id="ammo-b",
                definition_id="equipment:laser-pistol-cell",
                owner_id="a",
                charges=400,
            ),
        ),
    )
    await load_weapon(cid, play, mode_id="beam", seconds=3)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        mode_id="beam",
        laser_sight=True,
    )
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 4, 4, 4] + [3] * 12)
    result = await defend(cid, play, "b", "dodge")
    assert result.injury is not None and result.injury.resistance == 4
    assert result.injury.attack.effective_target == 14
    assert result.injury.defense is not None and result.injury.defense.effective_target == 10
    state = play._load(await play.store.read(cid))
    cell = next(item for item in state.resources.items if item.id == "ammo-a")
    assert (cell.quantity, cell.charges) == (1, 399)

    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "ready", item_id="sword-a", mode_id="beam", unload_ammunition=True)
    await turn(cid, play, "b", "do_nothing")
    for _ in range(3):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="beam",
            reload_ammunition_id="ammo-b",
        )
        await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    load = next(load for load in state.resources.ammunition_loads if load.weapon_id == "sword-a")
    assert (load.ammunition_item_id, load.rounds) == ("ammo-b", 400)
    assert next(item for item in state.resources.items if item.id == "ammo-a").charges == 399
    command = RechargePowerCell(
        id="recharge-cell",
        actor_id="a",
        expected_revision=state.resources.revision,
        item_id="ammo-a",
        charges=1,
        source_id="power-source:bench",
    )
    recharged = play.engine.resources.apply(state.resources, command, system=True)
    assert next(item for item in recharged.items if item.id == "ammo-a").charges == 400
    assert recharged.events[-1].kind == "power-cell:power-source:bench:1"
    assert play.engine.resources.apply(recharged, command, system=True) == recharged
    with pytest.raises(ValidationError, match="capacity"):
        play.engine.resources.apply(
            recharged,
            command.model_copy(
                update={"id": "overcharge", "expected_revision": recharged.revision}
            ),
            system=True,
        )


async def test_smartgun_rejects_an_unauthorized_owner(tmp_path: Path) -> None:
    mode = selected_mode("equipment:laser-pistol").model_copy(
        update={"skill_id": "skill:broadsword"}
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=(RangedSituation(attacker_id="a", defender_id="b", distance_yards=2),),
        power_cell_capacity=400,
    )
    state = play._load(await play.store.read(cid))
    denied = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        item.model_copy(update={"authorized_actor_ids": ("b",)})
                        if item.id == "sword-a"
                        else item
                        for item in state.resources.items
                    )
                }
            )
        }
    )
    from wayfarer.engine.simulation.combat.melee import mode as select_mode

    with pytest.raises(ValidationError, match="denies"):
        select_mode(play.rules_context, denied, "a", "sword-a", "beam")
