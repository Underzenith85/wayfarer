"""B400, B408, B483–484 object impacts in the live projectile transaction."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import ChooseDefense, CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def test_burst_targets_object_with_individual_receipts_and_restart(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=weapon(),
        ranged_scene=scene(size=3),
        durability=ObjectProfile(construction="homogenous", hp=12, dr=2, ht=12),
    )
    await load(cid, play)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        mode_id="ranged",
        target_id="b",
        target_item_id="sword-b",
        shots=6,
    )
    state = play._load(await play.store.read(cid))
    command = ChooseDefense(
        id="object-burst",
        actor_id="b",
        encounter_id="fight",
        expected_revision=state.revision,
        defense="none",
    )
    # Skill 13 + burst 1 - weapon size 4 = 10, irrespective of the owner's SM.
    # Roll 6 yields three hits: cr 4,5,6 minus DR 2 = 2,3,4 object HP.
    play.rng = RecordedDice([2, 2, 2, 4, 5, 6])
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert result.injury
    assert result.injury.attack.effective_target == 10
    assert result.injury.hp_before == result.injury.hp_after == 10
    state = play._load(await play.store.read(cid))
    assert [r.injury for r in state.resources.object_results] == [2, 3, 4]
    assert len({r.command_id for r in state.resources.object_results}) == 3
    assert state.resources.ammunition_loads == ()
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=RecordedDice([]))
    assert (
        await CombatService(restarted).execute(cid, command, authenticated_actor_id="b") == result
    )
    assert restarted._load(await restarted.store.read(cid)).resources == state.resources


async def test_projectile_weapon_target_has_no_block_or_shield_bonus(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(),
        durability=ObjectProfile(construction="homogenous", hp=12, dr=2, ht=12),
    )
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        mode_id="ranged",
        target_id="b",
        target_item_id="sword-b",
    )
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending and "block" not in pending.allowed
    with pytest.raises(ValidationError):
        await defend(cid, play, "b", defense="block", item_id="shield-b")
    play.rng = RecordedDice([3, 3, 3, 3, 3, 3, 4])
    result = await defend(cid, play, "b", defense="dodge")
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == 8
    assert result.injury.injury == 0
    state = play._load(await play.store.read(cid))
    assert state.resources.object_results[-1].item_id == "sword-b"


async def test_burst_shield_interception_overpenetrates_separately(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=weapon(),
        ranged_scene=scene(),
        durability=ObjectProfile(construction="homogenous", hp=12, dr=0, ht=12),
    )
    await load(cid, play)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", mode_id="ranged", target_id="b", shots=6
    )
    # Three hits, Dodge 9 succeeds only with DB 1: one shield hit, two body hits.
    play.rng = RecordedDice([3, 3, 4, 3, 3, 3, 6, 1, 1])
    result = await defend(cid, play, "b", defense="dodge")
    assert result.injury
    assert result.injury.per_hit_damage == (3, 1, 1)
    assert result.injury.per_hit_injury == (3, 1, 1)
    state = play._load(await play.store.read(cid))
    assert state.resources.object_results[-1].item_id == "shield-b"
    assert state.resources.object_results[-1].injury == 6


@pytest.mark.parametrize("target_object", [False, True])
async def test_fireball_object_target_or_shield_interception(
    tmp_path: Path, target_object: bool
) -> None:
    from test_spell_bindings import command, idle, start_fight
    from test_spell_bindings import setup as spell_setup

    from wayfarer.engine.simulation.equipment.catalog import LITE_SOURCE, EquipmentProfile, Shield
    from wayfarer.orchestration.spells import SpellService

    entry = EquipmentProfile(
        definition_id="equipment:target",
        provenance=LITE_SOURCE.model_copy(update={"source_id": "sjg:basic-set-characters-4e-2004"}),
        weight_millipounds=10,
        price=10,
        technology_level=1,
        slot="hand",
        shield=Shield(defense_bonus=1, skill_id="skill:innate-attack-projectile"),
        durability=ObjectProfile(construction="homogenous", hp=12, dr=0, ht=12, size_modifier=-2),
    )
    cid, play = await spell_setup(tmp_path, combat=True, execution_version=2, equipment=(entry,))
    combat = await start_fight(cid, play)
    service = SpellService(play)
    start = command(1).model_copy(update={"spell_id": "fireball", "channel_id": "fireball"})
    play.rng = RecordedDice([3, 3, 3])
    await service.execute(cid, start, principal_id="a")
    await idle(cid, play, "b")
    await service.execute(
        cid,
        start.model_copy(
            update={
                "id": "release",
                "kind": "release",
                "expected_revision": 3,
                "target_item_id": "target-0" if target_object else None,
            }
        ),
        principal_id="a",
    )
    # DX10 default Innate Attack: 6, or 4 for the object. A roll of 5 succeeds
    # normally; object case rolls 4 (critical) then table 10 with no extra effect.
    play.rng = RecordedDice([1, 1, 2, 3, 3, 4, 6] if target_object else [1, 2, 2, 3, 3, 3, 6])
    choice = ChooseDefense(
        id="fire-impact",
        actor_id="b",
        encounter_id="fight",
        expected_revision=4,
        defense="none" if target_object else "dodge",
    )
    result = await combat.execute(cid, choice, authenticated_actor_id="b")
    assert result.injury
    assert result.injury.injury == (0 if target_object else 3)
    state = play._load(await play.store.read(cid))
    assert state.resources.object_results[-1].item_id == "target-0"
    assert state.resources.object_results[-1].injury == 6
    play.rng = RecordedDice([])
    assert await combat.execute(cid, choice, authenticated_actor_id="b") == result


async def test_second_defense_stresses_only_after_failed_first_defense(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=0, ht=12),
        object_hp=0,
    )
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "all_out_defense", defense_option="double")
    play.rng = RecordedDice([3, 3, 3])  # Attacking weapon remains usable this second.
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice([3, 3, 3, 3, 3, 3, 4, 4, 4, 6, 6, 6, 2])
    result = await defend(
        cid, play, "b", defense="dodge", second_defense="parry", second_item_id="sword-b"
    )
    assert result.injury and result.injury.second_defense is None
    state = play._load(await play.store.read(cid))
    sword = next(i for i in state.resources.items if i.id == "sword-b")
    assert sword.condition and sword.condition.disabled
    assert [r.item_id for r in state.resources.object_results] == ["sword-a", "shield-b", "sword-b"]


async def test_destroyed_shield_remains_carried_until_minus_ten_hp(tmp_path: Path) -> None:
    from test_gurps_melee import attack

    from wayfarer.engine.simulation.combat.objects.combat import shield_damage
    from wayfarer.engine.simulation.equipment.catalog import Damage

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        durability=ObjectProfile(construction="homogenous", hp=12, dr=0, ht=12),
    )
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    damage = Damage(basis="fixed", dice=1, damage_type="cr")
    play.rng = RecordedDice([])
    state, encounter, residual = shield_damage(
        play.rules_context, state, state.encounters[0], "shield-b", 72, damage, impact=0
    )
    item = next(i for i in state.resources.items if i.id == "shield-b")
    assert item.condition and item.condition.destroyed and item.condition.hp == -60
    assert not item.ready and item.equipped and residual == 69
    state, _, residual = shield_damage(
        play.rules_context, state, encounter, "shield-b", 60, damage, impact=1
    )
    item = next(i for i in state.resources.items if i.id == "shield-b")
    assert item.condition and item.condition.hp == -120
    assert not item.equipped and item.owner_id == "b" and residual == 57


async def test_ground_projectile_uses_item_distance_and_zero_speed(tmp_path: Path) -> None:
    from wayfarer.contracts import Campaign, CommandReceipt
    from wayfarer.engine.rules.types.object import GroundPosition

    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=weapon(thrown=True),
        ranged_scene=scene(distance=20, speed=20),
        durability=ObjectProfile(construction="homogenous", hp=12, dr=0, ht=12),
    )

    def drop(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(
                                update={
                                    "ready": False,
                                    "equipped": False,
                                    "ground": GroundPosition(
                                        encounter_id="fight", geometry="grid", x=3, y=0
                                    ),
                                }
                            )
                            if i.id == "sword-b"
                            else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        )
        from wayfarer.engine.simulation.combat.equipment_effects import synchronize

        state = state.model_copy(
            update={
                "encounters": tuple(synchronize(state, e) for e in state.encounters),
                "actors": tuple(
                    a.model_copy(
                        update={
                            "held_item_hands": tuple(
                                (i, h) for i, h in a.held_item_hands if i != "sword-b"
                            )
                        }
                    )
                    for a in state.actors
                ),
            }
        )
        campaign["play_json"] = state.model_dump_json()
        return CommandReceipt(action="combat", outcome="drop")

    await play.store.commit_turn(cid, "drop-fixture", 1, "drop-fixture", drop)
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        target_id="b",
        target_item_id="sword-b",
        mode_id="ranged",
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense and state.encounters[0].pending_defense.allowed == (
        "none",
    )
    play.rng = RecordedDice([2, 3, 3, 2])
    result = await defend(cid, play, "b")
    assert result.injury and result.injury.attack.effective_target == 8  # 13 - size4 - range1
    assert result.injury.injury == 0
    assert play._load(await play.store.read(cid)).resources.object_results[-1].injury == 2
