"""B481: a new item/project cannot buy a second daily enchanting shift."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_enchanting_projects import advance, setup
from test_enchanting_source import recipe_runtime, start

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup
from wayfarer.engine.simulation.campaign.party import Subgroup
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter
from wayfarer.engine.simulation.magic.enchanting import EnchantmentWork
from wayfarer.engine.simulation.magic.enchanting_calendar import next_shift_at, record_rest
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    AbandonEnchantment,
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    InterruptEnchanting,
    SettleEnchanting,
    apply_enchantment,
    enchanting_work_active,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.persistence.replay import verify_commands


@pytest.mark.parametrize(
    ("now", "first", "due", "expected"),
    [
        (0, 0, 28_800, 0),
        (3_600, 0, 28_800, 86_400),
        (28_800, 0, 28_800, 86_400),
        (86_400, 0, 115_200, 86_400),
        (86_401, 0, 115_200, 172_800),
        (115_200, 0, 115_200, 172_800),
        (259_200, 0, 115_200, 259_200),
        (28_800, 86_400, 115_200, 28_800),
    ],
)
def test_actual_work_interval_bounds_rest(now: int, first: int, due: int, expected: int) -> None:
    resources = ResourceState(game_time=now)
    work = EnchantmentWork(id="work", start=0, due=due, enchanter_ids=("a", "b"))
    updated = record_rest(resources, work, "finished", first_shift_at=first)
    assert next_shift_at(updated, ("a",)) == expected
    assert next_shift_at(updated, ("b", "c")) == expected
    assert next_shift_at(updated, ("c",)) == now


@pytest.mark.parametrize("interrupt_first", [False, True])
def test_abandoning_work_does_not_reset_the_daily_limit(
    tmp_path: Path, interrupt_first: bool
) -> None:
    engine, runtime, state = setup(tmp_path)
    state = start(runtime, state)
    state = advance(engine, state, 3_600, "hour")
    if interrupt_first:
        state, _ = apply_enchantment(
            runtime,
            state,
            InterruptEnchanting(
                id="pause", actor_id="a", expected_revision=state.revision, project_id="project"
            ),
            system=True,
        )
    state, _ = apply_enchantment(
        runtime,
        state,
        AbandonEnchantment(
            id="abandon", actor_id="a", expected_revision=state.revision, project_id="project"
        ),
        system=True,
    )
    runtime = recipe_runtime(runtime, materials=())
    state, _ = apply_enchantment(
        runtime,
        state,
        CreateEnchantment(
            id="new-project",
            actor_id="a",
            expected_revision=state.revision,
            project_id="next",
            recipe_id="light-blade",
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        system=True,
    )
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="new-work", actor_id="a", expected_revision=state.revision, project_id="next"
        ),
        system=True,
    )
    work = next(p for p in state.resources.enchantment_projects if p.id == "next").active_work
    assert work is not None
    assert work.due == 201_600  # Rest to day two, then two eight-hour daily shifts.


@pytest.mark.parametrize("boundary", ["party", "combat"])
def test_project_clock_does_not_bypass_another_timeline(tmp_path: Path, boundary: str) -> None:
    _, runtime, state = setup(tmp_path)
    state = start(runtime, state)
    if boundary == "party":
        state = state.model_copy(
            update={
                "party": state.party.model_copy(
                    update={
                        "groups": (
                            Subgroup(
                                id="group", scene_id="forge", actor_ids=("a", "b"), ready_through=0
                            ),
                        )
                    }
                )
            }
        )
    else:
        encounter = Encounter(
            id="fight",
            legacy_battlefield_id="forge",
            participants=(
                Combatant(
                    actor_id="a",
                    initiative=10,
                    position=GridPoint(x=0, y=0),
                    reach=1,
                    movement_allowance=5,
                ),
                Combatant(
                    actor_id="b",
                    initiative=9,
                    position=GridPoint(x=1, y=0),
                    reach=1,
                    movement_allowance=5,
                ),
            ),
            turn_order=("a", "b"),
        )
        state = state.model_copy(update={"encounters": (encounter,)})
    command = AdvanceEnchanting(
        id="skip-time",
        actor_id="a",
        expected_revision=state.revision,
        project_id="project",
        work_id="begin",
        to=115_200,
    )
    with pytest.raises(ValidationError, match="timeline|Active combat"):
        apply_enchantment(runtime, state, command, system=True)
    assert state.resources.game_time == 0
    assert not any(receipt.command_id == "skip-time" for receipt in state.resources.receipts)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_project_boundary_retains_daily_work_and_reexecutes(
    tmp_path: Path, backend: str
) -> None:
    original, _, foundation = setup(tmp_path)
    rules = original.rules.enchanting
    assert rules is not None
    engine = ActionEngine(
        original.reviewer,
        original.resources,
        original.rules.model_copy(
            update={
                "enchanting": rules.model_copy(
                    update={"recipes": (rules.recipes[0].model_copy(update={"materials": ()}),)}
                )
            }
        ),
    )
    play = build_play(tmp_path, engine, backend=backend, rng=secrets)
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        foundation.world,
        foundation.resources,
        tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in foundation.actors),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    service = EnchantmentService(play)
    await service.execute(
        cid,
        CreateEnchantment(
            id="first-create",
            actor_id="a",
            expected_revision=0,
            project_id="first",
            recipe_id="light-blade",
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        BeginEnchanting(id="first-work", actor_id="a", expected_revision=1, project_id="first"),
        principal_id="gm",
    )
    clock = AdvanceEnchanting(
        id="work-days",
        actor_id="a",
        expected_revision=2,
        project_id="first",
        work_id="first-work",
        to=115_200,
    )
    before_clock = await play.store.read(cid)
    with pytest.raises(ValidationError, match="trusted director"):
        await service.execute(cid, clock, principal_id="a")
    with pytest.raises(ConflictError, match="not active"):
        await service.execute(cid, clock.model_copy(update={"work_id": "stale"}), principal_id="gm")
    for invalid in (0, 115_201):
        with pytest.raises(ValidationError, match="deadline"):
            await service.execute(cid, clock.model_copy(update={"to": invalid}), principal_id="gm")
    assert await play.store.read(cid) == before_clock
    advanced = await service.execute(cid, clock, principal_id="gm")
    assert await service.execute(cid, clock, principal_id="gm") == advanced
    revision = 3
    # build_play supplies deterministic command seeds, preserved by reexecution.
    await service.execute(
        cid,
        SettleEnchanting(
            id="first-finish",
            actor_id="a",
            expected_revision=revision,
            project_id="first",
            work_id="first-work",
        ),
        principal_id="gm",
    )
    revision += 1
    after = play._load(await play.store.read(cid))
    assert next_shift_at(after.resources, ("a", "b")) == 172_800
    assert any(item.id == "blade" for item in after.resources.items)
    await service.execute(
        cid,
        CreateEnchantment(
            id="second-create",
            actor_id="a",
            expected_revision=revision,
            project_id="second",
            recipe_id="light-blade",
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        principal_id="gm",
    )
    revision += 1
    command = BeginEnchanting(
        id="second-work", actor_id="a", expected_revision=revision, project_id="second"
    )
    with pytest.raises(ValidationError, match="trusted director"):
        await service.execute(cid, command, principal_id="a")
    result = await service.execute(cid, command, principal_id="gm")
    assert await service.execute(cid, command, principal_id="gm") == result
    current = await play.store.read(cid)
    after = play._load(current)
    project = next(p for p in after.resources.enchantment_projects if p.id == "second")
    assert project.active_work is not None
    assert project.active_work.start == 115_200
    assert project.active_work.due == 288_000  # Start day3, finish day4's eight-hour shift.
    assert not enchanting_work_active(after.resources, project)
    with pytest.raises(ConflictError, match="Campaign changed"):
        await service.execute(cid, command.model_copy(update={"id": "stale"}), principal_id="gm")
    assert await play.store.read(cid) == await play.store.replay(cid)
    records = await played(play.store, cid)
    final, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == current
