"""B378-B379 integer penetration under a caller's Decimal context.

Expectations use the already reviewed issue-728 positive/fractional matrices,
not a fresh review of the unavailable source PDFs. Kernel and authored-object
service regressions do not establish registered combat, HTTP, original-seed
reexecution or whole-capability certification.
"""

from decimal import ROUND_DOWN, ROUND_UP, Decimal, localcontext
from pathlib import Path

import pytest
from support.runtime import open_store
from test_hit_locations import human, wound
from test_objects import fixture as object_fixture
from test_objects import hit as object_hit
from test_resources import campaign

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.location import HumanLocation
from wayfarer.engine.rules.types.ranged_equipment import FollowUpSpec
from wayfarer.engine.simulation.combat.ranged.equipment import resolve_follow_up
from wayfarer.engine.simulation.combat.special_damage import (
    PenetrationContext,
    resolve_affliction_penetration,
)
from wayfarer.engine.simulation.equipment.catalog import DamageType
from wayfarer.engine.simulation.equipment.objects import apply_object
from wayfarer.engine.simulation.health.hit_locations import effective_dr
from wayfarer.engine.simulation.health.injury import apply_injury
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.resources import ResourceService


@pytest.mark.parametrize("precision", [1, 2, 28])
@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_UP])
@pytest.mark.parametrize(
    "resistance,divisor,location,damage_type,expected",
    [
        (15, "2", "torso", "cr", 7),
        (13, "2", "skull", "cr", 7),
        (15, "2", "skull", "tox", 7),
        (5, "2", "torso", "cr", 2),
        (5, "2", "skull", "cr", 3),
        (1, "10", "torso", "cr", 0),
        (0, "2", "torso", "cr", 0),
        (15, "0.5", "torso", "cr", 30),
        (0, "0.5", "torso", "cr", 1),
        (5, "0.2", "torso", "cr", 25),
        (15, "1", "torso", "cr", 15),
    ],
)
def test_reviewed_division_is_exact_before_penetration(
    precision: int,
    rounding: str,
    resistance: int,
    divisor: str,
    location: HumanLocation,
    damage_type: DamageType,
    expected: int,
) -> None:
    with localcontext() as context:
        context.prec, context.rounding, context.Emax = precision, rounding, 1
        assert (
            effective_dr(resistance, Decimal(divisor), location=location, damage_type=damage_type)
            == expected
        )
        assert (context.prec, context.rounding, context.Emax) == (precision, rounding, 1)
        assert not any(context.flags.values())


@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_UP])
@pytest.mark.parametrize(
    "resistance,divisor,damage,expected_dr",
    [(15, "2", 8, 7), (15, "0.5", 31, 30), (0, "0.5", 2, 1)],
)
def test_wound_applies_exact_dr_before_subtracting_damage(
    rounding: str, resistance: int, divisor: str, damage: int, expected_dr: int
) -> None:
    with localcontext() as context:
        context.prec, context.rounding = 1, rounding
        updated, result = apply_injury(
            human(),
            wound("torso", damage, "cr", resistance, Decimal(divisor)),
            ht=12,
            rng=RecordedDice([]),
            system=True,
        )
    assert (result.effective_resistance, result.injury) == (expected_dr, 1)
    assert updated.pools[0].current == 9


@pytest.mark.parametrize("divisor", ["0", "-1", "Infinity", "NaN"])
def test_invalid_divisors_still_reject(divisor: str) -> None:
    with pytest.raises(ValidationError, match="finite and positive"):
        effective_dr(15, Decimal(divisor), location="torso", damage_type="cr")


@pytest.mark.parametrize("precision", [1, 2, 28])
@pytest.mark.parametrize("rounding", [ROUND_DOWN, ROUND_UP])
@pytest.mark.parametrize("resistance,divisor,expected", [(15, "2", 7), (15, "0.5", 30)])
def test_object_and_resistance_consumers_share_exact_dr(
    precision: int, rounding: str, resistance: int, divisor: str, expected: int
) -> None:
    reducer, state = object_fixture()
    profile = reducer.specs["sword"].durability
    assert profile is not None
    reducer.specs["sword"] = reducer.specs["sword"].model_copy(
        update={"durability": profile.model_copy(update={"dr": resistance})}
    )
    with localcontext() as context:
        context.prec, context.rounding, context.Emax = precision, rounding, 1
        _, damaged = apply_object(
            reducer,
            state,
            object_hit(expected + 1, armor_divisor=Decimal(divisor)),
            system=True,
            rng=RecordedDice([]),
        )
        affliction = resolve_affliction_penetration(
            resistance, PenetrationContext(modifier="armor-divisor", armor_divisor=Decimal(divisor))
        )
        follow_up = resolve_follow_up(
            FollowUpSpec(
                payload_id="payload",
                kind="affliction",
                requires_penetration=False,
                armor_divisor=Decimal(divisor),
            ),
            penetrated_damage=0,
            target_ht=10,
            resistance_dr=resistance,
            resistance_dice=(3, 3, 3),
        )
        assert (context.prec, context.rounding, context.Emax) == (precision, rounding, 1)
    assert (damaged.effective_dr, damaged.injury) == (expected, 1)
    assert affliction.applies and affliction.dr_bonus == expected
    assert follow_up.resistance_target == 10 + expected


def test_fractional_zero_dr_preserves_each_delivery_policy() -> None:
    # These policies already differ; the arithmetic fix must not erase that
    # distinction without an independent source decision about linked delivery.
    assert effective_dr(0, Decimal("0.5"), location="torso", damage_type="cr") == 1
    assert (
        resolve_affliction_penetration(
            0, PenetrationContext(modifier="armor-divisor", armor_divisor=Decimal("0.5"))
        ).dr_bonus
        == 1
    )
    linked = resolve_follow_up(
        FollowUpSpec(
            payload_id="payload",
            kind="affliction",
            requires_penetration=False,
            armor_divisor=Decimal("0.5"),
        ),
        penetrated_damage=0,
        target_ht=10,
        resistance_dr=0,
        resistance_dice=(3, 3, 3),
    )
    assert linked.resistance_target == 10
    reducer, state = object_fixture()
    profile = reducer.specs["sword"].durability
    assert profile is not None
    reducer.specs["sword"] = reducer.specs["sword"].model_copy(
        update={"durability": profile.model_copy(update={"dr": 0})}
    )
    _, damaged = apply_object(
        reducer,
        state,
        object_hit(2, armor_divisor=Decimal("0.5")),
        system=True,
        rng=RecordedDice([]),
    )
    assert (damaged.effective_dr, damaged.injury) == (1, 1)


@pytest.mark.parametrize("divisor", ["0", "-1", "Infinity", "NaN"])
def test_affliction_invalid_divisor_still_rejects(divisor: str) -> None:
    # Bypass schema construction only to exercise the existing delivery guard.
    context = PenetrationContext.model_construct(
        modifier="armor-divisor", armor_divisor=Decimal(divisor)
    )
    with pytest.raises(ValidationError, match="finite nonnegative"):
        resolve_affliction_penetration(15, context)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authored_object_divisor_is_atomic_retryable_and_reloads(
    tmp_path: Path, backend: str
) -> None:
    # The resource-service shell is an authored object fixture, not a claim of
    # registered GURPS combat or HTTP coverage.
    store = open_store(tmp_path, backend=backend, filename="armor.sqlite")
    reducer, state = object_fixture()
    profile = reducer.specs["sword"].durability
    assert profile is not None
    reducer.specs["sword"] = reducer.specs["sword"].model_copy(
        update={"durability": profile.model_copy(update={"dr": 15})}
    )
    initial = campaign(reducer)
    cid = initial["id"]
    service = ResourceService(store, reducer)
    await service.create(initial, state)
    before = await store.read(cid)
    before_history, before_events = await store.history(cid), await store.stream(cid)
    command = object_hit(8, armor_divisor=Decimal(2))
    with pytest.raises(ValidationError, match="authority"):
        await service.execute_object(
            cid, command, principal_id="b", system=True, rng=RecordedDice([])
        )
    assert await store.read(cid) == before
    assert await store.history(cid) == before_history
    assert await store.stream(cid) == before_events
    with localcontext() as context:
        context.prec, context.rounding = 1, ROUND_UP
        result = await service.execute_object(
            cid, command, principal_id="a", system=True, rng=RecordedDice([])
        )
    damage = result.object_results[0]
    assert (damage.effective_dr, damage.injury, damage.condition.hp) == (7, 1, 9)
    after = await store.read(cid)
    after_history, after_events = await store.history(cid), await store.stream(cid)
    reopened = open_store(tmp_path, backend=backend, filename="armor.sqlite")
    restarted = ResourceService(reopened, reducer)
    assert (
        await restarted.execute_object(
            cid, command, principal_id="a", system=True, rng=RecordedDice([])
        )
        == result
    )
    with pytest.raises(ConflictError):
        await restarted.execute_object(
            cid,
            command.model_copy(update={"armor_divisor": Decimal(3)}),
            principal_id="a",
            system=True,
            rng=RecordedDice([]),
        )
    assert await reopened.read(cid) == after
    assert await reopened.history(cid) == after_history
    assert await reopened.stream(cid) == after_events
    assert await reopened.replay(cid) == after
