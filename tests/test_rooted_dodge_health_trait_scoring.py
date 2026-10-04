"""Scorer diagnostics: current health first, then Rooted (composed source inference)."""

from pathlib import Path

import pytest
from support.rooted_feet import fixture, observe, revision

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.encounter import Combatant
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.magic.rooted_feet_state import CastRootedFeet
from wayfarer.errors import ValidationError
from wayfarer.orchestration.rooted_feet import RootedFeetService

FEATURE = frozenset({"rooted-dodge-health-trait-composition"})


@pytest.mark.parametrize("base_dodge,ht", [(8, 8), (10, 16)])
@pytest.mark.parametrize("reflexes", [False, True])
@pytest.mark.parametrize(
    "hp_low,fp_low", [(False, False), (True, False), (False, True), (True, True)]
)
async def test_current_positive_health_and_approved_reflexes_score_after_rooting(
    tmp_path: Path, base_dodge: int, ht: int, reflexes: bool, hp_low: bool, fp_low: bool
) -> None:
    cid, play, _ = await fixture(
        tmp_path,
        "sqlite",
        bidirectional_visibility=True,
        subject_st=12,
        subject_ht=ht,
        subject_dx=12,
        subject_hp=12,
        subject_fp=ht,
        subject_purchases=(Purchase(definition_id="trait:combat-reflexes"),) if reflexes else (),
    )
    await observe(play, cid, target="b")
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    await RootedFeetService(play).execute(
        cid,
        CastRootedFeet(
            id="root",
            actor_id="c",
            expected_revision=await revision(play, cid),
            cast_id="root",
            subject_id="subject",
        ),
        principal_id="cora",
    )
    state = play._load(await play.store.read(cid))
    # Explicit scorer diagnostic, not a fabricated injury/FP service receipt.
    pools = tuple(
        p.model_copy(update={"current": max(1, (p.maximum - 1) // 3)})
        if (p.id == "hp:b" and hp_low) or (p.id == "fp:b" and fp_low)
        else p
        for p in state.resources.pools
    )
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"pools": pools})}
    )
    participant = Combatant(actor_id="b", initiative=10, reach=1, movement_allowance=5)
    hp = next(p for p in pools if p.id == "hp:b")
    assert hp.injury is not None
    assert hp.injury.physical_traits.combat_reflexes is reflexes
    # Independent numeric oracle: B419/B426 upward halves precede B244 downward half.
    current_dodge = base_dodge
    if hp_low:
        current_dodge = (current_dodge + 1) // 2
    if fp_low:
        current_dodge = (current_dodge + 1) // 2
    expected = current_dodge // 2 + int(reflexes)
    with combat_generation(FEATURE):
        value, _ = standard_defense_value(play.rules_context, state, participant, "dodge")
    assert value is not None and value.value == expected
    if hp_low or fp_low or reflexes:
        with pytest.raises(ValidationError, match="Dodge composition"):
            standard_defense_value(play.rules_context, state, participant, "dodge")
    else:
        legacy, _ = standard_defense_value(play.rules_context, state, participant, "dodge")
        assert legacy == value

    # HP12 at4 is exactly one-third; FP uses the first integer at/above one-third.
    threshold = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": (p.maximum + 2) // 3})
                        if p.id in ("hp:b", "fp:b")
                        else p
                        for p in pools
                    )
                }
            )
        }
    )
    with combat_generation(FEATURE):
        boundary, _ = standard_defense_value(play.rules_context, threshold, participant, "dodge")
    assert boundary is not None and boundary.value == base_dodge // 2 + int(reflexes)

    # Positive health admission stays bounded; zero is not a newly supported state.
    for pool_id in ("hp:b", "fp:b"):
        zero = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "pools": tuple(
                            p.model_copy(update={"current": 0}) if p.id == pool_id else p
                            for p in pools
                        )
                    }
                )
            }
        )
        with combat_generation(FEATURE), pytest.raises(ValidationError):
            standard_defense_value(play.rules_context, zero, participant, "dodge")
