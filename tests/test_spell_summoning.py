"""B236 row18 binds real approved opposition, even after noncombat casting."""

import asyncio
import secrets
from pathlib import Path

import pytest
from test_spell_bindings import command, setup

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.backfires import backfires
from wayfarer.engine.simulation.magic.bindings import BackfireAlternative
from wayfarer.engine.simulation.magic.summoning import SummonEncounter
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spell_backfires import ResolveSpellBackfire, SpellBackfireService
from wayfarer.orchestration.spells import SpellService


async def pending(
    tmp_path: Path,
    backend: str = "sqlite",
    *,
    seeded: bool = False,
    hex_grid: bool = False,
    target_id: str = "c",
) -> tuple[str, PlayService, ResolveSpellBackfire, Campaign]:
    from test_hex_geometry import board

    from wayfarer.engine.simulation.hex_geometry import Hex

    option = BackfireAlternative(
        id="malign-reserve",
        spell_id="light",
        rows=(18,),
        effect="summon",
        target_ids=(target_id,),
        position=(2, 1),
        reason="B236 authored malign entity",
    )
    cid, play = await setup(
        tmp_path,
        combat=True,
        backend=backend,
        execution_version=2,
        battlefield=board().model_copy(update={"id": "room", "location_id": "room"})
        if hex_grid
        else None,
        alternatives=(option,),
        reserve=True,
    )
    initial = await play.store.read(cid)
    if seeded:
        play.rng = secrets
        play.seeds = lambda: f"{1232:064x}"
    await SpellService(play).execute(cid, command(), principal_id="a")
    await play.execute(
        cid, Wait(id="time", actor_id="a", expected_revision=1, ticks=1), principal_id="a"
    )
    if not seeded:
        play.rng = RecordedDice((6, 6, 6, 6, 6, 6))
    result = await SpellService(play).execute(cid, command(2, "complete"), principal_id="a")
    assert result.outcome == "critical-failure"
    state = play._load(await play.store.read(cid))
    assert not state.encounters and backfires(state.resources)[0].row == 18
    decision = ResolveSpellBackfire(
        id="summon",
        actor_id="gm",
        expected_revision=3,
        backfire_id=backfires(state.resources)[0].id,
        alternative_id=option.id,
        summon_encounter=SummonEncounter(
            encounter_id="malign",
            battlefield_id="room",
            caster=Placement(
                actor_id="a",
                position=Hex(q=1, r=1) if hex_grid else GridPoint(x=1, y=1),
                hex_facing=0 if hex_grid else None,
            ),
            summoned_hex_facing=3 if hex_grid else None,
        ),
    )
    if not seeded:
        play.rng = RecordedDice(())
    return cid, play, decision, initial


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_noncombat_backfire_starts_canonical_encounter_once(
    tmp_path: Path, backend: str
) -> None:
    cid, play, decision, _ = await pending(tmp_path, backend)
    before = await play.store.read(cid)
    service = SpellBackfireService(play)
    with pytest.raises(AuthorizationError):
        await service.execute(cid, decision, principal_id="a")
    with pytest.raises(ValidationError, match="GM-authored"):
        await service.execute(
            cid, decision.model_copy(update={"summon_encounter": None}), principal_id="gm"
        )
    assert await play.store.read(cid) == before
    result = await service.execute(cid, decision, principal_id="gm")
    after = await play.store.read(cid)
    state = play._load(after)
    assert not result.pending and result.target_id == "c"
    assert state.revision == 4 and state.resources.game_time == 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    (encounter,) = state.encounters
    assert encounter.id == "malign" and encounter.status == "active"
    assert set(encounter.turn_order) == {"a", "c"}
    assert encounter.completion_policy == "gm" and encounter.oppositions
    assert next(p.position for p in encounter.participants if p.actor_id == "c") == GridPoint(
        x=2, y=1
    )
    assert "c" in {e.id for e in state.world.perspective("a").entities}
    assert await service.execute(cid, decision, principal_id="gm") == result
    assert await play.store.read(cid) == after == await play.store.replay(cid)
    with pytest.raises(ConflictError):
        await service.execute(cid, decision.model_copy(update={"id": "stale"}), principal_id="gm")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_summoning_seeded_retry_race_rollback_and_reexecution(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import played
    from test_gadgeteer_gizmos_persistence import FailingCommitPlay

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.orchestration.membership import member_for
    from wayfarer.orchestration.pipeline import submit
    from wayfarer.persistence.replay import verify_commands

    cid, play, decision, initial = await pending(tmp_path, backend, seeded=True)
    before = await play.store.read(cid)
    failed = FailingCommitPlay(play.store, play.engine, rng=secrets)
    state = play._load(before)
    with pytest.raises(RuntimeError, match="crash after candidate checkpoint"):
        await submit(
            failed,
            cid,
            SpellBackfireService(failed).plan(
                failed, state, member_for(state, "gm"), decision, principal_id="gm"
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    results = await asyncio.gather(
        *(SpellBackfireService(play).execute(cid, decision, principal_id="gm") for _ in range(3))
    )
    assert results == [results[0]] * 3 and not results[0].pending
    final = await play.store.read(cid)
    assert len(play._load(final).encounters) == 1 and final["revision"] == before["revision"] + 1
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert all(check.folded and check.reexecuted for check in checks)
    assert replayed == final


async def test_summoned_entity_can_attack_a_helpless_caster(tmp_path: Path) -> None:
    from wayfarer.contracts import CommandReceipt
    from wayfarer.orchestration.combat import CombatService, TakeUnarmedTurn

    cid, play, decision, _ = await pending(tmp_path)
    before = play._load(await play.store.read(cid))
    hp = next(p for p in before.resources.pools if p.id == "hp:a")
    assert hp.injury is not None
    hp = hp.model_copy(
        update={"injury": hp.injury.model_copy(update={"unconscious": True, "anatomy": "human"})}
    )

    def collapse(campaign: Campaign) -> CommandReceipt:
        updated = before.model_copy(
            update={
                "revision": before.revision + 1,
                "resources": before.resources.model_copy(
                    update={
                        "revision": before.revision + 1,
                        "pools": tuple(
                            hp
                            if p.id == hp.id
                            else p.model_copy(
                                update={"injury": p.injury.model_copy(update={"anatomy": "human"})}
                            )
                            if p.injury
                            else p
                            for p in before.resources.pools
                        ),
                    }
                ),
            }
        )
        play.commit(campaign, updated)
        return CommandReceipt(action="resource", outcome="unconscious")

    await play.store.commit_turn(
        cid, "collapse", before.revision, "collapse", collapse, actor_id="gm"
    )
    decision = decision.model_copy(update={"expected_revision": before.revision + 1})
    await SpellBackfireService(play).execute(cid, decision, principal_id="gm")
    state = play._load(await play.store.read(cid))
    (encounter,) = state.encounters
    assert encounter.status == "active" and encounter.current_actor_id == "c"
    assert next(p for p in state.resources.pools if p.id == "hp:a").injury == hp.injury
    play.rng = RecordedDice((3, 3, 3, 2))
    result = await CombatService(play).execute(
        cid,
        TakeUnarmedTurn(
            id="malign-attack",
            actor_id="c",
            expected_revision=state.revision,
            encounter_id=encounter.id,
            action="punch",
            enter_close_combat=True,
            target_id="a",
            hands=("right-hand",),
        ),
        principal_id="c",
    )
    assert result.code != "combat.unavailable"
    after = play._load(await play.store.read(cid))
    assert (
        after.encounters[0].pending_unarmed is not None
        or next(p.current for p in after.resources.pools if p.id == "hp:a") < 10
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_noncombat_summoning_preserves_authored_hex_geometry(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.hex_geometry import Hex

    cid, play, decision, _ = await pending(tmp_path, backend, hex_grid=True)
    await SpellBackfireService(play).execute(cid, decision, principal_id="gm")
    state = play._load(await play.store.read(cid))
    (encounter,) = state.encounters
    assert encounter.spatial_kind == "hex"
    summoned = next(p for p in encounter.participants if p.actor_id == "c")
    assert summoned.position == Hex(q=2, r=1) and summoned.hex_facing == 3
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("invalid", ["wrong-caster", "off-board", "occupied", "unknown-board"])
async def test_noncombat_summon_bad_placement_is_atomic(tmp_path: Path, invalid: str) -> None:
    cid, play, decision, _ = await pending(tmp_path)
    declaration = decision.summon_encounter
    assert declaration is not None
    if invalid == "wrong-caster":
        declaration = declaration.model_copy(
            update={"caster": declaration.caster.model_copy(update={"actor_id": "b"})}
        )
    elif invalid == "unknown-board":
        declaration = declaration.model_copy(update={"battlefield_id": "missing"})
    else:
        declaration = declaration.model_copy(
            update={
                "caster": declaration.caster.model_copy(
                    update={"position": GridPoint(x=7 if invalid == "off-board" else 2, y=1)}
                )
            }
        )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await SpellBackfireService(play).execute(
            cid, decision.model_copy(update={"summon_encounter": declaration}), principal_id="gm"
        )
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert backfires(play._load(before).resources)[0].pending
    await SpellBackfireService(play).execute(cid, decision, principal_id="gm")


@pytest.mark.parametrize("retry", [False, True])
async def test_summon_authority_is_current_at_commit_and_duplicate(
    tmp_path: Path, retry: bool
) -> None:
    from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm

    cid, original, decision, _ = await pending(tmp_path)
    if retry:
        await SpellBackfireService(original).execute(cid, decision, principal_id="gm")
    revision = 3 + int(retry)
    store = RevokingStore(tmp_path / "spells.sqlite", 10)
    play = PlayService(store, original.engine, rng=RecordedDice(()))
    store.revoke = lambda: revoke_gm(original, cid, revision)
    with pytest.raises(AuthorizationError, match="authority"):
        await SpellBackfireService(play).execute(
            cid,
            decision if retry else decision.model_copy(update={"expected_revision": revision + 1}),
            principal_id="gm",
        )
    assert (await original.store.read(cid))["revision"] == revision + 1


async def test_ordinary_start_cannot_bypass_pending_backfire(tmp_path: Path) -> None:
    from wayfarer.orchestration.combat import CombatService, StartEncounter

    cid, play, decision, _ = await pending(tmp_path)
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="backfire"):
        await CombatService(play).execute(
            cid,
            StartEncounter(
                id="start",
                actor_id="gm",
                expected_revision=3,
                encounter_id="current",
                battlefield_id="room",
                placements=(
                    Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                    Placement(actor_id="b", position=GridPoint(x=3, y=1)),
                ),
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
    await SpellBackfireService(play).execute(cid, decision, principal_id="gm")
    assert play._load(await play.store.read(cid)).encounters[0].id == "malign"


@pytest.mark.parametrize("explicit_sides", [False, True])
async def test_fast_summoned_reserve_does_not_steal_existing_current_turn(
    tmp_path: Path, explicit_sides: bool
) -> None:
    from test_spell_bindings import start_fight
    from test_statistics import gurps_draft

    from wayfarer.engine.simulation.combat.encounter import CombatAllegiance, SideOpposition
    from wayfarer.orchestration.combat import CombatService, StartEncounter

    draft = gurps_draft()
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 12}) if p.definition_id == "attribute:dx" else p
                for p in draft.purchases
            )
        }
    )
    option = BackfireAlternative(
        id="malign-reserve",
        spell_id="light",
        rows=(18,),
        effect="summon",
        target_ids=("c",),
        position=(4, 4),
        reason="B236 authored malign entity",
    )
    cid, play = await setup(
        tmp_path,
        combat=True,
        execution_version=2,
        alternatives=(option,),
        reserve=True,
        reserve_draft=draft,
    )
    if explicit_sides:
        await CombatService(play).execute(
            cid,
            StartEncounter(
                id="start",
                actor_id="gm",
                expected_revision=0,
                encounter_id="fight",
                battlefield_id="room",
                placements=(
                    Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                    Placement(actor_id="b", position=GridPoint(x=2, y=1)),
                ),
                allegiances=(
                    CombatAllegiance(actor_id="a", side_id="party"),
                    CombatAllegiance(actor_id="b", side_id="foe"),
                ),
                oppositions=(SideOpposition(side_ids=("foe", "party")),),
            ),
            principal_id="gm",
        )
    else:
        await start_fight(cid, play)
    play.rng = RecordedDice((6, 6, 6, 6, 6, 6))
    await SpellService(play).execute(cid, command(1), principal_id="a")
    before = play._load(await play.store.read(cid))
    assert before.encounters[0].current_actor_id == "b"
    decision = ResolveSpellBackfire(
        id="summon",
        actor_id="gm",
        expected_revision=2,
        backfire_id=backfires(before.resources)[0].id,
        alternative_id=option.id,
    )
    play.rng = RecordedDice(())
    await SpellBackfireService(play).execute(cid, decision, principal_id="gm")
    after = play._load(await play.store.read(cid))
    assert after.encounters[0].turn_order == ("c", "a", "b")
    assert after.encounters[0].current_actor_id == "b"
    sides = {a.actor_id: a.side_id for a in after.encounters[0].allegiances}
    assert set(sides) == {"a", "b", "c"}
    assert any(set(o.side_ids) == {sides["a"], sides["c"]} for o in after.encounters[0].oppositions)
    if explicit_sides:
        assert sides["a"] == "party" and sides["b"] == "foe"
        assert before.encounters[0].oppositions[0] in after.encounters[0].oppositions
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_noncombat_summon_transfers_only_reserve_actor_between_groups(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.campaign.party import PartyState, Subgroup
    from wayfarer.engine.simulation.campaign.scenes import ActorScene, Scene, SceneRules
    from wayfarer.engine.simulation.magic.backfire_transitions import resolve
    from wayfarer.orchestration.play import PlayService
    from wayfarer.orchestration.spell_summoning import start_summon

    cid, base, cmd, _ = await pending(tmp_path)
    scenes = SceneRules(
        id="scenes",
        version=1,
        scenes=(Scene(id="room-scene", version=1, location_id="room", title="Room"),),
    )
    engine = ActionEngine(
        base.engine.reviewer,
        base.engine.resources,
        base.engine.rules.model_copy(update={"scenes": scenes}),
    )
    play = PlayService(base.store, engine, rng=base.rng)
    state = base._load(await base.store.read(cid)).model_copy(
        update={
            "configuration_digest": engine.digest,
            "actor_scenes": tuple(
                ActorScene(actor_id=a, scene_id="room-scene") for a in ("a", "b", "c")
            ),
            "party": PartyState(
                groups=(
                    Subgroup(id="party", scene_id="room-scene", actor_ids=("a",), ready_through=1),
                    Subgroup(
                        id="reserve", scene_id="room-scene", actor_ids=("b", "c"), ready_through=1
                    ),
                )
            ),
        }
    )
    engine.validate(state)
    after, result = resolve(
        play.rules_context, state, cmd, start_summon=lambda s, c, x: start_summon(play, s, c, x)
    )
    engine.validate(after)
    assert not result.pending and after.revision == state.revision + 1
    groups = {g.id: g for g in after.party.groups}
    assert groups["party"].actor_ids == ("a", "c") and groups["party"].generation == 1
    assert groups["reserve"].actor_ids == ("b",) and groups["reserve"].generation == 1
    assert after.resources.game_time == 1
