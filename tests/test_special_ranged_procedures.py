"""Independent Campaigns fourth-printing B407-B413 fixtures for #511."""

from decimal import Decimal
from pathlib import Path
from typing import Literal

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import weapon

from wayfarer.engine.rules.checks import CheckTrace, RecordedDice
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.types.object import GroundPosition, ObjectProfile
from wayfarer.engine.rules.types.special_ranged import GuidanceSpec, GuidanceState
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import Combatant, Encounter, RangedSituation
from wayfarer.engine.simulation.combat.ranged.special import (
    acquire_guidance,
    advance_guidance,
    impact_cover,
    living_cover_dr,
    structural_cover_dr,
)
from wayfarer.engine.simulation.equipment.catalog import Damage, RangedMode
from wayfarer.errors import ConflictError, ValidationError


def trace(total: tuple[int, int, int] = (3, 3, 3)) -> CheckTrace:
    return success_roll("gurps-basic-set-4e-2004", 12, rng=RecordedDice(list(total)))


def missile(
    kind: Literal["guided", "homing", "semi-active"] = "guided", *, maximum: int = 100
) -> RangedMode:
    return RangedMode.model_validate(
        {
            "id": "missile",
            "skill_id": "skill:artillery-guided-missile",
            "minimum_st": 10,
            "damage": Damage(basis="fixed", dice=6, damage_type="imp"),
            "accuracy": 4,
            "range_basis": "yards",
            "half_damage_range": 20,
            "maximum_range": maximum,
            "shots": 1,
            "reload_seconds": 1,
            "bulk": -6,
            "ammunition_id": "equipment:guided-round",
            "guidance": GuidanceSpec(
                kind=kind,
                seeker_sense=None if kind == "guided" else "sense:radar",
            ),
        }
    )


def encounter() -> Encounter:
    participants = (
        Combatant(
            actor_id="operator",
            initiative=12,
            position=GridPoint(x=0, y=0),
            reach=1,
            movement_allowance=5,
        ),
        Combatant(
            actor_id="target",
            initiative=10,
            position=GridPoint(x=60, y=0),
            reach=1,
            movement_allowance=5,
        ),
        Combatant(
            actor_id="observer",
            initiative=9,
            position=GridPoint(x=2, y=0),
            reach=1,
            movement_allowance=5,
        ),
    )
    return Encounter(
        id="range",
        legacy_battlefield_id="range-map",
        participants=participants,
        turn_order=tuple(p.actor_id for p in participants),
    )


def test_b408_cover_dr_and_living_overpenetration_thresholds() -> None:
    structural = ObjectProfile(
        construction="homogenous", hp=12, dr=8, ht=12, cover_kind="structural"
    )
    thin = structural.model_copy(update={"cover_kind": "thin"})
    assert structural_cover_dr(structural, Decimal(2)) == 6
    assert structural_cover_dr(thin, Decimal(2)) == 4
    # The example's worn DR 8 is counted on both sides, then HP 12 is added,
    # before the armor divisor applies: (16 + 12) / 2 = 14.
    assert living_cover_dr(hp=12, armor_dr=8, armor_divisor=Decimal(2)) == 14
    with pytest.raises(ValidationError, match="cover adapter"):
        structural_cover_dr(
            ObjectProfile(construction="homogenous", hp=12, dr=8, ht=12), Decimal(1)
        )


async def test_b408_cover_uses_one_object_damage_transaction_and_replays(
    tmp_path: Path,
) -> None:
    profile = ObjectProfile(construction="homogenous", hp=12, dr=8, ht=12, cover_kind="structural")
    mode = weapon(thrown=True).model_copy(
        update={"damage": Damage(basis="fixed", dice=1, damage_type="pi-")}
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=(RangedSituation(attacker_id="a", defender_id="b", distance_yards=5),),
        durability=profile,
    )
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        mode_id="ranged",
        target_id="b",
    )
    state = play._load(await play.store.read(cid))
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                item.model_copy(
                    update={
                        "equipped": False,
                        "ready": False,
                        "ground": GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0),
                    }
                )
                if item.id == "sword-b"
                else item
                for item in state.resources.items
            )
        }
    )
    state = state.model_copy(update={"resources": resources})
    damage = Damage(basis="fixed", dice=7, damage_type="pi-", armor_divisor=Decimal(2))
    resolved, encounter_after, record = impact_cover(
        play.rules_context,
        state,
        state.encounters[0],
        projectile_id="defense:test:hit:0",
        barrier_item_id="sword-b",
        target_id="b",
        basic_damage=20,
        damage=damage,
    )
    assert (record.cover_dr, record.residual_damage) == (6, 14)
    assert len(resolved.resources.object_results) == len(state.resources.object_results) + 1
    replayed, _, replay_record = impact_cover(
        play.rules_context,
        resolved,
        encounter_after,
        projectile_id="defense:test:hit:0",
        barrier_item_id="sword-b",
        target_id="b",
        basic_damage=20,
        damage=damage,
    )
    assert replayed == resolved and replay_record == record


async def test_live_projectile_crosses_cover_once_before_injury(tmp_path: Path) -> None:
    profile = ObjectProfile(construction="homogenous", hp=12, dr=8, ht=12, cover_kind="structural")
    mode = weapon(thrown=True).model_copy(
        update={
            "damage": Damage(
                basis="fixed",
                dice=1,
                adds=14,
                damage_type="pi-",
                armor_divisor=Decimal(2),
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=(RangedSituation(attacker_id="a", defender_id="c", distance_yards=2),),
        durability=profile,
        third_actor=True,
    )
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        mode_id="ranged",
        target_id="c",
        cover_item_id="sword-b",
    )
    play.rng = RecordedDice([3, 3, 3, 6])
    result = await defend(cid, play, "c")
    assert result.injury and result.injury.injury > 0
    state = play._load(await play.store.read(cid))
    cover_results = [
        result for result in state.resources.object_results if result.item_id == "sword-b"
    ]
    assert len(cover_results) == 1
    assert sum(event.id.startswith("cover:cover-impact:") for event in state.resources.events) == 1


async def test_live_living_overpenetration_injures_the_actor_behind_once(tmp_path: Path) -> None:
    mode = weapon(thrown=True).model_copy(
        update={
            "damage": Damage(
                basis="fixed",
                dice=1,
                adds=14,
                damage_type="pi-",
                armor_divisor=Decimal(2),
            )
        }
    )
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=(RangedSituation(attacker_id="a", defender_id="b", distance_yards=1),),
        third_actor=True,
    )
    await turn(
        cid,
        play,
        "a",
        "attack",
        item_id="sword-a",
        mode_id="ranged",
        target_id="b",
        overpenetration_target_id="c",
    )
    play.rng = RecordedDice([3, 3, 3, 6, 3, 3, 3])
    await defend(cid, play, "b")
    state = play._load(await play.store.read(cid))
    pools = {pool.id: pool for pool in state.resources.pools}
    assert pools["hp:b"].current < pools["hp:b"].maximum
    assert pools["hp:c"].current < pools["hp:c"].maximum
    receipts = [
        receipt
        for receipt in state.resources.receipts
        if ":overpenetration:c" in receipt.command_id
    ]
    assert len(receipts) == 1


def test_b412_guided_flight_arrival_and_interruption_round_trip() -> None:
    launched = acquire_guidance(
        encounter(),
        missile(),
        projectile_id="shot-1",
        weapon_id="launcher",
        operator_id="operator",
        target_id="target",
        distance_yards=Decimal(60),
        lock=trace(),
    )
    assert launched.guided_projectiles[0].status == "locked"
    first = advance_guidance(launched, "shot-1")
    assert (first.guided_projectiles[0].status, first.guided_projectiles[0].remaining_yards) == (
        "in-flight",
        Decimal(40),
    )
    lost = advance_guidance(first, "shot-1", operator_continues=False)
    assert lost.guided_projectiles[0].lost_reason == "operator-interrupted"
    restored = Encounter.model_validate_json(lost.model_dump_json())
    assert restored == lost
    with pytest.raises(ConflictError, match="settled"):
        advance_guidance(restored, "shot-1")

    second = acquire_guidance(
        encounter(),
        missile(),
        projectile_id="shot-2",
        weapon_id="launcher",
        operator_id="operator",
        target_id="target",
        distance_yards=Decimal(20),
        lock=trace(),
    )
    arrived = advance_guidance(second, "shot-2")
    assert arrived.guided_projectiles[0].status == "arrived"


def test_b412_homing_roles_and_semi_active_designation_are_explicit() -> None:
    with pytest.raises(ValueError, match="seeker sense"):
        GuidanceSpec(kind="homing")
    with pytest.raises(ValidationError, match="designator"):
        acquire_guidance(
            encounter(),
            missile("semi-active"),
            projectile_id="shot",
            weapon_id="launcher",
            operator_id="operator",
            target_id="target",
            distance_yards=Decimal(40),
            lock=trace(),
        )
    launched = acquire_guidance(
        encounter(),
        missile("semi-active"),
        projectile_id="shot",
        weapon_id="launcher",
        operator_id="operator",
        target_id="target",
        distance_yards=Decimal(40),
        lock=trace(),
        designator_id="observer",
    )
    lost = advance_guidance(launched, "shot", designation_continues=False)
    assert lost.guided_projectiles[0].lost_reason == "designation-lost"


def test_guidance_rejects_unadapted_and_out_of_range_weapons() -> None:
    ordinary = missile().model_copy(update={"guidance": None})
    with pytest.raises(ValidationError, match="no guidance adapter"):
        acquire_guidance(
            encounter(),
            ordinary,
            projectile_id="shot",
            weapon_id="launcher",
            operator_id="operator",
            target_id="target",
            distance_yards=Decimal(20),
            lock=trace(),
        )
    with pytest.raises(ValidationError, match="maximum range"):
        acquire_guidance(
            encounter(),
            missile(maximum=50),
            projectile_id="shot",
            weapon_id="launcher",
            operator_id="operator",
            target_id="target",
            distance_yards=Decimal(51),
            lock=trace(),
        )
    with pytest.raises(ValueError, match="special rapid-fire"):
        RangedMode.model_validate(missile().model_dump() | {"rate_of_fire": 2})


def test_lost_guidance_requires_a_recorded_reason() -> None:
    fields = dict(
        id="shot",
        weapon_id="launcher",
        mode_id="missile",
        operator_id="operator",
        target_id="target",
        kind="guided",
        speed_yards_per_second=Decimal(20),
        remaining_yards=Decimal(40),
        remaining_endurance_yards=Decimal(80),
        accuracy=4,
        lock=trace(),
    )
    with pytest.raises(ValueError, match="requires a reason"):
        GuidanceState.model_validate(fields | {"status": "lost"})
