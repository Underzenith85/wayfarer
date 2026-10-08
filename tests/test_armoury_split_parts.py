"""B485's exact parts value can span multiple accessible owned inventory stacks."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play
from test_armoury_parts_assessment import assessment, revision, setup
from test_combat_sensory_authority import change
from test_issue_818_armoury_acceptance import Kind, begin

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.resources import Item
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def split_stock(play: PlayService, cid: str, total: int, *, decoys: bool = False) -> None:
    def split(state: PlayState) -> PlayState:
        first = next(i for i in state.resources.items if i.id == "parts-b")
        additions: tuple[Item, ...] = (
            first.model_copy(update={"id": "parts-b-second", "quantity": total - 2}),
        )
        if decoys:
            additions += (
                first.model_copy(update={"id": "parts-a", "owner_id": "a", "quantity": 100}),
                first.model_copy(
                    update={
                        "id": "parts-ground",
                        "quantity": 100,
                        "ground": GroundPosition(encounter_id="fight", geometry="grid", x=1, y=2),
                    }
                ),
            )
        resources = state.resources.model_copy(
            update={
                "items": tuple(
                    i.model_copy(update={"quantity": 2}) if i.id == first.id else i
                    for i in state.resources.items
                )
                + additions
            }
        )
        return state.model_copy(update={"resources": resources})

    await change(play, cid, split)


def parts(state: PlayState) -> dict[str, Item]:
    return {i.id: i for i in state.resources.items if i.definition_id == "equipment:spare-parts"}


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize("assessed", [False, True])
@pytest.mark.parametrize("die", [1, 2, 6])
async def test_split_parts_fund_exact_major_repair_and_survive_retry(
    tmp_path: Path, backend: str, kind: Kind, assessed: bool, die: int
) -> None:
    cid, play = await setup(tmp_path, backend, kind, stock=30)
    await split_stock(play, cid, 30)
    play.rng = RecordedDice((die,))
    if assessed:
        assert (await assessment(play, cid)).quantity == 5 * die
        assert play.rng.exhausted()
        play.rng = RecordedDice(())
    start = begin(await revision(play, cid))
    result = await CombatService(play).execute(cid, start, principal_id="b")
    assert play.rng.exhausted()
    pending = play._load(await play.store.read(cid))
    task = tasks(pending.resources)[0]
    assert task.parts_die == die and task.parts_quantity == 5 * die
    assert sum(i.quantity for i in parts(pending).values()) == 30 - 5 * die
    assert "parts-b" not in parts(pending)
    saved = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, start, principal_id="b") == result
    assert await play.store.read(cid) == saved
    await restarted.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=pending.revision, ticks=1800),
        principal_id="b",
    )
    finish = begin(await revision(restarted, cid)).model_copy(
        update={"id": "finish", "stage": "finish", "task_id": start.id}
    )
    restarted.rng = RecordedDice((3, 3, 3))
    await CombatService(restarted).execute(cid, finish, principal_id="b")
    after = restarted._load(await restarted.store.read(cid))
    completed = tasks(after.resources)[0]
    assert completed.restored_hp == 1 and completed.check and completed.check.effective_target == 10
    assert parts(after) == parts(pending)
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("assessed", [False, True])
async def test_unusable_stacks_do_not_cover_shortfall_or_spend_dice(
    tmp_path: Path, backend: str, assessed: bool
) -> None:
    cid, play = await setup(tmp_path, backend, "armor", stock=4)
    await split_stock(play, cid, 4, decoys=True)
    if assessed:
        play.rng = RecordedDice((1,))
        assert (await assessment(play, cid)).quantity == 5
    saved = await play.store.read(cid)
    stream = await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="parts quantity|maximum parts cost"):
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    assert play.rng.exhausted()
    assert await play.store.read(cid) == saved and await play.store.stream(cid) == stream


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("assessed", [False, True])
async def test_split_stock_command_reexecution_matches_committed_campaign(
    tmp_path: Path, backend: str, assessed: bool
) -> None:
    cid, play = await setup(tmp_path, backend, "armor", stock=30)
    await split_stock(play, cid, 30)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    if assessed:
        await assessment(play, cid)
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", expected_revision=await revision(play, cid), ticks=1800),
        principal_id="b",
    )
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    records = (await play.store.history(cid))[count:]
    identifiers = {record.command_id for record in records}
    events = [event for event in await play.store.stream(cid) if event.command_id in identifiers]
    replayed, checks = await verify_commands(
        initial,
        records,
        events,
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(check.folded and check.reexecuted for check in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)
