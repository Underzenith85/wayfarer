"""B178 unfamiliar Armoury models change actual HP, independently of TL."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_armoury_repair_current_state import fixture
from test_combat_sensory_authority import change

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.equipment.armoury_context import (
    DeclareArmouryFamiliarity,
    observations,
)
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService, RepairEquipment
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill_id", ["skill:armoury-melee-weapons", "skill:armoury-body-armor"])
@pytest.mark.parametrize(
    "basis,target,hp",
    [("unfamiliar-model", 10, 3), ("known-model", 12, 5), ("similar-model", 12, 5)],
)
async def test_director_observed_familiarity_changes_real_repair_hp(
    tmp_path: Path,
    backend: str,
    skill_id: str,
    basis: str,
    target: int,
    hp: int,
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, skill_id)
    state = play._load(await play.store.read(cid))
    command = DeclareArmouryFamiliarity.model_validate(
        dict(
            id="familiarity",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="b",
            item_id=item_id,
            basis=basis,
        )
    )
    play.rng = RecordedDice(())
    before = state.resources.items
    result = await ArmouryService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert result.skill_id == skill_id and result.performer_id == "b"
    assert state.resources.items == before and state.resources.game_time == 0
    assert observations(state.resources) == (result,) and play.rng.exhausted()
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    saved = await play.store.read(cid)
    assert await ArmouryService(restarted).execute(cid, command, principal_id="gm") == result
    assert saved == await play.store.read(cid)
    await CombatService(play).execute(
        cid,
        RepairEquipment(
            id="repair",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id=item_id,
            stage="start",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.skill == target and task.technology_level_penalty == 0
    # An accepted repair keeps its captured familiarity facts even after a new observation.
    if basis == "unfamiliar-model":
        await ArmouryService(play).execute(
            cid,
            command.model_copy(
                update={
                    "id": "new-observation",
                    "expected_revision": state.revision,
                    "basis": "known-model",
                }
            ),
            principal_id="gm",
        )
        state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=state.revision, ticks=1800),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    finish = RepairEquipment(
        id="finish",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        item_id=item_id,
        stage="finish",
        task_id="repair",
    )
    play.rng = RecordedDice((3, 3, 3))
    result_finish = await CombatService(play).execute(cid, finish, principal_id="b")
    state = play._load(await play.store.read(cid))
    item = next(i for i in state.resources.items if i.id == item_id)
    task = tasks(state.resources)[0]
    assert item.condition and item.condition.hp == hp
    assert task.check and task.check.effective_target == target and task.restored_hp == hp - 2
    assert play.rng.exhausted()
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    assert await CombatService(play).execute(cid, finish, principal_id="b") == result_finish
    assert saved == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_familiarity_authority_and_current_membership_on_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, "skill:armoury-melee-weapons")
    state = play._load(await play.store.read(cid))
    command = DeclareArmouryFamiliarity(
        id="familiarity",
        actor_id="gm",
        expected_revision=state.revision,
        performer_id="b",
        item_id=item_id,
        basis="unfamiliar-model",
    )
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="director"):
        await ArmouryService(play).execute(cid, command, principal_id="b")
    assert saved == await play.store.read(cid)
    await ArmouryService(play).execute(cid, command, principal_id="gm")
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid, command.model_copy(update={"basis": "known-model"}), principal_id="gm"
        )
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid, command.model_copy(update={"id": "stale"}), principal_id="gm"
        )

    def demote(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "members": tuple(
                    member.model_copy(update={"role": "player", "actor_ids": ("b",)})
                    if member.principal_id == "gm"
                    else member
                    for member in state.members
                )
            }
        )

    await change(play, cid, demote)
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director"):
        await ArmouryService(play).execute(cid, command, principal_id="gm")
    assert saved == await play.store.read(cid) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("invalid", ["other-owner", "remote", "unsupported", "unapproved"])
async def test_familiarity_source_admission_refuses_ineligible_current_equipment(
    tmp_path: Path, backend: str, invalid: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, "skill:armoury-melee-weapons")
    if invalid == "remote":

        def remote(state: PlayState) -> PlayState:
            return state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "items": tuple(
                                item.model_copy(update={"world_ground_location_id": "far"})
                                if item.id == item_id
                                else item
                                for item in state.resources.items
                            )
                        }
                    )
                }
            )

        await change(play, cid, remote)
    elif invalid == "unapproved":

        def withdraw(state: PlayState) -> PlayState:
            return state.model_copy(
                update={
                    "actors": tuple(
                        actor.model_copy(update={"approval": None})
                        if actor.actor_id == "b"
                        else actor
                        for actor in state.actors
                    )
                }
            )

        await change(play, cid, withdraw)
    state = play._load(await play.store.read(cid))
    command = DeclareArmouryFamiliarity(
        id="invalid",
        actor_id="gm",
        expected_revision=state.revision,
        performer_id="a" if invalid == "other-owner" else "b",
        item_id="tool-b" if invalid == "unsupported" else item_id,
        basis="known-model",
    )
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await ArmouryService(play).execute(cid, command, principal_id="gm")
    assert saved == await play.store.read(cid) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_familiarity_and_repair_seed_reexecution_match_hp_and_custody(
    tmp_path: Path, backend: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, "skill:armoury-melee-weapons")
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    for number in range(1000):
        seed = f"{number:064x}"
        rng = SeededRandom(seed)
        if sum(rng.randbelow(6) + 1 for _ in range(3)) == 9:
            break
    else:
        raise AssertionError("No source oracle seed")
    play.rng = secrets
    play.seeds = lambda: seed
    state = play._load(initial)
    await ArmouryService(play).execute(
        cid,
        DeclareArmouryFamiliarity(
            id="familiarity",
            actor_id="gm",
            expected_revision=state.revision,
            performer_id="b",
            item_id=item_id,
            basis="unfamiliar-model",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        RepairEquipment(
            id="repair",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id=item_id,
            stage="start",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=state.revision, ticks=1800),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        RepairEquipment(
            id="finish",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id=item_id,
            stage="finish",
            task_id="repair",
        ),
        principal_id="b",
    )
    records = (await play.store.history(cid))[count:]
    ids = {record.command_id for record in records}
    events = [event for event in await play.store.stream(cid) if event.command_id in ids]
    replayed, checks = await verify_commands(
        initial,
        records,
        events,
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == 4 and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)
    item = next(i for i in play._load(replayed).resources.items if i.id == item_id)
    assert item.condition and item.condition.hp == 3 and item.owner_id == "b" and item.quantity == 1
