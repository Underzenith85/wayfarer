"""Independent B407 table outcomes and B414 numerical blast expectations."""

from pathlib import Path

import pytest
from test_firearm_malfunctions import firearm
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.explosion_types import BlastResponse, ExplosionSpec
from wayfarer.engine.rules.firearm_types import FirearmSpec
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.simulation.explosions import blasts
from wayfarer.engine.simulation.gurps_equipment import Damage
from wayfarer.engine.simulation.mechanics.firearms import roll_malfunction
from wayfarer.errors import ConflictError
from wayfarer.orchestration.combat import CombatService, ResolveWeaponExplosion
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


@pytest.mark.parametrize(
    "tl,action,table,kind,fired",
    [
        (3, "muzzleloader", 15, "explosion", 0),
        (4, "muzzleloader", 15, "mechanical", 0),
        (4, "breechloader", 15, "explosion", 0),
        (4, "repeating", 15, "explosion", 0),
        (5, "repeating", 15, "mechanical", 0),
        (6, "beam", 9, "mechanical", 0),
        (6, "beam", 5, "misfire", 0),
        (4, "grenade", 3, "delayed", 0),
        (4, "grenade", 5, "dud", 0),
        (4, "grenade", 9, "dud", 0),
        (4, "grenade", 15, "explosion", 0),
        (6, "grenade", 15, "delayed", 0),
        (6, "single-use", 9, "dud", 0),
        (6, "repeating", 9, "stoppage", 1),
    ],
)
async def test_exotic_table_branches(
    tmp_path: Path, tl: int, action: str, table: int, kind: str, fired: int
) -> None:
    # Test the authoritative classifier with pinned facts; integration cases follow.
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004")
    spec = FirearmSpec.model_validate(
        {
            "technology_level": tl,
            "action": action,
            "malfunction_override": 3,
            **({"fuse_seconds": 2} if action == "grenade" else {}),
        }
    )
    mode = firearm().model_copy(update={"firearm": spec})
    dice = (
        [1, 1, 1]
        if table == 3
        else [1, 2, 2]
        if table == 5
        else [3, 3, 3]
        if table == 9
        else [5, 5, 5]
    )
    play.rng = RecordedDice(dice)
    attack = success_roll("gurps-basic-set-4e-2004", 15, rng=RecordedDice([4, 4, 4]))
    _, shots, rolled, failure = roll_malfunction(
        play.rules_context, mode, attack, cause_id="cause", shots=3, rapid_bonus=0
    )
    assert failure is not None and failure.kind == kind and shots == fired and sum(rolled) == table
    if action == "beam":
        assert not failure.blocked_round


async def test_low_tl_explosion_uses_pinned_warhead_and_restarts(tmp_path: Path) -> None:
    mode = firearm().model_copy(
        update={
            "firearm": FirearmSpec(
                technology_level=3, action="muzzleloader", malfunction_override=12
            )
        }
    )
    payload = ExplosionSpec(dice=1, adds=4)
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=mode,
        ranged_scene=scene(),
        warhead=payload,
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 5, 5, 5])
    result = await defend(cid, play, "b")
    assert (
        result.injury is not None
        and result.injury.malfunction == "explosion"
        and result.injury.shots_fired == 0
    )
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    assert blast.payload == payload
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 9
    with pytest.raises(ConflictError, match="explosion"):
        await turn(cid, play, "b", "do_nothing")
    command = ResolveWeaponExplosion(
        id="blast",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blast.id,
        responses=tuple(BlastResponse(actor_id=a, cover_dr=0, size_modifier=0) for a in ("a", "b")),
        object_cover={},
        environment="air",
    )
    # A: 1+4 direct damage 5; B is one grid yard away: floor((2+4)/3)=2.
    play.rng = RecordedDice([1, 3, 3, 3, 2])
    resolved = await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "melee.sqlite"), play.engine, rng=RecordedDice([])
    )
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="gm")
        == resolved
    )
    state = play._load(await play.store.read(cid))
    assert blasts(state.resources)[0].resolved
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 5
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 8
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("malfunction", [False, True])
async def test_beam_cell_preserves_physical_instance(tmp_path: Path, malfunction: bool) -> None:
    mode = firearm().model_copy(
        update={
            "damage": Damage(basis="fixed", dice=1, damage_type="burn", tight_beam=True),
            "firearm": FirearmSpec(technology_level=8, action="beam", malfunction_override=12),
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        power_cell_capacity=6,
    )
    await load(cid, play)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=6
    )
    play.rng = RecordedDice([4, 4, 4, 3, 3, 3] if malfunction else [3, 3, 3, 1, 1, 1, 1, 1])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    cell = next(i for i in state.resources.items if i.id == "ammo-a")
    assert cell.quantity == 1 and cell.charges == (6 if malfunction else 0)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(
    "table,late,expected", [([3, 3, 3], [], "dud"), ([1, 1, 1], [1], "delayed")]
)
async def test_grenade_dud_and_delayed_fuse(
    tmp_path: Path, table: list[int], late: list[int], expected: str
) -> None:
    from test_gurps_ranged import weapon

    from wayfarer.engine.simulation.mechanics.weapon_flight import position
    from wayfarer.orchestration.combat import DeclareThrownLanding

    mode = weapon(thrown=True).model_copy(
        update={
            "firearm": FirearmSpec(
                technology_level=4, action="grenade", fuse_seconds=1, malfunction_override=12
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=mode,
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1),
    )
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4] + table + late)
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.malfunction == expected
    state = play._load(await play.store.read(cid))
    spent = next(i for i in state.resources.expended_items if i.id == "sword-a")
    assert spent.firearm_failure is not None and spent.firearm_failure.kind == expected
    if expected == "dud":
        assert not blasts(state.resources)
        return
    blast = blasts(state.resources)[0]
    assert blast.due == 2 and blast.fuse_dice == (1,)
    from wayfarer.orchestration.combat import EndEncounter

    with pytest.raises(ConflictError, match="armed explosives"):
        await CombatService(play).execute(
            cid,
            EndEncounter(
                id="end-too-soon",
                actor_id="gm",
                expected_revision=state.revision,
                encounter_id="fight",
                reason="leave",
            ),
            authenticated_actor_id="gm",
        )
    center = position(state.encounters[0], state.encounters[0].participants[0])
    await CombatService(play).execute(
        cid,
        DeclareThrownLanding(
            id="landing",
            actor_id="gm",
            expected_revision=state.revision,
            encounter_id="fight",
            item_id="sword-a",
            landing=center,
        ),
        authenticated_actor_id="gm",
    )
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 2
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ConflictError, match="explosion"):
        await turn(cid, play, "a", "do_nothing")
    assert await play.store.read(cid) == before
    command = ResolveWeaponExplosion(
        id="detonate",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blast.id,
        responses=tuple(BlastResponse(actor_id=a, cover_dr=0, size_modifier=0) for a in ("a", "b")),
        object_cover={},
        environment="air",
    )
    play.rng = RecordedDice([1, 1])
    await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    state = play._load(await play.store.read(cid))
    assert blasts(state.resources)[0].resolved
    spent = next(i for i in state.resources.expended_items if i.id == "sword-a")
    assert spent.firearm_failure is not None and spent.firearm_failure.kind == "destroyed"
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_single_use_dud_retires_launcher_and_round(tmp_path: Path) -> None:
    mode = firearm().model_copy(
        update={
            "shots": 1,
            "rate_of_fire": 1,
            "firearm": FirearmSpec(
                technology_level=6, action="single-use", malfunction_override=12
            ),
        }
    )
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=mode, ranged_scene=scene()
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 3, 3, 3])
    result = await defend(cid, play, "b")
    assert result.injury is not None and result.injury.shots_fired == 0
    state = play._load(await play.store.read(cid))
    assert not any(i.id == "sword-a" for i in state.resources.items)
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 9
    assert sum(i.id == "sword-a" for i in state.resources.expended_items) == 1
    assert not state.resources.ammunition_loads


async def test_fragmentation_and_object_damage_share_the_transaction(tmp_path: Path) -> None:
    from wayfarer.engine.rules.object_types import ObjectProfile

    mode = firearm().model_copy(
        update={
            "firearm": FirearmSpec(
                technology_level=3, action="muzzleloader", malfunction_override=12
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=mode,
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1, fragmentation_dice=1),
        durability=ObjectProfile(construction="unliving", hp=100, dr=100, ht=10, size_modifier=0),
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 5, 5, 5])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    command = ResolveWeaponExplosion(
        id="fragment-blast",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blast.id,
        responses=tuple(BlastResponse(actor_id=a, cover_dr=0, size_modifier=0) for a in ("a", "b")),
        object_cover={i: 0 for i in ("sword-a", "sword-b", "shield-b")},
        object_sizes={i: 0 for i in ("sword-a", "sword-b", "shield-b")},
        environment="air",
    )
    # Direct A: one automatic fragment. B: skill15, roll15 gives one fragment.
    # Three durable items receive separate blast and fragmentation reducer commands.
    play.rng = RecordedDice([1, 3, 3, 4, 1] + [1, 5, 5, 5, 3, 3, 4, 1] + [1, 5, 5, 5, 1] * 3)
    await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    state = play._load(await play.store.read(cid))
    assert len(state.resources.object_results) == 6
    assert all(r.injury == 0 for r in state.resources.object_results)
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 8
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_failed_blast_declaration_consumes_no_rolls(tmp_path: Path) -> None:
    from wayfarer.errors import ValidationError

    mode = firearm().model_copy(
        update={
            "firearm": FirearmSpec(
                technology_level=3, action="muzzleloader", malfunction_override=12
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1),
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 5, 5, 5])
    await defend(cid, play, "b")
    before = await play.store.read(cid)
    state = play._load(before)
    command = ResolveWeaponExplosion(
        id="bad",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blasts(state.resources)[0].id,
        responses=(),
        object_cover={},
        environment="air",
    )
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="every actor"):
        await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    assert await play.store.read(cid) == before


async def test_beam_misfire_clearing_does_not_consume_charge(tmp_path: Path) -> None:
    mode = firearm().model_copy(
        update={
            "damage": Damage(basis="fixed", dice=1, damage_type="burn", tight_beam=True),
            "firearm": FirearmSpec(technology_level=8, action="beam", malfunction_override=12),
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        power_cell_capacity=6,
    )
    await load(cid, play)
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 1, 2, 2])
    await defend(cid, play, "b")
    await turn(cid, play, "b", "do_nothing")
    play.rng = RecordedDice([3, 3, 3, 3, 3, 3])
    await turn(cid, play, "a", "ready", item_id="sword-a", firearm_service="diagnose")
    await turn(cid, play, "b", "do_nothing")
    for _ in range(3):
        await turn(cid, play, "a", "ready", item_id="sword-a", firearm_service="clear")
        await turn(cid, play, "b", "do_nothing")
    state = play._load(await play.store.read(cid))
    assert next(i for i in state.resources.items if i.id == "sword-a").firearm_failure is None
    assert next(i.charges for i in state.resources.items if i.id == "ammo-a") == 6
    assert state.resources.ammunition_loads[0].rounds == 6


def test_beam_skill_accepts_only_explicit_beam_construction() -> None:
    from wayfarer.engine.simulation.gurps_equipment import require_skill_procedure

    mode = firearm().model_copy(
        update={
            "skill_id": "skill:beam-weapons-pistol",
            "damage": Damage(basis="fixed", dice=1, damage_type="burn", tight_beam=True),
            "firearm": FirearmSpec(technology_level=8, action="beam"),
        }
    )
    require_skill_procedure("gurps-basic-set-4e-2004", mode)


async def test_immediate_explosion_defers_and_restores_round_time(tmp_path: Path) -> None:
    from test_gurps_ranged import weapon

    mode = weapon(thrown=True).model_copy(
        update={
            "firearm": FirearmSpec(
                technology_level=4, action="grenade", fuse_seconds=1, malfunction_override=12
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=mode,
        ranged_scene=scene()
        + (scene()[0].model_copy(update={"attacker_id": "b", "defender_id": "a"}),),
        warhead=ExplosionSpec(dice=1),
    )
    # The last actor in the round malfunctions: the blast precedes the owed clock tick.
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "attack", item_id="sword-b", target_id="a", mode_id="ranged")
    play.rng = RecordedDice([4, 4, 4, 5, 5, 5])
    await defend(cid, play, "a")
    state = play._load(await play.store.read(cid))
    blast = blasts(state.resources)[0]
    assert state.resources.game_time == 0 and blast.due == 0 and blast.deferred_ticks == 1
    command = ResolveWeaponExplosion(
        id="last-actor-blast",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blast.id,
        responses=tuple(BlastResponse(actor_id=a, cover_dr=0, size_modifier=0) for a in ("a", "b")),
        object_cover={},
        environment="air",
    )
    play.rng = RecordedDice([1, 1])
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 1
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="gm") == result
    assert await play.store.read(cid) == await play.store.replay(cid)
