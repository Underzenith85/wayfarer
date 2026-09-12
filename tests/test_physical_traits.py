"""Literal expected results: Characters 4e third printing B35,39,43,55,59,71,79.

Campaigns B393/B420/B424/B426 supplies timing. Full profile certification is
separate from these runtime examples.
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_gurps_recovery import PROFILE, seed
from test_injury import wound
from test_mundane_trait_runtime import prepare
from test_mundane_traits import runtime_compiler
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.traits.physical import physical_traits
from wayfarer.engine.rules.catalog import ImplementationStatus
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.physical import PhysicalTraits, SurpriseState
from wayfarer.engine.simulation.health.fatigue import FatigueCost, apply_fatigue
from wayfarer.engine.simulation.health.injury import InjuryTurn, apply_injury
from wayfarer.engine.simulation.health.medical.commands import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
)
from wayfarer.engine.simulation.health.medical.recovery import apply_recovery
from wayfarer.engine.simulation.health.medical.rest import accrue_rest
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.physical_checks import (
    PhysicalCheck,
    PhysicalCheckCommand,
    PhysicalCheckService,
)


def physical_state(traits: PhysicalTraits, *, hp: int = 10, fp: int = 10) -> ResourceState:
    state = seed(fp=fp, hp=hp)
    health = state.pools[0]
    assert health.injury is not None
    return state.model_copy(
        update={
            "pools": (
                health.model_copy(
                    update={"injury": health.injury.model_copy(update={"physical_traits": traits})}
                ),
                state.pools[1],
            )
        }
    )


@pytest.mark.parametrize(
    ("identifier", "ht", "legal"),
    [
        ("rapid-healing", 9, False),
        ("rapid-healing", 10, True),
        ("very-rapid-healing", 11, False),
        ("very-rapid-healing", 12, True),
    ],
)
def test_healing_attribute_prerequisites(identifier: str, ht: int, legal: bool) -> None:
    draft = gurps_draft(Purchase(definition_id=f"trait:{identifier}"))
    draft = draft.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": ht}) if p.definition_id == "attribute:ht" else p
                for p in draft.purchases
            )
        }
    )
    compiled = runtime_compiler().compile(draft)
    assert compiled.legal is legal
    if not legal:
        assert "trait.prerequisite_ht" in {d.code for d in compiled.diagnostics}


def test_projection_requires_pinned_implemented_definition() -> None:
    compiler = runtime_compiler()
    build = compiler.compile(
        gurps_draft(Purchase(definition_id="trait:night-vision", amount=4))
    ).build
    assert build is not None
    assert physical_traits(build, compiler.definitions).night_vision == 4
    definition = compiler.definitions["trait:night-vision"]
    definitions = dict(compiler.definitions)
    definitions[definition.id] = replace(definition, status=ImplementationStatus.UNSUPPORTED)
    assert physical_traits(build, definitions) == PhysicalTraits()


@pytest.mark.parametrize(("penalty", "expected"), [(0, 0), (-3, 0), (-4, 0), (-7, -3), (-10, -10)])
def test_night_vision_four_never_grants_dark_vision(penalty: int, expected: int) -> None:
    assert PhysicalTraits(night_vision=4).darkness(penalty) == expected


def test_high_pain_threshold_and_fit_apply_to_injury_not_hp() -> None:
    state = physical_state(PhysicalTraits(high_pain_threshold=True, fitness=1))
    state, result = apply_injury(state, wound(6), ht=10, rng=RecordedDice([4, 4, 4]), system=True)
    assert state.pools[0].current == 4
    assert state.pools[0].injury is not None
    assert state.pools[0].injury.shock == 0
    assert not state.pools[0].injury.stunned
    assert result.checks[0].check.effective_target == 14  # HT10 + Fit1 + HPT3.
    replay = ResourceState.model_validate_json(state.model_dump_json())
    assert apply_injury(replay, wound(6), ht=10, rng=RecordedDice([]), system=True) == (
        state,
        result,
    )


def test_fitness_is_a_roll_bonus_and_does_not_raise_death_thresholds() -> None:
    state, result = apply_injury(
        physical_state(PhysicalTraits(fitness=2)),
        wound(20),
        ht=10,
        rng=RecordedDice([4, 4, 4, 4, 4, 4]),
        system=True,
    )
    assert state.pools[0].maximum == 10 and state.pools[0].current == -10
    assert [c.check.effective_target for c in result.checks] == [12, 12]


def test_very_fit_cost_rate_survives_split_commands_and_power_is_full_price() -> None:
    state = physical_state(PhysicalTraits(fitness=2))
    for index in range(4):
        state, result = apply_fatigue(
            state,
            FatigueCost(id=str(index), actor_id="a", expected_revision=state.revision, amount=1),
            ht=10,
            rng=RecordedDice([]),
            system=True,
        )
        assert result.fp_lost == (1 if index % 2 == 0 else 0)
        state = ResourceState.model_validate_json(state.model_dump_json())
    assert state.pools[1].current == 8
    state, result = apply_fatigue(
        state,
        FatigueCost(id="magic", actor_id="a", expected_revision=4, amount=3, power=True),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.fp_lost == 3 and state.pools[1].current == 5
    assert state.pools[1].fatigue is not None and state.pools[1].fatigue.power == 3


def test_fit_recovers_exertion_at_five_minutes_but_power_at_ten() -> None:
    state = physical_state(PhysicalTraits(fitness=1), fp=9)
    state, _ = apply_fatigue(
        state,
        FatigueCost(id="magic", actor_id="a", expected_revision=0, amount=1, power=True),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    state, _ = apply_recovery(
        state,
        BeginRecovery(
            id="rest", actor_id="a", target_id="a", kind="rest", seconds=900, expected_revision=1
        ),
        CareContext(PROFILE, 10),
        rng=RecordedDice([]),
        system=True,
    )
    assert state.recovery_tasks[0].fp_interval == 300
    state = accrue_rest(state, 300)
    assert state.pools[1].current == 9
    assert state.pools[1].fatigue is not None and state.pools[1].fatigue.power == 1
    state = ResourceState.model_validate_json(state.model_dump_json())
    state = accrue_rest(state, 600)
    assert state.pools[1].current == 9
    state = accrue_rest(state, 900)
    assert state.pools[1].current == 10
    assert state.pools[1].fatigue is not None and state.pools[1].fatigue.power == 0
    assert accrue_rest(state, 900) == state


def test_very_rapid_healing_snapshots_bonus_and_double_hp() -> None:
    state = physical_state(PhysicalTraits(healing=2, fitness=1), hp=5)
    state, _ = apply_recovery(
        state,
        BeginRecovery(id="day", actor_id="a", target_id="a", kind="natural", expected_revision=0),
        CareContext(PROFILE, 12, food=True),
        rng=RecordedDice([]),
        system=True,
    )
    state = state.model_copy(update={"game_time": 86400})
    state = ResourceState.model_validate_json(state.model_dump_json())
    # Completion uses the pinned task, not a newly supplied HT value.
    state, result = apply_recovery(
        state,
        FinishRecovery(id="finish", actor_id="a", task_id="day", expected_revision=1),
        CareContext(PROFILE, 8),
        rng=RecordedDice([5, 5, 5]),
        system=True,
    )
    assert result.check is not None and result.check.effective_target == 18
    assert result.hp_recovered == 2 and state.pools[0].current == 7


def test_combat_reflexes_recovers_surprise_with_iq_six_at_start_of_turn() -> None:
    state = physical_state(PhysicalTraits(combat_reflexes=True))
    hp = state.pools[0]
    assert hp.injury is not None
    hp = hp.model_copy(
        update={
            "injury": hp.injury.model_copy(
                update={"stunned": True, "surprise": SurpriseState(partial=True)}
            )
        }
    )
    state = state.model_copy(update={"pools": (hp, state.pools[1])})
    state, result = apply_injury(
        state,
        InjuryTurn(
            id="turn", actor_id="a", expected_revision=0, turn=1, phase="start", do_nothing=True
        ),
        ht=10,
        stun_iq=10,
        rng=RecordedDice([5, 5, 5]),
        system=True,
    )
    assert result.checks[0].check.effective_target == 16
    assert state.pools[0].injury is not None and not state.pools[0].injury.stunned
    assert state.pools[0].injury.surprise is None


async def test_approved_sense_check_is_private_authorized_and_retry_safe(tmp_path: Path) -> None:
    cid, play = await prepare(
        tmp_path,
        Purchase(definition_id="trait:acute-vision", amount=3),
        Purchase(definition_id="trait:night-vision", amount=4),
    )
    service = PhysicalCheckService(play, lambda *_: PhysicalCheck("sense", darkness=-7))
    command = PhysicalCheckCommand(
        id="look", actor_id="a", trigger_id="footprints", expected_revision=0
    )
    with pytest.raises(ValidationError, match="authority"):
        await service.execute(cid, command, gm_id="alice")
    assert not await service.execute(
        cid, command, gm_id="gm"
    )  # Per10 + Acute3 - residual darkness3 = 10; dice15.
    stored = play._load(await play.store.read(cid))
    trace = json.loads(stored.resources.events[-1].kind)
    assert trace["effective_target"] == 10
    assert not await service.execute(cid, command, gm_id="gm")
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


async def test_combat_reflexes_boosts_every_active_defense(tmp_path: Path) -> None:
    from test_gurps_melee import setup

    from wayfarer.engine.simulation.combat.melee.defense import defense_value
    from wayfarer.engine.simulation.combat.unarmed.defense import unarmed_defense

    plain_id, plain = await setup(tmp_path / "plain", PROFILE, unarmed_fixture=True)
    trait_id, enhanced = await setup(
        tmp_path / "traits",
        PROFILE,
        unarmed_fixture=True,
        physical_purchases=(Purchase(definition_id="trait:combat-reflexes"),),
    )
    baseline = plain._load(await plain.store.read(plain_id))
    updated = enhanced._load(await enhanced.store.read(trait_id))
    base_actor = next(p for p in baseline.encounters[0].participants if p.actor_id == "b")
    actor = next(p for p in updated.encounters[0].participants if p.actor_id == "b")
    for defense in ("dodge", "parry", "block"):
        a, _ = defense_value(plain.rules_context, baseline, base_actor, defense)
        b, _ = defense_value(enhanced.rules_context, updated, actor, defense)
        assert a is not None and b is not None and b.value == a.value + 1
    # Drop the sword to free the hand for a separately resolved unarmed parry.
    baseline = baseline.model_copy(
        update={
            "resources": baseline.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"ready": False, "equipped": False})
                        if i.id == "sword-b"
                        else i
                        for i in baseline.resources.items
                    )
                }
            )
        }
    )
    updated = updated.model_copy(
        update={
            "resources": updated.resources.model_copy(
                update={
                    "items": tuple(
                        i.model_copy(update={"ready": False, "equipped": False})
                        if i.id == "sword-b"
                        else i
                        for i in updated.resources.items
                    )
                }
            )
        }
    )
    base_parry, _ = unarmed_defense(
        plain.rules_context, baseline, baseline.encounters[0], "b", "parry", "right-hand"
    )
    boosted_parry, _ = unarmed_defense(
        enhanced.rules_context, updated, updated.encounters[0], "b", "parry", "right-hand"
    )
    assert base_parry is not None and boosted_parry == base_parry + 1


async def test_surprise_uses_leader_bonus_and_never_freezes_reflexes(tmp_path: Path) -> None:
    from test_gurps_melee import setup

    from wayfarer.orchestration.surprise import SurpriseCommand, SurpriseService, SurpriseSides

    cid, play = await setup(
        tmp_path, PROFILE, physical_purchases=(Purchase(definition_id="trait:combat-reflexes"),)
    )
    play.rng = RecordedDice([6, 1])  # First 6, second 1 + leader's CR2; defender loses.
    command = SurpriseCommand(
        id="ambush", actor_id="gm", expected_revision=1, encounter_id="fight", trigger_id="ambush"
    )
    service = SurpriseService(play, lambda *_: SurpriseSides(("a",), ("b",), "a", "b", total=True))
    await service.execute(cid, command, gm_id="gm")
    state = play._load(await play.store.read(cid))
    hp = next(p for p in state.resources.pools if p.id == "hp:b")
    assert hp.injury is not None and hp.injury.surprise == SurpriseState(partial=True)
    assert json.loads(state.resources.events[-1].kind) == {"initiative": [6, 3], "freeze": 0}
    await service.execute(cid, command, gm_id="gm")
    assert play.rng.exhausted()


@pytest.mark.parametrize(
    ("sense", "expected"), [("hearing", 2), ("taste-smell", 3), ("touch", 4), ("vision", 1)]
)
def test_acute_senses_do_not_cross_apply(sense: str, expected: int) -> None:
    from typing import cast

    from wayfarer.engine.rules.traits.physical import Sense

    traits = PhysicalTraits(
        acute_hearing=2, acute_taste_smell=3, acute_touch=4, acute_vision=5, night_vision=3
    )
    assert traits.sense_bonus(cast(Sense, sense), -7) == expected


def test_rapid_healing_improves_crippling_duration_but_not_stun_recovery() -> None:
    from test_hit_locations import human
    from test_hit_locations import wound as limb_wound

    from wayfarer.engine.simulation.health.injury import ResolveCrippling

    state = human()
    hp = state.pools[0]
    assert hp.injury is not None
    state = state.model_copy(
        update={
            "pools": (
                hp.model_copy(
                    update={
                        "injury": hp.injury.model_copy(
                            update={"physical_traits": PhysicalTraits(healing=1)}
                        )
                    }
                ),
            )
        }
    )
    state, hit = apply_injury(
        state, limb_wound("right-arm", 5), ht=10, rng=RecordedDice([3, 3, 3]), system=True
    )
    state, result = apply_injury(
        state,
        ResolveCrippling(
            id="duration", actor_id="a", expected_revision=1, injury_id=hit.lasting_injury_ids[0]
        ),
        ht=10,
        rng=RecordedDice([5, 5, 5]),
        system=True,
    )
    assert result.checks[0].check.effective_target == 15
    assert state.pools[0].injury is not None
    assert state.pools[0].injury.lasting_injuries[0].duration == "temporary"
    hp = state.pools[0]
    assert hp.injury is not None
    hp = hp.model_copy(
        update={
            "injury": hp.injury.model_copy(update={"stunned": True, "phase": "acting", "turn": 1})
        }
    )
    state = state.model_copy(update={"pools": (hp,)})
    _, result = apply_injury(
        state,
        InjuryTurn(
            id="recover", actor_id="a", expected_revision=2, turn=1, phase="end", do_nothing=True
        ),
        ht=10,
        rng=RecordedDice([4, 4, 4]),
        system=True,
    )
    assert result.checks[0].check.effective_target == 10


async def test_checkpoint_rejects_forged_physical_projection(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid))
    pools = tuple(
        p.model_copy(
            update={
                "injury": p.injury.model_copy(
                    update={"physical_traits": PhysicalTraits(combat_reflexes=True)}
                )
            }
        )
        if p.injury
        else p
        for p in state.resources.pools
    )
    with pytest.raises(ValidationError, match="projection"):
        play.engine.validate(
            state.model_copy(
                update={"resources": state.resources.model_copy(update={"pools": pools})}
            )
        )


async def test_night_vision_applies_in_melee_resolution(tmp_path: Path) -> None:
    from test_gurps_melee import setup

    from wayfarer.orchestration.combat import ChooseDefense, CombatService, TakeCombatTurn

    cid, play = await setup(
        tmp_path,
        PROFILE,
        physical_purchases=(Purchase(definition_id="trait:night-vision", amount=4),),
        darkness_penalty=-7,
    )
    service = CombatService(play)
    await service.execute(
        cid,
        TakeCombatTurn(
            id="wait-a",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id="a",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack-b",
            actor_id="b",
            expected_revision=2,
            encounter_id="fight",
            maneuver="attack",
            target_id="a",
            item_id="sword-b",
            mode_id="swing",
        ),
        authenticated_actor_id="b",
    )
    play.rng = RecordedDice([4, 4, 4])
    await service.execute(
        cid,
        ChooseDefense(
            id="defense-a", actor_id="a", expected_revision=3, encounter_id="fight", defense="none"
        ),
        authenticated_actor_id="a",
    )
    state = play._load(await play.store.read(cid))
    check = state.encounters[0].wounds[-1].attack
    # DX10/Average skill with 12 points = 13; -7 darkness + NV4 gives 10.
    assert check.effective_target == 10
    assert not check.outcome.succeeded


@pytest.mark.parametrize(
    ("trait", "kind", "target"),
    [
        ("ambidexterity", "off-hand", 10),
        ("combat-reflexes", "wake", 16),
        ("high-pain-threshold", "torture", 13),
        ("very-fit", "ht", 12),
    ],
)
async def test_approved_physical_checks_apply_selected_bonus(
    tmp_path: Path, trait: str, kind: str, target: int
) -> None:
    cid, play = await prepare(tmp_path, Purchase(definition_id="trait:" + trait))
    specs = {
        "off-hand": PhysicalCheck("off-hand"),
        "wake": PhysicalCheck("wake"),
        "torture": PhysicalCheck("torture"),
        "ht": PhysicalCheck("ht"),
    }
    service = PhysicalCheckService(play, lambda *_: specs[kind])
    command = PhysicalCheckCommand(id="check", actor_id="a", trigger_id=kind, expected_revision=0)
    await service.execute(cid, command, gm_id="gm")
    stored = play._load(await play.store.read(cid))
    assert json.loads(stored.resources.events[-1].kind)["effective_target"] == target
