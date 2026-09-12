"""B195/B270/B382 numeric fixtures, third/fourth printings; #191 remains open."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import scene, weapon
from test_rated_projectiles import rated

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.readiness import ProjectileReadiness
from wayfarer.engine.rules.types.skill import ControllingAttribute, Difficulty, SkillSpec, Specialty
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, TakeCombatTurn
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

FAST = RuleDefinition(
    "skill:fast-draw-arrow",
    DefinitionKind.SKILL,
    "Fast-Draw (Arrow)",
    "sjg:basic-set-characters-4e-2004",
    None,
    ImplementationStatus.IMPLEMENTED,
    hooks=("character.gurps-skill", "check.target"),
    skill=SkillSpec(
        ControllingAttribute.DX,
        Difficulty.EASY,
        "B195",
        specialty=Specialty("skill:fast-draw", "Arrow"),
    ),
)


async def ready(cid: str, play: PlayService, **options: object) -> None:
    await turn(
        cid,
        play,
        "a",
        "ready",
        item_id="sword-a",
        mode_id="ranged",
        reload_ammunition_id="ammo-a",
        **options,
    )
    await turn(cid, play, "b", "do_nothing")


@pytest.mark.parametrize("gun", [False, True])
@pytest.mark.parametrize(
    "dice,dropped,loaded", [([3, 3, 3], 0, 1), ([5, 5, 5], 1, 0), ([6, 6, 6], 10, 0)]
)
async def test_fast_draw_preserves_ammunition_and_receipts(
    tmp_path: Path,
    dice: list[int],
    dropped: int,
    loaded: int,
    gun: bool,
) -> None:
    from dataclasses import replace

    from test_firearm_malfunctions import firearm

    skill = (
        replace(
            FAST,
            id="skill:fast-draw-ammo-tl6",
            skill=SkillSpec(
                ControllingAttribute.DX,
                Difficulty.EASY,
                "B195",
                specialty=Specialty("skill:fast-draw", "Ammo"),
            ),
        )
        if gun
        else FAST
    )
    mode = (firearm() if gun else rated()).model_copy(
        update={
            "readiness": ProjectileReadiness(
                kind="firearm" if gun else "bow",
                fast_draw_skill_id=skill.id,
                fast_draw_specialty="Ammo" if gun else "Arrow",
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        extra_definitions=(skill,),
        extra_purchases=(Purchase(definition_id=skill.id, amount=1),),
    )
    state = play._load(await play.store.read(cid))
    command = TakeCombatTurn(
        id="fast-draw-test",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="ready",
        item_id="sword-a",
        mode_id="ranged",
        reload_ammunition_id="ammo-a",
        fast_draw=True,
    )
    play.rng = RecordedDice(dice)
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="a")
    saved = play._load(await play.store.read(cid))
    assert (
        sum(i.quantity for i in saved.resources.items if i.definition_id == "equipment:ammo") == 10
    )
    assert sum(i.quantity for i in saved.resources.items if i.ground) == dropped
    assert sum(v.rounds for v in saved.resources.ammunition_loads) == loaded * (6 if gun else 1)
    assert len([e for e in saved.resources.events if e.id.startswith("fast-draw:")]) == 1
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="a") == result
    )
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_bow_prepare_draw_let_down_without_extra_fatigue(tmp_path: Path) -> None:
    mode = rated().model_copy(update={"readiness": ProjectileReadiness(kind="bow")})
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=mode, ranged_scene=scene()
    )
    await ready(cid, play)
    state = play._load(await play.store.read(cid))
    progress = state.resources.ammunition_loads[0].readiness
    assert progress and progress.stage == "draw"
    assert state.resources.ammunition_loads[0].rounds == 0
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "do_nothing")
    await ready(cid, play)
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 1
    fp = next(p.current for p in state.resources.pools if p.id == "fp:a")
    await turn(cid, play, "a", "ready", item_id="sword-a", mode_id="ranged", let_down_bow=True)
    await turn(cid, play, "b", "do_nothing")
    await ready(cid, play)
    state = play._load(await play.store.read(cid))
    assert state.resources.ammunition_loads[0].rounds == 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == fp
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 10


async def test_authored_round_unloading_survives_interruption(tmp_path: Path) -> None:
    mode = weapon().model_copy(
        update={
            "readiness": ProjectileReadiness(
                kind="projectile",
                unload_seconds_per_round=2,
            )
        }
    )
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=mode, ranged_scene=scene()
    )
    await ready(cid, play)
    await ready(cid, play)
    for index in range(12):
        await turn(
            cid, play, "a", "ready", item_id="sword-a", mode_id="ranged", unload_ammunition=True
        )
        await turn(cid, play, "b", "do_nothing")
        state = play._load(await play.store.read(cid))
        assert sum(v.rounds for v in state.resources.ammunition_loads) == 6 - (index + 1) // 2
        assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 10
        if index == 0:
            await turn(cid, play, "a", "do_nothing")
            await turn(cid, play, "b", "do_nothing")
    await ready(cid, play)
    await ready(cid, play)
    assert play._load(await play.store.read(cid)).resources.ammunition_loads[0].rounds == 6


async def test_fast_draw_untrained_rejects_without_mutation(tmp_path: Path) -> None:
    mode = rated().model_copy(
        update={
            "readiness": ProjectileReadiness(
                kind="bow", fast_draw_skill_id=FAST.id, fast_draw_specialty="Arrow"
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        extra_definitions=(FAST,),
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="trained specialty"):
        await ready(cid, play, fast_draw=True)
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("st,turns", [(10, 4), (12, 8), (13, 20), (14, 20)])
async def test_crossbow_cocking_protocol(tmp_path: Path, st: int, turns: int) -> None:
    from wayfarer.engine.simulation.equipment.catalog import LITE_SOURCE, EquipmentProfile
    from wayfarer.engine.simulation.resources import Item

    aid = EquipmentProfile(
        definition_id="equipment:goats-foot",
        provenance=LITE_SOURCE,
        weight_millipounds=2000,
        price=50,
        technology_level=3,
    )
    mode = rated("crossbow", st).model_copy(
        update={
            "readiness": ProjectileReadiness(
                kind="crossbow",
                cocking_aid_definition_id=aid.definition_id,
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        extra_equipment=(aid,),
        extra_items=(Item(id="aid-a", definition_id=aid.definition_id, owner_id="a"),),
    )
    for index in range(turns):
        await ready(cid, play, cocking_aid_id="aid-a")
        state = play._load(await play.store.read(cid))
        assert state.resources.ammunition_loads[0].rounds == int(index == turns - 1)
        if index == 1:
            await turn(cid, play, "a", "do_nothing")
            await turn(cid, play, "b", "do_nothing")
            assert isinstance(play.store, AsyncSQLiteStore)
            play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert next(i.quantity for i in state.resources.items if i.id == "ammo-a") == 10
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_crossbow_missing_aid_rejects_before_dice(tmp_path: Path) -> None:
    mode = rated("crossbow", 13).model_copy(
        update={"readiness": ProjectileReadiness(kind="crossbow")}
    )
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=mode, ranged_scene=scene()
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="cocking aid"):
        await ready(cid, play)
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("quantity", [1, 10])
async def test_fast_draw_respects_shared_reservations(tmp_path: Path, quantity: int) -> None:
    from wayfarer.engine.rules.types.readiness import ProjectileProgress
    from wayfarer.engine.simulation.combat.ranged import reload_weapon
    from wayfarer.engine.simulation.resources import AmmunitionLoad, Item

    mode = rated().model_copy(
        update={
            "readiness": ProjectileReadiness(
                kind="bow", fast_draw_skill_id=FAST.id, fast_draw_specialty="Arrow"
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        extra_definitions=(FAST,),
        extra_purchases=(Purchase(definition_id=FAST.id, amount=1),),
        extra_items=(Item(id="other-a", definition_id="equipment:broadsword", owner_id="a"),),
    )
    state = play._load(await play.store.read(cid))
    shared = AmmunitionLoad(
        weapon_id="other-a",
        mode_id="ranged",
        ammunition_item_id="ammo-a",
        rounds=1,
        readiness=ProjectileProgress(stage="loaded"),
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "ammunition_loads": (shared,),
                    "items": tuple(
                        i.model_copy(update={"quantity": quantity}) if i.id == "ammo-a" else i
                        for i in state.resources.items
                    ),
                }
            )
        }
    )
    command = TakeCombatTurn(
        id="shared",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
        maneuver="ready",
        item_id="sword-a",
        mode_id="ranged",
        reload_ammunition_id="ammo-a",
        fast_draw=True,
    )
    play.rng = RecordedDice([6, 6, 6] if quantity > 1 else [])
    if quantity == 1:
        with pytest.raises(ValidationError, match="unreserved"):
            reload_weapon(play.rules_context, state, command)
        return
    updated = reload_weapon(play.rules_context, state, command)
    play.engine.resources.validate(updated)
    assert updated.ammunition_loads == (shared,)
    assert next(i.quantity for i in updated.items if i.id == "ammo-a") == 1
    assert sum(i.quantity for i in updated.items if i.ground) == quantity - 1


async def test_falling_interrupts_a_drawn_bow(tmp_path: Path) -> None:
    mode = rated().model_copy(update={"readiness": ProjectileReadiness(kind="bow")})
    cid, play = await setup(
        tmp_path, "gurps-basic-set-4e-2004", ranged_mode=mode, ranged_scene=scene()
    )
    await ready(cid, play)
    await ready(cid, play)
    await turn(cid, play, "a", "change_posture", posture="prone")
    saved = play._load(await play.store.read(cid))
    load = saved.resources.ammunition_loads[0]
    assert load.rounds == 0 and load.readiness and load.readiness.stage == "draw"
    assert next(i.quantity for i in saved.resources.items if i.id == "ammo-a") == 10
