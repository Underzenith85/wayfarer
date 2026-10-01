"""Approved learning to durable object opening and ordinary movement, B251/B253."""

import asyncio
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_play
from test_actions import campaign, world
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.movement import package
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Move, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneExit, SceneRules
from wayfarer.engine.simulation.combat.battlefield import Battlefield
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.equipment.catalog import EquipmentCatalog
from wayfarer.engine.simulation.magic.lock_bindings import LockChannel
from wayfarer.engine.simulation.magic.lock_host import DeclareLock, DeclareLockChannel, OperateLock
from wayfarer.engine.simulation.magic.lock_state import LockFixture, LockState
from wayfarer.engine.simulation.magic.lock_state import latest as locks
from wayfarer.engine.simulation.magic.spell_state import RuntimeSpellId, latest
from wayfarer.engine.simulation.magic.spells import PROFILE, RuntimeSpellCommand, SpellResult
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.locks import LockService, LockSpellService
from wayfarer.orchestration.play import PlayService


async def prepare(
    path: Path, backend: str = "sqlite", *, combat: bool = False, scenes: bool = False
) -> tuple[str, PlayService]:
    spells = package()
    base = profile_package(PROFILE, *spells.definitions)
    base = replace(
        base, sources=tuple({s.id: s for s in (*base.sources, *spells.sources)}.values())
    )
    compiled = profile_compiler(PROFILE, package=base)
    catalog = RulesCatalog((base,))
    compiled = CharacterCompiler(
        catalog,
        compiled.rules,
        replace(compiled.policy, point_budget=1000, allow_supernatural=True),
        statistics_profile=PROFILE,
    )
    authored = replace(world(), knowledge=(("a", "clue"),))
    engine = ActionEngine(
        PowerReviewer(compiled, PowerPolicy(id="lock-test", version=1), frozenset({"gm"})),
        ResourceEngine(authored, catalog, compiled.rules, compiled.policy, ()),
        ActionRules(
            id="lock-test",
            version=1,
            maximum_wait=10000,
            combat=CombatRules(
                id="locks",
                version=1,
                battlefields=(Battlefield(id="dock", location_id="dock", width=5, height=5),),
                gurps_equipment=EquipmentCatalog(profile_id=PROFILE, entries=()),
            )
            if combat
            else None,
            scenes=SceneRules(
                id="locks",
                version=1,
                scenes=(
                    Scene(
                        id="dock-scene",
                        version=1,
                        location_id="dock",
                        title="Dock",
                        exits=(SceneExit(id="out", destination_id="alley-scene", ticks=2),),
                    ),
                    Scene(
                        id="alley-scene",
                        version=1,
                        location_id="alley",
                        title="Alley",
                        exits=(SceneExit(id="back", destination_id="dock-scene", ticks=2),),
                    ),
                ),
            )
            if scenes
            else None,
        ),
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=2),
        Purchase(definition_id="spell:apportation"),
        Purchase(definition_id="spell:lockmaster", amount=4),
        Purchase(definition_id="spell:magelock", amount=4),
    )
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 12}) if p.definition_id == "attribute:iq" else p
                for p in draft.purchases
            )
        }
    )
    await seed_play(
        play,
        initial,
        authored,
        ResourceState(
            owners=tuple(
                Owner(actor_id=a, capacity=100) for a in (("a", "b") if combat else ("a",))
            ),
            pools=tuple(
                Pool(id="hp:" + a, current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE))
                for a in (("a", "b") if combat else ("a",))
            ),
        ),
        (
            ActorSetup(
                actor_id="a", proposal=CharacterProposal(draft=draft), aware_of=("chest", "alley")
            ),
        )
        + (
            (ActorSetup(actor_id="b", proposal=CharacterProposal(draft=gurps_draft())),)
            if combat
            else ()
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="gm", role="gm"),
        )
        + (
            (CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),) if combat else ()
        ),
    )
    return initial["id"], play


async def revision(play: PlayService, cid: str) -> int:
    return (await play.store.read(cid))["revision"]


async def declare(play: PlayService, cid: str) -> None:
    service = LockService(play)
    await service.execute(
        cid,
        DeclareLock(
            id="fixture",
            actor_id="gm",
            expected_revision=0,
            state=LockState(
                fixture=LockFixture(
                    object_id="chest", location_id="dock", kind="door", passage=("dock", "alley")
                ),
                locked=True,
                closed=True,
            ),
        ),
        principal_id="gm",
    )
    for spell in ("lockmaster", "magelock"):
        await service.execute(
            cid,
            DeclareLockChannel(
                id="channel:" + spell,
                actor_id="gm",
                expected_revision=await revision(play, cid),
                channel=LockChannel.model_validate(
                    dict(
                        id=spell,
                        actor_id="a",
                        target_id="chest",
                        location_id="dock",
                        spell_id=spell,
                    )
                ),
            ),
            principal_id="gm",
        )


async def cast(
    play: PlayService,
    cid: str,
    spell: RuntimeSpellId,
    cast_id: str,
    *,
    dice: tuple[int, ...] | None = (3, 3, 3),
    channel: str | None = None,
) -> tuple[SpellResult, RuntimeSpellCommand]:
    service = LockSpellService(play)
    start = RuntimeSpellCommand(
        id=cast_id + ":start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id=spell,
        cast_id=cast_id,
        channel_id=channel or spell,
    )
    await service.execute(cid, start, principal_id="gm")
    effect = latest(play._load(await play.store.read(cid)).resources)[cast_id]
    for second in range(effect.started_at + 1, effect.ready_at + 1):
        result = await play.execute(
            cid,
            Wait(
                id=cast_id + ":wait:" + str(second),
                actor_id="a",
                expected_revision=await revision(play, cid),
                ticks=1,
            ),
            principal_id="a",
        )
        assert result.status == "committed", result
        if second != effect.ready_at:
            await service.execute(
                cid,
                start.model_copy(
                    update={
                        "id": cast_id + ":concentrate:" + str(second),
                        "expected_revision": await revision(play, cid),
                        "kind": "concentrate",
                    }
                ),
                principal_id="gm",
            )
    complete = start.model_copy(
        update={
            "id": cast_id + ":complete",
            "expected_revision": await revision(play, cid),
            "kind": "complete",
        }
    )
    play.rng = secrets if dice is None else RecordedDice(dice)
    completion = await service.execute(cid, complete, principal_id="gm")
    play.rng = secrets if dice is None else RecordedDice(())
    return completion, complete


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_lockmaster_host_changes_later_opening_and_move_then_replays(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await declare(play, cid)
    service = LockService(play)
    opening = OperateLock(
        id="opening", actor_id="a", expected_revision=3, target_id="chest", operation="open"
    )
    with pytest.raises(ConflictError, match="prevents opening"):
        await service.execute(cid, opening, principal_id="gm")
    move = Move(id="move-before", actor_id="a", expected_revision=3, destination_id="alley")
    result = await play.execute(cid, move, principal_id="a")
    assert result.status == "rejected" and result.code == "move.closed_door"
    outcome, complete = await cast(play, cid, "lockmaster", "unlock")
    assert outcome.energy_spent == 3
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 10 and not locks(state.resources)["chest"].locked
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7
    opening = opening.model_copy(update={"expected_revision": state.revision})
    await service.execute(cid, opening, principal_id="gm")
    result = await play.execute(
        cid,
        move.model_copy(
            update={"id": "move-after", "expected_revision": await revision(play, cid)}
        ),
        principal_id="a",
    )
    assert result.status == "committed"
    saved = await play.store.read(cid)
    assert next(e.location_id for e in play._load(saved).world.entities if e.id == "a") == "alley"
    assert saved == await play.store.replay(cid)
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await LockSpellService(restarted).execute(cid, complete, principal_id="gm") == outcome
    assert await restarted.store.read(cid) == saved
    events = await build_runtime(restarted).events(cid, principal_id="alice")
    assert "lock-channel:" not in str(events) and "effective_target" not in str(events)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_magelock_hosts_contest_and_exact_duplicate(tmp_path: Path, backend: str) -> None:
    cid, play = await prepare(tmp_path, backend)
    await declare(play, cid)
    outcome, _ = await cast(play, cid, "magelock", "ward")
    assert outcome.energy_spent == 3
    result, complete = await cast(play, cid, "lockmaster", "open-ward", dice=(3, 3, 3, 4, 4, 4))
    assert result.outcome == "active" and result.energy_spent == 3
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["ward"].phase == "ended"
    assert not locks(state.resources)["chest"].locked
    assert result.checks[0].effective_target == 13 and result.checks[1].effective_target == 14
    assert (
        await asyncio.gather(
            *(LockSpellService(play).execute(cid, complete, principal_id="gm") for _ in range(3))
        )
        == [result] * 3
    )
    with pytest.raises(ValidationError):
        await LockSpellService(play).execute(cid, complete, principal_id="alice")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_private_contextual_backfire_reverses_real_object_and_recovers_play(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.backfires import backfires
    from wayfarer.engine.simulation.magic.bindings import RuntimeBackfireAlternative
    from wayfarer.engine.simulation.magic.lock_host import DeclareLockBackfire
    from wayfarer.orchestration.spell_backfires import ResolveSpellBackfire, SpellBackfireService

    cid, play = await prepare(tmp_path, backend)
    await declare(play, cid)
    await LockService(play).execute(
        cid,
        DeclareLockBackfire(
            id="interpretation",
            actor_id="gm",
            expected_revision=3,
            choice=RuntimeBackfireAlternative(
                id="reverse-door",
                spell_id="magelock",
                rows=(13,),
                effect="reverse",
                target_ids=("chest",),
                reason="A reversed door-closing spell opens its intended door",
            ),
        ),
        principal_id="gm",
    )
    outcome, _ = await cast(play, cid, "magelock", "backfire", dice=(6, 6, 6, 5, 4, 4))
    assert outcome.outcome == "critical-failure" and outcome.energy_spent == 3
    before = play._load(await play.store.read(cid))
    pending = backfires(before.resources)[0]
    assert pending.pending and pending.row == 13
    assert locks(before.resources)["chest"].locked
    command = ResolveSpellBackfire(
        id="resolve",
        actor_id="gm",
        expected_revision=before.revision,
        backfire_id=pending.id,
        alternative_id="reverse-door",
    )
    result = await SpellBackfireService(play).execute(cid, command, principal_id="gm")
    assert not result.pending
    saved = await play.store.read(cid)
    value = locks(play._load(saved).resources)["chest"]
    assert not value.locked and not value.closed
    assert saved == await play.store.replay(cid)
    assert await SpellBackfireService(play).execute(cid, command, principal_id="gm") == result
    move = await play.execute(
        cid,
        Move(
            id="after-backfire",
            actor_id="a",
            expected_revision=saved["revision"],
            destination_id="alley",
        ),
        principal_id="a",
    )
    assert move.status == "committed"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seeded_private_lock_commands_reexecute_from_genesis(
    tmp_path: Path, backend: str
) -> None:
    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play = await prepare(tmp_path, backend)
    initial = await play.store.read(cid)
    play.rng = secrets
    await declare(play, cid)
    await cast(play, cid, "lockmaster", "seeded", dice=None)
    records = await played(play.store, cid)
    replayed, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert replayed == await play.store.read(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_scene_exit_requires_open_door_and_closing_works_from_either_side(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.orchestration.scenes import SceneService, TravelScene

    cid, play = await prepare(tmp_path, backend, scenes=True)
    await declare(play, cid)
    scene = SceneService(play)
    out = TravelScene(id="out", actor_id="a", expected_revision=3, exit_id="out")
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="closed door"):
        await scene.execute(cid, out, principal_id="a")
    assert await play.store.read(cid) == before
    await cast(play, cid, "lockmaster", "unlock")
    with pytest.raises(ConflictError, match="closed door"):
        await scene.execute(
            cid,
            out.model_copy(update={"expected_revision": await revision(play, cid)}),
            principal_id="a",
        )
    await LockService(play).execute(
        cid,
        OperateLock(
            id="open",
            actor_id="a",
            expected_revision=await revision(play, cid),
            target_id="chest",
            operation="open",
        ),
        principal_id="gm",
    )
    moved = await scene.execute(
        cid,
        out.model_copy(update={"expected_revision": await revision(play, cid)}),
        principal_id="a",
    )
    assert moved.scene_id == "alley-scene"
    await LockService(play).execute(
        cid,
        OperateLock(
            id="close-behind",
            actor_id="a",
            expected_revision=await revision(play, cid),
            target_id="chest",
            operation="close",
        ),
        principal_id="gm",
    )
    back = TravelScene(
        id="back", actor_id="a", expected_revision=await revision(play, cid), exit_id="back"
    )
    with pytest.raises(ConflictError, match="closed door"):
        await scene.execute(cid, back, principal_id="a")
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("retry", [False, True])
async def test_lock_hosts_recheck_director_seat_after_early_authorization(
    tmp_path: Path, retry: bool
) -> None:
    from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm

    cid, original = await prepare(tmp_path)
    await declare(original, cid)
    command = RuntimeSpellCommand(
        id="cast",
        actor_id="a",
        expected_revision=3,
        kind="start",
        spell_id="lockmaster",
        cast_id="cast",
        channel_id="lockmaster",
    )
    if retry:
        await LockSpellService(original).execute(cid, command, principal_id="gm")
    prior = await revision(original, cid)
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    play = build_play(tmp_path, original.engine, store=store, rng=RecordedDice(()))
    store.revoke = lambda: revoke_gm(original, cid, prior)
    if not retry:
        command = command.model_copy(update={"expected_revision": prior + 1})
    with pytest.raises(ValidationError, match="director authority"):
        await LockSpellService(play).execute(cid, command, principal_id="gm")
    saved = original._load(await original.store.read(cid))
    assert saved.revision == prior + 1
    assert len(latest(saved.resources)) == int(retry)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_touching_an_unseen_adjacent_lock_removes_range_and_visibility_penalties(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    await declare(play, cid)
    await LockService(play).execute(
        cid,
        DeclareLockChannel(
            id="touch",
            actor_id="gm",
            expected_revision=3,
            channel=LockChannel(
                id="touch",
                actor_id="a",
                target_id="chest",
                location_id="dock",
                spell_id="lockmaster",
                distance_yards=1,
                visible=False,
                touching=True,
            ),
        ),
        principal_id="gm",
    )
    result, _ = await cast(play, cid, "lockmaster", "touched", channel="touch")
    assert result.checks[0].effective_target == 14
    assert not locks(play._load(await play.store.read(cid)).resources)["chest"].locked


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_noncombat_lock_backfire_summons_and_reexecutes_canonical_encounter(
    tmp_path: Path, backend: str
) -> None:
    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.combat.spatial import Placement
    from wayfarer.engine.simulation.magic.backfires import backfires
    from wayfarer.engine.simulation.magic.bindings import RuntimeBackfireAlternative
    from wayfarer.engine.simulation.magic.lock_host import DeclareLockBackfire
    from wayfarer.engine.simulation.magic.summoning import SummonEncounter
    from wayfarer.orchestration.spell_backfires import ResolveSpellBackfire, SpellBackfireService
    from wayfarer.persistence.replay import verify_commands

    cid, play = await prepare(tmp_path, backend, combat=True)
    initial = await play.store.read(cid)
    play.rng = secrets
    play.seeds = lambda: f"{1232:064x}"  # Casting17, then source critical-table18.
    await declare(play, cid)
    await LockService(play).execute(
        cid,
        DeclareLockBackfire(
            id="malign-choice",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            choice=RuntimeBackfireAlternative(
                id="malign",
                spell_id="magelock",
                rows=(18,),
                effect="summon",
                target_ids=("b",),
                position=(3, 1),
                reason="B236 approved nearby malign reserve",
            ),
        ),
        principal_id="gm",
    )
    result, _ = await cast(play, cid, "magelock", "failed-ward", dice=None)
    assert result.outcome == "critical-failure" and result.energy_spent == 3
    before = play._load(await play.store.read(cid))
    assert not before.encounters
    (pending,) = backfires(before.resources)
    decision = ResolveSpellBackfire(
        id="summon",
        actor_id="gm",
        expected_revision=before.revision,
        backfire_id=pending.id,
        alternative_id="malign",
        summon_encounter=SummonEncounter(
            encounter_id="malign",
            battlefield_id="dock",
            caster=Placement(actor_id="a", position=GridPoint(x=1, y=1)),
        ),
    )
    resolved = await SpellBackfireService(play).execute(cid, decision, principal_id="gm")
    assert not resolved.pending and resolved.target_id == "b"
    final = await play.store.read(cid)
    state = play._load(final)
    assert state.encounters[0].turn_order == ("a", "b")
    assert state.encounters[0].oppositions
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7
    assert latest(state.resources)["failed-ward"].phase == "ended"
    assert locks(state.resources)["chest"].locked
    assert await SpellBackfireService(play).execute(cid, decision, principal_id="gm") == resolved
    assert await play.store.read(cid) == final
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "summon-reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert replayed == final
