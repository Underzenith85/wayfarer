"""Independent B235/B481-482 loss and very-high-mana settlement host cases."""

import asyncio
import secrets
from copy import copy
from dataclasses import replace
from fractions import Fraction
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_enchanting_projects import advance, setup
from test_enchanting_source import recipe_runtime, start
from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm
from test_statistics import profile_compiler, profile_package
from trait_support import options

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import Outcome, RandomSource, RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.engine.rules.traits.physiology import PROFILE, RUNTIME_HOOKS
from wayfarer.engine.rules.traits.physiology import package as physiology_package
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.magic.backfires import ManaRefund, refund_due
from wayfarer.engine.simulation.magic.enchanting import EnergyContribution
from wayfarer.engine.simulation.magic.enchanting_lifecycle import (
    LOSS_PREFIX,
    PREFIX,
    EnchantmentLoss,
    checkpoint,
)
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    InterruptEnchanting,
    SettleEnchanting,
    apply_enchantment,
)
from wayfarer.engine.simulation.magic.staff_state import DeclareStaffConstruction, StaffConstruction
from wayfarer.engine.simulation.resources import Advance, ResourceEvent
from wayfarer.engine.simulation.traits.harmful_physiology_state import (
    AdvancePhysiology,
    ObservePhysiology,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.harmful_physiology import HarmfulPhysiologyService
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def prepare(
    path: Path,
    backend: str,
    *,
    mana: ManaLevel = "normal",
    quick: bool = False,
    seed: int = 1,
    weakness: bool = False,
    staff: bool = False,
) -> tuple[PlayService, Campaign, PlayState]:
    original, runtime, foundation = setup(
        path, method="quick-and-dirty" if quick else "slow-and-sure", learn_source_spells=staff
    )
    runtime = recipe_runtime(runtime, mana=mana)
    if staff:
        runtime = recipe_runtime(
            runtime,
            spell_id="spell:staff",
            effect_id="effect:staff",
            runtime_spell_id=None,
            runtime_family="staff",
            activation="always-on",
            requires_magery=True,
            maximum_charges=None,
            energy_required=30,
        )
    engine = ActionEngine(original.reviewer, original.resources, runtime.rules)
    actors = tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in foundation.actors)
    if weakness:
        source = profile_package(PROFILE)
        package = replace(
            source,
            definitions=tuple(original.reviewer.compiler.definitions.values())
            + physiology_package().definitions,
            sources=tuple(
                {s.id: s for s in (*source.sources, *enchantment_package().sources)}.values()
            ),
        )
        base = profile_compiler(PROFILE, package=package)
        compiler = CharacterCompiler(
            RulesCatalog((package,)),
            base.rules,
            original.reviewer.compiler.policy,
            statistics_profile=PROFILE,
            trait_runtime_hooks=RUNTIME_HOOKS,
        )
        resources = copy(original.resources)
        resources.rules = compiler.rules
        engine = ActionEngine(
            PowerReviewer(compiler, original.reviewer.policy, original.reviewer.gm_ids),
            resources,
            runtime.rules,
        )
        actors = tuple(
            a.model_copy(
                update={
                    "proposal": a.proposal.model_copy(
                        update={
                            "draft": a.proposal.draft.model_copy(
                                update={
                                    "purchases": a.proposal.draft.purchases
                                    + (
                                        Purchase(
                                            definition_id="disadvantage:weakness",
                                            trait=options(rarity="common", interval="minute"),
                                        ),
                                    )
                                }
                            )
                        }
                    )
                }
            )
            if a.actor_id == "b"
            else a
            for a in actors
        )
    play = build_play(
        path, engine, backend=backend, rng=secrets, seeds=lambda: format(seed, "064x")
    )
    initial = campaign(engine)
    state = play.initial_state(initial, foundation.world, foundation.resources, actors)
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return play, initial, state


async def begin(play: PlayService, cid: str, *, quick: bool = False, revision: int = 0) -> None:
    service = EnchantmentService(play)
    await service.execute(
        cid,
        CreateEnchantment(
            id="create",
            actor_id="a",
            expected_revision=revision,
            project_id="project",
            recipe_id="light-blade",
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        BeginEnchanting(
            id="begin",
            actor_id="a",
            expected_revision=revision + 1,
            project_id="project",
            contributions=(
                EnergyContribution(actor_id="a", fp=2),
                EnergyContribution(actor_id="b", fp=2),
            )
            if quick
            else (),
        ),
        principal_id="gm",
    )


def settle(revision: int = 3) -> SettleEnchanting:
    return SettleEnchanting(
        id="settle", actor_id="a", expected_revision=revision, project_id="project", work_id="begin"
    )


async def assert_reexec(play: PlayService, initial: Campaign, state: PlayState, path: Path) -> None:
    current = await play.store.read(initial["id"])
    assert current == await play.store.replay(initial["id"])
    final, checks = await verify_commands(
        initial,
        await played(play.store, initial["id"]),
        await play.store.stream(initial["id"]),
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, path),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert final == current


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("mana", "seed", "expected"),
    [
        ("normal", 48, "perverted"),
        ("very-high", 48, "critical-failure"),
        ("very-high", 1, "completed"),
        ("very-high", 30, "critical-failure"),
    ],
)
async def test_actual_quick_mana_settlement_cost_refund_retry_and_seed_replay(
    tmp_path: Path,
    backend: str,
    mana: ManaLevel,
    seed: int,
    expected: str,
) -> None:
    play, initial, state = await prepare(tmp_path, backend, quick=True, mana=mana, seed=seed)
    cid = initial["id"]
    await begin(play, cid, quick=True)
    service = EnchantmentService(play)
    clock = AdvanceEnchanting(
        id="clock",
        actor_id="a",
        expected_revision=2,
        project_id="project",
        work_id="begin",
        to=3600,
    )
    await service.execute(cid, clock, principal_id="gm")
    saved = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director"):
        await service.execute(cid, settle(), principal_id="a")
    with pytest.raises(ConflictError, match="Campaign changed"):
        await service.execute(cid, settle(2), principal_id="gm")
    assert await play.store.read(cid) == saved
    first, duplicate = await asyncio.gather(
        service.execute(cid, settle(), principal_id="gm"),
        service.execute(cid, settle(), principal_id="gm"),
    )
    assert first == duplicate and first.status == expected
    assert first.check is not None and first.check.effective_target == 16
    assert first.check.total == {1: 9, 48: 16, 30: 17}[seed]
    if seed == 48:
        assert first.check.dice == (6, 6, 4) and first.check.outcome is Outcome.FAILURE
    after = play._load(await play.store.read(cid))
    for actor in ("a", "b"):
        assert next(p.current for p in after.resources.pools if p.id == "fp:" + actor) == 8
        assert next(p.current for p in after.resources.pools if p.id == "hp:" + actor) == 10
    assert any(i.id == "blade" for i in after.resources.expended_items) == (
        expected == "critical-failure"
    )
    assert any(i.id == "blade" for i in after.resources.items) == (expected != "critical-failure")
    assert any(e.id.startswith("enchantment-perversion:") for e in after.resources.events) == (
        expected == "perverted"
    )
    refunds = tuple(
        ManaRefund.model_validate_json(e.kind)
        for e in after.resources.events
        if e.id.startswith("mana-refund:")
    )
    assert len(refunds) == (2 if mana == "very-high" else 0)
    assert all(r.amount == 2 and r.due_at == 3601 and not r.settled for r in refunds)
    wait = Wait(id="next-second", actor_id="a", expected_revision=4, ticks=1)
    await play.execute(cid, wait, principal_id="a")
    final = await play.store.read(cid)
    await play.execute(cid, wait, principal_id="a")
    assert await play.store.read(cid) == final
    after = play._load(final)
    assert [p.current for p in after.resources.pools if p.id.startswith("fp:")] == (
        [10, 10] if mana == "very-high" else [8, 8]
    )
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=secrets)
    assert await EnchantmentService(restarted).execute(cid, settle(), principal_id="gm") == first
    assert await play.store.read(cid) == final
    await assert_reexec(play, initial, state, tmp_path / "reexecuted")


@pytest.mark.parametrize("actor", ["a", "b"])
@pytest.mark.parametrize("paused", [False, True])
def test_canonical_fatal_injury_ends_work_without_restoring_or_destroying_history(
    tmp_path: Path,
    actor: str,
    paused: bool,
) -> None:
    engine, runtime, state = setup(tmp_path)
    state = start(runtime, state)
    state = advance(engine, state, 3600, "one-hour")
    if paused:
        runtime = replace(runtime, rng=RecordedDice((1, 1)))
        state, _ = apply_enchantment(
            runtime,
            state,
            InterruptEnchanting(
                id="pause", actor_id="a", expected_revision=state.revision, project_id="project"
            ),
            system=True,
        )
    resources, injury = apply_injury(
        state.resources,
        Wound(
            id="fatal",
            actor_id=actor,
            expected_revision=state.revision,
            basic_damage=60,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice(()),
        system=True,
    )
    assert injury.injury == 60
    dead = state.model_copy(update={"resources": resources, "revision": resources.revision})
    after = checkpoint(dead, before=state)
    assert after.resources.pools == dead.resources.pools
    hp = next(p for p in after.resources.pools if p.id == "hp:" + actor)
    assert hp.current == -50 and hp.injury is not None and hp.injury.dead
    project = after.resources.enchantment_projects[0]
    assert project.status == "failed" and project.active_work is None and project.check is None
    assert project.interruptions == dead.resources.enchantment_projects[0].interruptions
    loss = next(e for e in after.resources.events if e.id == LOSS_PREFIX + "project")
    assert loss.at == 3600 and EnchantmentLoss.model_validate_json(loss.kind).enchanter_ids == (
        actor,
    )
    assert after.resources.events[: len(dead.resources.events)] == dead.resources.events
    assert any(i.id == "blade" and not i.enchantments for i in after.resources.items)
    assert not after.resources.expended_items
    assert checkpoint(after) == after
    assert not any(e.id.startswith("enchantment-power:") for e in after.resources.events)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_physiology_death_checkpoints_project_loss_at_its_due_time(
    tmp_path: Path,
    backend: str,
) -> None:
    play, initial, state = await prepare(tmp_path, backend, weakness=True, staff=True)
    cid = initial["id"]
    await EnchantmentService(play).declare_staff(
        cid,
        DeclareStaffConstruction(
            id="staff-facts",
            actor_id="a",
            expected_revision=0,
            construction=StaffConstruction(
                item_id="blade",
                definition_id="equipment:sword",
                length_yards=Fraction(2),
                once_living=True,
                form="full-staff",
                material="oak",
            ),
        ),
        principal_id="gm",
    )
    await begin(play, cid, revision=1)
    begun = play._load(await play.store.read(cid))
    work = begun.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == 1_238_400  # 30 energy / 2 mages = 15 daily shifts.
    physiology = HarmfulPhysiologyService(play)
    await physiology.execute(
        cid,
        ObservePhysiology(
            id="sunlight",
            actor_id="b",
            expected_revision=3,
            source="weakness",
            condition_id="sunlight",
            present=True,
        ),
        principal_id="gm",
    )
    clock = AdvancePhysiology(id="injury-clock", actor_id="a", expected_revision=4, to=3600)
    await physiology.execute(cid, clock, principal_id="gm")
    final = await play.store.read(cid)
    after = play._load(final)
    hp = next(p for p in after.resources.pools if p.id == "hp:b")
    assert hp.current == -40 and hp.injury is not None and hp.injury.dead
    loss = next(e for e in after.resources.events if e.id == LOSS_PREFIX + "project")
    # Seed1 gives14 damage dice totaling50; the fourth death check is14 vs HT10.
    # Death therefore occurs at minute14, not the eventual outer clock3600.
    assert loss.at == 840
    project = after.resources.enchantment_projects[0]
    assert project.status == "failed" and project.active_work is None
    with pytest.raises(ConflictError, match="not active"):
        await EnchantmentService(play).execute(cid, settle(5), principal_id="gm")
    assert await play.store.read(cid) == final
    await physiology.execute(cid, clock, principal_id="gm")
    assert await play.store.read(cid) == final
    await assert_reexec(play, initial, state, tmp_path / "reexecuted")


def test_delayed_mana_refund_preserves_new_fatigue_injury_and_current_capacity(
    tmp_path: Path,
) -> None:
    engine, runtime, state = setup(tmp_path, method="quick-and-dirty")
    runtime = recipe_runtime(runtime, mana="very-high")
    runtime = replace(runtime, rng=RecordedDice((6, 6, 4)))
    state = start(
        runtime,
        state,
        contributions=(
            EnergyContribution(actor_id="a", fp=2),
            EnergyContribution(actor_id="b", fp=1, hp=1),
        ),
    )
    state = advance(engine, state, 3600, "casting-hour")
    state, result = apply_enchantment(runtime, state, settle(state.revision), system=True)
    assert result.status == "critical-failure"
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 9
    resources, _ = apply_fatigue(
        state.resources,
        FatigueCost(id="later-fatigue", actor_id="b", expected_revision=state.revision, amount=3),
        ht=10,
        rng=RecordedDice(()),
        system=True,
    )
    resources, _ = apply_injury(
        resources,
        Wound(
            id="later-injury",
            actor_id="b",
            expected_revision=resources.revision,
            basic_damage=1,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice(()),
        system=True,
    )
    assert refund_due(resources, "b") == resources
    before = resources.model_copy(update={"game_time": 3601})
    refunded = refund_due(before, "b")
    assert next(p.current for p in refunded.pools if p.id == "fp:b") == 7  # 10−1−3+1
    assert next(p for p in refunded.pools if p.id == "hp:b") == next(
        p for p in before.pools if p.id == "hp:b"
    )
    assert next(p.current for p in refunded.pools if p.id == "hp:b") == 8
    assert refund_due(refunded, "b") == refunded
    # A changed body/resource ceiling is evaluated at refund time.
    reduced = before.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"maximum": 6}) if p.id == "fp:b" else p for p in before.pools
            )
        }
    )
    assert next(p.current for p in refund_due(reduced, "b").pools if p.id == "fp:b") == 6


def test_temporary_night_absence_and_unconsciousness_are_not_lost_mages(tmp_path: Path) -> None:
    engine, runtime, state = setup(tmp_path)
    state = start(runtime, state)
    state = advance(engine, state, 28_800, "night")
    world = state.world
    away = state.model_copy(
        update={
            "world": replace(
                world,
                entities=tuple(
                    replace(e, location_id=None) if e.id == "b" else e for e in world.entities
                ),
            ),
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(
                            update={"injury": p.injury.model_copy(update={"unconscious": True})}
                        )
                        if p.id == "hp:b" and p.injury
                        else p
                        for p in state.resources.pools
                    )
                }
            ),
        }
    )
    assert checkpoint(away, before=state) == away
    assert away.resources.enchantment_projects[0].status == "active"
    removed = away.model_copy(update={"actors": tuple(a for a in away.actors if a.actor_id != "b")})
    failed = checkpoint(removed, before=away)
    assert failed.resources.enchantment_projects[0].status == "failed"
    restored = failed.model_copy(update={"actors": away.actors})
    assert checkpoint(restored).resources.enchantment_projects[0].status == "failed"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_old_recorded_enchantment_generation_replays_without_new_mana_or_lifecycle(
    tmp_path: Path,
    backend: str,
) -> None:
    play, initial, state = await prepare(tmp_path, backend, quick=True, mana="very-high", seed=48)
    cid = initial["id"]
    commands = (
        CreateEnchantment(
            id="create",
            actor_id="a",
            expected_revision=0,
            project_id="project",
            recipe_id="light-blade",
            target_item_id="blade",
            enchanter_ids=("a", "b"),
        ),
        BeginEnchanting(
            id="begin",
            actor_id="a",
            expected_revision=1,
            project_id="project",
            contributions=(
                EnergyContribution(actor_id="a", fp=2),
                EnergyContribution(actor_id="b", fp=2),
            ),
        ),
        AdvanceEnchanting(
            id="clock",
            actor_id="a",
            expected_revision=2,
            project_id="project",
            work_id="begin",
            to=3600,
        ),
        settle(),
    )
    service = EnchantmentService(play)
    for command in commands:
        current = play._load(await play.store.read(cid))
        await submit(
            play,
            cid,
            service.plan(play, current, command, principal_id="gm", correct_settlement=False),
            principal_id="gm",
        )
    after = play._load(await play.store.read(cid))
    assert any(e.id.startswith("enchantment-perversion:") for e in after.resources.events)
    assert not any(e.id.startswith((PREFIX, "mana-refund:")) for e in after.resources.events)
    # Ordinary live exact retries select the saved generation too.
    final = await play.store.read(cid)
    assert (await service.execute(cid, settle(), principal_id="gm")).status == "perverted"
    assert await play.store.read(cid) == final
    await assert_reexec(play, initial, state, tmp_path / "historical-reexecuted")


@pytest.mark.parametrize("prefix", [PREFIX, LOSS_PREFIX])
def test_initial_resources_cannot_forge_lifecycle_or_loss(tmp_path: Path, prefix: str) -> None:
    engine, _, foundation = setup(tmp_path)
    play = build_play(tmp_path, engine)
    resources = foundation.resources.model_copy(
        update={"events": (ResourceEvent(id=prefix + "fake", at=0, target_id="a", kind="{}"),)}
    )
    with pytest.raises(ValidationError, match="supernatural execution receipts"):
        play.initial_state(
            campaign(engine),
            foundation.world,
            resources,
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in foundation.actors),
        )


@pytest.mark.parametrize("retry", [False, True])
async def test_mana_settlement_rechecks_current_gm_under_the_store_lock(
    tmp_path: Path,
    retry: bool,
) -> None:
    play, initial, _ = await prepare(tmp_path, "sqlite", quick=True, mana="very-high", seed=48)
    cid = initial["id"]
    await begin(play, cid, quick=True)
    service = EnchantmentService(play)
    await service.execute(
        cid,
        AdvanceEnchanting(
            id="clock",
            actor_id="a",
            expected_revision=2,
            project_id="project",
            work_id="begin",
            to=3600,
        ),
        principal_id="gm",
    )
    if retry:
        await service.execute(cid, settle(), principal_id="gm")
    revision = 4 if retry else 3
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    restarted = build_play(tmp_path, play.engine, store=store, rng=secrets)
    store.revoke = lambda: revoke_gm(play, cid, revision)
    with pytest.raises(ValidationError, match="director authority"):
        await EnchantmentService(restarted).execute(
            cid, settle() if retry else settle(revision + 1), principal_id="gm"
        )
    after = restarted._load(await store.read(cid))
    assert after.revision == revision + 1
    assert len(after.resources.expended_items) == int(retry)
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == (8 if retry else 10)


def test_project_advance_keeps_the_loss_checkpoint_returned_by_its_shared_clock(
    tmp_path: Path,
) -> None:
    engine, runtime, state = setup(tmp_path)
    state = start(runtime, state)

    def fatal_clock(current: PlayState, command: Advance, rng: RandomSource) -> PlayState:
        at_loss = advance(engine, current, 120, "first-boundary")
        resources, _ = apply_injury(
            at_loss.resources,
            Wound(
                id="clock-injury",
                actor_id="b",
                expected_revision=at_loss.resources.revision,
                basic_damage=60,
                resistance=0,
                damage_type="cr",
            ),
            ht=10,
            rng=rng,
            system=True,
        )
        at_loss = checkpoint(at_loss.model_copy(update={"resources": resources}), before=current)
        # The outer clock retains one command revision and keeps its inner checkpoints.
        at_loss = at_loss.model_copy(update={"revision": resources.revision})
        return advance(engine, at_loss, command.to, "last-boundary").model_copy(
            update={"revision": current.revision}
        )

    runtime = replace(runtime, rng=RecordedDice(()), clock=fatal_clock)
    command = AdvanceEnchanting(
        id="advance-with-loss",
        actor_id="a",
        expected_revision=state.revision,
        project_id="project",
        work_id="begin",
        to=115_200,
    )
    after, result = apply_enchantment(runtime, state, command, system=True)
    assert result.status == "mage-lost" and result.check is None
    assert after.resources.enchantment_projects[0].status == "failed"
    assert after.resources.enchantment_projects[0].active_work is None
    assert next(e.at for e in after.resources.events if e.id == LOSS_PREFIX + "project") == 120
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == -50
    assert apply_enchantment(runtime, after, command, system=True) == (after, result)
