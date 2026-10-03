"""B346 selected repair methods change actual deadlines and durability."""

import json
import secrets
from pathlib import Path

import pytest
from pydantic import ValidationError as ModelError
from support.runtime import build_play, build_runtime
from test_armoury_repair_current_state import fixture
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import FailingCommitPlay

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.equipment.repair_time import SelectRepairTime
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService, RepairEquipment
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands

# Independent printed-source values; not imported from the implementation table.
CASES = [
    ("ordinary", 1800, 0),
    ("extra-2", 3600, 1),
    ("extra-4", 7200, 2),
    ("extra-8", 14400, 3),
    ("extra-15", 27000, 4),
    ("extra-30", 54000, 5),
    ("haste-10", 1620, -1),
    ("haste-20", 1440, -2),
    ("haste-30", 1260, -3),
    ("haste-40", 1080, -4),
    ("haste-50", 900, -5),
    ("haste-60", 720, -6),
    ("haste-70", 540, -7),
    ("haste-80", 360, -8),
    ("haste-90", 180, -9),
]
SKILLS = ["skill:armoury-melee-weapons", "skill:armoury-body-armor"]


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def advance(play: PlayService, cid: str, ticks: int, prefix: str = "work") -> None:
    chunks = [1800] * (ticks // 1800) + ([ticks % 1800] if ticks % 1800 else [])
    for number, amount in enumerate(chunks):
        before = play._load(await play.store.read(cid))
        await play.execute(
            cid,
            Wait(
                id=f"{prefix}-{number}",
                actor_id="b",
                expected_revision=before.revision,
                ticks=amount,
            ),
            principal_id="b",
        )
        assert (
            play._load(await play.store.read(cid)).resources.game_time
            == before.resources.game_time + amount
        )


def choice(rev: int, item_id: str, method: str = "extra-2") -> SelectRepairTime:
    return SelectRepairTime.model_validate(
        dict(
            id="selection",
            actor_id="b",
            expected_revision=rev,
            item_id=item_id,
            start_command_id="repair",
            method=method,
        )
    )


def start(rev: int, item_id: str) -> RepairEquipment:
    return RepairEquipment(
        id="repair",
        actor_id="b",
        expected_revision=rev,
        encounter_id="fight",
        item_id=item_id,
        stage="start",
    )


def finish(rev: int, item_id: str, identifier: str = "finish") -> RepairEquipment:
    return start(rev, item_id).model_copy(
        update={"id": identifier, "stage": "finish", "task_id": "repair"}
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill_id", SKILLS)
@pytest.mark.parametrize("method,seconds,adjustment", CASES)
async def test_all_selected_methods_apply_actual_deadline_skill_and_hp(
    tmp_path: Path, backend: str, skill_id: str, method: str, seconds: int, adjustment: int
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, skill_id)
    before = play._load(await play.store.read(cid))
    play.rng = RecordedDice(())
    selection = choice(before.revision, item_id, method)
    plan = await ArmouryService(play).execute(cid, selection, principal_id="b")
    assert plan.duration_seconds == seconds and plan.modifier == adjustment
    selected = await play.store.read(cid)
    assert play._load(selected).resources.items == before.resources.items
    assert play._load(selected).resources.game_time == 0
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await ArmouryService(restarted).execute(cid, selection, principal_id="b") == plan
    assert selected == await play.store.read(cid)
    public = json.dumps(
        {
            "operation": "armoury",
            "generation": 1,
            "principal_id": "b",
            "command": selection.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    assert await play.store.duplicate(cid, selection.id, public) == selected
    with pytest.raises(ConflictError, match="different input"):
        await play.store.duplicate(cid, selection.id, public + " ")
    command = start(await revision(play, cid), item_id)
    await CombatService(play).execute(cid, command, principal_id="b")
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.due - task.start == seconds and task.skill == 12 + adjustment
    assert task.time_plan == plan and play.rng.exhausted()
    # One second before the selected deadline still cannot finish; no RNG is spent.
    await advance(play, cid, seconds - 1)
    saved = await play.store.read(cid)
    with pytest.raises(ConflictError, match="deadline"):
        await CombatService(play).execute(
            cid, finish(await revision(play, cid), item_id, "early"), principal_id="b"
        )
    assert isinstance(play.rng, RecordedDice)
    assert saved == await play.store.read(cid) and play.rng.exhausted()
    await advance(play, cid, 1, "last-second")
    play.rng = RecordedDice((3, 3, 3))
    completion = finish(await revision(play, cid), item_id)
    receipt = await CombatService(play).execute(cid, completion, principal_id="b")
    final = await play.store.read(cid)
    state = play._load(final)
    item = next(i for i in state.resources.items if i.id == item_id)
    restored = max(1, 12 + adjustment - 9) if 12 + adjustment >= 9 else 0
    assert item.condition and item.condition.hp == 2 + restored
    settled = tasks(state.resources)[0]
    assert settled.check and settled.check.effective_target == 12 + adjustment
    assert settled.restored_hp == restored and settled.time_plan == plan and play.rng.exhausted()
    restarted.rng = RecordedDice(())
    assert await CombatService(restarted).execute(cid, completion, principal_id="b") == receipt
    assert final == await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill_id", SKILLS)
@pytest.mark.parametrize("ticks", [3600, 54000])
async def test_passive_wait_and_other_start_id_never_grant_time_bonus(
    tmp_path: Path, backend: str, skill_id: str, ticks: int
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, skill_id)
    await ArmouryService(play).execute(
        cid,
        choice(await revision(play, cid), item_id, "extra-30").model_copy(
            update={"start_command_id": "different-start"}
        ),
        principal_id="b",
    )
    await CombatService(play).execute(
        cid, start(await revision(play, cid), item_id), principal_id="b"
    )
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.time_plan is None and "time_plan" not in task.model_dump_json()
    assert task.skill == 12 and task.due == 1800
    await advance(play, cid, ticks)
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(
        cid, finish(await revision(play, cid), item_id), principal_id="b"
    )
    item = next(
        i for i in play._load(await play.store.read(cid)).resources.items if i.id == item_id
    )
    assert item.condition and item.condition.hp == 5


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selection_is_authorized_immutable_private_and_transactional(
    tmp_path: Path, backend: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, SKILLS[0])
    command = choice(await revision(play, cid), item_id)
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises((AuthorizationError, ValidationError)):
        await ArmouryService(play).execute(cid, command, principal_id="a")
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid,
            command.model_copy(update={"expected_revision": command.expected_revision - 1}),
            principal_id="b",
        )
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(()))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await ArmouryService(failing).execute(cid, command, principal_id="b")
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and events == await play.store.stream(cid)
    plan = await ArmouryService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError, match="immutable"):
        await ArmouryService(play).execute(
            cid,
            command.model_copy(
                update={
                    "id": "edit",
                    "method": "haste-50",
                    "expected_revision": await revision(play, cid),
                }
            ),
            principal_id="b",
        )
    with pytest.raises(ConflictError, match="different input"):
        await ArmouryService(play).execute(
            cid, command.model_copy(update={"method": "haste-50"}), principal_id="b"
        )
    view = json.dumps(await build_runtime(play).read(cid, principal_id="a"))
    assert "armoury-time:" not in view and plan.equipment_digest not in view
    await CombatService(play).execute(
        cid, start(await revision(play, cid), item_id), principal_id="b"
    )
    with pytest.raises(ConflictError, match="existing repair"):
        await ArmouryService(play).execute(
            cid,
            command.model_copy(
                update={
                    "id": "during",
                    "start_command_id": "later",
                    "expected_revision": await revision(play, cid),
                }
            ),
            principal_id="b",
        )
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("problem", ["owner", "damage", "tool", "approval"])
async def test_current_selection_setup_changes_refuse_start_before_rng(
    tmp_path: Path, backend: str, problem: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, SKILLS[0])
    await ArmouryService(play).execute(
        cid, choice(await revision(play, cid), item_id), principal_id="b"
    )

    def invalidate(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"approval": None})
                    if problem == "approval" and a.actor_id == "b"
                    else a
                    for a in state.actors
                ),
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"owner_id": "a"})
                            if problem == "owner" and i.id == item_id
                            else i.model_copy(
                                update={
                                    "condition": i.condition.model_copy(
                                        update={"hp": i.condition.hp + 1}
                                    )
                                }
                            )
                            if problem == "damage" and i.id == item_id and i.condition
                            else i
                            for i in state.resources.items
                            if not (problem == "tool" and i.id == "tool-b")
                        )
                    }
                ),
            }
        )

    await change(play, cid, invalidate)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises((ConflictError, ValidationError)):
        await CombatService(play).execute(
            cid, start(await revision(play, cid), item_id), principal_id="b"
        )
    assert isinstance(play.rng, RecordedDice)
    assert saved == await play.store.read(cid) and play.rng.exhausted()


@pytest.mark.parametrize(
    "method", ["extra-16", "extra-3", "haste-15", "haste-100", "instant", "extra-60"]
)
def test_unprinted_or_cinematic_methods_cannot_be_authored(method: str) -> None:
    with pytest.raises(ModelError):
        choice(1, "item", method)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill_id", SKILLS)
@pytest.mark.parametrize("method", [None, "extra-2", "haste-50"])
async def test_whole_selection_work_and_completion_seed_reexecution(
    tmp_path: Path, backend: str, skill_id: str, method: str | None
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, skill_id)
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    play.rng = secrets
    play.seeds = lambda: f"{3:064x}"
    if method is not None:
        await ArmouryService(play).execute(
            cid, choice(await revision(play, cid), item_id, method), principal_id="b"
        )
    await CombatService(play).execute(
        cid, start(await revision(play, cid), item_id), principal_id="b"
    )
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    await advance(play, cid, task.due - task.start)
    await CombatService(play).execute(
        cid, finish(await revision(play, cid), item_id), principal_id="b"
    )
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    final = await play.store.read(cid)
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert len(checks) == len(records) and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final == await play.store.replay(cid)
    settled = tasks(play._load(final).resources)[0]
    assert (settled.time_plan is None) == (method is None)
    if method is None:
        assert "time_plan" not in settled.model_dump_json()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize(
    "method,seconds,target,hp",
    [("extra-2", 3600, 11, 2), ("haste-50", 900, 5, 0), ("haste-70", 540, 3, 0)],
)
async def test_selected_major_repair_consumes_assessed_parts_once_and_rolls_actual_target(
    tmp_path: Path, backend: str, kind: str, method: str, seconds: int, target: int, hp: int
) -> None:
    from typing import cast

    from test_armoury_parts_assessment import assessment, setup
    from test_issue_818_armoury_acceptance import Kind

    cid, play = await setup(tmp_path, backend, cast(Kind, kind), stock=5, hp=0)
    play.rng = RecordedDice((1,))
    requirement = await assessment(play, cid)
    assert requirement.quantity == 5
    play.rng = RecordedDice(())
    plan = await ArmouryService(play).execute(
        cid, choice(await revision(play, cid), "repair-target", method), principal_id="b"
    )
    assert plan.duration_seconds == seconds
    command = start(await revision(play, cid), "repair-target")
    before = await play.store.read(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice(()))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await CombatService(failing).execute(cid, command, principal_id="b")
    assert before == await play.store.read(cid)
    receipt = await CombatService(play).execute(cid, command, principal_id="b")
    pending = await play.store.read(cid)
    task = tasks(play._load(pending).resources)[0]
    assert task.parts_die == 1 and task.parts_quantity == 5 and task.skill == target
    assert not any(i.id == "parts-b" for i in play._load(pending).resources.items)
    restarted = build_play(
        tmp_path / "restart-major", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == receipt
    assert pending == await play.store.read(cid)
    await advance(play, cid, seconds)
    completion = finish(await revision(play, cid), "repair-target")
    before_finish = await play.store.read(cid)
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await CombatService(failing).execute(cid, completion, principal_id="b")
    assert before_finish == await play.store.read(cid)
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(cid, completion, principal_id="b")
    final = play._load(await play.store.read(cid))
    task = tasks(final.resources)[0]
    assert task.check and task.check.effective_target == target and task.restored_hp == hp
    item = next(i for i in final.resources.items if i.id == "repair-target")
    assert item.condition and item.condition.hp == hp
    assert task.parts_quantity == 5 and task.time_plan == plan and play.rng.exhausted()
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("method", ["haste-80", "haste-90"])
async def test_haste_below_minimum_effective_skill_refuses_without_parts_roll(
    tmp_path: Path, backend: str, method: str
) -> None:
    from test_armoury_parts_assessment import setup

    cid, play = await setup(tmp_path, backend, "melee", stock=30, hp=0)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="at least 3"):
        await ArmouryService(play).execute(
            cid, choice(await revision(play, cid), "repair-target", method), principal_id="b"
        )
    assert before == await play.store.read(cid) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("selected", [True, False])
async def test_unassessed_parts_stream_remains_one_die_at_actual_start(
    tmp_path: Path, backend: str, selected: bool
) -> None:
    from test_armoury_parts_assessment import setup

    cid, play = await setup(tmp_path, backend, "armor", stock=30, hp=0)
    play.rng = RecordedDice(())
    if selected:
        await ArmouryService(play).execute(
            cid, choice(await revision(play, cid), "repair-target", "haste-10"), principal_id="b"
        )
    play.rng = RecordedDice((2,))
    await CombatService(play).execute(
        cid, start(await revision(play, cid), "repair-target"), principal_id="b"
    )
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.parts_die == 2 and task.parts_quantity == 10 and play.rng.exhausted()
    assert next(i.quantity for i in state.resources.items if i.id == "parts-b") == 20
    assert task.due == (1620 if selected else 1800) and task.skill == (9 if selected else 10)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_genesis_cannot_forge_time_selection(tmp_path: Path, backend: str) -> None:
    from test_actions import campaign

    from wayfarer.engine.simulation.actions import ActorSetup
    from wayfarer.engine.simulation.resources import ResourceEvent

    cid, play, _ = await fixture(tmp_path, backend, SKILLS[0])
    saved = await play.store.read(cid)
    state = play._load(saved)
    forged = state.resources.model_copy(
        update={
            "revision": 0,
            "events": (ResourceEvent(id="armoury-time:forged", at=0, target_id="b", kind="{}"),),
        }
    )
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            forged,
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )
    assert saved == await play.store.read(cid)


def test_public_repair_and_selector_cannot_author_private_plan_numbers() -> None:
    with pytest.raises(ModelError):
        RepairEquipment.model_validate(
            {**start(1, "item").model_dump(), "time_plan": {"modifier": 100}}
        )
    with pytest.raises(ModelError):
        SelectRepairTime.model_validate(
            {**choice(1, "item").model_dump(), "modifier": 100, "duration_seconds": 1}
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_extra_time_can_raise_actual_effective_skill_to_minimum(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import seed_campaign
    from test_issue_818_armoury_acceptance import workshop

    cid, original = await workshop(tmp_path / "source", "melee", hp=2, technology_level=4)
    play = build_play(tmp_path / "actual", original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="at least 3"):
        await CombatService(play).execute(
            cid, start(await revision(play, cid), "repair-target"), principal_id="b"
        )
    assert before == await play.store.read(cid)
    await ArmouryService(play).execute(
        cid, choice(await revision(play, cid), "repair-target", "extra-2"), principal_id="b"
    )
    await CombatService(play).execute(
        cid, start(await revision(play, cid), "repair-target"), principal_id="b"
    )
    state = play._load(await play.store.read(cid))
    assert (
        tasks(state.resources)[0].skill == 3
        and tasks(state.resources)[0].technology_level_penalty == -10
    )
    await advance(play, cid, 3600)
    play.rng = RecordedDice((1, 1, 1))
    await CombatService(play).execute(
        cid, finish(await revision(play, cid), "repair-target"), principal_id="b"
    )
    item = next(
        i for i in play._load(await play.store.read(cid)).resources.items if i.id == "repair-target"
    )
    assert item.condition and item.condition.hp == 3 and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("problem", ["owner", "damage", "tool"])
async def test_started_plan_preserves_deadline_and_refuses_current_setup_damage(
    tmp_path: Path, backend: str, problem: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, SKILLS[1])
    plan = await ArmouryService(play).execute(
        cid, choice(await revision(play, cid), item_id), principal_id="b"
    )
    await CombatService(play).execute(
        cid, start(await revision(play, cid), item_id), principal_id="b"
    )
    task = tasks(play._load(await play.store.read(cid)).resources)[0]
    await advance(play, cid, 3600)

    def invalidate(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"owner_id": "a"})
                            if problem == "owner" and i.id == item_id
                            else i.model_copy(
                                update={
                                    "condition": i.condition.model_copy(
                                        update={"hp": i.condition.hp + 1}
                                    )
                                }
                            )
                            if problem == "damage" and i.id == item_id and i.condition
                            else i
                            for i in state.resources.items
                            if not (problem == "tool" and i.id == "tool-b")
                        )
                    }
                )
            }
        )

    await change(play, cid, invalidate)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises((ValidationError, ConflictError)):
        await CombatService(play).execute(
            cid, finish(await revision(play, cid), item_id), principal_id="b"
        )
    assert isinstance(play.rng, RecordedDice)
    assert saved == await play.store.read(cid) and play.rng.exhausted()
    retained = tasks(play._load(saved).resources)[0]
    assert retained == task and retained.time_plan == plan and retained.due == 3600


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_cancelled_work_never_reuses_its_immutable_start_selection(
    tmp_path: Path, backend: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, SKILLS[0])
    command = choice(await revision(play, cid), item_id)
    plan = await ArmouryService(play).execute(cid, command, principal_id="b")
    await CombatService(play).execute(
        cid, start(await revision(play, cid), item_id), principal_id="b"
    )
    cancel = finish(await revision(play, cid), item_id, "cancel").model_copy(
        update={"stage": "cancel"}
    )
    receipt = await CombatService(play).execute(cid, cancel, principal_id="b")
    saved = await play.store.read(cid)
    task = tasks(play._load(saved).resources)[0]
    assert task.status == "cancelled" and task.restored_hp == 0 and task.time_plan == plan
    assert await CombatService(play).execute(cid, cancel, principal_id="b") == receipt
    with pytest.raises(ConflictError, match="immutable"):
        await ArmouryService(play).execute(
            cid,
            command.model_copy(
                update={"id": "reuse", "expected_revision": await revision(play, cid)}
            ),
            principal_id="b",
        )
    assert isinstance(play.rng, RecordedDice)
    assert saved == await play.store.read(cid) and play.rng.exhausted()
    later = start(await revision(play, cid), item_id).model_copy(update={"id": "new-attempt"})
    await CombatService(play).execute(cid, later, principal_id="b")
    fresh = tasks(play._load(await play.store.read(cid)).resources)[-1]
    assert fresh.time_plan is None and fresh.skill == 12 and fresh.due == 1800


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_bound_actor_and_item_mismatch_cannot_consume_selected_plan(
    tmp_path: Path, backend: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, SKILLS[0])
    await ArmouryService(play).execute(
        cid, choice(await revision(play, cid), item_id), principal_id="b"
    )

    def transfer(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"owner_id": "a"})
                            if i.id in (item_id, "tool-b")
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )

    await change(play, cid, transfer)
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises((ConflictError, ValidationError)):
        await CombatService(play).execute(
            cid,
            start(await revision(play, cid), item_id).model_copy(update={"actor_id": "a"}),
            principal_id="a",
        )
    assert isinstance(play.rng, RecordedDice)
    assert saved == await play.store.read(cid) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selection_clock_before_start_is_not_counted_as_work(
    tmp_path: Path, backend: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, SKILLS[0])
    await ArmouryService(play).execute(
        cid, choice(await revision(play, cid), item_id), principal_id="b"
    )
    await advance(play, cid, 600, "before-start")
    await CombatService(play).execute(
        cid, start(await revision(play, cid), item_id), principal_id="b"
    )
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.start == 600 and task.due == 4200 and task.skill == 13
    saved = await play.store.read(cid)
    with pytest.raises(ConflictError, match="deadline"):
        await CombatService(play).execute(
            cid, finish(await revision(play, cid), item_id), principal_id="b"
        )
    assert isinstance(play.rng, RecordedDice)
    assert saved == await play.store.read(cid) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selector_cannot_reserve_its_own_already_consumed_command_id(
    tmp_path: Path, backend: str
) -> None:
    cid, play, item_id = await fixture(tmp_path, backend, SKILLS[0])
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ConflictError, match="immutable"):
        await ArmouryService(play).execute(
            cid,
            choice(await revision(play, cid), item_id).model_copy(
                update={"start_command_id": "selection"}
            ),
            principal_id="b",
        )
    assert isinstance(play.rng, RecordedDice)
    assert saved == await play.store.read(cid) and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("method", ["extra-2", "haste-50"])
async def test_selected_time_cannot_bypass_unsupported_tool_operating_limit(
    tmp_path: Path, backend: str, method: str
) -> None:
    from support.runtime import seed_campaign
    from test_issue_818_armoury_acceptance import workshop

    from wayfarer.engine.rules.types.general_equipment import GeneralEquipmentFeature

    cid, original = await workshop(
        tmp_path / "source",
        "melee",
        hp=2,
        tool_features=(
            GeneralEquipmentFeature(kind="tool", skill_id=SKILLS[0], duration_seconds=60),
        ),
    )
    play = build_play(tmp_path / "actual", original.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, await original.store.read(cid))
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="operating limits"):
        await ArmouryService(play).execute(
            cid, choice(await revision(play, cid), "repair-target", method), principal_id="b"
        )
    assert isinstance(play.rng, RecordedDice)
    assert saved == await play.store.read(cid) and play.rng.exhausted()
