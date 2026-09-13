"""B25-B31, B291-B294 and B513-B519 economics acceptance evidence."""

from typing import cast

import pytest
from test_actions import engine, seed

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, PlayState
from wayfarer.engine.simulation.campaign.administration import (
    ActivityAllocation,
    ActivityRule,
    AdministrationRules,
    SettleTimeUse,
)
from wayfarer.engine.simulation.campaign.economics import (
    CheckLoyalty,
    CurrencyRule,
    EconomicCommand,
    EconomicsOutcome,
    EconomicsRules,
    EconomicsState,
    Employment,
    ExchangeOffer,
    ExecuteExchange,
    ExecuteTrade,
    FinancialProfile,
    FindHireling,
    HirelingRule,
    JobRule,
    LoyaltyCircumstance,
    MoneyAccount,
    PayCostOfLiving,
    PriceContext,
    SettleJob,
    TradeOffer,
)
from wayfarer.engine.simulation.resources import Item, Owner
from wayfarer.errors import ConflictError, ValidationError


def configured() -> ActionEngine:
    base = engine()
    administration = AdministrationRules(
        id="administration",
        version=1,
        activities=(
            ActivityRule(
                id="dock-work",
                kind="job",
                credit_kind="study",
                credit_numerator=1,
                credit_denominator=4,
            ),
            ActivityRule(id="unpaid-study", kind="study", credit_kind="study"),
        ),
    )
    economics = EconomicsRules(
        id="economics",
        version=1,
        currencies=(
            CurrencyRule(id="dock-dollar", world_id="dock", name="Dock dollar"),
            CurrencyRule(id="far-credit", world_id="far", name="Far credit"),
        ),
        price_contexts=(
            PriceContext(
                id="dock-market", world_id="dock", currency_id="dock-dollar", label="Dock market"
            ),
        ),
        trades=(
            TradeOffer(
                id="buy-sword",
                context_id="dock-market",
                kind="purchase",
                payer_account_id="a-cash",
                payee_account_id="b-cash",
                item_id="sword-b",
                quantity=1,
                price=300,
            ),
        ),
        exchanges=(
            ExchangeOffer(
                id="travel-money",
                source_account_id="a-cash",
                source_clearing_account_id="b-cash",
                target_clearing_account_id="b-far",
                target_account_id="a-far",
                source_amount=100,
                target_amount=50,
            ),
        ),
        profiles=(
            FinancialProfile(
                actor_id="a",
                wealth="average",
                status=0,
                living_account_id="a-cash",
                cost_of_living_account_id="b-cash",
                monthly_cost=600,
                dependent_cost=100,
                unpaid_consequence="Reduced living standard",
            ),
        ),
        jobs=(
            JobRule(
                id="dock-clerk",
                activity_id="dock-work",
                employer_account_id="b-cash",
                worker_account_id="a-cash",
                prerequisite_id="skill:observation",
                minimum_level=1,
                roll_target_id="skill:observation",
                monthly_pay=1_000,
                period_seconds=100,
                population=10_000,
                typical_status=0,
            ),
        ),
        hirelings=(
            HirelingRule(
                id="guide",
                employer_id="a",
                hireling_id="b",
                employer_account_id="a-cash",
                hireling_account_id="b-cash",
                search_target_id="attribute:iq",
                competence=12,
                normal_pay=100,
                offered_pay=120,
                private_motive="Owes the smugglers a secret debt",
            ),
        ),
        loyalty=(
            LoyaltyCircumstance(
                id="danger",
                hireling_rule_id="guide",
                kind="danger",
                loyalty_change_on_failure=-1,
            ),
        ),
    )
    return ActionEngine(
        base.reviewer,
        base.resources,
        ActionRules(
            id="actions",
            version=1,
            checks=base.rules.checks,
            consumables=base.rules.consumables,
            administration=administration,
            economics=economics,
        ),
    )


def state(reducer: ActionEngine) -> PlayState:
    current = seed(reducer)
    resources = current.resources.model_copy(
        update={
            "owners": current.resources.owners + (Owner(actor_id="b", capacity=100),),
            "items": current.resources.items
            + (Item(id="sword-b", definition_id="sword", owner_id="b"),),
        }
    )
    economics = EconomicsState(
        accounts=(
            MoneyAccount(
                id="a-cash",
                owner_id="a",
                currency_id="dock-dollar",
                location_id="dock",
                portable=True,
                balance=2_000,
            ),
            MoneyAccount(
                id="b-cash",
                owner_id="b",
                currency_id="dock-dollar",
                location_id="dock",
                portable=True,
                balance=5_000,
            ),
            MoneyAccount(
                id="a-far",
                owner_id="a",
                currency_id="far-credit",
                location_id="far",
                portable=True,
                balance=0,
            ),
            MoneyAccount(
                id="b-far",
                owner_id="b",
                currency_id="far-credit",
                location_id="far",
                portable=False,
                balance=500,
            ),
        )
    )
    result = current.model_copy(update={"resources": resources, "economics": economics})
    reducer.validate(result)
    return result


def apply(
    reducer: ActionEngine,
    current: PlayState,
    command: EconomicCommand,
    dice: tuple[int, ...] = (1, 1, 1),
) -> tuple[PlayState, EconomicsOutcome]:
    updated, outcome = reducer.campaign.apply(current, command, rng=RecordedDice(dice), system=True)
    return updated, cast(EconomicsOutcome, outcome)


def balances(current: PlayState) -> dict[str, int]:
    return {account.id: account.balance for account in current.economics.accounts}


def test_trade_conserves_money_and_item_across_retry_and_conflict() -> None:
    reducer = configured()
    before = state(reducer)
    command = ExecuteTrade(id="trade", actor_id="a", expected_revision=0, offer_id="buy-sword")
    bought, outcome = apply(reducer, before, command)
    assert outcome.status == "purchase"
    assert balances(bought) == {
        "a-cash": 1_700,
        "b-cash": 5_300,
        "a-far": 0,
        "b-far": 500,
    }
    assert sum(balances(before).values()) == sum(balances(bought).values())
    assert next(item for item in bought.resources.items if item.id == "sword-b").owner_id == "a"
    assert bought.revision == 2  # item transfer and economics receipt share one persisted command
    replayed, replay_outcome = apply(reducer, bought, command, ())
    assert replayed == bought and replay_outcome == outcome
    with pytest.raises(ConflictError, match="reused"):
        apply(
            reducer,
            bought,
            ExecuteTrade(id="trade", actor_id="a", expected_revision=0, offer_id="missing"),
            (),
        )


def test_cross_world_exchange_conserves_each_currency_and_survives_reload() -> None:
    reducer = configured()
    before = state(reducer)
    exchanged, outcome = apply(
        reducer,
        before,
        ExecuteExchange(id="exchange", actor_id="a", expected_revision=0, offer_id="travel-money"),
        (),
    )
    assert outcome.status == "exchanged" and outcome.amount == 50
    assert balances(exchanged) == {
        "a-cash": 1_900,
        "b-cash": 5_100,
        "a-far": 50,
        "b-far": 450,
    }
    reloaded = PlayState.model_validate_json(exchanged.model_dump_json())
    assert reloaded == exchanged
    reducer.validate(reloaded)


def test_job_income_requires_one_authored_time_use_interval() -> None:
    reducer = configured()
    current = state(reducer).model_copy(
        update={
            "economics": state(reducer).economics.model_copy(
                update={
                    "employment": (
                        Employment(
                            id="employment:a:dock-clerk",
                            actor_id="a",
                            job_id="dock-clerk",
                            monthly_pay=1_000,
                        ),
                    )
                }
            )
        }
    )
    wrong, _ = reducer.campaign.apply(
        current,
        SettleTimeUse(
            id="study",
            actor_id="a",
            expected_revision=0,
            allocations=(ActivityAllocation(activity_id="unpaid-study", start=0, end=100),),
        ),
        rng=RecordedDice(()),
        system=True,
    )
    with pytest.raises(ValidationError, match="authored work interval"):
        apply(
            reducer,
            wrong,
            SettleJob(
                id="invalid-pay",
                actor_id="a",
                expected_revision=wrong.revision,
                job_id="dock-clerk",
                time_use_id="study",
            ),
        )

    current = state(reducer).model_copy(update={"economics": current.economics})
    worked, _ = reducer.campaign.apply(
        current,
        SettleTimeUse(
            id="work",
            actor_id="a",
            expected_revision=0,
            allocations=(ActivityAllocation(activity_id="dock-work", start=0, end=100),),
        ),
        rng=RecordedDice(()),
        system=True,
    )
    assert worked.administration.time_use[-1].credits[0].seconds == 25
    paid, outcome = apply(
        reducer,
        worked,
        SettleJob(
            id="pay",
            actor_id="a",
            expected_revision=worked.revision,
            job_id="dock-clerk",
            time_use_id="work",
        ),
    )
    assert outcome.amount == 1_000 and balances(paid)["a-cash"] == 3_000
    with pytest.raises(ConflictError, match="already paid"):
        apply(
            reducer,
            paid,
            SettleJob(
                id="double-pay",
                actor_id="a",
                expected_revision=paid.revision,
                job_id="dock-clerk",
                time_use_id="work",
            ),
        )


def test_hireling_loyalty_is_persisted_without_private_event_leakage() -> None:
    reducer = configured()
    hired, outcome = apply(
        reducer,
        state(reducer),
        FindHireling(id="find-guide", actor_id="a", expected_revision=0, hireling_rule_id="guide"),
        (1, 1, 1, 3, 3, 3),
    )
    assert outcome.status == "hireling-found"
    contract = hired.economics.hirelings[0]
    assert contract.loyalty == 9 and contract.private_motive
    assert all(contract.private_motive not in event.kind for event in hired.resources.events)
    checked, result = apply(
        reducer,
        hired,
        CheckLoyalty(
            id="danger-check",
            actor_id="a",
            expected_revision=hired.revision,
            contract_id=contract.id,
            circumstance_id="danger",
        ),
        (6, 6, 6),
    )
    assert result.status == "self-interest"
    assert checked.economics.hirelings[0].loyalty == 8
    assert checked.economics.loyalty_checks[-1].passed is False
    assert all(contract.private_motive not in event.kind for event in checked.resources.events)


def test_cost_of_living_includes_authored_dependents_and_is_atomic() -> None:
    reducer = configured()
    paid, result = apply(
        reducer,
        state(reducer),
        PayCostOfLiving(
            id="living", actor_id="a", expected_revision=0, period_id="campaign-month:1"
        ),
        (),
    )
    assert result.amount == 700
    assert balances(paid) == {
        "a-cash": 1_300,
        "b-cash": 5_700,
        "a-far": 0,
        "b-far": 500,
    }
    replayed, _ = apply(
        reducer,
        paid,
        PayCostOfLiving(
            id="living", actor_id="a", expected_revision=0, period_id="campaign-month:1"
        ),
        (),
    )
    assert replayed == paid
    with pytest.raises(ConflictError, match="already settled"):
        apply(
            reducer,
            paid,
            PayCostOfLiving(
                id="living-again",
                actor_id="a",
                expected_revision=paid.revision,
                period_id="campaign-month:1",
            ),
            (),
        )

    reduced, consequence = apply(
        reducer,
        paid,
        PayCostOfLiving(
            id="living-unpaid",
            actor_id="a",
            expected_revision=paid.revision,
            period_id="campaign-month:2",
            maintain_status=False,
        ),
        (),
    )
    assert balances(reduced) == balances(paid)
    assert consequence.consequence == "Reduced living standard"
    assert reduced.economics.living_periods[-1].paid is False
