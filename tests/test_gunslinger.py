"""Independent B58 third-printing cases through approved builds and real attacks.

Full Acc: one-handed weapon with RoF 1-3. Two-handed or higher RoF: half,
rounded up. Ordinary Aim replaces this benefit; muscle-powered missiles and
unrelated skills never receive it. No later supplement movement/Bulk relief.
"""

from dataclasses import replace
from pathlib import Path

import pytest
from test_gurps_maneuvers import defend, turn
from test_gurps_melee import setup
from test_gurps_ranged import load, scene, weapon
from test_liquid_projector_streams import projector

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.skills.mundane.ranged import definitions
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.simulation.equipment.catalog import RangedMode
from wayfarer.orchestration.play import PlayService

GUNSLINGER = "trait:advantage:gunslinger"
TRAIT = replace(
    next(d for d in candidate_package().definitions if d.id == GUNSLINGER),
    source_id=definitions()[0].source_id,
)


async def prepared(
    tmp_path: Path, mode: RangedMode, *, purchased: bool = True
) -> tuple[str, PlayService]:
    purchases: tuple[Purchase, ...] = (Purchase(definition_id=mode.skill_id, amount=4),)
    if purchased:
        purchases += (Purchase(definition_id=GUNSLINGER, amount=1),)
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        extra_definitions=definitions() + (TRAIT,),
        trait_runtime_hooks=frozenset({"mundane-trait:defense"}),
        extra_purchases=purchases,
        campaign_technology_level=2,
    )
    await load(cid, play)
    return cid, play


@pytest.mark.parametrize(
    ("skill", "hands", "rof", "shots", "purchased", "expected"),
    [
        ("skill:guns-pistol", 1, 1, 1, True, 17),  # 12 + full Acc 5
        ("skill:guns-pistol", 1, 3, 3, True, 17),
        ("skill:guns-pistol", 1, 4, 1, True, 15),  # profile RoF, not declared rounds
        ("skill:guns-pistol", 1, 6, 6, True, 16),  # 12 + ceil(5/2) + RoF bonus 1
        ("skill:guns-rifle", 2, 1, 1, True, 15),
        ("skill:beam-weapons-pistol", 1, 3, 1, True, 17),
        ("skill:beam-weapons-rifle", 2, 6, 1, True, 15),
        ("skill:guns-pistol", 1, 3, 1, False, 12),
        ("skill:bow", 2, 1, 1, True, 11),
        ("skill:crossbow", 1, 1, 1, True, 12),
    ],
)
async def test_b58_actual_attack_targets(
    tmp_path: Path, skill: str, hands: int, rof: int, shots: int, purchased: bool, expected: int
) -> None:
    mode = weapon().model_copy(
        update={
            "skill_id": skill,
            "accuracy": 5,
            "hands": hands,
            "rate_of_fire": rof,
            "recoil": 1 if skill in {"skill:bow", "skill:crossbow"} else 2,
        }
    )
    cid, play = await prepared(tmp_path, mode, purchased=purchased)
    await turn(
        cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged", shots=shots
    )
    play.rng = RecordedDice([3, 3, 4] + [1] * 10)
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == expected
    assert result.injury.hp_after < 10
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize(("hands", "rof"), [(1, 3), (2, 1), (2, 6)])
async def test_aim_replaces_gunslinger_and_keeps_additional_seconds(
    tmp_path: Path, hands: int, rof: int
) -> None:
    mode = weapon().model_copy(
        update={"skill_id": "skill:guns-pistol", "accuracy": 5, "hands": hands, "rate_of_fire": rof}
    )
    cid, play = await prepared(tmp_path, mode)
    for _ in range(3):
        await turn(cid, play, "a", "aim", item_id="sword-a", target_id="b", mode_id="ranged")
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 4, 1])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 19  # skill 12 + Acc 5 + two extra seconds


async def test_liquid_projector_uses_legal_stream_and_half_accuracy(tmp_path: Path) -> None:
    mode = projector("skill:liquid-projector-sprayer", accuracy=5)
    cid, play = await setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        ranged_mode=mode,
        ranged_scene=scene(),
        extra_definitions=definitions() + (TRAIT,),
        trait_runtime_hooks=frozenset({"mundane-trait:defense"}),
        extra_purchases=(
            Purchase(definition_id=mode.skill_id, amount=4),
            Purchase(definition_id=GUNSLINGER, amount=1),
        ),
        campaign_technology_level=2,
    )
    for _ in range(3):
        await turn(
            cid,
            play,
            "a",
            "ready",
            item_id="sword-a",
            mode_id="stream",
            reload_ammunition_id="ammo-a",
        )
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="stream")
    play.rng = RecordedDice([3, 3, 4, 1])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 15
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[0].stream is not None
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("aimed", [False, True])
async def test_scope_and_bracing_require_actual_aim(tmp_path: Path, aimed: bool) -> None:
    mode = weapon().model_copy(
        update={
            "skill_id": "skill:guns-rifle",
            "accuracy": 4,
            "hands": 2,
            "rate_of_fire": 1,
            "brace_kind": "bipod",
            "scope_bonus": 2,
        }
    )
    cid, play = await prepared(tmp_path, mode)
    if aimed:
        await turn(cid, play, "a", "change_posture", posture="prone")
        await turn(cid, play, "b", "do_nothing")
        await turn(
            cid,
            play,
            "a",
            "aim",
            item_id="sword-a",
            target_id="b",
            mode_id="ranged",
            braced=True,
        )
        await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="ranged")
    play.rng = RecordedDice([3, 3, 4, 1])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    # Unaided: 12 + half Acc 2. Aimed: 12 + Acc 4 + brace 1 + scope 1.
    assert result.injury.attack.effective_target == (18 if aimed else 14)


async def test_move_and_attack_keeps_ordinary_bulk_penalty(tmp_path: Path) -> None:
    mode = weapon().model_copy(
        update={"skill_id": "skill:guns-pistol", "accuracy": 5, "rate_of_fire": 3}
    )
    cid, play = await prepared(tmp_path, mode)
    await turn(
        cid, play, "a", "move_and_attack", item_id="sword-a", target_id="b", mode_id="ranged"
    )
    play.rng = RecordedDice([3, 3, 4, 1])
    result = await defend(cid, play, "b")
    assert result.injury is not None
    assert result.injury.attack.effective_target == 13  # 12 + Acc 5 - Bulk 4


@pytest.mark.parametrize(
    ("skill", "expected"),
    [
        ("skill:gunner-machine-gun", 3),
        ("skill:gunner-beams", 3),
        ("skill:gunner-catapult", 0),
        ("skill:artillery-cannon", 0),
    ],
)
def test_approved_projection_for_mounted_skill_classes(skill: str, expected: int) -> None:
    from test_mounted_ranged_skills import mounted
    from test_mundane_traits import runtime_compiler
    from test_statistics import gurps_draft

    from wayfarer.engine.simulation.combat.ranged.gunslinger import accuracy_bonus

    compiler = runtime_compiler()
    result = compiler.compile(gurps_draft(Purchase(definition_id=GUNSLINGER, amount=1)))
    assert result.build is not None, result.diagnostics
    # B58: a legal two-handed Gunner mode receives ceil(Acc 5 / 2).
    # Muscle-powered catapults and the unlisted Artillery skill remain excluded.
    mode = mounted(skill, accuracy=5)
    assert accuracy_bonus(result.build, compiler.definitions, mode) == expected
