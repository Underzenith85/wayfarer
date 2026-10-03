"""Actual B485 low-stock weapon/armor repairs and durable rolled requirements."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_issue_818_armoury_acceptance import Kind, begin, workshop

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.equipment.repair_parts import (
    AssessRepairParts,
    RepairPartsAssessment,
)
from wayfarer.engine.simulation.equipment.repairs import tasks
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.armoury import ArmouryService
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def setup(
    path: Path, backend: str, kind: Kind, stock: int = 5, hp: int = 0
) -> tuple[str, PlayService]:
    cid, source = await workshop(path / "original", kind, hp=hp)
    initial = await source.store.read(cid)
    state = source._load(initial)
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"quantity": stock}) if i.id == "parts-b" else i
                        for i in state.resources.items
                    )
                }
            )
        }
    )
    initial["play_json"] = state.model_dump_json()
    play = build_play(path / "actual", source.engine, backend=backend, rng=RecordedDice(()))
    await seed_campaign(play.store, initial)
    return cid, play


async def revision(play: PlayService, cid: str) -> int:
    return play._load(await play.store.read(cid)).revision


async def assessment(
    play: PlayService, cid: str, identifier: str = "assess"
) -> RepairPartsAssessment:
    result = await ArmouryService(play).execute(
        cid,
        AssessRepairParts(
            id=identifier,
            actor_id="b",
            item_id="repair-target",
            expected_revision=await revision(play, cid),
        ),
        principal_id="b",
    )
    assert isinstance(result, RepairPartsAssessment)
    return result


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["melee", "armor"])
@pytest.mark.parametrize("success", [True, False])
async def test_source_sufficient_below_maximum_stock_repairs_real_equipment(
    tmp_path: Path, backend: str, kind: Kind, success: bool
) -> None:
    cid, play = await setup(tmp_path, backend, kind)
    play.rng = RecordedDice((1,))
    requirement = await assessment(play, cid)
    assert requirement.die == 1 and requirement.quantity == 5
    assert play.rng.exhausted()
    play.rng = RecordedDice(())
    assert await assessment(play, cid, "assess-again") == requirement
    command = begin(await revision(play, cid))
    receipt = await CombatService(play).execute(cid, command, principal_id="b")
    pending = await play.store.read(cid)
    state = play._load(pending)
    assert tasks(state.resources)[0].parts_die == 1
    assert tasks(state.resources)[0].parts_quantity == 5
    assert not any(i.id == "parts-b" for i in state.resources.items)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, command, principal_id="b") == receipt
    assert await restarted.store.read(cid) == pending
    await restarted.execute(
        cid,
        Wait(id="work", actor_id="b", ticks=1800, expected_revision=await revision(restarted, cid)),
        principal_id="b",
    )
    restarted.rng = RecordedDice((3, 3, 3) if success else (4, 4, 4))
    await CombatService(restarted).execute(
        cid,
        command.model_copy(
            update={
                "id": "finish",
                "stage": "finish",
                "task_id": "repair",
                "expected_revision": await revision(restarted, cid),
            }
        ),
        principal_id="b",
    )
    final = restarted._load(await restarted.store.read(cid))
    task = tasks(final.resources)[0]
    assert task.restored_hp == (1 if success else 0)
    item = next(i for i in final.resources.items if i.id == "repair-target")
    assert item.condition and item.condition.hp == (1 if success else 0)
    assert task.status == "completed"
    assert await restarted.store.read(cid) == await restarted.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_insufficient_rolled_stock_retains_assessment_without_rerolls(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await setup(tmp_path, backend, "melee")
    play.rng = RecordedDice((2,))
    requirement = await assessment(play, cid)
    assert requirement.quantity == 10
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    for identifier in ("start-one", "start-two"):
        with pytest.raises(ValidationError, match="recorded rolled"):
            await CombatService(play).execute(
                cid,
                begin(await revision(play, cid)).model_copy(update={"id": identifier}),
                principal_id="b",
            )
        assert await play.store.read(cid) == before
    assert await assessment(play, cid, "repeat") == requirement
    with pytest.raises(ConflictError):
        await ArmouryService(play).execute(
            cid,
            AssessRepairParts(
                id="stale", actor_id="b", item_id="repair-target", expected_revision=0
            ),
            principal_id="b",
        )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["melee", "armor"])
async def test_seeded_assessment_and_repair_reexecute_whole_campaign(
    tmp_path: Path, backend: str, kind: Kind
) -> None:
    cid, play = await setup(tmp_path, backend, kind, stock=29)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    requirement = await assessment(play, cid)
    assert requirement.quantity <= 29
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await play.execute(
        cid,
        Wait(id="seed-work", actor_id="b", ticks=1800, expected_revision=await revision(play, cid)),
        principal_id="b",
    )
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "seed-finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    committed = await play.store.read(cid)
    assert tasks(play._load(committed).resources)[0].status == "completed"
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == committed


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("problem", ["authority", "owner", "damage", "tools"])
async def test_assessment_current_authority_and_equipment_refusal_before_rng(
    tmp_path: Path, backend: str, problem: str
) -> None:
    from test_combat_sensory_authority import change

    cid, play = await setup(tmp_path, backend, "armor")
    play.rng = RecordedDice((1,))
    requirement = await assessment(play, cid)
    play.rng = RecordedDice(())
    if problem != "authority":

        def altered(state: PlayState) -> PlayState:
            items = tuple(
                item.model_copy(update={"owner_id": "a"})
                if problem == "owner" and item.id == "repair-target"
                else item.model_copy(
                    update={"condition": item.condition.model_copy(update={"hp": -1})}
                )
                if problem == "damage" and item.id == "repair-target" and item.condition
                else item
                for item in state.resources.items
                if not (problem == "tools" and item.id == "tool-b")
            )
            return state.model_copy(
                update={"resources": state.resources.model_copy(update={"items": items})}
            )

        await change(play, cid, altered)
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError, ConflictError)):
        await ArmouryService(play).execute(
            cid,
            AssessRepairParts(
                id="changed",
                actor_id="b",
                item_id="repair-target",
                expected_revision=await revision(play, cid),
            ),
            principal_id="a" if problem == "authority" else "b",
        )
    assert await play.store.read(cid) == before
    assert requirement.die == 1
    with pytest.raises((AuthorizationError, ValidationError, ConflictError)):
        await CombatService(play).execute(
            cid,
            begin(await revision(play, cid)),
            principal_id="a" if problem == "authority" else "b",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_assessment_private_projection_and_exact_retry_authority(
    tmp_path: Path, backend: str
) -> None:
    import json

    from support.runtime import build_runtime

    cid, play = await setup(tmp_path, backend, "melee")
    command = AssessRepairParts(
        id="private",
        actor_id="b",
        item_id="repair-target",
        expected_revision=await revision(play, cid),
    )
    play.rng = RecordedDice((1,))
    result = await ArmouryService(play).execute(cid, command, principal_id="b")
    saved = await play.store.read(cid)
    play.rng = RecordedDice(())
    assert await ArmouryService(play).execute(cid, command, principal_id="b") == result
    with pytest.raises(AuthorizationError):
        await ArmouryService(play).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == saved
    view = json.dumps(await build_runtime(play).read(cid, principal_id="a"))
    assert "armoury-parts:" not in view
    assert result.profile_digest not in view


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["melee", "armor"])
async def test_new_requirement_requires_actual_settled_condition_change(
    tmp_path: Path, backend: str, kind: Kind
) -> None:
    cid, play = await setup(tmp_path, backend, kind, hp=-3)
    play.rng = RecordedDice((1,))
    first = await assessment(play, cid)
    await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
    await play.execute(
        cid,
        Wait(id="work", actor_id="b", ticks=1800, expected_revision=await revision(play, cid)),
        principal_id="b",
    )
    play.rng = RecordedDice((3, 3, 3))
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"id": "finish", "stage": "finish", "task_id": "repair"}
        ),
        principal_id="b",
    )
    settled = play._load(await play.store.read(cid))
    assert tasks(settled.resources)[0].restored_hp == 1
    assert (
        next(
            i.condition.hp
            for i in settled.resources.items
            if i.id == "repair-target" and i.condition
        )
        == -2
    )
    play.rng = RecordedDice((2,))
    second = await assessment(play, cid, "after-proven-work")
    assert second.condition.hp == -2 and second.die == 2 and second.quantity == 10
    assert first.condition.hp == -3 and first.die == 1
    play.rng = RecordedDice(())
    assert await assessment(play, cid, "no-free-roll") == second


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("after_completed", [False, True])
async def test_lawful_new_owner_reuses_item_requirement_and_assessor_provenance(
    tmp_path: Path, backend: str, after_completed: bool
) -> None:
    from test_combat_sensory_authority import change

    from wayfarer.engine.simulation.resources import Transfer

    cid, play = await setup(
        tmp_path,
        backend,
        "melee",
        stock=10 if after_completed else 5,
        hp=-3 if after_completed else 0,
    )
    play.rng = RecordedDice((1,))
    original = await assessment(play, cid)
    if after_completed:
        await CombatService(play).execute(cid, begin(await revision(play, cid)), principal_id="b")
        await play.execute(
            cid,
            Wait(
                id="work-before-transfer",
                actor_id="b",
                ticks=1800,
                expected_revision=await revision(play, cid),
            ),
            principal_id="b",
        )
        play.rng = RecordedDice((3, 3, 3))
        await CombatService(play).execute(
            cid,
            begin(await revision(play, cid)).model_copy(
                update={"id": "finish-before-transfer", "stage": "finish", "task_id": "repair"}
            ),
            principal_id="b",
        )

    def transfer(state: PlayState) -> PlayState:
        resources = state.resources
        for identifier in ("repair-target", "tool-b", "parts-b"):
            resources = play.engine.resources.apply(
                resources,
                Transfer(
                    id="handoff:" + identifier,
                    actor_id="b",
                    expected_revision=resources.revision,
                    item_id=identifier,
                    quantity=next(i.quantity for i in resources.items if i.id == identifier),
                    owner_id="a",
                ),
            )
        return state.model_copy(update={"resources": resources})

    await change(play, cid, transfer)
    play.rng = RecordedDice((1,)) if after_completed else RecordedDice(())
    rebound = await ArmouryService(play).execute(
        cid,
        AssessRepairParts(
            id="new-owner",
            actor_id="a",
            item_id="repair-target",
            expected_revision=await revision(play, cid),
        ),
        principal_id="a",
    )
    assert rebound.assessor_id == ("a" if after_completed else original.assessor_id)
    assert original.assessor_id == "b"
    assert rebound.condition.hp == (-2 if after_completed else 0)
    assert rebound.actor_id == "a" and rebound.die == original.die == 1
    assert rebound.quantity == original.quantity == 5
    with pytest.raises(ValidationError):
        await CombatService(play).execute(
            cid,
            begin(await revision(play, cid)).model_copy(update={"id": "old-owner-refused"}),
            principal_id="b",
        )
    await CombatService(play).execute(
        cid,
        begin(await revision(play, cid)).model_copy(
            update={"actor_id": "a", "id": "new-owner-repair"}
        ),
        principal_id="a",
    )
    pending = play._load(await play.store.read(cid))
    assert tasks(pending.resources)[-1].parts_quantity == 5
    assert not any(i.id == "parts-b" for i in pending.resources.items)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_genesis_cannot_forge_private_parts_requirement(tmp_path: Path, backend: str) -> None:
    from test_actions import campaign

    from wayfarer.engine.simulation.actions import ActorSetup
    from wayfarer.engine.simulation.resources import ResourceEvent

    cid, play = await setup(tmp_path, backend, "melee")
    saved = await play.store.read(cid)
    state = play._load(saved)
    forged = state.resources.model_copy(
        update={
            "revision": 0,
            "events": (ResourceEvent(id="armoury-parts:forged", at=0, target_id="b", kind="{}"),),
        }
    )
    with pytest.raises(ValidationError, match="cannot seed supernatural execution receipts"):
        play.initial_state(
            campaign(play.engine),
            state.world,
            forged,
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in state.actors),
        )
    assert await play.store.read(cid) == saved
