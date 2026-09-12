"""Campaigns fourth-printing B409-410 suppression-fire integration."""

from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup

from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.combat import Placement, RangedSituation, SuppressionZone
from wayfarer.simulation.combat_commands import TakeCombatTurn
from wayfarer.simulation.gurps_equipment import Damage, RangedMode
from wayfarer.simulation.hex_geometry import Cell, Hex, HexBattlefield


def automatic_weapon() -> RangedMode:
    return RangedMode(
        id="suppress",
        skill_id="skill:broadsword",
        minimum_st=10,
        damage=Damage(basis="fixed", dice=1, damage_type="cr"),
        accuracy=3,
        range_basis="yards",
        maximum_range=100,
        half_damage_range=50,
        shots=20,
        reload_seconds=2,
        rate_of_fire=10,
        recoil=2,
        bulk=-3,
        ammunition_id="equipment:ammo",
    )


def battlefield() -> HexBattlefield:
    return HexBattlefield(
        id="hex-dock",
        location_id="dock",
        coordinate_system="hex-axial-v1",
        profile_id="gurps-basic-set-4e-2004",
        baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
        darkness_penalty=-10,
        cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(7) for r in range(-3, 3)),
    )


async def suppression_fixture(tmp_path: Path) -> tuple[str, PlayService]:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=automatic_weapon(),
        ranged_scene=(RangedSituation(attacker_id="a", defender_id="b", distance_yards=2),),
        third_actor=True,
        battlefield=battlefield(),
        placements=(
            Placement(actor_id="a", position=Hex(q=0, r=0), hex_facing=0),
            Placement(actor_id="b", position=Hex(q=2, r=-2), hex_facing=3),
            Placement(actor_id="c", position=Hex(q=6, r=-2), hex_facing=3),
        ),
    )
    for _ in range(2):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="suppress",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
        await turn(cid, play, "c", "do_nothing")
    return cid, play


async def test_suppression_fire_pays_up_front_attacks_entry_and_expires(tmp_path: Path) -> None:
    cid, play = await suppression_fixture(tmp_path)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="suppress-lane",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="all_out_attack",
            attack_option="suppression",
            item_id="sword-a",
            mode_id="suppress",
            suppression_zones=(SuppressionZone(center=Hex(q=4, r=0), shots=10),),
        ),
        authenticated_actor_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert not state.resources.ammunition_loads
    assert len(state.encounters[0].suppression_zones) == 1

    movement = await turn(
        cid,
        play,
        "b",
        "move",
        hex_path=(Hex(q=2, r=-1), Hex(q=2, r=0)),
        hex_facing=3,
    )
    assert movement.code == "combat.defense_required"
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    assert (pending.attacker_id, pending.defender_id, pending.suppression_skill_cap) == (
        "a",
        "b",
        6,
    )
    assert pending.hit_location == "random"

    play.rng = RecordedDice([3, 3, 2, 3, 3, 3, 1])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 8
    assert result.injury.shots_fired == 10
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].current_actor_id == "c"
    assert state.encounters[0].suppression_zones[0].remaining_hits == 9
    assert await play.store.read(cid) == await play.store.replay(cid)

    await turn(cid, play, "c", "do_nothing")
    assert not play._load(await play.store.read(cid)).encounters[0].suppression_zones


async def test_invalid_multiple_suppression_zones_reject_before_mutation(tmp_path: Path) -> None:
    cid, play = await suppression_fixture(tmp_path)
    before = await play.store.read(cid)
    state = play._load(before)
    with pytest.raises(ValidationError, match="at least five shots"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="underfilled-zones",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="all_out_attack",
                attack_option="suppression",
                item_id="sword-a",
                mode_id="suppress",
                suppression_zones=(
                    SuppressionZone(center=Hex(q=3, r=0), shots=4),
                    SuppressionZone(center=Hex(q=5, r=0), shots=5),
                ),
            ),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == before


async def test_multiple_adjacent_zones_queue_separate_attacks(tmp_path: Path) -> None:
    cid, play = await suppression_fixture(tmp_path)
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="suppress-two-zones",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="all_out_attack",
            attack_option="suppression",
            item_id="sword-a",
            mode_id="suppress",
            suppression_zones=(
                SuppressionZone(center=Hex(q=3, r=0), shots=5),
                SuppressionZone(center=Hex(q=5, r=0), shots=5),
            ),
        ),
        authenticated_actor_id="a",
    )
    movement = await turn(
        cid,
        play,
        "b",
        "move",
        hex_path=(Hex(q=2, r=-1), Hex(q=2, r=0)),
        hex_facing=3,
    )
    assert movement.code == "combat.defense_required"
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    assert len(pending.suppression_attacks) == 1

    play.rng = RecordedDice([5, 5, 5, 5, 5, 5])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    assert pending.suppression_zone_id == "suppression:suppress-two-zones:1"
    assert state.encounters[0].current_actor_id == "b"

    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].pending_defense is None
    assert state.encounters[0].current_actor_id == "c"
    assert all(zone.attacked_actor_ids == ("b",) for zone in state.encounters[0].suppression_zones)
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_suppression_fire_fails_closed_without_exact_hex_geometry(tmp_path: Path) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        ranged_mode=automatic_weapon(),
    )
    before = await play.store.read(cid)
    state = play._load(before)
    with pytest.raises(ValidationError, match="exact hex path"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="mapless-suppression",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="all_out_attack",
                attack_option="suppression",
                item_id="sword-a",
                mode_id="suppress",
                suppression_zones=(SuppressionZone(center=Hex(q=3, r=0), shots=5),),
            ),
            authenticated_actor_id="a",
        )
    assert await play.store.read(cid) == before
