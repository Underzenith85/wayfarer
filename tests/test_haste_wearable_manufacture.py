"""B482 trusted plain wearable prototypes reach the paid Haste consumer."""

import json
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime
from test_haste_manufacture import begin_project, paid_cast, prepare, revision
from test_power_wearer_haste import scores

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.haste_state import items as haste_items
from wayfarer.engine.simulation.magic.haste_wearable_construction import (
    DeclareHasteWearableConstruction,
    WearableConstruction,
)
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.projections import ViewRequest, project
from wayfarer.persistence.replay import verify_commands


def construction(play: PlayService, form: str) -> WearableConstruction:
    runtime = play.rules_context
    assert runtime.rules.combat is not None
    equipment = runtime.rules.combat.gurps_equipment
    assert equipment is not None
    profile = equipment.entries[0]
    spec = runtime.resources.specs[profile.definition_id]
    return WearableConstruction.model_validate(
        {
            "item_id": "cloak",
            "definition_id": profile.definition_id,
            "form": form,
            "slot": spec.slot,
            "spec_json": spec.model_dump_json(),
            "profile_json": profile.model_dump_json(),
        }
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("form", ["clothing", "jewelry"])
@pytest.mark.parametrize("levels", [1, 2, 3])
async def test_plain_wearable_manufacture_paid_consumer_and_replay(
    tmp_path: Path, backend: str, form: str, levels: int
) -> None:
    cid, play = await prepare(
        tmp_path, backend, levels, "plain-wearable", jewelry=form == "jewelry"
    )
    play.rng, play.seeds = secrets, lambda: f"{1:064x}"
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    command = DeclareHasteWearableConstruction(
        id="physical",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        construction=construction(play, form),
    )
    service = EnchantmentService(play)
    fact = await service.declare_haste_wearable(cid, command, principal_id="gm")
    play.rng, play.seeds = secrets, lambda: f"{1:064x}"
    settle = await begin_project(play, cid, levels)
    outcome = await service.execute(cid, settle, principal_id="gm")
    assert outcome.status == "completed"
    state = play._load(await play.store.read(cid))
    assert haste_items(state.resources)[0].form == form
    payload = await play.store.command_input(cid, "create")
    assert payload is not None and payload.text is not None
    assert json.loads(payload.text)["haste_manufacture_generation"] == 2
    for member in state.members:
        if member.role == "gm":
            continue
        for name in ("campaign", "stream"):
            visible = json.dumps(
                project(name, ViewRequest(build_runtime(play), state, member)), default=str
            )
            assert "haste-wearable-construction:" not in visible
            assert "spec_json" not in visible and "profile_json" not in visible
    await paid_cast(play, cid, levels)
    await play.execute(
        cid,
        Wait(id="expiry", actor_id="a", expected_revision=await revision(play, cid), ticks=60),
        principal_id="a",
    )
    assert scores(play, play._load(await play.store.read(cid))) == (5, 8)
    final = await play.store.read(cid)
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert (
        await EnchantmentService(restarted).declare_haste_wearable(cid, command, principal_id="gm")
        == fact
    )
    assert await EnchantmentService(restarted).execute(cid, settle, principal_id="gm") == outcome
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_construction_authority_stale_exact_retry_and_no_capability(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import ConflictError, ValidationError

    cid, play = await prepare(tmp_path, backend, 1, "plain-wearable")
    service = EnchantmentService(play)
    command = DeclareHasteWearableConstruction(
        id="physical",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        construction=construction(play, "clothing"),
    )
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    for principal in ("alice", "watcher"):
        with pytest.raises(ValidationError):
            await service.declare_haste_wearable(cid, command, principal_id=principal)
    with pytest.raises(ConflictError):
        await service.declare_haste_wearable(
            cid, command.model_copy(update={"expected_revision": 0}), principal_id="gm"
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    fact = await service.declare_haste_wearable(cid, command, principal_id="gm")
    final = await play.store.read(cid)
    assert not haste_items(play._load(final).resources)
    assert scores(play, play._load(final)) == (5, 8)
    assert await service.declare_haste_wearable(cid, command, principal_id="gm") == fact
    with pytest.raises(ConflictError):
        await service.declare_haste_wearable(
            cid,
            command.model_copy(
                update={"construction": fact.model_copy(update={"form": "jewelry"})}
            ),
            principal_id="gm",
        )
    with pytest.raises(ConflictError):
        await service.declare_haste_wearable(
            cid,
            command.model_copy(
                update={"id": "replace", "expected_revision": await revision(play, cid)}
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("change", ["slot", "definition_id", "spec_json", "profile_json"])
async def test_construction_rejects_unbound_physical_fact(
    tmp_path: Path, backend: str, change: str
) -> None:
    from wayfarer.errors import ValidationError

    cid, play = await prepare(tmp_path, backend, 1, "plain-wearable")
    values = {
        "slot": "hand:right",
        "definition_id": "equipment:workshop",
        "spec_json": "{}",
        "profile_json": "{}",
    }
    command = DeclareHasteWearableConstruction(
        id="bad",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        construction=construction(play, "clothing").model_copy(update={change: values[change]}),
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await EnchantmentService(play).declare_haste_wearable(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("change", ["quantity", "definition", "owner", "disabled", "contained"])
async def test_wearable_project_rechecks_current_item_before_roll(
    tmp_path: Path, backend: str, change: str
) -> None:
    from wayfarer.engine.rules.types.object import ObjectCondition
    from wayfarer.errors import ValidationError

    cid, play = await prepare(tmp_path, backend, 1, "plain-wearable")
    service = EnchantmentService(play)
    await service.declare_haste_wearable(
        cid,
        DeclareHasteWearableConstruction(
            id="physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            construction=construction(play, "clothing"),
        ),
        principal_id="gm",
    )
    settle = await begin_project(play, cid, 1)
    before = await play.store.read(cid)
    state = play._load(before)
    updates: dict[str, dict[str, object]] = {
        "quantity": {"quantity": 2},
        "definition": {"definition_id": "equipment:workshop"},
        "owner": {"owner_id": "b"},
        "disabled": {"condition": ObjectCondition(hp=0, disabled=True)},
        "contained": {"container_id": "workshop"},
    }
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update=updates[change]) if i.id == "cloak" else i
                for i in state.resources.items
            )
        }
    )
    candidate = before.copy()
    candidate["play_json"] = state.model_copy(update={"resources": resources}).model_dump_json()
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        service.plan(play, state, settle, principal_id="gm").resolve(candidate)
    assert await play.store.read(cid) == before
    assert not haste_items(state.resources)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("finish", ["failure", "abandon", "rollback"])
async def test_wearable_failed_abandoned_and_rolled_back_projects_grant_no_haste(
    tmp_path: Path, backend: str, finish: str
) -> None:
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    from wayfarer.engine.simulation.magic.enchanting_transitions import AbandonEnchantment
    from wayfarer.orchestration.pipeline import submit

    cid, play = await prepare(tmp_path, backend, 1, "plain-wearable", jewelry=True)
    service = EnchantmentService(play)
    await service.declare_haste_wearable(
        cid,
        DeclareHasteWearableConstruction(
            id="physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            construction=construction(play, "jewelry"),
        ),
        principal_id="gm",
    )
    settle = await begin_project(play, cid, 1)
    before = await play.store.read(cid)
    if finish == "rollback":
        failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
        with pytest.raises(RuntimeError, match="candidate checkpoint"):
            await submit(
                failing,
                cid,
                EnchantmentService(failing).plan(
                    failing, failing._load(before), settle, principal_id="gm"
                ),
                principal_id="gm",
            )
        assert await play.store.read(cid) == before
    elif finish == "abandon":
        outcome = await service.execute(
            cid,
            AbandonEnchantment(
                id="abandon",
                actor_id="a",
                expected_revision=await revision(play, cid),
                project_id="made",
            ),
            principal_id="gm",
        )
        assert outcome.status == "abandoned"
    else:
        play.rng = RecordedDice((6, 5, 5))
        outcome = await service.execute(cid, settle, principal_id="gm")
        assert outcome.status != "completed" and outcome.check is not None
    state = play._load(await play.store.read(cid))
    assert not haste_items(state.resources) and scores(play, state) == (5, 8)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_generation_one_cannot_capture_plain_wearable_and_absence_replays(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.enchanting_transitions import CreateEnchantment
    from wayfarer.engine.simulation.magic.haste_manufacture import ObserveHasteManufacture
    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.pipeline import submit

    cid, play = await prepare(tmp_path, backend, 1, "plain-wearable")
    play.rng, play.seeds = secrets, lambda: f"{1:064x}"
    initial = await play.store.read(cid)
    count = len(await play.store.history(cid))
    service = EnchantmentService(play)
    await service.declare_haste_wearable(
        cid,
        DeclareHasteWearableConstruction(
            id="physical",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            construction=construction(play, "clothing"),
        ),
        principal_id="gm",
    )
    await service.observe_haste_manufacture(
        cid,
        ObserveHasteManufacture(
            id="first-observe",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            recipe_id="source-haste",
            target_item_id="cloak",
            levels=1,
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    create = CreateEnchantment(
        id="gen1",
        actor_id="a",
        expected_revision=await revision(play, cid),
        project_id="made",
        recipe_id="source-haste",
        target_item_id="cloak",
        enchanter_ids=("a", "b"),
    )
    with pytest.raises(ValidationError, match="generation"):
        await submit(
            play,
            cid,
            service.plan(
                play,
                play._load(before),
                create,
                principal_id="gm",
                haste_manufacture=True,
                manufacture_generation=1,
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    # An independent fixture supplies the complete old-absence stream without
    # replacing the immutable fact in the refused generation-one ledger.
    cid2, old = await prepare(tmp_path / "old", backend, 1, "plain-wearable")
    old.rng, old.seeds = secrets, lambda: f"{1:064x}"
    old_initial = await old.store.read(cid2)
    old_count = len(await old.store.history(cid2))
    await EnchantmentService(old).declare_haste_wearable(
        cid2,
        DeclareHasteWearableConstruction(
            id="physical",
            actor_id="gm",
            expected_revision=await revision(old, cid2),
            construction=construction(old, "clothing"),
        ),
        principal_id="gm",
    )
    settle = await begin_project(old, cid2, 1, legacy=True)
    assert (
        await EnchantmentService(old).execute(cid2, settle, principal_id="gm")
    ).status == "completed"
    state = old._load(await old.store.read(cid2))
    assert not haste_items(state.resources)
    assert (
        next(i for i in state.resources.items if i.id == "cloak").enchantments[0].runtime_family
        is None
    )
    final = await old.store.read(cid2)
    records = (await old.store.history(cid2))[old_count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        old_initial,
        records,
        [e for e in await old.store.stream(cid2) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(old.engine, tmp_path / "old-reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks) and replayed == final
    assert await play.store.read(cid) == before
    assert len(await play.store.history(cid)) == count + 2
    assert initial != before


@pytest.mark.parametrize("bad", [True, False, 0, 3, "2", 2.0])
async def test_private_producer_generation_refuses_invalid_envelopes(
    tmp_path: Path, bad: object
) -> None:
    from dataclasses import replace

    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.enchantment_generations import current_haste_generation
    from wayfarer.orchestration.replay_inputs import replay_inputs

    cid, play = await prepare(tmp_path, "sqlite", 1, "plain-wearable")
    record = (await play.store.history(cid))[0]
    payload = json.dumps({"operation": "enchantment", "haste_manufacture_generation": bad})
    with replay_inputs(replace(record, command_input=payload)):
        with pytest.raises(ValidationError, match="generation"):
            await current_haste_generation(play, cid, "fresh", fresh=2)
