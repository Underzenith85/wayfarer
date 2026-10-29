"""B348 actual system-provider attempts; there is no creature campaign service."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict

import pytest
from support.quick_contest_evidence import record_quick_contest
from test_creatures import catalog

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.gurps_checks import ResistanceTrace
from wayfarer.engine.rules.randomness import SeededRandom
from wayfarer.engine.rules.resistance_attempts import FailedResistanceAttempt
from wayfarer.engine.rules.types.creature import CreatureVariation, SwarmCell
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.creature_combat import (
    CreatureCombatEvent,
    DamageSwarm,
    NaturalAttackOutcome,
    ResolveNaturalAttack,
    ResolveSwarmTurn,
    SetSwarmArea,
    apply_creature_combat,
    apply_current_creature_combat,
    replay_creature_combat,
    replay_recorded_creature_combat,
)
from wayfarer.engine.simulation.resources import Pool, ResourceEvent, ResourceState
from wayfarer.errors import ConflictError, ValidationError


class AuditedDice(RecordedDice):
    def __init__(self, dice: tuple[int, ...] = ()) -> None:
        super().__init__(dice)
        self.calls = 0

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        self.calls += 1
        return super().randbelow(exclusive_upper_bound)


def fixture(*, will: int = 10, subject_will: int = 10, iq: int = 5) -> ResourceState:
    entries = catalog()
    basilisk = entries.compile(
        "basilisk", "Basilisk", "creature:basilisk", CreatureVariation(will=will)
    ).creature
    dog = entries.compile(
        "dog", "Dog", "creature:large-guard-dog", CreatureVariation(will=subject_will, iq=iq)
    ).creature
    assert basilisk.statistics.will == will and dog.statistics.will == subject_will
    return ResourceState(
        creatures=(basilisk, dog),
        pools=(
            Pool(
                id="hp:basilisk",
                current=2,
                maximum=2,
                injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
            ),
            Pool(
                id="hp:dog",
                current=9,
                maximum=9,
                injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
            ),
        ),
    )


def command(
    *, separation: int = 0, subject_will: int = 10, scope: bool | None = True
) -> ResolveNaturalAttack:
    return ResolveNaturalAttack(
        id="gaze",
        actor_id="basilisk",
        expected_revision=0,
        target_id="dog",
        attack_id="death-gaze",
        maneuver="concentrate",
        motivation="predatory",
        attacker_position=SwarmCell(q=0, r=0),
        target_position=SwarmCell(q=separation, r=0),
        defense="none",
        target_ht=12,
        target_will=subject_will,
        target_living_or_sapient=scope,
        target_dr=99,
        vision_contact=True,
    )


def native(state: ResourceState) -> ResourceEvent:
    return next(e for e in state.events if e.id.startswith("creature-combat:"))


def record_native_creature(
    case_id: str,
    before: ResourceState,
    after: ResourceState,
    action: ResolveNaturalAttack,
    result: NaturalAttackOutcome,
    record_property: Callable[[str, object], None],
) -> None:
    """Join actual native declarations/receipts, independently of fixture expectations."""
    profile = catalog().compiler.statistics_profile
    assert profile == "gurps-basic-set-4e-2004"
    runtime_rules = catalog().compiler.rules
    assert runtime_rules.packages and all(pin.digest for pin in runtime_rules.packages)
    record_property("b348_runtime_profile", profile)
    record_property("b348_runtime_rules", json.dumps(asdict(runtime_rules), sort_keys=True))
    receipt = CreatureCombatEvent.model_validate_json(native(after).kind)
    prepared = receipt.resistance_preparation
    resistance = result.resistance
    assert prepared is not None and resistance is not None and result.attack is not None
    assert prepared.profile_id == profile and resistance.profile_id == profile
    assert result.attack == resistance.attacker
    assert receipt.resistance_attempt_generation == 1 and receipt.outcome == result
    actor = next(c for c in before.creatures if c.actor_id == action.actor_id)
    assert actor.build_revision and actor.point_provenance
    assert prepared.attacker.base_target == actor.statistics.will == result.attack.base_target
    assert len(prepared.attacker.modifiers) == 1
    modifier = prepared.attacker.modifiers[0]
    assert modifier.reason == "Malediction 1 distance"
    assert modifier.source_id == "advantage:innate-attack"
    assert result.attack.modifiers == prepared.attacker.modifiers
    target = next(c for c in before.creatures if c.actor_id == action.target_id)
    assert prepared.resister.base_target == action.target_will == target.statistics.will
    assert prepared.resister.modifiers == () and prepared.cap_modifiers == ()
    assert action.target_living_or_sapient is True and prepared.rule_of_16 is True
    assert resistance.rule_of_16 is True
    assert result.effect is not None and result.effect.profile_id == profile
    assert result.effect.modified.malediction_range == "yards"
    assert result.effect.modified.penetration_sense == "vision"
    assert after.game_time == before.game_time == 0
    assert len(after.pools) == len(before.pools) == 2
    assert next(p for p in after.pools if p.id == "hp:basilisk") == next(
        p for p in before.pools if p.id == "hp:basilisk"
    )
    subject = resistance.resister if isinstance(resistance, ResistanceTrace) else None
    if subject is not None:
        assert subject.base_target == prepared.resister.base_target and subject.modifiers == ()
    inputs: dict[str, object] = {
        "compiled_will": actor.statistics.will,
        "distance_yards": action.target_position.q - action.attacker_position.q,
        "native_trace_attacker_base": result.attack.base_target,
        "typed_distance_modifier": modifier.value,
        "declared_resister_base": prepared.resister.base_target,
        "attacker_dice": list(result.attack.dice),
        "resister_dice": list(subject.dice) if subject is not None else None,
        "target_DR": action.target_dr,
        "rule_of_16": resistance.rule_of_16,
    }
    observed: dict[str, object] = {
        "effective_attacker": result.attack.effective_target,
        "attacker_margin": result.attack.margin,
        "resister_margin": subject.margin if subject is not None else None,
        "victory_margin": resistance.contest.victory_margin
        if isinstance(resistance, ResistanceTrace)
        else None,
        "subject_attempted": subject is not None,
        "affected": resistance.affected,
        "damage_dice": list(result.damage_dice),
        "target_hp": next(pool.current for pool in after.pools if pool.id == "hp:dog"),
    }
    record_quick_contest(case_id, inputs, observed, record_property)


def test_failed_attacker_uses_only_three_actual_dice_no_damage_and_real_origin(
    record_property: Callable[[str, object], None],
) -> None:
    before = fixture()
    action = command(separation=2)
    dice = AuditedDice((4, 4, 4))
    after, result = apply_current_creature_combat(before, action, rng=dice, system=True)
    assert isinstance(result, NaturalAttackOutcome)
    assert isinstance(result.resistance, FailedResistanceAttempt)
    assert result.attack and result.attack.effective_target == 8 and result.attack.total == 12
    assert not result.hit and result.damage_dice == () and result.injury is None
    assert next(p.current for p in after.pools if p.id == "hp:dog") == 9
    assert dice.calls == 3 and dice.exhausted()
    event = native(after)
    receipt = CreatureCombatEvent.model_validate_json(event.kind)
    assert receipt.resistance_attempt_generation == 1
    assert receipt.command_id == action.id and receipt.outcome == result
    assert not hasattr(result.resistance, "resister")
    assert not hasattr(result.resistance, "contest")
    assert (
        replay_recorded_creature_combat(before, ((action, event),), rng=RecordedDice((4, 4, 4)))
        == after
    )
    restored = ResourceState.model_validate_json(after.model_dump_json())
    retry = AuditedDice()
    assert apply_current_creature_combat(restored, action, rng=retry, system=True) == (
        restored,
        result,
    )
    assert retry.calls == 0
    assert result.basic_damage == 0 and result.attack.margin == -4
    record_native_creature(
        "basic-b348-r7-creature-gaze", before, after, action, result, record_property
    )


@pytest.mark.parametrize(
    "subject_will,subject_dice,hit,damage",
    [(10, (4, 4, 4), True, (1, 1, 1)), (15, (4, 4, 4), False, ())],
    ids=("R9-winning-attack", "R8-resistance-tie"),
)
def test_successful_attack_requires_actual_subject_roll_and_only_winner_causes_injury(
    subject_will: int,
    subject_dice: tuple[int, int, int],
    hit: bool,
    damage: tuple[int, ...],
    record_property: Callable[[str, object], None],
) -> None:
    before = fixture(will=12, subject_will=subject_will)
    action = command(subject_will=subject_will)
    dice = AuditedDice((3, 3, 3) + subject_dice + damage)
    after, result = apply_current_creature_combat(before, action, rng=dice, system=True)
    assert isinstance(result, NaturalAttackOutcome) and isinstance(
        result.resistance, ResistanceTrace
    )
    assert result.hit is hit and result.resistance.affected is hit
    assert result.resistance.resister.dice == subject_dice
    assert result.resistance.attacker.effective_target == 12
    assert result.resistance.attacker.margin == 3
    if not hit:
        assert result.resistance.resister.effective_target == 15
        assert result.resistance.resister.margin == 3
        assert result.resistance.contest.winner is None
    assert result.damage_dice == damage
    assert next(p.current for p in after.pools if p.id == "hp:dog") == (6 if hit else 9)
    assert dice.calls == 6 + len(damage) and dice.exhausted()
    assert (
        replay_recorded_creature_combat(
            before, ((action, native(after)),), rng=RecordedDice((3, 3, 3) + subject_dice + damage)
        )
        == after
    )
    assert result.resistance.resister.margin == (-2 if hit else 3)
    assert result.resistance.contest.victory_margin == (5 if hit else 0)
    assert result.basic_damage == (3 if hit else 0)
    if hit:
        assert result.injury is not None and result.injury.injury == 3
        assert result.injury.effective_resistance == 0 and result.injury.penetration == 3
    else:
        assert result.injury is None
    record_native_creature(
        "basic-b348-r9-creature-gaze" if hit else "basic-b348-r8-creature-gaze",
        before,
        after,
        action,
        result,
        record_property,
    )


@pytest.mark.parametrize("separation,allowed", [(8, False), (7, True)])
def test_voluntary_floor_uses_actual_compiled_will_and_distance_before_entropy(
    separation: int, allowed: bool
) -> None:
    before = fixture()
    encoded = before.model_dump_json()
    dice = AuditedDice((1, 1, 1) if allowed else ())
    if allowed:
        # Skill3/roll3 succeeds, so an actual mandatory subject roll must follow.
        dice = AuditedDice((1, 1, 1, 1, 1, 1))
        _, result = apply_current_creature_combat(
            before, command(separation=separation), rng=dice, system=True
        )
        assert (
            isinstance(result, NaturalAttackOutcome)
            and result.attack
            and result.attack.effective_target == 3
        )
        assert dice.calls == 6
    else:
        with pytest.raises(ValidationError, match="at least 3"):
            apply_current_creature_combat(
                before, command(separation=separation), rng=dice, system=True
            )
        assert dice.calls == 0
    assert before.model_dump_json() == encoded


def test_mandatory_subject_will_one_is_not_given_voluntary_floor() -> None:
    before = fixture(subject_will=1)
    action = command(subject_will=1)
    dice = AuditedDice((3, 3, 3, 4, 4, 4, 1, 1, 1))
    after, result = apply_current_creature_combat(before, action, rng=dice, system=True)
    assert isinstance(result, NaturalAttackOutcome) and isinstance(
        result.resistance, ResistanceTrace
    )
    assert result.resistance.resister.effective_target == 1 and result.hit
    assert dice.calls == 9 and next(p.current for p in after.pools if p.id == "hp:dog") == 6


@pytest.mark.parametrize("scope,iq,effective", [(True, 5, 16), (False, 5, 20), (True, 6, 16)])
def test_rule16_still_uses_explicit_authoritative_living_or_sapient_scope(
    scope: bool, iq: int, effective: int
) -> None:
    before = fixture(will=20, iq=iq)
    # Roll17 is a failed attack in both cases; only the actual attacker triple exists.
    dice = AuditedDice((5, 6, 6))
    _, result = apply_current_creature_combat(before, command(scope=scope), rng=dice, system=True)
    assert isinstance(result, NaturalAttackOutcome) and result.attack
    assert result.attack.effective_target == effective and dice.calls == 3


@pytest.mark.parametrize("scope,iq", [(None, 5), (False, 6)])
def test_unknown_or_contradictory_scope_refuses_without_draws(scope: bool | None, iq: int) -> None:
    before = fixture(iq=iq)
    dice = AuditedDice()
    with pytest.raises(ValidationError, match="scope"):
        apply_current_creature_combat(before, command(scope=scope), rng=dice, system=True)
    assert dice.calls == 0


def test_old_provider_keeps_two_roll_bytes_and_accepted_old_retry_before_scope() -> None:
    before = fixture()
    action = command(scope=None)
    dice = AuditedDice((4, 4, 4, 6, 6, 5))
    after, result = apply_creature_combat(
        before, action, rng=dice, system=True, historical_scope=True
    )
    assert isinstance(result, NaturalAttackOutcome) and isinstance(
        result.resistance, ResistanceTrace
    )
    assert not result.hit and dice.calls == 6
    event = native(after)
    assert "resistance_attempt_generation" not in json.loads(event.kind)
    # Captured independently from unchanged primary d766's historical provider.
    assert hashlib.sha256(event.model_dump_json().encode()).hexdigest() == (
        "c0a4e64b3111fb393dee20c60573dc1a5987fcc0cedb735c7924c1c09c87f190"
    )
    # The explicit current entry must return the actual accepted old receipt unchanged,
    # without asking for a new scope fact, a new revision, or any new dice.
    retry = AuditedDice()
    assert apply_current_creature_combat(after, action, rng=retry, system=True) == (after, result)
    assert retry.calls == 0
    assert replay_creature_combat(before, (action,), rng=RecordedDice((4, 4, 4, 6, 6, 5))) == after
    assert (
        replay_recorded_creature_combat(
            before, ((action, event),), rng=RecordedDice((4, 4, 4, 6, 6, 5))
        )
        == after
    )


@pytest.mark.parametrize(
    "tamper",
    [
        "version",
        "zero",
        "null",
        "bool",
        "float",
        "string",
        "absent",
        "command",
        "target",
        "time",
        "receipt",
        "duplicate",
        "effect",
    ],
)
def test_forged_private_current_origin_refuses_without_entropy(tamper: str) -> None:
    before = fixture()
    action = command(separation=2)
    after, _ = apply_current_creature_combat(
        before, action, rng=RecordedDice((4, 4, 4)), system=True
    )
    event = native(after)
    data = json.loads(event.kind)
    if tamper in {"version", "zero", "null", "bool", "float", "string"}:
        data["resistance_attempt_generation"] = {
            "version": 2,
            "zero": 0,
            "null": None,
            "bool": True,
            "float": 1.0,
            "string": "1",
        }[tamper]
    elif tamper == "command":
        data["command_id"] = "foreign"
    elif tamper == "absent":
        del data["resistance_attempt_generation"]
    elif tamper == "effect":
        data["outcome"]["effect"] = None
    changed = event.model_copy(update={"kind": json.dumps(data)})
    if tamper == "target":
        changed = changed.model_copy(update={"target_id": "basilisk"})
    if tamper == "time":
        changed = changed.model_copy(update={"at": 1})
    corrupted = after.model_copy(
        update={
            "events": tuple(changed if e.id == event.id else e for e in after.events)
            + ((changed,) if tamper == "duplicate" else ()),
            "receipts": () if tamper == "receipt" else after.receipts,
        }
    )
    dice = AuditedDice()
    with pytest.raises(ValidationError):
        apply_current_creature_combat(corrupted, action, rng=dice, system=True)
    assert dice.calls == 0


def test_current_provider_requires_system_authority_and_exact_revision() -> None:
    before = fixture()
    dice = AuditedDice()
    with pytest.raises(ValidationError, match="authority"):
        apply_current_creature_combat(before, command(), rng=dice)
    with pytest.raises(ConflictError, match="revision"):
        apply_current_creature_combat(
            before, command().model_copy(update={"expected_revision": 1}), rng=dice, system=True
        )
    assert dice.calls == 0


@pytest.mark.parametrize("will", [1, 2])
@pytest.mark.parametrize("include_preparation", [False, True])
def test_old_low_skill_two_roll_receipt_cannot_be_stamped_current(
    will: int, include_preparation: bool
) -> None:
    before = fixture(will=will, subject_will=15)
    action = command(subject_will=15)
    after, result = apply_creature_combat(
        before, action, rng=RecordedDice((1, 1, 1, 1, 1, 1)), system=True
    )
    assert isinstance(result, NaturalAttackOutcome) and result.attack
    assert result.attack.effective_target == will and result.attack.outcome.succeeded
    assert not result.hit and isinstance(result.resistance, ResistanceTrace)
    event = native(after)
    data = json.loads(event.kind)
    data["resistance_attempt_generation"] = 1
    if include_preparation:
        lawful, _ = apply_current_creature_combat(
            fixture(will=12, subject_will=15),
            action,
            rng=RecordedDice((3, 3, 3, 4, 4, 4)),
            system=True,
        )
        captured = json.loads(native(lawful).kind)["resistance_preparation"]
        captured["attacker"]["base_target"] = will
        data["resistance_preparation"] = captured
    changed = event.model_copy(update={"kind": json.dumps(data)})
    forged = after.model_copy(
        update={"events": tuple(changed if e.id == event.id else e for e in after.events)}
    )
    dice = AuditedDice()
    with pytest.raises(ValidationError):
        apply_current_creature_combat(forged, action, rng=dice, system=True)
    assert dice.calls == 0
    # The same native accepted historical command still returns its old receipt.
    assert apply_current_creature_combat(after, action, rng=dice, system=True) == (after, result)
    assert dice.calls == 0


@pytest.mark.parametrize("key", ["resistance_attempt_generation", "resistance_preparation"])
@pytest.mark.parametrize("first", ["true", "null"])
def test_duplicate_private_origin_keys_are_rejected_before_last_key_wins(
    key: str, first: str
) -> None:
    action = command(subject_will=15)
    after, _ = apply_current_creature_combat(
        fixture(will=12, subject_will=15),
        action,
        rng=RecordedDice((3, 3, 3, 4, 4, 4)),
        system=True,
    )
    event = native(after)
    changed = event.model_copy(
        update={
            "kind": event.kind.replace(
                '"' + key + '":', '"' + key + '":' + first + ',"' + key + '":', 1
            )
        }
    )
    forged = after.model_copy(
        update={"events": tuple(changed if e.id == event.id else e for e in after.events)}
    )
    dice = AuditedDice()
    with pytest.raises(ValidationError, match="Duplicate"):
        apply_current_creature_combat(forged, action, rng=dice, system=True)
    assert dice.calls == 0


def test_old_failed_attacker_with_real_optional_subject_roll_cannot_claim_current_rng_origin() -> (
    None
):
    before = fixture()
    action = command(separation=2)
    historical, result = apply_creature_combat(
        before, action, rng=RecordedDice((4, 4, 4, 6, 6, 5)), system=True
    )
    assert isinstance(result, NaturalAttackOutcome) and isinstance(
        result.resistance, ResistanceTrace
    )
    assert not result.resistance.attacker.outcome.succeeded
    assert result.resistance.contest.winner == action.actor_id and not result.hit
    current, _ = apply_current_creature_combat(
        before, action, rng=RecordedDice((4, 4, 4)), system=True
    )
    event = native(historical)
    data = json.loads(event.kind)
    data["resistance_attempt_generation"] = 1
    data["resistance_preparation"] = json.loads(native(current).kind)["resistance_preparation"]
    changed = event.model_copy(update={"kind": json.dumps(data)})
    forged = historical.model_copy(
        update={"events": tuple(changed if e.id == event.id else e for e in historical.events)}
    )
    dice = AuditedDice()
    with pytest.raises(ValidationError, match="optional subject"):
        apply_current_creature_combat(forged, action, rng=dice, system=True)
    assert dice.calls == 0


@pytest.mark.parametrize(
    "tamper", ["missing", "null", "scope", "cap", "distance", "subject", "orphan"]
)
def test_current_captured_preparation_is_independent_of_the_two_roll_trace(tamper: str) -> None:
    action = command(subject_will=15)
    after, _ = apply_current_creature_combat(
        fixture(will=20, subject_will=15),
        action,
        rng=RecordedDice((3, 3, 3, 1, 1, 1)),
        system=True,
    )
    event = native(after)
    data = json.loads(event.kind)
    if tamper == "missing":
        del data["resistance_preparation"]
    elif tamper == "null":
        data["resistance_preparation"] = None
    elif tamper == "scope":
        data["resistance_preparation"]["rule_of_16"] = False
    elif tamper == "cap":
        data["resistance_preparation"]["cap_modifiers"] = []
    elif tamper == "distance":
        data["resistance_preparation"]["attacker"]["modifiers"][0]["value"] = -1
    elif tamper == "subject":
        data["resistance_preparation"]["resister"]["base_target"] = 14
    else:
        del data["resistance_attempt_generation"]
    changed = event.model_copy(update={"kind": json.dumps(data)})
    forged = after.model_copy(
        update={"events": tuple(changed if e.id == event.id else e for e in after.events)}
    )
    dice = AuditedDice()
    with pytest.raises(ValidationError):
        apply_current_creature_combat(forged, action, rng=dice, system=True)
    assert dice.calls == 0


@pytest.mark.parametrize("change", ["remove", "new_stats"])
@pytest.mark.parametrize("will,dice", [(3, (1, 1, 1, 1, 1, 1)), (20, (3, 3, 3, 1, 1, 1))])
def test_accepted_current_two_roll_retry_uses_captured_original_not_live_creature(
    change: str, will: int, dice: tuple[int, ...]
) -> None:
    action = command(subject_will=15)
    after, result = apply_current_creature_combat(
        fixture(will=will, subject_will=15),
        action,
        rng=RecordedDice(dice),
        system=True,
    )
    assert isinstance(result, NaturalAttackOutcome) and isinstance(
        result.resistance, ResistanceTrace
    )
    later = after.model_copy(
        update={
            "creatures": ()
            if change == "remove"
            else fixture(will=1, subject_will=1, iq=6).creatures
        }
    )
    retry_dice = AuditedDice()
    assert apply_current_creature_combat(later, action, rng=retry_dice, system=True) == (
        later,
        result,
    )
    assert retry_dice.calls == 0


@pytest.mark.parametrize(
    "changed",
    [
        {"target_will": 9},
        {"target_ht": 11},
        {"vision_contact": False},
        {"actor_id": "foreign"},
    ],
)
def test_trusted_native_target_and_actor_facts_cannot_be_swapped(
    changed: dict[str, object],
) -> None:
    before = fixture()
    dice = AuditedDice()
    with pytest.raises(ValidationError):
        apply_current_creature_combat(
            before, command().model_copy(update=changed), rng=dice, system=True
        )
    assert dice.calls == 0


def test_system_seed_replay_uses_recorded_provider_and_exact_event_not_default_guess() -> None:
    before = fixture()
    action = command(separation=2)
    seed = "01" * 32
    after, _ = apply_current_creature_combat(before, action, rng=SeededRandom(seed), system=True)
    assert (
        replay_recorded_creature_combat(before, ((action, native(after)),), rng=SeededRandom(seed))
        == after
    )
    changed = native(after).model_copy(update={"target_id": "foreign"})
    dice = AuditedDice()
    with pytest.raises(ValidationError, match="identity"):
        replay_recorded_creature_combat(before, ((action, changed),), rng=dice)
    assert dice.calls == 0


def test_successful_system_seed_replay_keeps_actual_damage_and_health_consequences() -> None:
    before = fixture(will=20)
    action = command()
    seed = f"{1:064x}"
    after, result = apply_current_creature_combat(
        before, action, rng=SeededRandom(seed), system=True
    )
    assert isinstance(result, NaturalAttackOutcome) and result.hit and result.injury is not None
    assert isinstance(result.resistance, ResistanceTrace)
    assert len(result.damage_dice) == 3 and result.injury.injury > 0
    assert next(p.current for p in after.pools if p.id == "hp:dog") < 9
    assert (
        replay_recorded_creature_combat(before, ((action, native(after)),), rng=SeededRandom(seed))
        == after
    )


def test_recorded_current_attempt_rejects_a_different_compiled_producer_before_rng() -> None:
    before = fixture()
    action = command(separation=2)
    after, _ = apply_current_creature_combat(
        before, action, rng=RecordedDice((4, 4, 4)), system=True
    )
    dice = AuditedDice()
    with pytest.raises(ValidationError, match="compiled preparation"):
        replay_recorded_creature_combat(fixture(will=11), ((action, native(after)),), rng=dice)
    assert dice.calls == 0


def test_new_native_event_origin_is_required_without_guessing_from_failed_shape() -> None:
    before = fixture()
    action = command(separation=2)
    after, _ = apply_current_creature_combat(
        before, action, rng=RecordedDice((4, 4, 4)), system=True
    )
    event = native(after)
    data = json.loads(event.kind)
    del data["resistance_attempt_generation"]
    del data["resistance_preparation"]
    dice = AuditedDice()
    with pytest.raises(ValidationError, match="requires its current origin"):
        replay_recorded_creature_combat(
            before, ((action, event.model_copy(update={"kind": json.dumps(data)})),), rng=dice
        )
    assert dice.calls == 0


def test_ordinary_native_attack_remains_byte_identical_across_provider_wrappers() -> None:
    from test_creature_combat import creature_state, natural

    before = creature_state()
    action = natural()
    old, _ = apply_creature_combat(before, action, rng=RecordedDice((2, 2, 2, 4)), system=True)
    current, _ = apply_current_creature_combat(
        before, action, rng=RecordedDice((2, 2, 2, 4)), system=True
    )
    assert current.model_dump_json() == old.model_dump_json()
    assert "resistance_attempt_generation" not in native(current).kind


@pytest.mark.parametrize("kind", ["move", "turn", "damage"])
def test_swarm_provider_bytes_are_not_enrolled_in_resistance_policy(kind: str) -> None:
    from test_creature_combat import swarm_state

    before = swarm_state()
    action: SetSwarmArea | ResolveSwarmTurn | DamageSwarm
    if kind == "move":
        action = SetSwarmArea(
            id="swarm",
            actor_id="swarm-actor",
            expected_revision=0,
            swarm_id="cloud",
            area=(SwarmCell(q=0, r=0),),
        )
    elif kind == "turn":
        action = ResolveSwarmTurn(
            id="swarm", actor_id="swarm-actor", expected_revision=0, swarm_id="cloud"
        )
    else:
        action = DamageSwarm(
            id="swarm",
            actor_id="swarm-actor",
            expected_revision=0,
            swarm_id="cloud",
            countermeasure="area-attack",
            basic_damage=1,
        )
    old, _ = apply_creature_combat(before, action, rng=RecordedDice(()), system=True)
    current, _ = apply_current_creature_combat(before, action, rng=RecordedDice(()), system=True)
    assert current.model_dump_json() == old.model_dump_json()
    assert "resistance_attempt_generation" not in native(current).kind
