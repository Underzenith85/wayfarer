"""Independent expectations from Basic Set: Characters B33-34, B36, B46, B61, B120-121."""

from types import SimpleNamespace
from typing import cast

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.engine.rules.checks import Modifier, RecordedDice
from wayfarer.engine.rules.conformance_vocabulary import BASIC_PROFILE_ID
from wayfarer.engine.rules.traits.base import TraitOptions, TraitRules
from wayfarer.engine.rules.traits.mental import (
    Frequency,
    Relationship,
    frequency_roll,
    relationship_cost,
)
from wayfarer.engine.rules.traits.obligations import (
    ObligationClause,
    ObligationCommand,
    ObligationState,
    record_obligation,
)
from wayfarer.engine.rules.traits.procedures import (
    AdvantageActivation,
    AdvantageOrigin,
    AlternativeAttack,
    AlternativeAttackGroup,
    CampaignOrigins,
    DefenseRarity,
    LimitedDefense,
    PotentialAdvantage,
    PotentialKind,
    SecretDisadvantage,
    buy_off_disadvantage,
    change_activation,
    choose_advantage_origin,
    default_activation,
    disable_alternative,
    impose_self_imposed,
    meet_potential_condition,
    pay_potential,
    reserve_potential,
    resolve_self_control,
    reveal_secret,
    select_alternative,
    settle_secret,
)
from wayfarer.errors import ConflictError, ValidationError


def test_b33_unusual_advantage_origin_is_required_and_campaign_scoped() -> None:
    campaign = CampaignOrigins(allowed=("magic", "psionic", "mutation"))
    magic = choose_advantage_origin("advantage:magery", "supernatural", "magic", campaign)
    assert magic.affected_by("magic") and not magic.affected_by("psionic")
    assert (
        choose_advantage_origin("advantage:regeneration", "exotic", "mutation", campaign).origin
        == "mutation"
    )
    with pytest.raises(ValidationError, match="unavailable"):
        choose_advantage_origin("advantage:magery", "supernatural", "divine", campaign)
    with pytest.raises(SchemaError, match="require an in-game origin"):
        AdvantageOrigin(advantage_id="advantage:magery", classification="supernatural")


@pytest.mark.parametrize(
    ("kind", "benefit"),
    [("heir", "partial"), ("schrodinger", "none"), ("secret", "uncontrolled")],
)
def test_b33_potential_advantage_deposit_benefit_and_due_payment(kind: str, benefit: str) -> None:
    state = reserve_potential("advantage:status", cast(PotentialKind, kind), "inherit", 21)
    assert state.paid_points == 11 and state.benefit == benefit
    due = meet_potential_condition(state, "inherit")
    paid = pay_potential(due, 13)
    assert due.points_due == 10
    assert paid.state.status == "complete" and paid.state.benefit == "full"
    assert (paid.points_spent, paid.points_remaining) == (10, 3)
    assert PotentialAdvantage.model_validate_json(paid.state.model_dump_json()) == paid.state


def test_b33_potential_advantage_fails_closed_on_condition_and_balance() -> None:
    state = reserve_potential("advantage:wealth", "heir", "inherit", 20)
    with pytest.raises(ValidationError, match="condition"):
        meet_potential_condition(state, "train")
    with pytest.raises(ValidationError, match="paid in full"):
        pay_potential(meet_potential_condition(state, "inherit"), 9)


def test_b34_activation_uses_ready_or_attack_and_never_leaves_attacks_on() -> None:
    switchable = default_activation("advantage:invisibility", "switchable")
    assert switchable.active  # the default while asleep or unconscious
    off = change_activation(switchable, "turn-off", "ready")
    assert not off.state.active and off.seconds == 1
    attack = AdvantageActivation(
        advantage_id="advantage:innate-attack", mode="attack-only", active=False
    )
    used = change_activation(attack, "attack", "attack")
    assert used.used_for_attack and not used.state.active
    with pytest.raises(ValidationError, match="Ready"):
        change_activation(switchable, "turn-on", "attack")
    with pytest.raises(SchemaError, match="cannot be inactive"):
        AdvantageActivation(advantage_id="advantage:extra-arms", mode="always-on", active=False)


@pytest.mark.parametrize(
    ("frequency", "expected_cost"), [(6, 3), (9, 5), (12, 10), (15, 15), (18, 20)]
)
def test_b36_frequency_prices_and_resolves_one_recorded_roll(
    frequency: int, expected_cost: int
) -> None:
    ally = Relationship(
        id="ally:a",
        person_id="a",
        kind="ally",
        frequency=cast(Frequency, frequency),
        character_points_percent=100,
    )
    assert relationship_cost(ally) == expected_cost
    result = frequency_roll(ally, RecordedDice([] if frequency == 18 else [2, 3, 4]))
    assert result.appears == (frequency >= 9)
    assert result.dice is None if frequency == 18 else result.dice == (2, 3, 4)


@pytest.mark.parametrize(
    ("rarity", "percent", "limited_cost"),
    [("very-common", -20, 8), ("common", -40, 6), ("occasional", -60, 4), ("rare", -80, 2)],
)
def test_b46_limited_defense_rarity_and_direct_effect_boundary(
    rarity: str, percent: int, limited_cost: int
) -> None:
    defense = LimitedDefense(category_id="magic", rarity=cast(DefenseRarity, rarity))
    assert (defense.percent, defense.limited_cost(10)) == (percent, limited_cost)
    assert defense.protects(direct_damage=True, categories=("magic", "burning"))
    assert not defense.protects(direct_damage=False, categories=("magic", "falling"))


def alternative_group() -> AlternativeAttackGroup:
    return AlternativeAttackGroup(
        members=(
            AlternativeAttack(attack_id="beam", kind="innate-attack", point_cost=31),
            AlternativeAttack(attack_id="snare", kind="binding", point_cost=12),
            AlternativeAttack(attack_id="stun", kind="affliction", point_cost=9),
        )
    )


def test_b61_alternative_attacks_price_one_primary_and_one_fifth_others() -> None:
    group = alternative_group()
    assert group.primary_id == "beam" and group.purchase_cost == 36
    assert select_alternative(group, ("snare",)) == "snare"
    with pytest.raises(ValidationError, match="Exactly one"):
        select_alternative(group, ("beam", "snare"))
    with pytest.raises(SchemaError, match="cannot use Link"):
        AlternativeAttack(attack_id="linked", kind="binding", point_cost=10, has_link=True)


def test_b61_malfunction_or_primary_neutralization_disables_every_alternative() -> None:
    group = alternative_group()
    assert disable_alternative(group, "snare", "neutralized").available("beam")
    failed = disable_alternative(group, "snare", "malfunction")
    assert not any(failed.available(member.attack_id) for member in failed.members)
    drained = disable_alternative(group, "beam", "drained")
    assert not drained.available("snare")
    assert AlternativeAttackGroup.model_validate_json(failed.model_dump_json()) == failed


def test_b120_secret_disadvantage_grants_then_buys_off_the_extra_five() -> None:
    hidden = SecretDisadvantage(
        secret_id="secret:one", definition_ids=("trait:unluckiness",), underlying_cost=-10
    )
    assert hidden.credited_cost == -15 and hidden.buyoff_due == 0
    revealed = reveal_secret(hidden)
    assert revealed.credited_cost == -15 and revealed.buyoff_due == 5
    settled = settle_secret(revealed, 8)
    assert settled.state.credited_cost == -10 and settled.points_remaining == 3
    with pytest.raises(ValidationError, match="already revealed"):
        reveal_secret(revealed)


def test_b120_self_control_supports_giving_in_modified_roll_and_paid_success() -> None:
    options = TraitOptions(self_control=12)
    rules = TraitRules(BASIC_PROFILE_ID, self_control=True)
    gave_in = resolve_self_control(
        BASIC_PROFILE_ID,
        -10,
        1,
        options,
        rules,
        "give-in",
        available_points=2,
        gm_allows_paid_success=False,
        rng=RecordedDice([]),
    )
    assert not gave_in.resisted and gave_in.dice is None
    rolled = resolve_self_control(
        BASIC_PROFILE_ID,
        -10,
        1,
        options,
        rules,
        "roll",
        available_points=2,
        gm_allows_paid_success=False,
        rng=RecordedDice([4, 4, 4]),
        modifiers=(Modifier(-1, "severe stimulus", "scene:1", "1"),),
    )
    assert not rolled.resisted and rolled.dice == (4, 4, 4)
    bought = resolve_self_control(
        BASIC_PROFILE_ID,
        -10,
        1,
        options,
        rules,
        "buy-success",
        available_points=2,
        gm_allows_paid_success=True,
        rng=RecordedDice([]),
    )
    assert bought.resisted and (bought.points_spent, bought.points_remaining) == (1, 1)


def test_b121_self_imposed_traits_reject_quick_alteration_and_allow_pacts() -> None:
    assert impose_self_imposed("trait:vow", "chosen", pact_condition=True).pact_condition
    for method in ("affliction", "drug", "brain-surgery"):
        with pytest.raises(ValidationError, match="Quick behavior"):
            impose_self_imposed("trait:vow", method)
    assert impose_self_imposed("trait:honesty", "magic").method == "magic"
    assert impose_self_imposed("trait:code-of-honor-soldier", "chosen").method == "chosen"
    assert impose_self_imposed("trait:sense-of-duty-small-group", "chosen").method == "chosen"
    with pytest.raises(ValidationError, match="not a Basic Set self-imposed"):
        impose_self_imposed("trait:quirk-careful", "chosen")


def test_b163_careful_records_only_authored_preparation_without_a_penalty() -> None:
    build = SimpleNamespace(trait_purchases=(SimpleNamespace(definition_id="trait:quirk-careful"),))
    command = ObligationCommand(
        id="careful-1",
        actor_id="hero",
        definition_id="trait:quirk-careful",
        situation_id="enter-dangerous-ruin",
        expected_revision=0,
        clause="prepare-before-danger",
        outcome="fulfilled",
        preparation_time=30,
        preparation_expense=12,
        reason="The GM authored the rope, lamps, and planning required here.",
    )
    state, receipt = record_obligation(
        ObligationState(), build, command, principal_id="gm", gm_ids=frozenset({"gm"})
    )
    assert state.revision == 1
    assert (receipt.command.preparation_time, receipt.command.preparation_expense) == (30, 12)
    assert not receipt.compulsory_roll
    assert receipt.point_change == 0 and receipt.mechanical_penalty is None

    # A retry returns the exact persisted judgment without consuming a revision.
    repeated, replayed = record_obligation(
        state, build, command, principal_id="gm", gm_ids=frozenset({"gm"})
    )
    assert repeated == state and replayed == receipt
    with pytest.raises(ConflictError, match="different facts"):
        record_obligation(
            state,
            build,
            command.model_copy(update={"reason": "changed"}),
            principal_id="gm",
            gm_ids=frozenset({"gm"}),
        )


@pytest.mark.parametrize(
    ("definition_id", "clause", "beneficiaries", "kind", "scope"),
    (
        ("trait:code-of-honor-soldier", "obey-rules-of-war", ("enemy-unit",), "soldier-code", None),
        (
            "trait:sense-of-duty-individual",
            "never-abandon",
            ("ward",),
            "sense-of-duty",
            "individual",
        ),
        (
            "trait:sense-of-duty-small-group",
            "share-equipment",
            ("party",),
            "sense-of-duty",
            "small-group",
        ),
        (
            "trait:sense-of-duty-large-group",
            "render-aid",
            ("town",),
            "sense-of-duty",
            "large-group",
        ),
        ("trait:sense-of-duty-race", "prevent-suffering", ("humans",), "sense-of-duty", "race"),
        (
            "trait:sense-of-duty-all-living",
            "prevent-hunger",
            ("refugees",),
            "sense-of-duty",
            "all-living",
        ),
    ),
)
def test_b127_b153_obligations_are_exact_gm_judgments_without_invented_mechanics(
    definition_id: str,
    clause: str,
    beneficiaries: tuple[str, ...],
    kind: str,
    scope: str | None,
) -> None:
    build = SimpleNamespace(trait_purchases=(SimpleNamespace(definition_id=definition_id),))
    command = ObligationCommand(
        id="decision",
        actor_id="hero",
        definition_id=definition_id,
        situation_id="scene",
        expected_revision=0,
        clause=cast(ObligationClause, clause),
        outcome="breached",
        beneficiary_ids=beneficiaries,
        reason="GM judgment from the played scene",
    )
    with pytest.raises(ValidationError, match="GM authority"):
        record_obligation(
            ObligationState(),
            build,
            command,
            principal_id="hero",
            gm_ids=frozenset({"gm"}),
        )
    _, receipt = record_obligation(
        ObligationState(), build, command, principal_id="gm", gm_ids=frozenset({"gm"})
    )
    assert (receipt.kind, receipt.scope) == (kind, scope)
    assert receipt.mechanical_penalty is None and receipt.point_change == 0


def test_authored_obligation_rejects_player_mechanics_wrong_traits_and_stale_state() -> None:
    with pytest.raises(SchemaError, match="Only Careful"):
        ObligationCommand(
            id="expense",
            actor_id="hero",
            definition_id="trait:code-of-honor-soldier",
            situation_id="scene",
            expected_revision=0,
            clause="maintain-kit",
            outcome="fulfilled",
            preparation_expense=1,
            reason="player supplied cost",
        )
    build = SimpleNamespace(
        trait_purchases=(SimpleNamespace(definition_id="trait:code-of-honor-soldier"),)
    )
    wrong = ObligationCommand(
        id="wrong",
        actor_id="hero",
        definition_id="trait:sense-of-duty-individual",
        situation_id="scene",
        expected_revision=0,
        clause="never-betray",
        outcome="fulfilled",
        beneficiary_ids=("ward",),
        reason="wrong approved build",
    )
    with pytest.raises(ValidationError, match="approved obligation"):
        record_obligation(
            ObligationState(), build, wrong, principal_id="gm", gm_ids=frozenset({"gm"})
        )
    with pytest.raises(ConflictError, match="state changed"):
        record_obligation(
            ObligationState(revision=1),
            build,
            wrong.model_copy(
                update={
                    "definition_id": "trait:code-of-honor-soldier",
                    "clause": "obey-orders",
                }
            ),
            principal_id="gm",
            gm_ids=frozenset({"gm"}),
        )


def test_b121_buyoff_uses_compiled_cost_difference_one_step_at_a_time() -> None:
    receipt = buy_off_disadvantage(
        -5,
        3,
        TraitOptions(),
        2,
        TraitOptions(),
        TraitRules(BASIC_PROFILE_ID, maximum_level=3),
        available_points=7,
        gm_permits=True,
    )
    assert (receipt.old_cost, receipt.new_cost, receipt.points_spent, receipt.points_remaining) == (
        -15,
        -10,
        5,
        2,
    )
    control = TraitRules(BASIC_PROFILE_ID, self_control=True)
    improved = buy_off_disadvantage(
        -10,
        1,
        TraitOptions(self_control=9),
        1,
        TraitOptions(self_control=12),
        control,
        available_points=5,
        gm_permits=True,
    )
    assert improved.points_spent == 5
    with pytest.raises(ValidationError, match="one level or raise self-control one step"):
        buy_off_disadvantage(
            -10,
            1,
            TraitOptions(self_control=6),
            1,
            TraitOptions(self_control=15),
            control,
            available_points=20,
            gm_permits=True,
        )
