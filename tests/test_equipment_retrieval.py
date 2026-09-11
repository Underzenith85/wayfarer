"""Owned ground custody, elapsed recovery work and durable command retries."""

from pathlib import Path

import pytest
from test_gurps_melee import attack, choice, setup

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, EndEncounter, RetrieveEquipment
from wayfarer.orchestration.equipment_view import equipment_view
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.actions import Wait
from wayfarer.simulation.mechanics.equipment_retrieval import tasks


async def test_off_board_retrieval_requires_completed_encounter_owner_and_time(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    await attack(cid, play)
    play.rng = RecordedDice([6, 6, 6, 4, 5, 5, 6, 6])
    await CombatService(play).execute(cid, choice(), authenticated_actor_id="b")
    state = play._load(await play.store.read(cid))
    sword = next(i for i in state.resources.items if i.id == "sword-a")
    assert sword.ground and sword.ground.x == -6
    command = RetrieveEquipment(
        id="retrieve",
        actor_id="a",
        expected_revision=3,
        encounter_id="fight",
        item_id="sword-a",
        stage="start",
    )
    with pytest.raises(ValidationError, match="completed"):
        await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end",
            actor_id="gm",
            expected_revision=3,
            encounter_id="fight",
            reason="finished",
        ),
        authenticated_actor_id="gm",
    )
    command = command.model_copy(update={"expected_revision": 4})
    play.rng = RecordedDice([])
    projected = equipment_view(play, play._load(await play.store.read(cid)), "a")
    assert projected[0].ground == sword.ground
    assert projected[0].choices[0].label == "Start retrieval"
    assert equipment_view(play, play._load(await play.store.read(cid)), "b") == ()
    with pytest.raises(ValidationError, match="owned"):
        await CombatService(play).execute(
            cid, command.model_copy(update={"actor_id": "b"}), authenticated_actor_id="b"
        )
    first = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="a") == first
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.due > state.resources.game_time
    finish = command.model_copy(
        update={
            "id": "finish",
            "stage": "finish",
            "task_id": command.id,
            "expected_revision": state.revision,
        }
    )
    with pytest.raises(ConflictError, match="deadline"):
        await CombatService(play).execute(cid, finish, authenticated_actor_id="a")
    await play.execute(
        cid,
        Wait(
            id="walk",
            actor_id="a",
            expected_revision=state.revision,
            ticks=task.due - state.resources.game_time,
        ),
        authenticated_actor_id="a",
    )
    state = play._load(await play.store.read(cid))
    finish = finish.model_copy(update={"expected_revision": state.revision})
    result = await CombatService(play).execute(cid, finish, authenticated_actor_id="a")
    state = play._load(await play.store.read(cid))
    sword = next(i for i in state.resources.items if i.id == "sword-a")
    assert sword.ground is None and sword.owner_id == "a" and not sword.ready and not sword.equipped
    assert tasks(state.resources)[0].status == "completed"
    assert await CombatService(play).execute(cid, finish, authenticated_actor_id="a") == result
