"""Authoritative money, employment and hireling procedures (B513-B519)."""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Callable, Mapping
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter, model_validator

from wayfarer.engine.rules.checks import Modifier, Outcome, RandomSource
from wayfarer.engine.rules.economics import (
    job_search_adjustment,
    loyalty_pay_bonus,
    monthly_income,
)
from wayfarer.engine.rules.gurps_checks import success_roll
from wayfarer.engine.rules.social.gurps_social import ReactionModifier, reaction_roll
from wayfarer.engine.rules.traits.background import BackgroundTraits
from wayfarer.engine.simulation.campaign.administration import (
    AdministrationRules,
    AdministrationState,
)
from wayfarer.engine.simulation.resources import Receipt, ResourceEvent, ResourceState
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record


class CurrencyRule(Record):
    id: Id
    world_id: Id
    name: str = Field(min_length=1, max_length=100)


class PriceContext(Record):
    id: Id
    world_id: Id
    currency_id: Id
    label: str = Field(min_length=1, max_length=200)


class TradeOffer(Record):
    id: Id
    context_id: Id
    kind: Literal["purchase", "sale", "loot"]
    payer_account_id: Id
    payee_account_id: Id
    item_id: Id
    quantity: int = Field(ge=1)
    price: int = Field(ge=0)
    new_item_id: Id | None = None


class ExchangeOffer(Record):
    id: Id
    source_account_id: Id
    source_clearing_account_id: Id
    target_clearing_account_id: Id
    target_account_id: Id
    source_amount: int = Field(gt=0)
    target_amount: int = Field(gt=0)
    carried_only: bool = True


class FinancialProfile(Record):
    actor_id: Id
    wealth: str
    status: int = Field(ge=-2, le=8)
    rank_definition_ids: tuple[Id, ...] = ()
    relationship_definition_ids: tuple[Id, ...] = ()
    living_account_id: Id
    cost_of_living_account_id: Id
    monthly_cost: int = Field(ge=0)
    dependent_cost: int = Field(default=0, ge=0)
    unpaid_consequence: str = Field(min_length=1, max_length=1000)


class JobRule(Record):
    id: Id
    activity_id: Id
    employer_account_id: Id
    worker_account_id: Id
    prerequisite_id: Id
    minimum_level: int
    roll_target_id: Id
    monthly_pay: int = Field(ge=0)
    period_seconds: int = Field(gt=0)
    population: int = Field(gt=0)
    typical_status: int = Field(ge=-2, le=8)
    simultaneous_jobs: int = Field(default=1, ge=1)
    advertising_steps: int = Field(default=0, ge=0)
    advertising_amount: int = Field(default=0, ge=0)
    advertising_account_id: Id | None = None
    variable_income: bool = False
    critical_failure: Literal["no-pay", "pay-cut", "job-loss", "injury", "arrest"] = "no-pay"
    employment_kind: Literal["employed"] = "employed"

    @model_validator(mode="after")
    def valid_advertising(self) -> JobRule:
        if (self.advertising_amount > 0) != (self.advertising_account_id is not None):
            raise ValueError("Advertising cost and destination must be authored together")
        return self


class HirelingRule(Record):
    id: Id
    employer_id: Id
    hireling_id: Id
    employer_account_id: Id
    hireling_account_id: Id
    search_target_id: Id
    competence: int
    normal_pay: int = Field(gt=0)
    offered_pay: int = Field(ge=0)
    search_modifiers: tuple[Modifier, ...] = ()
    loyalty_modifiers: tuple[ReactionModifier, ...] = ()
    private_motive: str = Field(default="", max_length=1000)
    employment_kind: Literal["hireling"] = "hireling"


class LoyaltyCircumstance(Record):
    id: Id
    hireling_rule_id: Id
    kind: Literal["danger", "temptation", "rescue", "service", "competence"]
    modifiers: tuple[Modifier, ...] = ()
    loyalty_change_on_success: int = Field(default=0, ge=-10, le=10)
    loyalty_change_on_failure: int = Field(default=0, ge=-10, le=10)


class EconomicsRules(Record):
    id: Id
    version: int = Field(ge=1)
    profile_id: Literal["gurps-basic-set-4e-2004"] = "gurps-basic-set-4e-2004"
    currencies: tuple[CurrencyRule, ...] = ()
    price_contexts: tuple[PriceContext, ...] = ()
    trades: tuple[TradeOffer, ...] = ()
    exchanges: tuple[ExchangeOffer, ...] = ()
    profiles: tuple[FinancialProfile, ...] = ()
    jobs: tuple[JobRule, ...] = ()
    hirelings: tuple[HirelingRule, ...] = ()
    loyalty: tuple[LoyaltyCircumstance, ...] = ()

    @model_validator(mode="after")
    def unique_rules(self) -> EconomicsRules:
        groups = (
            self.currencies,
            self.price_contexts,
            self.trades,
            self.exchanges,
            self.profiles,
            self.jobs,
            self.hirelings,
            self.loyalty,
        )
        for values in groups:
            identifiers = tuple(
                value.actor_id if isinstance(value, FinancialProfile) else value.id
                for value in values
            )
            if len(set(identifiers)) != len(identifiers):
                raise ValueError("Duplicate economics rule ID")
        currencies = {value.id for value in self.currencies}
        contexts = {value.id for value in self.price_contexts}
        if any(value.currency_id not in currencies for value in self.price_contexts):
            raise ValueError("Price context references an unknown currency")
        if any(value.context_id not in contexts for value in self.trades):
            raise ValueError("Trade references an unknown price context")
        hirelings = {value.id for value in self.hirelings}
        if any(value.hireling_rule_id not in hirelings for value in self.loyalty):
            raise ValueError("Loyalty circumstance references an unknown hireling")
        return self


class MoneyAccount(Record):
    id: Id
    owner_id: Id
    currency_id: Id
    location_id: Id
    portable: bool = False
    balance: int = Field(ge=0)


class EconomicEntry(Record):
    id: Id
    kind: Literal["trade", "exchange", "job", "living", "hireling-pay"]
    account_deltas: tuple[tuple[Id, int], ...]
    at: int = Field(ge=0)
    revision: int = Field(ge=1)


class JobSearchAttempt(Record):
    id: Id
    actor_id: Id
    job_id: Id
    at: int = Field(ge=0)
    succeeded: bool


class Employment(Record):
    id: Id
    actor_id: Id
    job_id: Id
    monthly_pay: int = Field(ge=0)
    active: bool = True


class JobPeriod(Record):
    id: Id
    employment_id: Id
    time_use_id: Id
    income: int = Field(ge=0)
    consequence: str = ""


class LivingPeriod(Record):
    id: Id
    actor_id: Id
    period_id: Id
    paid: bool
    amount: int = Field(ge=0)
    consequence: str = ""


class HirelingContract(Record):
    id: Id
    rule_id: Id
    employer_id: Id
    hireling_id: Id
    competence: int
    loyalty: int
    private_motive: str = ""
    paid_periods: int = Field(default=0, ge=0)
    active: bool = True


class LoyaltyCheck(Record):
    id: Id
    contract_id: Id
    circumstance_id: Id
    passed: bool
    loyalty_after: int


class EconomicsState(Record):
    accounts: tuple[MoneyAccount, ...] = ()
    entries: tuple[EconomicEntry, ...] = ()
    searches: tuple[JobSearchAttempt, ...] = ()
    employment: tuple[Employment, ...] = ()
    periods: tuple[JobPeriod, ...] = ()
    living_periods: tuple[LivingPeriod, ...] = ()
    hirelings: tuple[HirelingContract, ...] = ()
    loyalty_checks: tuple[LoyaltyCheck, ...] = ()


class EconomicsCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class ExecuteTrade(EconomicsCommand):
    kind: Literal["trade"] = "trade"
    offer_id: Id


class ExecuteExchange(EconomicsCommand):
    kind: Literal["exchange"] = "exchange"
    offer_id: Id


class SearchForJob(EconomicsCommand):
    kind: Literal["job-search"] = "job-search"
    job_id: Id


class SettleJob(EconomicsCommand):
    kind: Literal["job-settlement"] = "job-settlement"
    job_id: Id
    time_use_id: Id


class PayCostOfLiving(EconomicsCommand):
    kind: Literal["cost-of-living"] = "cost-of-living"
    period_id: Id
    maintain_status: bool = True


class FindHireling(EconomicsCommand):
    kind: Literal["hire"] = "hire"
    hireling_rule_id: Id


class PayHireling(EconomicsCommand):
    kind: Literal["hireling-pay"] = "hireling-pay"
    contract_id: Id


class CheckLoyalty(EconomicsCommand):
    kind: Literal["loyalty"] = "loyalty"
    contract_id: Id
    circumstance_id: Id


EconomicCommand = Annotated[
    ExecuteTrade
    | ExecuteExchange
    | SearchForJob
    | SettleJob
    | PayCostOfLiving
    | FindHireling
    | PayHireling
    | CheckLoyalty,
    Field(discriminator="kind"),
]
ECONOMIC_COMMAND_ADAPTER: TypeAdapter[EconomicCommand] = TypeAdapter(EconomicCommand)


class EconomicsOutcome(Record):
    status: str
    amount: int = 0
    consequence: str = ""
    private: tuple[str, ...] = ()


def _rule[RuleT](values: tuple[RuleT, ...], identifier: str, label: str) -> RuleT:
    result = next(
        (
            value
            for value in values
            if getattr(value, "id", getattr(value, "actor_id", None)) == identifier
        ),
        None,
    )
    if result is None:
        raise ValidationError(f"Unknown authored {label}")
    return result


def _digest(command: EconomicCommand) -> str:
    return hashlib.sha256(command.model_dump_json().encode()).hexdigest()


def _prior(resources: ResourceState, command: EconomicCommand) -> EconomicsOutcome | None:
    receipt = next((item for item in resources.receipts if item.command_id == command.id), None)
    if receipt is None:
        return None
    if receipt.digest != _digest(command):
        raise ConflictError("Economics command ID reused")
    event = next(item for item in resources.events if item.id == "economics:" + command.id)
    return EconomicsOutcome.model_validate_json(event.kind)


def _finish(
    resources: ResourceState, command: EconomicCommand, outcome: EconomicsOutcome
) -> ResourceState:
    return resources.model_copy(
        update={
            "revision": resources.revision + 1,
            "receipts": resources.receipts
            + (Receipt(command_id=command.id, digest=_digest(command)),),
            # Only the public outcome enters the event stream. Hireling motives and
            # loyalty traces remain in the authoritative private checkpoint.
            "events": resources.events
            + (
                ResourceEvent(
                    id="economics:" + command.id,
                    at=resources.game_time,
                    kind=outcome.model_dump_json(),
                    target_id=command.actor_id,
                ),
            ),
        }
    )


def _move_money(
    state: EconomicsState, moves: tuple[tuple[str, str, int], ...]
) -> tuple[EconomicsState, tuple[tuple[str, int], ...]]:
    accounts = {account.id: account for account in state.accounts}
    deltas: dict[str, int] = defaultdict(int)
    currency_totals: dict[str, int] = defaultdict(int)
    for source_id, target_id, amount in moves:
        if amount < 0 or source_id == target_id:
            raise ValidationError("Money movement requires distinct accounts and nonnegative value")
        source, target = accounts.get(source_id), accounts.get(target_id)
        if source is None or target is None or source.currency_id != target.currency_id:
            raise ValidationError("Money movement requires known accounts in one currency")
        deltas[source_id] -= amount
        deltas[target_id] += amount
        currency_totals[source.currency_id] += -amount + amount
    if any(value != 0 for value in currency_totals.values()):
        raise ValidationError("Money movement does not conserve currency")
    if any(accounts[key].balance + value < 0 for key, value in deltas.items()):
        raise ConflictError("Insufficient funds")
    updated = tuple(
        account.model_copy(update={"balance": account.balance + deltas.get(account.id, 0)})
        for account in state.accounts
    )
    return state.model_copy(update={"accounts": updated}), tuple(sorted(deltas.items()))


def _target(values: Mapping[str, Decimal], identifier: str) -> int:
    value = values.get(identifier)
    if value is None or not value.is_finite() or value != value.to_integral_value():
        raise ValidationError("Economic procedure requires an integral compiled target")
    return int(value)


def _entry(
    state: EconomicsState,
    command: EconomicCommand,
    kind: Literal["trade", "exchange", "job", "living", "hireling-pay"],
    deltas: tuple[tuple[str, int], ...],
    resources: ResourceState,
) -> EconomicsState:
    return state.model_copy(
        update={
            "entries": state.entries
            + (
                EconomicEntry(
                    id=command.id,
                    kind=kind,
                    account_deltas=deltas,
                    at=resources.game_time,
                    revision=resources.revision + 1,
                ),
            )
        }
    )


def _execute_trade(
    state: EconomicsState,
    resources: ResourceState,
    command: ExecuteTrade,
    rules: EconomicsRules,
    transfer: Callable[[ResourceState, str, int, str, str | None, str], ResourceState],
) -> tuple[EconomicsState, ResourceState, EconomicsOutcome, tuple[tuple[str, int], ...]]:
    offer = _rule(rules.trades, command.offer_id, "trade offer")
    accounts = {account.id: account for account in state.accounts}
    payer, payee = accounts.get(offer.payer_account_id), accounts.get(offer.payee_account_id)
    item = next((value for value in resources.items if value.id == offer.item_id), None)
    if payer is None or payee is None or item is None or item.owner_id != payee.owner_id:
        raise ConflictError("Trade parties no longer own the offered assets")
    if payer.owner_id != command.actor_id:
        raise ValidationError("Only the authored payer may execute this trade")
    state, deltas = _move_money(
        state, ((offer.payer_account_id, offer.payee_account_id, offer.price),)
    )
    resources = transfer(
        resources, offer.item_id, offer.quantity, payer.owner_id, offer.new_item_id, command.id
    )
    return state, resources, EconomicsOutcome(status=offer.kind, amount=offer.price), deltas


def _execute_exchange(
    state: EconomicsState,
    command: ExecuteExchange,
    rules: EconomicsRules,
) -> tuple[EconomicsState, EconomicsOutcome, tuple[tuple[str, int], ...]]:
    offer = _rule(rules.exchanges, command.offer_id, "exchange offer")
    accounts = {account.id: account for account in state.accounts}
    source, target = accounts.get(offer.source_account_id), accounts.get(offer.target_account_id)
    if not set((offer.source_clearing_account_id, offer.target_clearing_account_id)) <= set(
        accounts
    ):
        raise ValidationError("Exchange references an unknown clearing account")
    if (
        source is None
        or target is None
        or source.owner_id != command.actor_id
        or target.owner_id != command.actor_id
    ):
        raise ValidationError("Exchange accounts do not belong to the actor")
    if offer.carried_only and (not source.portable or not target.portable):
        raise ValidationError("Cross-world exchange is limited to carried wealth")
    state, deltas = _move_money(
        state,
        (
            (offer.source_account_id, offer.source_clearing_account_id, offer.source_amount),
            (offer.target_clearing_account_id, offer.target_account_id, offer.target_amount),
        ),
    )
    return state, EconomicsOutcome(status="exchanged", amount=offer.target_amount), deltas


def _search_for_job(
    state: EconomicsState,
    resources: ResourceState,
    command: SearchForJob,
    rules: EconomicsRules,
    build_values: Mapping[str, Decimal],
    purchased_ids: frozenset[str],
    rng: RandomSource,
) -> tuple[EconomicsState, EconomicsOutcome]:
    job = _rule(rules.jobs, command.job_id, "job")
    worker = _rule(state.accounts, job.worker_account_id, "worker account")
    if worker.owner_id != command.actor_id:
        raise ValidationError("Job worker account does not belong to the actor")
    if job.prerequisite_id not in purchased_ids:
        raise ValidationError("Job prerequisite requires purchased skill, not a default")
    level = _target(build_values, job.prerequisite_id)
    if level < job.minimum_level:
        raise ValidationError("Job prerequisite level is not met")
    previous = [
        attempt
        for attempt in state.searches
        if attempt.actor_id == command.actor_id and attempt.job_id == job.id
    ]
    if previous and resources.game_time - previous[-1].at < 7 * 24 * 60 * 60:
        raise ConflictError("A job search may be attempted only once per week")
    if any(
        employment.actor_id == command.actor_id
        and employment.job_id == job.id
        and employment.active
        for employment in state.employment
    ):
        raise ConflictError("Actor already holds this job")
    adjustment = job_search_adjustment(
        population=job.population,
        overqualification=level - job.minimum_level,
        typical_status=job.typical_status,
        simultaneous_jobs=job.simultaneous_jobs,
        advertising_steps=job.advertising_steps,
        lazy="trait:laziness" in purchased_ids,
    )
    if job.advertising_amount:
        assert job.advertising_account_id is not None
        state, _ = _move_money(
            state,
            ((job.worker_account_id, job.advertising_account_id, job.advertising_amount),),
        )
    trace = success_roll(
        rules.profile_id, _target(build_values, "attribute:iq") + adjustment, rng=rng
    )
    employment = state.employment
    if trace.outcome.succeeded:
        employment += (
            Employment(
                id=f"employment:{command.actor_id}:{job.id}",
                actor_id=command.actor_id,
                job_id=job.id,
                monthly_pay=job.monthly_pay,
            ),
        )
    state = state.model_copy(
        update={
            "searches": state.searches
            + (
                JobSearchAttempt(
                    id=command.id,
                    actor_id=command.actor_id,
                    job_id=job.id,
                    at=resources.game_time,
                    succeeded=trace.outcome.succeeded,
                ),
            ),
            "employment": employment,
        }
    )
    return state, EconomicsOutcome(status="employed" if trace.outcome.succeeded else "not-found")


def _settle_job(
    state: EconomicsState,
    administration: AdministrationState,
    resources: ResourceState,
    command: SettleJob,
    rules: EconomicsRules,
    build_values: Mapping[str, Decimal],
    rng: RandomSource,
) -> tuple[EconomicsState, EconomicsOutcome, tuple[tuple[str, int], ...]]:
    job = _rule(rules.jobs, command.job_id, "job")
    worker = _rule(state.accounts, job.worker_account_id, "worker account")
    if worker.owner_id != command.actor_id:
        raise ValidationError("Job worker account does not belong to the actor")
    employment = next(
        (
            value
            for value in state.employment
            if value.actor_id == command.actor_id and value.job_id == job.id and value.active
        ),
        None,
    )
    time_use = next(
        (
            value
            for value in administration.time_use
            if value.id == command.time_use_id and value.actor_id == command.actor_id
        ),
        None,
    )
    if employment is None or time_use is None:
        raise ValidationError("Job settlement requires active employment and settled Time Use")
    if any(value.time_use_id == time_use.id for value in state.periods):
        raise ConflictError("Time Use interval already paid")
    worked = sum(
        value.end - value.start
        for value in time_use.allocations
        if value.activity_id == job.activity_id
    )
    if worked < job.period_seconds:
        raise ValidationError("Time Use does not contain the authored work interval")
    trace = success_roll(rules.profile_id, _target(build_values, job.roll_target_id), rng=rng)
    income = monthly_income(
        employment.monthly_pay, trace.outcome, trace.margin, variable=job.variable_income
    )
    state, deltas = _move_money(state, ((job.employer_account_id, job.worker_account_id, income),))
    consequence = job.critical_failure if trace.outcome is Outcome.CRITICAL_FAILURE else ""
    pay = (
        employment.monthly_pay * 9 // 10
        if consequence == "pay-cut"
        else employment.monthly_pay * 11 // 10
        if trace.outcome is Outcome.CRITICAL_SUCCESS and not job.variable_income
        else employment.monthly_pay
    )
    update = employment.model_copy(
        update={"monthly_pay": pay, "active": employment.active and consequence != "job-loss"}
    )
    state = state.model_copy(
        update={
            "employment": tuple(
                update if value.id == employment.id else value for value in state.employment
            ),
            "periods": state.periods
            + (
                JobPeriod(
                    id=command.id,
                    employment_id=employment.id,
                    time_use_id=time_use.id,
                    income=income,
                    consequence=consequence,
                ),
            ),
        }
    )
    return state, EconomicsOutcome(status="paid", amount=income, consequence=consequence), deltas


def _find_hireling(
    state: EconomicsState,
    command: FindHireling,
    rules: EconomicsRules,
    build_values: Mapping[str, Decimal],
    rng: RandomSource,
) -> tuple[EconomicsState, EconomicsOutcome]:
    rule = _rule(rules.hirelings, command.hireling_rule_id, "hireling")
    if command.actor_id != rule.employer_id:
        raise ValidationError("Only the authored employer may recruit this hireling")
    if any(value.rule_id == rule.id and value.active for value in state.hirelings):
        raise ConflictError("Hireling already has an active contract")
    trace = success_roll(
        rules.profile_id,
        _target(build_values, rule.search_target_id),
        rule.search_modifiers,
        rng=rng,
    )
    if trace.outcome.succeeded:
        loyalty = reaction_roll(rules.profile_id, rule.loyalty_modifiers, rng=rng).total
        state = state.model_copy(
            update={
                "hirelings": state.hirelings
                + (
                    HirelingContract(
                        id=f"hireling:{rule.id}",
                        rule_id=rule.id,
                        employer_id=rule.employer_id,
                        hireling_id=rule.hireling_id,
                        competence=rule.competence,
                        loyalty=loyalty,
                        private_motive=rule.private_motive,
                    ),
                )
            }
        )
    return state, EconomicsOutcome(
        status="hireling-found" if trace.outcome.succeeded else "not-found"
    )


def _check_loyalty(
    state: EconomicsState,
    command: CheckLoyalty,
    rules: EconomicsRules,
    rng: RandomSource,
) -> tuple[EconomicsState, EconomicsOutcome]:
    contract = _rule(state.hirelings, command.contract_id, "hireling contract")
    circumstance = _rule(rules.loyalty, command.circumstance_id, "loyalty circumstance")
    rule = _rule(rules.hirelings, contract.rule_id, "hireling")
    if command.actor_id != contract.employer_id:
        raise ValidationError("Only the employer may make this loyalty check")
    if circumstance.hireling_rule_id != rule.id or not contract.active:
        raise ValidationError("Loyalty circumstance does not apply to this contract")
    target = contract.loyalty + loyalty_pay_bonus(rule.offered_pay, rule.normal_pay)
    trace = (
        None
        if target >= 20
        else success_roll(rules.profile_id, target, circumstance.modifiers, rng=rng)
    )
    passed = trace is None or trace.outcome.succeeded
    change = (
        circumstance.loyalty_change_on_success if passed else circumstance.loyalty_change_on_failure
    )
    update = contract.model_copy(update={"loyalty": contract.loyalty + change})
    state = state.model_copy(
        update={
            "hirelings": tuple(
                update if value.id == contract.id else value for value in state.hirelings
            ),
            "loyalty_checks": state.loyalty_checks
            + (
                LoyaltyCheck(
                    id=command.id,
                    contract_id=contract.id,
                    circumstance_id=circumstance.id,
                    passed=passed,
                    loyalty_after=update.loyalty,
                ),
            ),
        }
    )
    return state, EconomicsOutcome(status="loyal" if passed else "self-interest")


def apply_economics(
    state: EconomicsState,
    resources: ResourceState,
    administration: AdministrationState,
    command: EconomicCommand,
    rules: EconomicsRules,
    *,
    world: World,
    build_values: Mapping[str, Decimal],
    purchased_ids: frozenset[str],
    rng: RandomSource,
    transfer: Callable[[ResourceState, str, int, str, str | None, str], ResourceState],
    system: bool = False,
) -> tuple[EconomicsState, ResourceState, EconomicsOutcome]:
    """Apply one trusted campaign-economics command."""
    if not system:
        raise ValidationError("Economics procedures require engine authority")
    prior = _prior(resources, command)
    if prior is not None:
        return state, resources, prior
    if command.expected_revision != resources.revision:
        raise ConflictError("Economics revision changed")
    if command.actor_id not in {entity.id for entity in world.entities}:
        raise ValidationError("Economics actor is not in the world")

    deltas: tuple[tuple[str, int], ...] = ()
    entry_kind: Literal["trade", "exchange", "job", "living", "hireling-pay"] | None = None
    if isinstance(command, ExecuteTrade):
        state, resources, outcome, deltas = _execute_trade(
            state, resources, command, rules, transfer
        )
        entry_kind = "trade"
    elif isinstance(command, ExecuteExchange):
        state, outcome, deltas = _execute_exchange(state, command, rules)
        entry_kind = "exchange"
    elif isinstance(command, SearchForJob):
        state, outcome = _search_for_job(
            state, resources, command, rules, build_values, purchased_ids, rng
        )
    elif isinstance(command, SettleJob):
        state, outcome, deltas = _settle_job(
            state, administration, resources, command, rules, build_values, rng
        )
        entry_kind = "job"
    elif isinstance(command, PayCostOfLiving):
        profile = _rule(rules.profiles, command.actor_id, "financial profile")
        amount = profile.monthly_cost + profile.dependent_cost
        if any(
            value.actor_id == command.actor_id and value.period_id == command.period_id
            for value in state.living_periods
        ):
            raise ConflictError("Cost-of-living period already settled")
        if command.maintain_status:
            state, deltas = _move_money(
                state,
                ((profile.living_account_id, profile.cost_of_living_account_id, amount),),
            )
            outcome = EconomicsOutcome(status="living-cost-paid", amount=amount)
        else:
            amount = 0
            outcome = EconomicsOutcome(
                status="living-standard-reduced", consequence=profile.unpaid_consequence
            )
        state = state.model_copy(
            update={
                "living_periods": state.living_periods
                + (
                    LivingPeriod(
                        id=command.id,
                        actor_id=command.actor_id,
                        period_id=command.period_id,
                        paid=command.maintain_status,
                        amount=amount,
                        consequence=outcome.consequence,
                    ),
                )
            }
        )
        entry_kind = "living"
    elif isinstance(command, FindHireling):
        state, outcome = _find_hireling(state, command, rules, build_values, rng)
    elif isinstance(command, PayHireling):
        paid_contract = _rule(state.hirelings, command.contract_id, "hireling contract")
        pay_rule = _rule(rules.hirelings, paid_contract.rule_id, "hireling")
        if command.actor_id != paid_contract.employer_id or not paid_contract.active:
            raise ValidationError("Hireling payment requires the active employer")
        state, deltas = _move_money(
            state,
            ((pay_rule.employer_account_id, pay_rule.hireling_account_id, pay_rule.offered_pay),),
        )
        paid_update = paid_contract.model_copy(
            update={"paid_periods": paid_contract.paid_periods + 1}
        )
        state = state.model_copy(
            update={
                "hirelings": tuple(
                    paid_update if value.id == paid_contract.id else value
                    for value in state.hirelings
                )
            }
        )
        outcome = EconomicsOutcome(status="hireling-paid", amount=pay_rule.offered_pay)
        entry_kind = "hireling-pay"
    else:
        state, outcome = _check_loyalty(state, command, rules, rng)

    if entry_kind is not None:
        state = _entry(state, command, entry_kind, deltas, resources)
    resources = _finish(resources, command, outcome)
    return state, resources, outcome


def _validate_market_bindings(
    rules: EconomicsRules,
    accounts: Mapping[str, MoneyAccount],
    locations: set[str],
) -> None:
    if any(value.world_id not in locations for value in rules.currencies):
        raise ValidationError("Currency references an unknown world location")
    currency_worlds = {value.id: value.world_id for value in rules.currencies}
    if any(
        value.world_id not in locations or currency_worlds.get(value.currency_id) != value.world_id
        for value in rules.price_contexts
    ):
        raise ValidationError("Price context disagrees with its currency world")
    context_currencies = {value.id: value.currency_id for value in rules.price_contexts}
    if any(
        accounts[value.payer_account_id].currency_id != accounts[value.payee_account_id].currency_id
        or accounts[value.payer_account_id].currency_id != context_currencies[value.context_id]
        for value in rules.trades
    ):
        raise ValidationError("Trade accounts disagree with the authored price context")
    if any(
        accounts[value.source_account_id].currency_id
        != accounts[value.source_clearing_account_id].currency_id
        or accounts[value.target_account_id].currency_id
        != accounts[value.target_clearing_account_id].currency_id
        for value in rules.exchanges
    ):
        raise ValidationError("Exchange clearing accounts disagree with their currency")


def _validate_work_bindings(
    rules: EconomicsRules,
    accounts: Mapping[str, MoneyAccount],
    administration_rules: AdministrationRules | None,
) -> None:
    activities = (
        {value.id: value for value in administration_rules.activities}
        if administration_rules
        else {}
    )
    if any(
        job.activity_id not in activities or activities[job.activity_id].kind != "job"
        for job in rules.jobs
    ):
        raise ValidationError("Job requires an authored Time Use activity")
    if any(
        accounts[value.employer_account_id].owner_id != value.employer_id
        or accounts[value.hireling_account_id].owner_id != value.hireling_id
        for value in rules.hirelings
    ):
        raise ValidationError("Hireling accounts disagree with contract parties")


def validate_economics(
    rules: EconomicsRules | None,
    state: EconomicsState,
    world: World,
    resources: ResourceState,
    administration_rules: AdministrationRules | None,
    administration: AdministrationState,
    backgrounds: Mapping[str, BackgroundTraits],
    purchases: Mapping[str, frozenset[str]],
) -> None:
    if rules is None:
        if state != EconomicsState():
            raise ValidationError("Economics state requires authored rules")
        return
    entities = {entity.id for entity in world.entities}
    locations = {entity.id for entity in world.entities if entity.kind.value == "location"}
    currencies = {value.id for value in rules.currencies}
    accounts = {value.id: value for value in state.accounts}
    if len(accounts) != len(state.accounts):
        raise ValidationError("Duplicate money account")
    if any(
        value.owner_id not in entities
        or value.location_id not in locations
        or value.currency_id not in currencies
        for value in state.accounts
    ):
        raise ValidationError("Money account references unknown campaign data")
    for values, label in (
        (state.entries, "economics entry"),
        (state.searches, "job search"),
        (state.employment, "employment"),
        (state.periods, "job period"),
        (state.living_periods, "living period"),
        (state.hirelings, "hireling contract"),
        (state.loyalty_checks, "loyalty check"),
    ):
        if len({value.id for value in values}) != len(values):
            raise ValidationError(f"Duplicate {label}")
    referenced_accounts: list[str] = []
    for trade_offer in rules.trades:
        referenced_accounts.extend((trade_offer.payer_account_id, trade_offer.payee_account_id))
    for exchange_offer in rules.exchanges:
        referenced_accounts.extend(
            (
                exchange_offer.source_account_id,
                exchange_offer.source_clearing_account_id,
                exchange_offer.target_clearing_account_id,
                exchange_offer.target_account_id,
            )
        )
    for job in rules.jobs:
        referenced_accounts.extend((job.employer_account_id, job.worker_account_id))
        if job.advertising_account_id:
            referenced_accounts.append(job.advertising_account_id)
    for profile in rules.profiles:
        referenced_accounts.extend((profile.living_account_id, profile.cost_of_living_account_id))
        if (
            profile.living_account_id in accounts
            and accounts[profile.living_account_id].owner_id != profile.actor_id
        ):
            raise ValidationError("Living account does not belong to its financial profile")
        background = backgrounds.get(profile.actor_id)
        bought = purchases.get(profile.actor_id, frozenset())
        if background is None or (background.wealth, background.status) != (
            profile.wealth,
            profile.status,
        ):
            raise ValidationError("Financial profile disagrees with compiled Wealth or Status")
        expected_ranks = frozenset(value for value in bought if "rank-" in value)
        expected_relationships = frozenset(
            value
            for value in bought
            if any(part in value for part in ("trait:ally-", "trait:contact-", "trait:dependent-"))
        )
        if (
            frozenset(profile.rank_definition_ids) != expected_ranks
            or frozenset(profile.relationship_definition_ids) != expected_relationships
        ):
            raise ValidationError("Financial profile disagrees with Rank or relationship traits")
    for hireling in rules.hirelings:
        referenced_accounts.extend((hireling.employer_account_id, hireling.hireling_account_id))
    if not set(referenced_accounts) <= set(accounts):
        raise ValidationError("Economics rule references an unknown account")
    _validate_market_bindings(rules, accounts, locations)
    _validate_work_bindings(rules, accounts, administration_rules)
    time_use_ids = {value.id for value in administration.time_use}
    if any(value.time_use_id not in time_use_ids for value in state.periods):
        raise ValidationError("Job period lacks its authoritative Time Use entry")
    if any(value.revision > resources.revision for value in state.entries):
        raise ValidationError("Economics entry is ahead of campaign state")
    private_motives = {value.private_motive for value in state.hirelings if value.private_motive}
    if any(motive in event.kind for motive in private_motives for event in resources.events):
        raise ValidationError("Private hireling motive leaked into the public event stream")
