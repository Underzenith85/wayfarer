"""Independent B437-B441 toxin, alcohol, and withdrawal fixtures."""

import pytest

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.rules.types.toxin import DeliveryEvidence, DeliveryVector, ToxinProfile
from wayfarer.engine.simulation.health.toxins import (
    DrinkCommand,
    ToxinCommand,
    WithdrawalCommand,
    apply_drinking,
    apply_toxin,
    apply_withdrawal,
    overdose_profile,
    project_toxin,
)
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError


def state() -> ResourceState:
    return ResourceState(
        pools=(
            Pool(
                id="hp:a",
                current=10,
                maximum=10,
                injury=InjuryStatus(profile_id="gurps-basic-set-4e-2004"),
            ),
            Pool(
                id="fp:a",
                current=10,
                maximum=10,
                fatigue=FatigueStatus(profile_id="gurps-basic-set-4e-2004"),
            ),
        )
    )


def arsenic() -> ToxinProfile:
    return ToxinProfile(
        id="arsenic",
        vector="digestive",
        delay=3600,
        interval=3600,
        cycles=8,
        resistance_modifier=-2,
        hp_dice=1,
        treatment_owner="physician",
        reference="B437-B439",
    )


def test_delivery_fails_closed_and_identity_is_private() -> None:
    command = ToxinCommand(
        id="dose", actor_id="a", expected_revision=0, kind="expose", exposure_id="hidden-dose"
    )
    with pytest.raises(ValidationError, match="did not deliver"):
        apply_toxin(
            state(),
            command,
            profile=arsenic(),
            evidence=DeliveryEvidence(touched_skin=True),
            ht=10,
            rng=RecordedDice([]),
            system=True,
        )
    after, _ = apply_toxin(
        state(),
        command,
        profile=arsenic(),
        evidence=DeliveryEvidence(swallowed=True),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    # Neither the victim nor an unrelated observer gets substance identity merely
    # because an exposure was persisted; discovery is a separate procedure.
    assert project_toxin(after.toxins[0], authorized_actor_id="observer").substance_id is None
    assert project_toxin(after.toxins[0], authorized_actor_id="a").substance_id is None


@pytest.mark.parametrize(
    ("vector", "evidence"),
    [
        ("contact", DeliveryEvidence(touched_skin=True)),
        ("blood", DeliveryEvidence(mucous_or_open_wound=True)),
        ("digestive", DeliveryEvidence(swallowed=True)),
        ("respiratory", DeliveryEvidence(inhaled=True)),
        ("sense", DeliveryEvidence(relevant_sense=True)),
        ("follow-up", DeliveryEvidence(penetrated_damage=True)),
    ],
)
def test_each_delivery_vector_requires_matching_exposure(
    vector: DeliveryVector, evidence: DeliveryEvidence
) -> None:
    profile = arsenic().model_copy(update={"vector": vector})
    after, result = apply_toxin(
        state(),
        ToxinCommand(
            id="delivery", actor_id="a", expected_revision=0, kind="expose", exposure_id="dose"
        ),
        profile=profile,
        evidence=evidence,
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert result.active and len(after.toxins) == 1


@pytest.mark.parametrize(
    ("vector", "evidence"),
    [
        ("contact", DeliveryEvidence(touched_skin=True, skin_covered=True)),
        ("blood", DeliveryEvidence(mucous_or_open_wound=True, sealed=True)),
        ("respiratory", DeliveryEvidence(inhaled=True, filter_lungs=True)),
        ("sense", DeliveryEvidence(relevant_sense=True, protected_sense=True)),
        ("follow-up", DeliveryEvidence()),
    ],
)
def test_delivery_protection_and_nonpenetration_reject(
    vector: DeliveryVector, evidence: DeliveryEvidence
) -> None:
    with pytest.raises(ValidationError, match="did not deliver"):
        apply_toxin(
            state(),
            ToxinCommand(
                id="blocked", actor_id="a", expected_revision=0, kind="expose", exposure_id="dose"
            ),
            profile=arsenic().model_copy(update={"vector": vector}),
            evidence=evidence,
            ht=10,
            rng=RecordedDice([]),
            system=True,
        )


def test_resisted_failed_and_repeated_doses_are_independent() -> None:
    original = state()
    one, _ = apply_toxin(
        original,
        ToxinCommand(id="one", actor_id="a", expected_revision=0, kind="expose", exposure_id="one"),
        profile=arsenic(),
        evidence=DeliveryEvidence(swallowed=True),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    two, _ = apply_toxin(
        one,
        ToxinCommand(id="two", actor_id="a", expected_revision=1, kind="expose", exposure_id="two"),
        profile=arsenic(),
        evidence=DeliveryEvidence(swallowed=True),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    assert len(two.toxins) == 2
    due = two.model_copy(update={"game_time": 3600})
    resisted, result = apply_toxin(
        due,
        ToxinCommand(id="resist", actor_id="a", expected_revision=2, kind="resolve", exposure_id="one"),
        rng=RecordedDice([2, 2, 2]),
        system=True,
    )
    assert result.resisted and not result.active
    failed, result = apply_toxin(
        resisted,
        ToxinCommand(id="fail", actor_id="a", expected_revision=3, kind="resolve", exposure_id="two"),
        rng=RecordedDice([6, 6, 6, 4]),
        system=True,
    )
    assert not result.resisted and result.hp_lost == 4 and result.active
    assert next(p.current for p in failed.pools if p.id == "hp:a") == 6


def test_cyclic_settlement_replay_and_future_only_treatment() -> None:
    exposed, _ = apply_toxin(
        state(),
        ToxinCommand(id="start", actor_id="a", expected_revision=0, kind="expose", exposure_id="dose"),
        profile=arsenic(),
        evidence=DeliveryEvidence(swallowed=True),
        ht=10,
        rng=RecordedDice([]),
        system=True,
    )
    exposed = exposed.model_copy(update={"game_time": 3600})
    command = ToxinCommand(
        id="cycle-1", actor_id="a", expected_revision=1, kind="resolve", exposure_id="dose"
    )
    damaged, result = apply_toxin(
        exposed, command, rng=RecordedDice([6, 6, 6, 3]), system=True
    )
    assert result.hp_lost == 3
    checkpoint = ResourceState.model_validate_json(damaged.model_dump_json())
    replay, repeated = apply_toxin(
        checkpoint, command, rng=RecordedDice([]), system=True
    )
    assert replay == checkpoint and repeated == result
    treated, _ = apply_toxin(
        checkpoint,
        ToxinCommand(id="care", actor_id="a", expected_revision=2, kind="treat", exposure_id="dose"),
        treatment_bonus=3,
        rng=RecordedDice([]),
        system=True,
    )
    assert next(p.current for p in treated.pools if p.id == "hp:a") == 7
    treated = treated.model_copy(update={"game_time": 7200})
    treated, result = apply_toxin(
        treated,
        ToxinCommand(id="cycle-2", actor_id="a", expected_revision=3, kind="resolve", exposure_id="dose"),
        rng=RecordedDice([3, 3, 3]),
        system=True,
    )
    assert result.resisted and next(p.current for p in treated.pools if p.id == "hp:a") == 7


def test_double_depressant_overdose_is_persistent() -> None:
    profile = overdose_profile("sedative-overdose")
    profile = profile.model_copy(update={"resistance_modifier": -2})
    exposed, _ = apply_toxin(
        state(),
        ToxinCommand(id="overdose", actor_id="a", expected_revision=0, kind="expose", exposure_id="od"),
        profile=profile,
        evidence=DeliveryEvidence(swallowed=True),
        ht=10,
        dose=2,
        rng=RecordedDice([]),
        system=True,
    )
    exposed, result = apply_toxin(
        exposed,
        ToxinCommand(id="od-cycle", actor_id="a", expected_revision=1, kind="resolve", exposure_id="od"),
        rng=RecordedDice([6, 6, 6]),
        system=True,
    )
    assert result.hp_lost == 2 and exposed.toxins[0].remaining == 23
    assert exposed.toxins[0].overdose_until == 43200
    hp = next(p for p in exposed.pools if p.id == "hp:a")
    assert hp.injury is not None and hp.injury.unconscious


def test_intoxication_and_withdrawal_are_durable() -> None:
    drank, result = apply_drinking(
        state(),
        DrinkCommand(id="drinks", actor_id="a", expected_revision=0, kind="drink", drinks=4),
        st=10,
        ht=10,
        rng=RecordedDice([6, 5, 4]),
        system=True,
    )
    assert result.level == "tipsy"
    withdrawing, _ = apply_withdrawal(
        drank,
        WithdrawalCommand(
            id="begin", actor_id="a", expected_revision=1, kind="begin-withdrawal", dependency_id="dep"
        ),
        substance_id="stimulant",
        dependency_kind="physiological",
        ht=10,
        will=10,
        rng=RecordedDice([]),
        system=True,
    )
    withdrawing = withdrawing.model_copy(update={"game_time": 86400})
    hurt, withdrawal_result = apply_withdrawal(
        withdrawing,
        WithdrawalCommand(
            id="day-1", actor_id="a", expected_revision=2, kind="resolve-withdrawal", dependency_id="dep"
        ),
        ht=10,
        will=10,
        available=False,
        rng=RecordedDice([6, 6, 6]),
        system=True,
    )
    assert withdrawal_result.hp_lost == 1 and withdrawal_result.successes == 0
    assert hurt.illnesses[0].blocks_natural_healing
    with pytest.raises(ConflictError, match="daily deadline"):
        apply_withdrawal(
            hurt,
            WithdrawalCommand(
                id="early", actor_id="a", expected_revision=3, kind="resolve-withdrawal", dependency_id="dep"
            ),
            ht=10,
            will=10,
            rng=RecordedDice([1, 1, 1]),
            system=True,
        )
