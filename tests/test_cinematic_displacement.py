"""B201/B378 stance and balance alter actual placement and posture."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_gurps_melee import setup
from test_mastery_combat import DEFINITIONS, purchase
from test_statistics import profile_package

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.cinematic import package
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.simulation.combat.battlefield import Battlefield
from wayfarer.engine.simulation.combat.displacement import displace

STANCE = tuple(
    replace(d, source_id=profile_package("gurps-basic-set-4e-2004").sources[0].id)
    for d in package().definitions
    if d.id == "skill:immovable-stance"
)


@pytest.mark.parametrize(
    "dice,prevented,fell",
    [
        ([2, 2, 2], True, False),
        ([3, 3, 3, 3, 3, 3], False, True),
        ([6, 6, 6], False, True),
    ],
)
async def test_stance_has_actual_prevention_failure_and_critical_fall(
    tmp_path: Path, dice: list[int], prevented: bool, fell: bool
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        extra_definitions=DEFINITIONS + STANCE,
        battlefield=Battlefield(id="dock", location_id="dock", width=10, height=10),
        extra_purchases=(
            purchase("trained-by-a-master"),
            Purchase(definition_id="skill:immovable-stance", amount=4),
        ),
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    # The approved attacker knows Stance, so knock him away from the defender.
    actor = next(p for p in encounter.participants if p.actor_id == "a")
    opponent = next(p for p in encounter.participants if p.actor_id == "b")
    # Move the attacker one square left; three yards can then move him right on the board.
    from wayfarer.engine.simulation.combat.battlefield import GridPoint
    from wayfarer.engine.simulation.combat.engine import CombatEngine

    encounter = CombatEngine._replace(
        encounter, actor.model_copy(update={"position": GridPoint(x=2, y=2)})
    )
    encounter = CombatEngine._replace(
        encounter, opponent.model_copy(update={"position": GridPoint(x=1, y=2)})
    )
    play.rng = RecordedDice(dice)
    changed, moved, result = displace(
        play.rules_context,
        state,
        encounter,
        source_id="b",
        target_id="a",
        basic_damage=24,
        use_immovable=True,
    )
    target = next(p for p in moved.participants if p.actor_id == "a")
    assert result.potential_yards == 3 and result.prevented == prevented and result.fell == fell
    assert target.position == GridPoint(x=2 if prevented else 5, y=2)
    assert target.posture == ("prone" if fell else "standing")
    assert result.checks[0].effective_target == 7  # Stance10 minus potential3.
    if len(dice) == 6:
        assert result.checks[1].effective_target == 8  # DX10 minus two extra yards.
    assert next(p for p in changed.resources.pools if p.id == "hp:a").current == 10
    assert next(p for p in changed.resources.pools if p.id == "fp:a").current == 10


@pytest.mark.parametrize("penetrated,yards", [(False, 3), (True, 0)])
async def test_cutting_knockback_requires_no_penetration(
    tmp_path: Path, penetrated: bool, yards: int
) -> None:
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        battlefield=Battlefield(id="dock", location_id="dock", width=10, height=10),
    )
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    play.rng = RecordedDice([1, 1, 1] if yards else [])
    _, _, result = displace(
        play.rules_context,
        state,
        encounter,
        source_id="a",
        target_id="b",
        basic_damage=24,
        damage_type="cut",
        penetrated=penetrated,
    )
    assert result.potential_yards == yards
    assert len(result.checks) == int(bool(yards))
