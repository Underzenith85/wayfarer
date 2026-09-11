"""Live B238/B241/B249 spell and maneuver integration cases."""

import hashlib
from pathlib import Path

import pytest
from test_spell_bindings import command, idle, setup, start_fight

from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    ResumeInterruptedTurn,
    TakeCombatTurn,
)
from wayfarer.orchestration.spells import SpellService
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.combat import GridPoint
from wayfarer.simulation.injury import Wound, apply_injury
from wayfarer.simulation.maneuvers import WaitTrigger
from wayfarer.simulation.spells import latest


async def test_daze_completes_on_second_concentrate_turn(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, combat=True, execution_version=2)
    await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "daze", "channel_id": "daze"})
    assert (await service.execute(cid, start, principal_id="a")).outcome == "casting"
    await idle(cid, play, "b")
    play.rng = RecordedDice([3, 3, 3, 6, 6, 5])
    result = await service.execute(
        cid,
        start.model_copy(update={"id": "second", "kind": "concentrate", "expected_revision": 3}),
        principal_id="a",
    )
    assert result.outcome == "active" and result.energy_spent == 3
    saved = play._load(await play.store.read(cid))
    assert saved.resources.game_time == 1
    assert saved.encounters[0].current_actor_id == "b"


async def test_light_manipulation_costs_a_turn_and_changes_maintenance_penalty(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, combat=True, execution_version=2)
    await start_fight(cid, play)
    service = SpellService(play)
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(cid, command(1), principal_id="a")
    await idle(cid, play, "b")
    await service.execute(
        cid, command(3, "focus").model_copy(update={"position": (3, 2)}), principal_id="a"
    )
    await idle(cid, play, "b")
    new = command(5).model_copy(
        update={"cast_id": "daze", "spell_id": "daze", "channel_id": "daze"}
    )
    await service.execute(cid, new, principal_id="a")
    saved = play._load(await play.store.read(cid))
    effects = latest(saved.resources)
    assert effects["cast"].position == (3, 2)
    assert effects["daze"].skill == 10  # IQ/Magery skill14 - distance1 - concentrated spell3.


@pytest.mark.parametrize("roll,lost", [([3, 3, 3], False), ([5, 5, 3, 4], True)])
async def test_held_fireball_injury_checks_will_and_hits_caster(
    tmp_path: Path, roll: list[int], lost: bool
) -> None:
    cid, play = await setup(tmp_path, combat=True, execution_version=2)
    await start_fight(cid, play)
    play.rng = RecordedDice([3, 3, 3])
    await SpellService(play).execute(
        cid,
        command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"}),
        principal_id="a",
    )
    before = play._load(await play.store.read(cid))
    resources, _ = apply_injury(
        before.resources,
        Wound(
            id="wound",
            actor_id="a",
            expected_revision=before.revision,
            basic_damage=1,
            resistance=0,
            damage_type="cr",
        ),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    injured = before.model_copy(update={"revision": resources.revision, "resources": resources})
    play.rng = RecordedDice(roll)
    after = play.checkpoint(injured, before=before, run_npcs=False)
    assert latest(after.resources)["cast"].phase == ("ended" if lost else "active")
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == (5 if lost else 9)
    play.rng = RecordedDice([])
    assert play.checkpoint(after, before=before, run_npcs=False) == after


async def test_wait_releases_held_missile_then_resumes_interrupted_move(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, combat=True, execution_version=2)
    combat = await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="wait",
            actor_id="a",
            expected_revision=3,
            encounter_id="fight",
            maneuver="wait",
            wait_trigger=WaitTrigger(
                actor_id="b",
                action="move",
                item_id="spell:" + hashlib.sha256(b"cast").hexdigest(),
                reaction_target_id="b",
            ),
        ),
        authenticated_actor_id="a",
    )
    result = await combat.execute(
        cid,
        TakeCombatTurn(
            id="move",
            actor_id="b",
            expected_revision=4,
            encounter_id="fight",
            maneuver="move",
            destination=GridPoint(x=3, y=1),
        ),
        authenticated_actor_id="b",
    )
    assert result.code == "combat.wait_triggered"
    await service.execute(
        cid,
        start.model_copy(update={"id": "release", "kind": "release", "expected_revision": 5}),
        principal_id="a",
    )
    play.rng = RecordedDice([1, 2, 2, 4])
    await combat.execute(
        cid,
        ChooseDefense(
            id="defense", actor_id="b", expected_revision=6, encounter_id="fight", defense="none"
        ),
        authenticated_actor_id="b",
    )
    saved = play._load(await play.store.read(cid))
    assert saved.encounters[0].wait_interrupt and saved.encounters[0].wait_interrupt.ready
    await combat.execute(
        cid,
        ResumeInterruptedTurn(id="resume", actor_id="b", expected_revision=7, encounter_id="fight"),
        authenticated_actor_id="b",
    )
    saved = play._load(await play.store.read(cid))
    assert next(
        p.position for p in saved.encounters[0].participants if p.actor_id == "b"
    ) == GridPoint(x=3, y=1)
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_hex_fire_crossing_hurts_even_when_endpoint_is_outside(tmp_path: Path) -> None:
    from test_tactical import migration

    from wayfarer.simulation.hex_geometry import Hex

    cid, play = await setup(tmp_path, combat=True, execution_version=2)
    combat = await start_fight(cid, play)
    migrate = migration()
    await combat.execute(
        cid,
        migrate.model_copy(
            update={
                "placements": migrate.placements[:2],
                "battlefield": migrate.battlefield.model_copy(update={"id": "room"}),
            }
        ),
        authenticated_actor_id="gm",
    )
    play = play.for_campaign(await play.store.read(cid))
    combat = CombatService(play)
    play.rng = RecordedDice([3, 3, 3])
    await SpellService(play).execute(
        cid,
        command(2).model_copy(update={"spell_id": "create-fire", "channel_id": "create-fire"}),
        principal_id="a",
    )
    # b leaves the burning center and ends two hexes away. Contact rolls 1d-3;
    # the elapsed schedule independently settles its preceding full interval.
    play.rng = RecordedDice([5, 1])
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="cross",
            actor_id="b",
            expected_revision=3,
            encounter_id="fight",
            maneuver="move",
            hex_path=(Hex(q=2, r=0), Hex(q=3, r=0)),
        ),
        authenticated_actor_id="b",
    )
    saved = play._load(await play.store.read(cid))
    assert next(p.current for p in saved.resources.pools if p.id == "hp:b") == 8
    assert not any(h.active for h in saved.resources.hazards)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_gm_backfire_retarget_is_atomic_authorized_and_replayable(
    tmp_path: Path, backend: str
) -> None:
    import asyncio

    from wayfarer.errors import AuthorizationError, ConflictError
    from wayfarer.orchestration.spell_backfires import ResolveSpellBackfire, SpellBackfireService
    from wayfarer.simulation.spell_backfires import backfires
    from wayfarer.simulation.spell_bindings import BackfireAlternative
    from wayfarer.simulation.spell_effects import dazed

    option = BackfireAlternative(
        id="self-daze",
        spell_id="daze",
        rows=(4,),
        effect="retarget",
        target_ids=("a",),
        relationship="caster",
        reason="B236 harmful spell affects its caster",
    )
    cid, play = await setup(
        tmp_path, combat=True, execution_version=2, backend=backend, alternatives=(option,)
    )
    await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "daze", "channel_id": "daze"})
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    play.rng = RecordedDice([6, 6, 6, 1, 1, 2])
    await service.execute(
        cid,
        start.model_copy(update={"id": "finish", "kind": "concentrate", "expected_revision": 3}),
        principal_id="a",
    )
    saved = play._load(await play.store.read(cid))
    with pytest.raises(ConflictError, match="backfire"):
        await idle(cid, play, "b")
    decision = ResolveSpellBackfire(
        id="interpret",
        actor_id="gm",
        expected_revision=4,
        backfire_id=backfires(saved.resources)[0].id,
        alternative_id=option.id,
    )
    resolver = SpellBackfireService(play)
    with pytest.raises(AuthorizationError):
        await resolver.execute(cid, decision, authenticated_gm_id="a")
    play.rng = RecordedDice([])
    results = await asyncio.gather(
        *(resolver.execute(cid, decision, authenticated_gm_id="gm") for _ in range(3))
    )
    assert all(r == results[0] and not r.pending for r in results)
    saved = play._load(await play.store.read(cid))
    assert dazed(saved.resources, "a") and not dazed(saved.resources, "b")
    assert next(p.current for p in saved.resources.pools if p.id == "fp:a") == 7
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_failed_consciousness_on_release_commits_without_cost_or_reroll(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, combat=True, execution_version=2, caster_hp=1)
    await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(
        update={"spell_id": "fireball", "channel_id": "fireball", "hp_energy": 1}
    )
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    play.rng = RecordedDice([6, 6, 6, 4])
    release = start.model_copy(
        update={"id": "release", "kind": "release", "expected_revision": 3, "hp_energy": 0}
    )
    result = await service.execute(cid, release, principal_id="a")
    assert result.outcome == "interrupted" and result.energy_spent == 0
    saved = play._load(await play.store.read(cid))
    hp = next(p for p in saved.resources.pools if p.id == "hp:a")
    assert hp.current == -4 and hp.injury and hp.injury.unconscious
    assert saved.encounters[0].status == "completed"
    play.rng = RecordedDice([])
    assert await service.execute(cid, release, principal_id="a") == result


async def test_expansion_refund_waits_until_next_caster_turn(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, combat=True, execution_version=2, mana="very-high")
    await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    await service.execute(
        cid,
        start.model_copy(update={"id": "expand", "kind": "expand", "expected_revision": 3}),
        principal_id="a",
    )
    saved = play._load(await play.store.read(cid))
    assert next(p.current for p in saved.resources.pools if p.id == "fp:a") == 9
    await idle(cid, play, "b")
    await idle(cid, play, "a")
    saved = play._load(await play.store.read(cid))
    assert next(p.current for p in saved.resources.pools if p.id == "fp:a") == 10


@pytest.mark.parametrize(
    "table,expected",
    [([1, 1, 1], 6), ([2, 2, 2], 6), ([1, 2, 2], 4), ([3, 3, 2], 2), ([4, 4, 4], 2)],
)
async def test_fireball_body_criticals_execute_damage(
    tmp_path: Path, table: list[int], expected: int
) -> None:
    cid, play = await setup(tmp_path, combat=True, execution_version=2)
    combat = await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    await service.execute(
        cid,
        start.model_copy(update={"id": "release", "kind": "release", "expected_revision": 3}),
        principal_id="a",
    )
    play.rng = RecordedDice([1, 1, 1] + table + ([] if sum(table) == 6 else [2]) + [3, 3, 3])
    result = await combat.execute(
        cid,
        ChooseDefense(
            id="hit", actor_id="b", expected_revision=4, encounter_id="fight", defense="none"
        ),
        authenticated_actor_id="b",
    )
    assert result.injury and result.injury.injury == expected
    assert result.injury.adjudication_required is None


async def test_noncombat_mental_stun_recovers_at_next_second_with_iq(tmp_path: Path) -> None:
    from wayfarer.simulation.actions import Wait
    from wayfarer.simulation.spell_backfires import backfires

    cid, play = await setup(tmp_path, execution_version=2)
    service = SpellService(play)
    await service.execute(cid, command(), principal_id="a")
    await play.execute(
        cid, Wait(id="time", actor_id="a", expected_revision=1, ticks=1), authenticated_actor_id="a"
    )
    play.rng = RecordedDice([6, 6, 6, 3, 3, 3])
    await service.execute(cid, command(2, "complete"), principal_id="a")
    saved = play._load(await play.store.read(cid))
    assert backfires(saved.resources)[0].stunned
    play.rng = RecordedDice([4, 4, 4])  # IQ12 succeeds; HT10 would fail.
    await play.execute(
        cid,
        Wait(id="recover", actor_id="a", expected_revision=3, ticks=1),
        authenticated_actor_id="a",
    )
    saved = play._load(await play.store.read(cid))
    assert not backfires(saved.resources)[0].stunned
    hp = next(p for p in saved.resources.pools if p.id == "hp:a")
    assert hp.injury and not hp.injury.stunned


async def test_backfire_illusion_exposes_only_appearance(tmp_path: Path) -> None:
    from wayfarer.simulation.actions import Wait

    cid, play = await setup(tmp_path, execution_version=2)
    service = SpellService(play)
    await service.execute(cid, command(), principal_id="a")
    await play.execute(
        cid, Wait(id="time", actor_id="a", expected_revision=1, ticks=1), authenticated_actor_id="a"
    )
    play.rng = RecordedDice([6, 6, 6, 5, 5, 4])
    await service.execute(cid, command(2, "complete"), principal_id="a")
    saved = play._load(await play.store.read(cid))
    appearances = [f for f in saved.world.perspective("a").facts if f.predicate == "appearance"]
    assert [f.value for f in appearances] == ["The spell appears to take effect."]
    assert latest(saved.resources)["cast"].phase == "ended"


async def test_demon_result_adds_only_an_approved_reserve_combatant(tmp_path: Path) -> None:
    from wayfarer.orchestration.spell_backfires import ResolveSpellBackfire, SpellBackfireService
    from wayfarer.simulation.spell_backfires import backfires
    from wayfarer.simulation.spell_bindings import BackfireAlternative

    alternative = BackfireAlternative(
        id="malign-reserve",
        spell_id="light",
        rows=(18,),
        effect="summon",
        target_ids=("c",),
        position=(3, 3),
        reason="B236 authored malign entity",
    )
    cid, play = await setup(
        tmp_path, combat=True, execution_version=2, alternatives=(alternative,), reserve=True
    )
    await start_fight(cid, play)
    play.rng = RecordedDice([6, 6, 6, 6, 6, 6])
    await SpellService(play).execute(cid, command(1), principal_id="a")
    saved = play._load(await play.store.read(cid))
    decision = ResolveSpellBackfire(
        id="summon",
        actor_id="gm",
        expected_revision=2,
        backfire_id=backfires(saved.resources)[0].id,
        alternative_id=alternative.id,
    )
    await SpellBackfireService(play).execute(cid, decision, authenticated_gm_id="gm")
    saved = play._load(await play.store.read(cid))
    assert saved.encounters[0].turn_order == ("a", "b", "c")
    assert next(
        p.position for p in saved.encounters[0].participants if p.actor_id == "c"
    ) == GridPoint(x=3, y=3)
    assert "c" in {e.id for e in saved.world.perspective("a").entities}
    assert await play.store.read(cid) == await play.store.replay(cid)
