"""B378-381 melee damage faces resume the actual armor and injury tail."""

from pathlib import Path

import pytest
from test_gurps_melee import attack, setup

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.combat.melee.damage_records import PreparedMeleeDamage
from wayfarer.engine.simulation.combat.melee.resolution import finish_melee_damage, resolve_melee
from wayfarer.errors import ValidationError


@pytest.mark.parametrize("secret", [False, True])
async def test_captured_melee_damage_preserves_attack_and_changes_real_cutting_injury(
    tmp_path: Path, secret: bool
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3) + (() if secret else (1,)))
    captured = resolve_melee(
        play.rules_context,
        state,
        state.encounters[0],
        "none",
        None,
        prepare_damage=True,
        secret_damage=secret,
    )
    preparation = PreparedMeleeDamage.model_validate_json(captured.preparation.model_dump_json())
    assert preparation.inputs.attack.dice == (3, 3, 3)
    assert preparation.original == (None if secret else (1,)) and play.rng.exhausted()
    assert next(p.current for p in captured.state.resources.pools if p.id == "hp:b") == 10
    play.rng = RecordedDice((2, 2, 2))
    updated, _, injury = finish_melee_damage(
        play.rules_context,
        captured.state,
        captured.encounter,
        preparation.inputs,
        selected_damage=(6,),
    )
    # ST10 sword swing is1d+1 cutting:7 basic, floor(7*1.5)=10 injury.
    assert injury.damage_dice == (6,) and injury.basic_damage == 7 and injury.injury == 10
    assert injury.attack == preparation.inputs.attack
    assert next(p.current for p in updated.resources.pools if p.id == "hp:b") == 0
    assert play.rng.exhausted()


async def test_captured_melee_expression_rejects_forged_faces_before_wound(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3, 1))
    captured = resolve_melee(
        play.rules_context, state, state.encounters[0], "none", None, prepare_damage=True
    )
    play.rng = RecordedDice(())
    for faces in ((7,), (1, 1), ()):
        with pytest.raises(ValidationError, match="captured expression"):
            finish_melee_damage(
                play.rules_context,
                captured.state,
                captured.encounter,
                captured.preparation.inputs,
                selected_damage=faces,
            )
    assert captured.state == state
