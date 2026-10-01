"""Independent Campaigns fourth-printing B378-381/B419-423 injury matrix (#836)."""

from decimal import Decimal

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.health.injury import Wound, apply_injury, impaired_movement
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def resources(maximum: int = 11, current: int | None = None) -> ResourceState:
    return ResourceState(
        pools=(
            Pool(
                id="hp:a",
                maximum=maximum,
                current=maximum if current is None else current,
                injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
            ),
        )
    )


def hit(damage: int, kind: DamageType = "cr", dr: int = 0, divisor: str = "1") -> Wound:
    return Wound(
        id="source-hit",
        actor_id="a",
        expected_revision=0,
        basic_damage=damage,
        damage_type=kind,
        resistance=dr,
        armor_divisor=Decimal(divisor),
    )


@pytest.mark.parametrize(
    ("kind", "injury"),
    [
        ("cr", 3),
        ("burn", 3),
        ("cor", 3),
        ("tox", 3),
        ("cut", 4),
        ("imp", 6),
        ("pi-", 1),
        ("pi", 3),
        ("pi+", 4),
        ("pi++", 6),
    ],
)
def test_torso_penetration_and_every_damage_multiplier(kind: DamageType, injury: int) -> None:
    # B379: 5 damage - DR2 = 3 penetrating; multiply then drop fractions,
    # minimum injury 1 for a penetrating hit. Large pool avoids HT checks.
    updated, result = apply_injury(
        resources(30), hit(5, kind, 2), ht=12, rng=RecordedDice([]), system=True
    )
    assert (result.penetration, result.injury, updated.pools[0].current) == (3, injury, 30 - injury)
    assert result.checks == ()


@pytest.mark.parametrize(
    ("dr", "divisor", "effective", "penetration"),
    [(5, "2", 2, 5), (5, "3", 1, 6), (5, "0.5", 10, 0), (0, "0.5", 1, 6)],
)
def test_armor_division_precedes_penetration(
    dr: int, divisor: str, effective: int, penetration: int
) -> None:
    # B378: armor division drops fractions; sub-one divisors give bare skin DR1.
    updated, result = apply_injury(
        resources(30), hit(7, dr=dr, divisor=divisor), ht=12, rng=RecordedDice([]), system=True
    )
    assert result.effective_resistance == effective
    assert result.penetration == result.injury == penetration
    assert updated.pools[0].current == 30 - penetration


@pytest.mark.parametrize(("damage", "checks"), [(5, 0), (6, 1)])
def test_major_wound_requires_more_than_half_maximum_hp(damage: int, checks: int) -> None:
    # B420: an odd HP11 maximum uses >5.5, not >=5 or >floor(HP/2).
    rng = RecordedDice([4, 4, 5] if checks else [])
    updated, result = apply_injury(resources(), hit(damage), ht=12, rng=rng, system=True)
    status = updated.pools[0].injury
    assert status is not None
    assert updated.pools[0].current == 11 - damage
    assert len(result.checks) == checks
    assert status.stunned is bool(checks) and status.prone is bool(checks)
    assert not status.unconscious
    assert rng.exhausted()


@pytest.mark.parametrize(
    ("maximum", "damage", "shock"),
    [
        (19, 1, 1),
        (20, 1, 0),
        (20, 3, 1),
        (29, 8, 4),
        (30, 8, 2),
        (39, 12, 4),
        (40, 3, 0),
        (40, 4, 1),
    ],
)
def test_high_hp_shock_divisor_and_fraction_boundary(maximum: int, damage: int, shock: int) -> None:
    # B419: high HP shock divisor uses complete tens of maximum HP.
    updated, _ = apply_injury(
        resources(maximum), hit(damage), ht=12, rng=RecordedDice([]), system=True
    )
    assert updated.pools[0].injury is not None and updated.pools[0].injury.shock == shock


@pytest.mark.parametrize(("current", "move"), [(4, 7), (3, 4), (0, 4), (-10, 4)])
def test_reeling_uses_strict_one_third_and_rounds_up(current: int, move: int) -> None:
    # B419: HP11 reeling below 11/3; Move7 halves to4, fractions round up.
    assert impaired_movement(resources(current=current).pools[0], 7) == move


def test_multiple_death_thresholds_and_resource_receipt_are_exact() -> None:
    # B419: HP11 -> -34 crosses -11/-22/-33. Then major wound requires HT.
    rng = RecordedDice([3, 3, 3] * 4)
    command = hit(45)
    initial = resources()
    updated, result = apply_injury(initial, command, ht=12, rng=rng, system=True)
    assert updated.pools[0].current == -34
    assert [check.threshold for check in result.checks if check.reason == "death"] == [
        -11,
        -22,
        -33,
    ]
    assert [check.check.effective_target for check in result.checks] == [12, 12, 12, 12]
    assert rng.exhausted()
    restored = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_injury(restored, command, ht=12, rng=RecordedDice([]), system=True) == (
        updated,
        result,
    )
    with pytest.raises(ConflictError):
        apply_injury(
            restored,
            command.model_copy(update={"id": "stale"}),
            ht=12,
            rng=RecordedDice([]),
            system=True,
        )
    with pytest.raises(ValidationError, match="server authority"):
        apply_injury(initial, command, ht=12, rng=RecordedDice([]))
    assert initial.pools[0].current == 11


@pytest.mark.parametrize(
    ("dice", "mortal", "dead"),
    [([4, 4, 5], True, False), ([4, 5, 5], True, False), ([5, 5, 5], False, True)],
)
def test_death_failure_margin_mortal_boundary(dice: list[int], mortal: bool, dead: bool) -> None:
    # B419/B423: death-check failure by1/2 is mortal; failure by3 kills.
    updated, _ = apply_injury(
        resources(current=-10), hit(1), ht=12, rng=RecordedDice(dice), system=True
    )
    status = updated.pools[0].injury
    assert status is not None
    assert status.mortal_wound is mortal and status.dead is dead
    assert status.mortal_wound_due == (1800 if mortal else None)


def test_minus_five_maximum_hp_is_automatic_death() -> None:
    # B419: HP11 -> -55 automatically dead, without further HT rolls.
    updated, result = apply_injury(resources(), hit(66), ht=12, rng=RecordedDice([]), system=True)
    assert updated.pools[0].current == -55
    assert updated.pools[0].injury is not None and updated.pools[0].injury.dead
    assert result.checks == ()


@pytest.mark.parametrize(("roll", "unconscious"), [([4, 4, 5], False), ([5, 6, 6], True)])
def test_knockdown_failure_and_failure_by_five(roll: list[int], unconscious: bool) -> None:
    # B420: HT12 failure by1 is stun/knockdown; failure by5 unconscious.
    updated, _ = apply_injury(resources(), hit(6), ht=12, rng=RecordedDice(roll), system=True)
    status = updated.pools[0].injury
    assert status is not None and status.prone
    assert status.unconscious is unconscious


@pytest.mark.parametrize(("tl", "months"), [(None, 5), (4, 5), (5, 4), (6, 3), (7, 2), (12, 2)])
def test_source_lasting_injury_duration_and_medical_tl(tl: int | None, months: int) -> None:
    # B422: failed end-of-combat HT roll => lasting; 1d months minus 1/2/3
    # at medical TL5/6/7+, independently fixing the d6 result at5.
    from test_hit_locations import human, wound

    from wayfarer.engine.simulation.health.injury import ResolveCrippling

    updated, result = apply_injury(
        human(), wound("left-foot", 3), ht=12, rng=RecordedDice([3, 3, 3]), system=True
    )
    settled, _ = apply_injury(
        updated,
        ResolveCrippling(
            id="duration",
            actor_id="a",
            expected_revision=1,
            injury_id=result.lasting_injury_ids[0],
            physician_tl=tl,
        ),
        ht=12,
        rng=RecordedDice([5, 5, 5, 5]),
        system=True,
    )
    status = settled.pools[0].injury
    assert status is not None
    lasting = status.lasting_injuries[0]
    assert lasting.duration == "lasting" and lasting.recovery_at == months * 30 * 86400
    assert lasting.active(now=months * 30 * 86400 - 1, full_hp=True)
    assert not lasting.active(now=months * 30 * 86400, full_hp=True)
