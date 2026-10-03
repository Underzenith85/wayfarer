"""B480–482 two actual producers feed the same initially mundane wearable."""

import json
import secrets
from collections.abc import Mapping
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_campaign
from test_actions import campaign
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_haste_manufacture import begin_project, revision
from test_haste_manufacture import prepare as manufacture_blueprint
from test_power_project_haste import _total
from test_power_wearer_haste import scores

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.magic.enchanting import EnchantmentRecipe
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    CALENDAR_DAY,
    MAGE_DAY,
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    SettleEnchanting,
)
from wayfarer.engine.simulation.magic.haste_state import (
    DeclareHasteChannel,
    HasteChannel,
    HasteMana,
    HasteSwitch,
    ObserveHasteMana,
    SwitchHasteItem,
)
from wayfarer.engine.simulation.magic.haste_state import (
    items as haste_items,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.haste import HasteService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.projections import ViewRequest, project
from wayfarer.persistence.replay import verify_commands


async def prepare(path: Path, backend: str, reduction: int) -> tuple[str, PlayService, Campaign]:
    cid, original = await manufacture_blueprint(path / "blueprint", backend, 1)
    foundation = original._load(await original.store.read(cid))
    assert all(not i.enchantments for i in foundation.resources.items)
    old = original.engine.rules.enchanting
    assert old is not None
    power = EnchantmentRecipe(
        id="source-power",
        spell_id="spell:power",
        effect_id="effect:power",
        method="slow-and-sure",
        energy_required=500 * 2 ** (reduction - 1),
        target_definition_ids=("equipment:cloak",),
        workspace_definition_id="equipment:workshop",
        runtime_family="power",
        activation="always-on",
        power_reduction=reduction,
    )
    engine = ActionEngine(
        original.engine.reviewer,
        original.engine.resources,
        original.engine.rules.model_copy(
            update={"enchanting": old.model_copy(update={"recipes": old.recipes + (power,)})}
        ),
    )
    play = build_play(path / "composition", engine, backend=backend, rng=secrets)
    play.seeds = lambda: f"{1:064x}"
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        foundation.world,
        ResourceState(owners=foundation.resources.owners, items=foundation.resources.items),
        tuple(
            ActorSetup(actor_id=a.actor_id, proposal=a.proposal, body=a.body)
            for a in foundation.actors
        ),
        members=foundation.members,
    )
    assert not haste_items(state.resources) and not state.resources.enchantment_projects
    initial["play_json"] = state.model_dump_json()
    initial = await seed_campaign(play.store, initial)
    await HasteService(play).execute(
        initial["id"],
        ObserveHasteMana(
            id="mana",
            actor_id="gm",
            expected_revision=0,
            environment=HasteMana(location_id="dock", mana="normal"),
        ),
        principal_id="gm",
    )
    return initial["id"], play, initial


async def power_project(play: PlayService, cid: str, reduction: int) -> SettleEnchanting:
    service = EnchantmentService(play)
    start = play._load(await play.store.read(cid)).resources.game_time
    await service.execute(
        cid,
        CreateEnchantment(
            id="power-create",
            actor_id="a",
            expected_revision=await revision(play, cid),
            project_id="power",
            recipe_id="source-power",
            target_item_id="cloak",
            enchanter_ids=("a", "b"),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        BeginEnchanting(
            id="power-begin",
            actor_id="a",
            expected_revision=await revision(play, cid),
            project_id="power",
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    work = next(p for p in state.resources.enchantment_projects if p.id == "power").active_work
    assert work is not None
    days = 250 * 2 ** (reduction - 1)
    assert work.start == start
    # First project used today's full eight-hour shift; no second shift is legal.
    expected_first_day = ((start // CALENDAR_DAY) + 1) * CALENDAR_DAY
    assert work.due == expected_first_day + (days - 1) * CALENDAR_DAY + MAGE_DAY
    await service.execute(
        cid,
        AdvanceEnchanting(
            id="power-work",
            actor_id="a",
            expected_revision=state.revision,
            project_id="power",
            work_id=work.id,
            to=work.due,
        ),
        principal_id="gm",
    )
    return SettleEnchanting(
        id="power-settle",
        actor_id="a",
        expected_revision=await revision(play, cid),
        project_id="power",
        work_id=work.id,
    )


async def wait(play: PlayService, cid: str, name: str, ticks: int) -> None:
    await play.execute(
        cid,
        Wait(id=name, actor_id="a", expected_revision=await revision(play, cid), ticks=ticks),
        principal_id="a",
    )


async def paid_cast(play: PlayService, cid: str, name: str) -> None:
    haste = HasteService(play)
    await haste.execute(
        cid,
        DeclareHasteChannel(
            id=name + "-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=HasteChannel(
                id=name, actor_id="a", target_id="a", location_id="dock", magic_item_id="cloak"
            ),
        ),
        principal_id="gm",
    )
    command = RuntimeSpellCommand(
        id=name + "-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="haste",
        cast_id=name,
        channel_id=name,
        energy=1,
    )
    await haste.execute(cid, command, principal_id="alice")
    await wait(play, cid, name + "-one", 1)
    await haste.execute(
        cid,
        command.model_copy(
            update={
                "id": name + "-concentrate",
                "kind": "concentrate",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    await wait(play, cid, name + "-two", 1)
    await haste.execute(
        cid,
        command.model_copy(
            update={
                "id": name + "-complete",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("reduction", [1, 2])
async def test_two_project_producers_actual_wearer_upkeep_and_seed(
    tmp_path: Path,
    backend: str,
    reduction: int,
) -> None:
    cid, play, initial = await prepare(tmp_path, backend, reduction)
    play.rng, play.seeds = secrets, lambda: f"{1:064x}"
    service = EnchantmentService(play)
    haste_settle = await begin_project(play, cid, 1)
    made = await service.execute(cid, haste_settle, principal_id="gm")
    state = play._load(await play.store.read(cid))
    cloak = next(i for i in state.resources.items if i.id == "cloak")
    assert made.status == "completed" and made.check and made.check.effective_target == 17
    assert len(cloak.enchantments) == 1 and cloak.enchantments[0].project_id == "made"
    haste_id = cloak.enchantments[0].id
    assert haste_items(state.resources)[0].binding_id == haste_id
    assert scores(play, state) == (5, 8)
    await paid_cast(play, cid, "before-power")
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["before-power"].cost == 2
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 8
    await HasteService(play).execute(
        cid,
        RuntimeSpellCommand(
            id="cancel-before-power",
            actor_id="a",
            expected_revision=state.revision,
            kind="cancel",
            spell_id="haste",
            cast_id="before-power",
            channel_id="before-power",
            energy=1,
        ),
        principal_id="alice",
    )
    settle = await power_project(play, cid, reduction)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="trusted"):
        await service.execute(cid, settle, principal_id="alice")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, settle.model_copy(update={"expected_revision": 0}), principal_id="gm"
        )
    failing = FailingCommitPlay(play.store, play.engine, rng=RecordedDice((3, 3, 3)))
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await submit(
            failing,
            cid,
            EnchantmentService(failing).plan(
                failing, failing._load(before), settle, principal_id="gm"
            ),
            principal_id="gm",
        )
    assert before == await play.store.read(cid)
    assert history == await play.store.history(cid) and stream == await play.store.stream(cid)
    result = await service.execute(cid, settle, principal_id="gm")
    assert result.status == "completed" and result.check and result.check.effective_target == 17
    state = play._load(await play.store.read(cid))
    bindings = next(i for i in state.resources.items if i.id == "cloak").enchantments
    assert len(bindings) == 2 and bindings[0].id == haste_id
    assert (bindings[1].spell_id, bindings[1].project_id, bindings[1].power_reduction) == (
        "power",
        "power",
        reduction,
    )
    assert bindings[1].power == 17 and bindings[1].runtime_family == "power"
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7
    if reduction == 1:
        assert scores(play, state) == (5, 8)
        await paid_cast(play, cid, "paid")
        state = play._load(await play.store.read(cid))
        assert latest(state.resources)["paid"].cost == 1
        assert latest(state.resources)["paid"].maintenance == 0
    else:
        assert scores(play, state) == (6, 9)
        assert any(
            e.spell_id == "haste" and e.cost == 0 and e.expires_at is None
            for e in latest(state.resources).values()
        )
    await wait(play, cid, "upkeep", 121)
    state = play._load(await play.store.read(cid))
    assert scores(play, state) == (6, 9)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (
        6 if reduction == 1 else 7
    )
    if reduction == 2:
        for name, enabled, expected in (("off", False, (5, 8)), ("on", True, (6, 9))):
            await HasteService(play).execute(
                cid,
                SwitchHasteItem(
                    id=name,
                    actor_id="a",
                    expected_revision=await revision(play, cid),
                    switch=HasteSwitch(item_id="cloak", binding_id=haste_id, enabled=enabled),
                ),
                principal_id="alice",
            )
            assert scores(play, play._load(await play.store.read(cid))) == expected
    state = play._load(await play.store.read(cid))
    for member in state.members:
        if member.role == "gm":
            continue
        for name in ("campaign", "stream"):
            visible = json.dumps(
                project(name, ViewRequest(build_runtime(play), state, member)), default=str
            )
            assert "haste-manufacture:" not in visible and "enchantment_projects" not in visible
            if "a" not in member.actor_ids:
                assert (
                    all(b.id not in visible for b in bindings) and "power_reduction" not in visible
                )
    final = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await EnchantmentService(restarted).execute(cid, settle, principal_id="gm") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    exact_history, exact_stream = await play.store.history(cid), await play.store.stream(cid)
    with pytest.raises(ConflictError):
        await service.execute(
            cid, settle.model_copy(update={"work_id": "another"}), principal_id="gm"
        )
    assert final == await play.store.read(cid)
    assert exact_history == await play.store.history(
        cid
    ) and exact_stream == await play.store.stream(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("failed_stage", ["haste", "power", "power-critical"])
async def test_failed_producer_preserves_exact_existing_item(
    tmp_path: Path,
    backend: str,
    failed_stage: str,
) -> None:
    cid, play, initial = await prepare(tmp_path, backend, 2)
    play.rng, play.seeds = secrets, lambda: f"{1:064x}"
    service = EnchantmentService(play)
    haste_settle = await begin_project(play, cid, 1)
    failure_total = 17 if failed_stage == "power-critical" else 16
    failed_seed = next(f"{n:064x}" for n in range(10000) if _total(f"{n:064x}") == failure_total)
    if failed_stage == "haste":
        play.seeds = lambda: failed_seed
    result = await service.execute(cid, haste_settle, principal_id="gm")
    if failed_stage.startswith("power"):
        assert result.status == "completed"
        settle = await power_project(play, cid, 2)
        play.seeds = lambda: failed_seed
        result = await service.execute(cid, settle, principal_id="gm")
    assert result.status == ("critical-failure" if failed_stage == "power-critical" else "failed")
    assert result.check and result.check.total == failure_total
    state = play._load(await play.store.read(cid))
    bindings = next((i.enchantments for i in state.resources.items if i.id == "cloak"), ())
    assert len(bindings) == (1 if failed_stage == "power" else 0)
    if failed_stage != "power":
        destroyed = next(i for i in state.resources.expended_items if i.id == "cloak")
        assert len(destroyed.enchantments) == (1 if failed_stage == "power-critical" else 0)
    assert not any(b.power_reduction or b.spell_id == "power" for b in bindings)
    assert scores(play, state) == (5, 8)
    if failed_stage == "haste":
        before = await play.store.read(cid)
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError, match="not suitable"):
            await power_project(play, cid, 2)
        assert await play.store.read(cid) == before and play.rng.exhausted()
    elif failed_stage == "power":
        play.seeds = lambda: f"{1:064x}"
        await paid_cast(play, cid, "paid")
        state = play._load(await play.store.read(cid))
        assert scores(play, state) == (6, 9) and latest(state.resources)["paid"].cost == 2
        assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 8
    final = await play.store.read(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert _canonical_campaign(replayed) == _canonical_campaign(final)


def _canonical_campaign(value: Mapping[str, object]) -> dict[str, object]:
    # Normalize JSON object key order only; retain every key/value and list order.
    # Recorded command bytes and stored retry history are checked separately.
    result = dict(value)
    encoded = result["play_json"]
    assert isinstance(encoded, str)
    result["play_json"] = json.dumps(json.loads(encoded), sort_keys=True)
    return result
