"""#977 internally timed, explicitly selected kits conserve native active seconds.

The kit duration is an authored constraint, not a universal Basic Set fuel rate.
B345 quality and B484 half-hour repair values remain independent source oracles.
"""

import secrets
from copy import copy
from pathlib import Path

import pytest
from support.armoury_defaults import Kind, declare, fixture, revision, select, wait
from support.runtime import build_play, build_runtime, played, seed_campaign
from test_armoury_alternative_tools import alternative, choose_alternative
from test_issue_818_armoury_acceptance import begin

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.equipment.general import (
    UseGeneralEquipment,
    apply_general_equipment,
)
from wayfarer.engine.simulation.equipment.repair_time import SelectRepairTime
from wayfarer.engine.simulation.equipment.repair_tool_operation import PREFIX, current
from wayfarer.engine.simulation.equipment.repair_transitions import repair
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.engine.simulation.resources import ResourceEvent, Transfer
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.advancement import ApplyMigration, MigrationService
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService, generations
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.projections import ViewRequest, project
from wayfarer.persistence.replay import verify_commands


async def prepared(
    path: Path,
    backend: str,
    kind: Kind,
    *,
    charge: int = 3600,
    duration: int = 3600,
    default_selected: bool = True,
) -> tuple[str, PlayService]:
    skill = "skill:armoury-body-armor" if kind == "armor" else "skill:armoury-small-arms"
    source = "skill:armoury-melee-weapons" if kind == "armor" else "skill:engineer-small-arms"
    cid, donor = await fixture(
        path / "donor",
        "sqlite",
        kind,
        source,
        alternative_tools=(alternative(skill, duration_seconds=duration),),
    )
    initial = await donor.store.read(cid)
    state = donor._load(initial)
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(update={"charges": charge})
                if item.id == "alternative-tool-0"
                else item
                for item in state.resources.items
            )
        }
    )
    initial["play_json"] = state.model_copy(update={"resources": resources}).model_dump_json()
    play = build_play(path / "actual", donor.engine, backend=backend, seeds=lambda: f"{11:064x}")
    await seed_campaign(play.store, initial)
    await declare(play, cid)
    if default_selected:
        await select(play, cid, source)
    await choose_alternative(play, cid)
    return cid, play


def charge(play: PlayService, state: Campaign) -> int:
    value = next(
        i.charges for i in play._load(state).resources.items if i.id == "alternative-tool-0"
    )
    assert value is not None
    return value


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind,expected", [("armor", 8), ("firearm", 11)])
@pytest.mark.parametrize("success", [False, True])
async def test_selected_timed_kit_reserves_then_spends_exact_repair_time_once(
    tmp_path: Path, backend: str, kind: Kind, expected: int, success: bool
) -> None:
    cid, play = await prepared(tmp_path, backend, kind)
    start = begin(await revision(play, cid))
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="authorized"):
        await CombatService(play).execute(cid, start, principal_id="a")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid, start.model_copy(update={"expected_revision": 0}), principal_id="b"
        )
    assert await play.store.read(cid) == saved
    play.rng = secrets
    await CombatService(play).execute(cid, start, principal_id="b")
    started = await play.store.read(cid)
    task = tasks(play._load(started).resources)[0]
    assert task.skill == expected and task.due - task.start == 1800
    assert charge(play, started) == 3600
    assert current(play._load(started).resources, task) is not None
    assert await CombatService(play).execute(cid, start, principal_id="b")
    assert await play.store.read(cid) == started
    await wait(play, cid, 1800)
    finish = begin(await revision(play, cid)).model_copy(
        update={"id": "finish", "stage": "finish", "task_id": "repair"}
    )
    play.rng = RecordedDice((2, 2, 2) if success else (4, 4, 4))
    await CombatService(play).execute(cid, finish, principal_id="b")
    final = await play.store.read(cid)
    state = play._load(final)
    done = tasks(state.resources)[0]
    assert done.check and done.check.effective_target == expected
    assert done.check.total == (6 if success else 12)
    restored = expected - 6 if success else 0
    assert done.restored_hp == restored
    item = next(i for i in state.resources.items if i.id == "repair-target")
    assert item.condition and item.condition.hp == restored
    assert item.condition.disabled is (not success)
    assert charge(play, final) == 1800
    assert (
        next(i.quantity for i in state.resources.items if i.id == "parts-b")
        == 30 - done.parts_quantity
    )
    assert await CombatService(play).execute(cid, finish, principal_id="b")
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    view = await build_runtime(play).read(cid, principal_id="b")
    assert PREFIX not in str(view)
    member = next(member for member in state.members if member.principal_id == "b")
    stream = project("stream", ViewRequest(build_runtime(play), state, member))
    assert PREFIX not in str(stream)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("elapsed", [0, 100, 1800])
async def test_cancellation_charges_only_actual_elapsed_and_releases_reservation(
    tmp_path: Path, backend: str, elapsed: int
) -> None:
    cid, play = await prepared(tmp_path, backend, "armor")
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    state = play._load(await play.store.read(cid))
    use = UseGeneralEquipment(
        id="second-use",
        actor_id="b",
        item_id="alternative-tool-0",
        expected_revision=state.resources.revision,
    )
    with pytest.raises(ConflictError, match="pending timed repair"):
        apply_general_equipment(
            play.engine.resources, state.resources, use, actor_technology_level=4
        )
    with pytest.raises(ConflictError, match="pending repair"):
        play.engine.resources.apply(
            state.resources,
            Transfer(
                id="give-kit",
                actor_id="b",
                item_id="alternative-tool-0",
                quantity=1,
                owner_id="a",
                expected_revision=state.resources.revision,
            ),
        )
    if elapsed:
        await wait(play, cid, elapsed)
    cancel = begin(await revision(play, cid)).model_copy(
        update={"id": "cancel", "stage": "cancel", "task_id": "repair"}
    )
    await CombatService(play).execute(cid, cancel, principal_id="b")
    cancelled = await play.store.read(cid)
    assert charge(play, cancelled) == 3600 - elapsed
    assert tasks(play._load(cancelled).resources)[0].status == "cancelled"
    assert await CombatService(play).execute(cid, cancel, principal_id="b")
    assert await play.store.read(cid) == cancelled
    state = play._load(cancelled)
    used, outcome = apply_general_equipment(
        play.engine.resources,
        state.resources,
        use.model_copy(update={"expected_revision": state.resources.revision}),
        actor_technology_level=4,
    )
    assert outcome.active_seconds == 1
    assert next(i.charges for i in used.items if i.id == "alternative-tool-0") == 3599 - elapsed


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("insufficient", ["charge", "duration"])
async def test_insufficient_operating_budget_refuses_before_parts_or_dice(
    tmp_path: Path, backend: str, insufficient: str
) -> None:
    cid, play = await prepared(
        tmp_path,
        backend,
        "firearm",
        charge=1799,
        duration=1799 if insufficient == "duration" else 3600,
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="operating time or charge"):
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    assert await play.store.read(cid) == before and play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_timed_tool_accepted_commands_reexecute_from_original_genesis(
    tmp_path: Path, backend: str, cancel: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    cid, play = await prepared(tmp_path, backend, "firearm")
    initial = (await play.store.history(cid))[0].state_after
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"repair-operating-tools"})
    await wait(play, cid, 100 if cancel else 1800)
    finish = begin(await revision(play, cid)).model_copy(
        update={"id": "settle", "stage": "cancel" if cancel else "finish", "task_id": "repair"}
    )
    await CombatService(play).execute(cid, finish, principal_id="b")
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-replay"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid) == await play.store.replay(cid)
    assert charge(play, replayed) == (3500 if cancel else 1800)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_selected_haste_uses_actual_900_seconds_and_source_minus_five(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepared(tmp_path, backend, "firearm")
    await ArmouryService(play).execute(
        cid,
        SelectRepairTime(
            id="haste",
            actor_id="b",
            item_id="repair-target",
            start_command_id="repair",
            method="haste-50",
            expected_revision=await revision(play, cid),
        ),
        principal_id="b",
    )
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    state = play._load(await play.store.read(cid))
    task = tasks(state.resources)[0]
    assert task.due - task.start == 900 and task.skill == 6
    assert charge(play, await play.store.read(cid)) == 3600
    await wait(play, cid, 900)
    command = begin(await revision(play, cid)).model_copy(
        update={"id": "finish", "stage": "finish", "task_id": "repair"}
    )
    play.rng = RecordedDice((2, 2, 2))
    await CombatService(play).execute(cid, command, principal_id="b")
    state = play._load(await play.store.read(cid))
    assert tasks(state.resources)[0].restored_hp == 1
    assert charge(play, await play.store.read(cid)) == 2700


@pytest.mark.parametrize("changed", ["approval", "health", "owner", "ground"])
async def test_reducer_cancel_retains_native_escape_after_context_changes(
    tmp_path: Path, changed: str
) -> None:
    cid, play = await prepared(tmp_path, "sqlite", "armor")
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await wait(play, cid, 100)
    state = play._load(await play.store.read(cid))
    if changed == "approval":
        state = state.model_copy(
            update={
                "actors": tuple(
                    actor.model_copy(update={"approval": None}) if actor.actor_id == "b" else actor
                    for actor in state.actors
                )
            }
        )
    elif changed == "health":
        pools = []
        for pool in state.resources.pools:
            if pool.id == "hp:b":
                assert pool.injury
                pool = pool.model_copy(
                    update={"injury": pool.injury.model_copy(update={"unconscious": True})}
                )
            pools.append(pool)
        state = state.model_copy(
            update={"resources": state.resources.model_copy(update={"pools": tuple(pools)})}
        )
    else:
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            item.model_copy(
                                update={"owner_id": "a"}
                                if changed == "owner"
                                else {"world_ground_location_id": "dock"}
                            )
                            if item.id == "alternative-tool-0"
                            else item
                            for item in state.resources.items
                        )
                    }
                )
            }
        )
    # Pure continuation diagnostic: these are not claimed new persisted mutation hosts.
    cancelled, task = repair(
        play.rules_context,
        state,
        actor_id="b",
        item_id="repair-target",
        command_id="cancel",
        stage="cancel",
        task_id="repair",
    )
    assert task.status == "cancelled"
    tool = next(item for item in cancelled.resources.items if item.id == "alternative-tool-0")
    assert tool.charges == 3500


async def test_authored_genesis_cannot_supply_operating_producer_records(tmp_path: Path) -> None:
    cid, play = await prepared(tmp_path, "sqlite", "armor")
    initial = (await play.store.history(cid))[0].state_after.copy()
    initial["revision"] = 0
    original = play._load((await play.store.history(cid))[0].state_after)
    resources = original.resources.model_copy(
        update={
            "revision": 0,
            "events": (ResourceEvent(id=PREFIX + "forged", at=0, target_id="b", kind="{}"),),
        }
    )
    with pytest.raises(ValidationError, match="reserved|runtime|private|receipt|command"):
        play.initial_state(initial, original.world, resources, ())


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("elapsed", [100, 1800])
async def test_actual_price_only_migration_keeps_finish_guard_but_cannot_strand_cancel(
    tmp_path: Path, backend: str, elapsed: int
) -> None:
    cid, play = await prepared(tmp_path / "source", backend, "armor")
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await wait(play, cid, elapsed)
    state = play._load(await play.store.read(cid))
    combat = play.engine.rules.combat
    assert combat and combat.gurps_equipment
    equipment = combat.gurps_equipment
    original = next(
        entry
        for entry in equipment.entries
        if entry.definition_id == "equipment:alternative-repair-kit"
    )
    changed = original.model_copy(update={"price": original.price + 1})
    resources = copy(play.engine.resources)
    resources.specs = {**resources.specs, changed.definition_id: changed.inventory_spec()}
    catalog = equipment.model_copy(
        update={
            "entries": tuple(
                changed if entry.definition_id == changed.definition_id else entry
                for entry in equipment.entries
            )
        }
    )
    engine = ActionEngine(
        play.engine.reviewer,
        resources,
        play.engine.rules.model_copy(
            update={"combat": combat.model_copy(update={"gurps_equipment": catalog})}
        ),
    )
    target = build_play(tmp_path / "target", engine, store=play.store, rng=secrets)
    await MigrationService(play, target).apply(
        cid,
        ApplyMigration(
            id="price-migration",
            actor_id="gm",
            expected_revision=state.revision,
            expected_from_digest=play.engine.digest,
            reason="GM corrects toolkit price; physical reservoir and operating units are unchanged",
        ),
        principal_id="gm",
    )
    migrated = await target.store.read(cid)
    assert charge(target, migrated) == 3600
    if elapsed == 1800:
        finish = begin(await revision(target, cid)).model_copy(
            update={"id": "finish-refused", "stage": "finish", "task_id": "repair"}
        )
        target.rng = RecordedDice(())
        with pytest.raises(ConflictError, match="observation|profile changed"):
            await CombatService(target).execute(cid, finish, principal_id="b")
        assert await target.store.read(cid) == migrated and target.rng.exhausted()
    target.rng = secrets
    cancel = begin(await revision(target, cid)).model_copy(
        update={"id": "cancel", "stage": "cancel", "task_id": "repair"}
    )
    await CombatService(target).execute(cid, cancel, principal_id="b")
    final = await target.store.read(cid)
    assert charge(target, final) == 3600 - elapsed
    assert tasks(target._load(final).resources)[0].status == "cancelled"
    assert await CombatService(target).execute(cid, cancel, principal_id="b")
    assert await target.store.read(cid) == final == await target.store.replay(cid)
