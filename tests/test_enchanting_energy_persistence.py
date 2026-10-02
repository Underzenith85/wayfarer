"""Real services: extra Staff work, nearby Quick and Dirty facts, and old bytes."""

import asyncio
import hashlib
import json
import secrets
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_campaign
from test_actions import campaign
from test_enchanting_energy_rules import add_bystanders, distances, finish_command
from test_enchanting_nighttime import assert_reexecution
from test_enchanting_projects import setup
from test_enchanting_source import QUICK_ENERGY, source_recipe
from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    CALENDAR_DAY,
    MAGE_DAY,
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    InterruptEnchanting,
)
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.command_inputs import intent_input


async def prepare(
    tmp_path: Path,
    backend: str,
    *,
    quick: bool = False,
) -> tuple[Campaign, PlayService, PlayState]:
    original, _, foundation = setup(
        tmp_path,
        method="quick-and-dirty" if quick else "slow-and-sure",
        learn_source_spells=not quick,
    )
    rules = original.rules
    assert rules.enchanting is not None
    if quick:
        foundation = add_bystanders(foundation, "c", "d")
    else:
        rules = rules.model_copy(
            update={
                "enchanting": rules.enchanting.model_copy(
                    update={"recipes": (source_recipe("staff"),)}
                )
            }
        )
    engine = ActionEngine(original.reviewer, original.resources, rules)
    play = build_play(
        tmp_path, engine, backend=backend, rng=secrets, seeds=lambda: format(1, "064x")
    )
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        foundation.world,
        foundation.resources,
        tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in foundation.actors),
    )
    state = state.model_copy(
        update={
            "members": state.members + (CampaignMember(principal_id="watcher", role="spectator"),)
        }
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return initial, play, state


async def begin(play: PlayService, cid: str, *, quick: bool = False, legacy: bool = False) -> None:
    service = EnchantmentService(play)
    if not quick:
        await service.declare_staff(
            cid,
            DeclareStaffConstruction(
                id="staff-fact",
                actor_id="a",
                expected_revision=0,
                construction=StaffConstruction(
                    item_id="blade",
                    definition_id="equipment:sword",
                    form="wand",
                    length_yards=Fraction(1),
                    material="reed",
                    once_living=True,
                ),
            ),
            principal_id="gm",
        )
    current = play._load(await play.store.read(cid))
    commands = (
        CreateEnchantment(
            id="create",
            actor_id="a",
            expected_revision=current.revision,
            project_id="project",
            recipe_id="light-blade" if quick else "source-staff",
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        BeginEnchanting(
            id="begin",
            actor_id="a",
            expected_revision=current.revision + 1,
            project_id="project",
            contributions=QUICK_ENERGY if quick else (),
            extra_energy=None if quick else 6,
        ),
    )
    for command in commands:
        if legacy:
            current = play._load(await play.store.read(cid))
            await submit(
                play,
                cid,
                service.plan(play, current, command, principal_id="gm", correct_energy=False),
                principal_id="gm",
            )
        else:
            await service.execute(cid, command, principal_id="gm")


async def clock(play: PlayService, cid: str, to: int, *, legacy: bool = False) -> None:
    current = play._load(await play.store.read(cid))
    work = current.resources.enchantment_projects[0].active_work
    assert work is not None
    command = AdvanceEnchanting(
        id=f"advance-{to}",
        actor_id="a",
        expected_revision=current.revision,
        project_id="project",
        work_id=work.id,
        to=to,
    )
    service = EnchantmentService(play)
    if legacy:
        await submit(
            play,
            cid,
            service.plan(play, current, command, principal_id="gm", correct_energy=False),
            principal_id="gm",
        )
    else:
        await service.execute(cid, command, principal_id="gm")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_extra_staff_days_survive_base_completion_interruption_restart_and_seed_replay(
    tmp_path: Path,
    backend: str,
) -> None:
    initial, play, genesis = await prepare(tmp_path, backend)
    cid = initial["id"]
    await begin(play, cid)
    current = play._load(await play.store.read(cid))
    work = current.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == 17 * CALENDAR_DAY + MAGE_DAY
    await clock(play, cid, 14 * CALENDAR_DAY + MAGE_DAY)  # Base Staff30 is worked; extra6 is not.
    service = EnchantmentService(play)
    current = play._load(await play.store.read(cid))
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="deadline"):
        await service.execute(cid, finish_command(current), principal_id="gm")
    assert await play.store.read(cid) == before
    interrupted = InterruptEnchanting(
        id="pause", actor_id="a", expected_revision=current.revision, project_id="project"
    )
    result = await service.execute(cid, interrupted, principal_id="gm")
    assert result.energy_completed == 30
    current = play._load(await play.store.read(cid))
    project = current.resources.enchantment_projects[0]
    assert project.status == "interrupted" and project.interruptions[0].credited_energy == 30
    assert not next(i for i in current.resources.items if i.id == "blade").enchantments
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert (
        await EnchantmentService(restarted).execute(cid, interrupted, principal_id="gm") == result
    )
    resume = BeginEnchanting(
        id="resume", actor_id="a", expected_revision=current.revision, project_id="project"
    )
    await service.execute(cid, resume, principal_id="gm")
    await clock(play, cid, 17 * CALENDAR_DAY + MAGE_DAY)
    current = play._load(await play.store.read(cid))
    settle = finish_command(current)
    results = await asyncio.gather(
        *(service.execute(cid, settle, principal_id="gm") for _ in range(2))
    )
    assert results[0] == results[1]
    assert results[0].check is not None and results[0].check.effective_target == 18
    current = play._load(await play.store.read(cid))
    item = next(i for i in current.resources.items if i.id == "blade")
    assert item.enchantments[0].spell_id == "staff" and item.enchantments[0].power == 18
    assert item.enchantments[0].created_at == 1_497_600
    assert [(p.id, p.current) for p in current.resources.pools] == [
        (p.id, p.maximum) for p in current.resources.pools
    ]
    assert not any(e.id.startswith("mana-refund:") for e in current.resources.events)
    assert await EnchantmentService(restarted).execute(cid, settle, principal_id="gm") == results[0]
    await assert_reexecution(initial, play, genesis, tmp_path)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_nearby_settlement_spends_once_uses_private_generation_and_reexecutes(
    tmp_path: Path,
    backend: str,
) -> None:
    initial, play, genesis = await prepare(tmp_path, backend, quick=True)
    cid = initial["id"]
    await begin(play, cid, quick=True)
    await clock(play, cid, 3600)
    current = play._load(await play.store.read(cid))
    settle = finish_command(current, nonparticipant_distances=distances(("c", 10), ("d", 1)))
    service = EnchantmentService(play)
    saved = await play.store.read(cid)
    for principal in ("a", "b"):
        with pytest.raises(ValidationError, match="director authority"):
            await service.execute(cid, settle, principal_id=principal)
    with pytest.raises(ConflictError):
        await service.execute(
            cid, settle.model_copy(update={"expected_revision": 2}), principal_id="gm"
        )
    with pytest.raises(ValidationError, match="owned"):
        await service.execute(cid, settle.model_copy(update={"actor_id": "b"}), principal_id="gm")
    assert await play.store.read(cid) == saved
    results = await asyncio.gather(
        *(service.execute(cid, settle, principal_id="gm") for _ in range(2))
    )
    assert results[0] == results[1]
    assert results[0].check is not None and results[0].check.effective_target == 15
    current = play._load(await play.store.read(cid))
    assert next(i for i in current.resources.items if i.id == "blade").enchantments[0].power == 15
    assert [p.current for p in current.resources.pools if p.fatigue] == [8, 8]
    assert current.resources.game_time == 3600
    record = await play.store.command_input(cid, settle.id)
    assert record is not None and record.text is not None
    assert json.loads(intent_input(record.text))["enchantment_energy_generation"] == 1
    runtime = build_runtime(play)
    for principal in ("a", "b", "watcher"):
        view = json.dumps(await runtime.read(cid, principal_id=principal))
        stream = await runtime.events(cid, principal_id=principal)
        encoded = json.dumps([entry.model_dump(mode="json") for entry in stream])
        for secret in (
            '"c"',
            '"d"',
            "Hidden bystander",
            "nonparticipant_distances",
            "distance_yards",
            "effective_target",
            "dice",
            "margin",
            "enchantment:",
        ):
            assert secret not in view and secret not in encoded
        assert all(entry.action == "private" and entry.outcome == "" for entry in stream)
    gm_history = await runtime.history(cid)
    recorded = next(entry for entry in gm_history if entry.command_id == settle.id)
    assert recorded.event["outcome"] == "enchantment:completed"
    assert "effective_target" not in json.dumps(recorded.event)
    assert json.loads(intent_input(record.text))["command"]["nonparticipant_distances"] == [
        {"actor_id": "c", "distance_yards": "10"},
        {"actor_id": "d", "distance_yards": "1"},
    ]
    original_project = PlayState.model_validate_json(
        recorded.state_after["play_json"]
    ).resources.enchantment_projects[0]
    assert original_project.check == results[0].check
    assert (await runtime.events(cid, principal_id="gm"))[-1].outcome == "enchantment:completed"
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await EnchantmentService(restarted).execute(cid, settle, principal_id="gm") == results[0]
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            settle.model_copy(update={"nonparticipant_distances": distances(("c", 11), ("d", 11))}),
            principal_id="gm",
        )
    await assert_reexecution(initial, play, genesis, tmp_path)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_old_commands_keep_bytes_receipt_hash_target_and_seed_reexecution(
    tmp_path: Path,
    backend: str,
) -> None:
    initial, play, genesis = await prepare(tmp_path, backend, quick=True)
    cid = initial["id"]
    await begin(play, cid, quick=True, legacy=True)
    await clock(play, cid, 3600, legacy=True)
    current = play._load(await play.store.read(cid))
    settle = finish_command(current)
    service = EnchantmentService(play)
    result = await submit(
        play,
        cid,
        service.plan(play, current, settle, principal_id="gm", correct_energy=False),
        principal_id="gm",
    )
    assert isinstance(result, type(await service.execute(cid, settle, principal_id="gm")))
    current = play._load(await play.store.read(cid))
    assert next(i for i in current.resources.items if i.id == "blade").enchantments[0].power == 16
    # This is the old command producer's actual default-filled byte shape. No
    # new fields, reconstruction tolerance or regenerated golden is allowed.
    raw = '{"id":"settle","actor_id":"a","expected_revision":3,"kind":"settle","project_id":"project","work_id":"begin"}'
    receipt = next(r for r in current.resources.receipts if r.command_id == "settle")
    assert receipt.digest == hashlib.sha256(raw.encode()).hexdigest()
    record = await play.store.command_input(cid, "settle")
    assert record is not None and record.text is not None
    original = json.dumps(
        {
            "operation": "enchantment",
            "principal_id": "gm",
            "command": json.loads(raw),
            "enchantment_settlement_generation": 1,
        },
        sort_keys=True,
    )
    assert intent_input(record.text) == original
    saved = await play.store.read(cid)
    assert await service.execute(cid, settle, principal_id="gm") == result
    assert await play.store.read(cid) == saved
    await assert_reexecution(initial, play, genesis, tmp_path)


@pytest.mark.parametrize("retry", [False, True])
async def test_current_gm_seat_is_rechecked_for_nearby_observation_and_retry(
    tmp_path: Path,
    retry: bool,
) -> None:
    initial, play, _ = await prepare(tmp_path, "sqlite", quick=True)
    cid = initial["id"]
    await begin(play, cid, quick=True)
    await clock(play, cid, 3600)
    current = play._load(await play.store.read(cid))
    command = finish_command(current, nonparticipant_distances=distances(("c", 10), ("d", 1)))
    if retry:
        await EnchantmentService(play).execute(cid, command, principal_id="gm")
    revision = 4 if retry else 3
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    restarted = build_play(tmp_path, play.engine, store=store, rng=secrets)
    store.revoke = lambda: revoke_gm(play, cid, revision)
    with pytest.raises(ValidationError, match="director authority"):
        await EnchantmentService(restarted).execute(
            cid,
            command if retry else command.model_copy(update={"expected_revision": revision + 1}),
            principal_id="gm",
        )
    current = play._load(await play.store.read(cid))
    assert [p.current for p in current.resources.pools if p.fatigue] == (
        [8, 8] if retry else [10, 10]
    )


async def mutate_current(play: PlayService, cid: str, change: str) -> None:
    saved = await play.store.read(cid)
    revision = saved["revision"]

    def resolve(value: Campaign) -> CommandReceipt:
        current = play._load(value)
        updates: dict[str, object] = {"revision": revision + 1}
        resources = current.resources.model_copy(update={"revision": revision + 1})
        if change == "custody":
            resources = resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"owner_id": "b"}) if i.id == "blade" else i
                        for i in resources.items
                    )
                }
            )
        elif change == "approval":
            updates["actors"] = tuple(
                a.model_copy(update={"approval": None}) if a.actor_id == "a" else a
                for a in current.actors
            )
        else:
            updates["world"] = replace(
                current.world,
                entities=current.world.entities
                + (Entity("new-hidden", EntityKind.ACTOR, "New hidden person", "forge"),),
            )
        updates["resources"] = resources
        play.commit(value, current.model_copy(update=updates))
        return CommandReceipt(action="resource", outcome="trusted-current-facts")

    await play.store.commit_turn(
        cid, "current-facts", revision, "current-facts", resolve, actor_id="gm"
    )


@pytest.mark.parametrize("change", ["bystander", "custody", "approval"])
async def test_settlement_revalidates_actual_worksite_custody_and_approval_under_lock(
    tmp_path: Path,
    change: str,
) -> None:
    initial, play, _ = await prepare(tmp_path, "sqlite", quick=True)
    cid = initial["id"]
    await begin(play, cid, quick=True)
    await clock(play, cid, 3600)
    current = play._load(await play.store.read(cid))
    command = finish_command(
        current,
        expected_revision=current.revision + 1,
        nonparticipant_distances=distances(("c", 10), ("d", 1)),
    )
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    restarted = build_play(tmp_path, play.engine, store=store, rng=RecordedDice(()))
    store.revoke = lambda: mutate_current(play, cid, change)
    with pytest.raises(ValidationError, match="nonparticipants|suitable|approved"):
        await EnchantmentService(restarted).execute(cid, command, principal_id="gm")
    current = play._load(await play.store.read(cid))
    assert current.resources.game_time == 3600 and current.revision == 4
    assert [p.current for p in current.resources.pools if p.fatigue] == [10, 10]
    assert not next(i for i in current.resources.items if i.id == "blade").enchantments
    assert len(await played(play.store, cid)) == 4
