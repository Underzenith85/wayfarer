"""B349 target scope is a strict trusted fact for special creature attacks."""

import hashlib
from collections.abc import Callable
from pathlib import Path
from typing import Literal

import pytest
from pydantic import ValidationError as SchemaError
from test_creature_combat import natural
from test_creatures import catalog
from test_gurps_conformance import load_cases

from wayfarer.certification.executable_evidence import provenance
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_checks import ResistanceTrace
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.types.creature import CreatureVariation
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.creature_combat import (
    NaturalAttackOutcome,
    ResolveNaturalAttack,
    apply_creature_combat,
    replay_creature_combat,
)
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError

PROFILE: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"


def seed(*, sapient: bool = False) -> ResourceState:
    attacker = (
        catalog()
        .compile("basilisk", "Basilisk", "creature:basilisk", CreatureVariation(will=20))
        .creature
    )
    target = (
        catalog()
        .compile("target", "Target", "creature:large-guard-dog", CreatureVariation(iq=6))
        .creature
    )
    return ResourceState(
        creatures=(attacker, target) if sapient else (attacker,),
        pools=(
            Pool(id="hp:basilisk", current=2, maximum=2, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="hp:target", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
        ),
    )


def command(scope: bool | None = False) -> ResolveNaturalAttack:
    return natural(
        id="gaze",
        actor_id="basilisk",
        target_id="target",
        attack_id="death-gaze",
        maneuver="concentrate",
        motivation="predatory",
        target_will=10,
        target_living_or_sapient=scope,
        vision_contact=True,
    )


def test_creature_scope_source_case_and_historical_replay(
    record_property: Callable[[str, object], None],
) -> None:
    case = next(c for c in load_cases() if c["id"] == "basic-rule-of-16-creature-scope")
    assert case["input"] == {
        "creature": "basilisk",
        "attacker_will": 20,
        "distance_yards": 0,
        "target_will": 10,
        "target_living_or_sapient": False,
        "attacker_roll": [5, 6, 6],
        "resister_roll": [4, 4, 3],
    }
    assert case["expected"] == {
        "attacker_effective_target": 20,
        "rule_of_16": False,
        "affected": False,
        "defender_rolls": 1,
        "target_hp": 10,
    }
    initial = seed()
    roll = (5, 6, 6, 4, 4, 3)
    rng = RecordedDice(roll)
    updated, outcome = apply_creature_combat(initial, command(), rng=rng, system=True)
    assert isinstance(outcome, NaturalAttackOutcome) and isinstance(
        outcome.resistance, ResistanceTrace
    )
    assert outcome.attack and outcome.attack.effective_target == 20
    assert not outcome.resistance.rule_of_16 and not outcome.resistance.affected
    assert outcome.resistance.resister.dice == (4, 4, 3) and outcome.damage_dice == ()
    assert next(p.current for p in updated.pools if p.id == "hp:target") == 10 and rng.exhausted()
    assert replay_creature_combat(initial, (command(),), rng=RecordedDice(roll)) == updated
    restored = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_creature_combat(restored, command(), rng=RecordedDice(()), system=True) == (
        restored,
        outcome,
    )
    with pytest.raises(ConflictError, match="payload"):
        apply_creature_combat(restored, command(True), rng=RecordedDice(()), system=True)
    # The pre-correction command has no scope field. Its serialized digest and
    # result remain reproducible only through the explicit historical replay path.
    historical = command(None)
    assert "target_living_or_sapient" not in historical.model_dump()
    old = replay_creature_combat(initial, (historical,), rng=RecordedDice(roll))
    old_json = ResourceState.model_validate_json(old.model_dump_json())
    assert replay_creature_combat(initial, (historical,), rng=RecordedDice(roll)) == old_json
    assert (
        apply_creature_combat(old_json, historical, rng=RecordedDice(()), system=True)[0]
        == old_json
    )
    assert "target_living_or_sapient" not in historical.model_dump_json()
    # Captured from the frozen pre-correction checkout, not this implementation.
    assert hashlib.sha256(historical.model_dump_json().encode()).hexdigest() == (
        "87b96130eace4d766a0b48724f689726d8d6ea79aeb64774e67f3f9a7cdceb64"
    )
    old_seeded = replay_creature_combat(initial, (historical,), rng=SeededRandom(f"{2:064x}"))
    assert hashlib.sha256(old_seeded.model_dump_json().encode()).hexdigest() == (
        "f5f02dc277cd65910ad66f67f8dc887751d5c69c349ee91813cf51e789204be9"
    )
    fresh_seeded = replay_creature_combat(initial, (command(True),), rng=SeededRandom(f"{2:064x}"))
    assert (
        replay_creature_combat(initial, (command(True),), rng=SeededRandom(f"{2:064x}"))
        == fresh_seeded
    )
    for key, value in provenance(Path(__file__).resolve().parents[1], case).items():
        record_property(key, value)


@pytest.mark.parametrize("scope", [None, False])
def test_unknown_or_contradictory_creature_scope_refuses_before_entropy(scope: bool | None) -> None:
    with pytest.raises(ValidationError, match="scope"):
        apply_creature_combat(
            seed(sapient=scope is False), command(scope), rng=RecordedDice(()), system=True
        )


@pytest.mark.parametrize("scope", [True, False])
def test_creature_target_scope_produces_distinct_canonical_targets(scope: bool) -> None:
    updated, outcome = apply_creature_combat(
        seed(), command(scope), rng=RecordedDice((5, 6, 6, 4, 4, 3)), system=True
    )
    assert isinstance(outcome, NaturalAttackOutcome) and outcome.attack and outcome.resistance
    assert outcome.attack.effective_target == (16 if scope else 20)
    assert outcome.resistance.rule_of_16 is scope
    assert not outcome.hit and updated.revision == 1


@pytest.mark.parametrize("bad", [0, 1, "true", "false"])
def test_creature_scope_does_not_coerce_untrusted_scalars(bad: object) -> None:
    with pytest.raises(SchemaError):
        ResolveNaturalAttack.model_validate(
            command().model_dump() | {"target_living_or_sapient": bad}
        )
