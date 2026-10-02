"""B376 separates accepted counterdamage facts from current victim injury."""

from pathlib import Path

import pytest
from test_unarmed import action, setup, state_of
from test_unarmed_integrations import arm_defender

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.unarmed.damage_records import ArmedParryDamageInputs
from wayfarer.engine.simulation.combat.unarmed.injury import (
    finish_armed_parry_damage,
    prepare_armed_parry_damage,
)
from wayfarer.errors import ValidationError


async def test_counterdamage_roundtrip_freezes_check_and_uses_current_hp(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await arm_defender(cid, play)
    await action(cid, play, "a", "kick")
    state = await state_of(cid, play)
    encounter = state.encounters[0]
    pending = encounter.pending_unarmed
    assert pending is not None
    play.rng = RecordedDice((3, 3, 3))
    check, inputs = prepare_armed_parry_damage(
        play.rules_context, state, encounter, pending, "sword-b", "swing"
    )
    assert inputs is not None and inputs.actor_id == "b" and inputs.target_id == "a"
    assert inputs.check == check and check.effective_target == 13
    assert inputs.location == "right-leg" and inputs.dice_count == 1 and inputs.adds == 1
    assert play.rng.exhausted()
    assert next(p.current for p in state.resources.pools if p.id == "hp:a") == 10
    restored = ArmedParryDamageInputs.model_validate_json(inputs.model_dump_json())
    current = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": 8}) if p.id == "hp:a" else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )
    play.rng = RecordedDice(())
    after, _, dice = finish_armed_parry_damage(
        play.rules_context, current, encounter, restored, selected_damage=(1,)
    )
    assert dice == (1,) and play.rng.exhausted()
    assert next(p.current for p in after.resources.pools if p.id == "hp:a") == 5
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 10
    assert restored.check == check


@pytest.mark.parametrize("dice", [(), (0,), (7,), (True,), (1, 2)])
async def test_invalid_selected_counterdamage_rejects_before_dice(
    tmp_path: Path, dice: tuple[int, ...]
) -> None:
    cid, play = await setup(tmp_path)
    await arm_defender(cid, play)
    await action(cid, play, "a", "kick")
    state = await state_of(cid, play)
    encounter = state.encounters[0]
    pending = encounter.pending_unarmed
    assert pending is not None
    play.rng = RecordedDice((3, 3, 3))
    _, inputs = prepare_armed_parry_damage(
        play.rules_context, state, encounter, pending, "sword-b", "swing"
    )
    assert inputs is not None
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="captured expression"):
        finish_armed_parry_damage(
            play.rules_context, state, encounter, inputs, selected_damage=dice
        )
    assert play.rng.exhausted() and await state_of(cid, play) == state


async def test_counterdamage_rejects_replaced_delivery_before_randomness(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    await arm_defender(cid, play)
    await action(cid, play, "a", "kick")
    state = await state_of(cid, play)
    encounter = state.encounters[0]
    pending = encounter.pending_unarmed
    assert pending is not None
    play.rng = RecordedDice((3, 3, 3))
    _, inputs = prepare_armed_parry_damage(
        play.rules_context, state, encounter, pending, "sword-b", "swing"
    )
    assert inputs is not None
    changed = encounter.model_copy(
        update={"pending_unarmed": pending.model_copy(update={"id": "new"})}
    )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="delivery identity"):
        finish_armed_parry_damage(play.rules_context, state, changed, inputs)
    with pytest.raises(ValidationError, match="delivery identity"):
        prepare_armed_parry_damage(play.rules_context, state, changed, pending, "sword-b", "swing")
    assert play.rng.exhausted()
