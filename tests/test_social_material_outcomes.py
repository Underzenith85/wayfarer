"""B183/B212/B216 material and crowd outcomes for #370."""

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.economics import (
    CarousingOutcomeRule,
    CurrencyRule,
    EconomicsRules,
    EconomicsState,
    MoneyAccount,
    PanhandlingOutcomeRule,
    PerformanceOutcomeRule,
    PublicSpeakingOutcomeRule,
    bind_social_material_outcome,
)
from wayfarer.engine.simulation.resources import ResourceState

ACTORS = frozenset({"actor", "donors", "venue", "audience-a", "audience-b"})


def state() -> EconomicsState:
    return EconomicsState(
        accounts=(
            MoneyAccount(
                id="actor-cash",
                owner_id="actor",
                currency_id="dollars",
                location_id="city",
                balance=20,
            ),
            MoneyAccount(
                id="donor-cash",
                owner_id="donors",
                currency_id="dollars",
                location_id="city",
                balance=1_000,
            ),
            MoneyAccount(
                id="venue-cash",
                owner_id="venue",
                currency_id="dollars",
                location_id="city",
                balance=0,
            ),
        )
    )


def rules() -> EconomicsRules:
    return EconomicsRules(
        id="social-money",
        version=1,
        currencies=(CurrencyRule(id="dollars", world_id="city", name="dollars"),),
        carousing=(
            CarousingOutcomeRule(
                id="night-out",
                trigger_id="carousing-trigger",
                actor_account_id="actor-cash",
                venue_account_id="venue-cash",
                outlay=5,
                duration_seconds=4 * 3600,
                intoxication="drunk",
            ),
        ),
        panhandling=(
            PanhandlingOutcomeRule(
                id="begging-hour",
                trigger_id="panhandling-trigger",
                donor_account_id="donor-cash",
                actor_account_id="actor-cash",
            ),
        ),
        performances=(
            PerformanceOutcomeRule(
                id="paid-show",
                trigger_id="performance-trigger",
                payer_account_id="donor-cash",
                actor_account_id="actor-cash",
                base_pay=10,
                pay_per_margin=2,
                audience_actor_ids=("audience-a", "audience-b"),
            ),
        ),
        public_speaking=(
            PublicSpeakingOutcomeRule(
                id="speech",
                trigger_id="speech-trigger",
                audience_actor_ids=("audience-a", "audience-b"),
            ),
        ),
    )


def bind(
    economics: EconomicsState,
    resources: ResourceState,
    *,
    command_id: str,
    trigger_id: str,
    procedure_id: str,
    margin: int,
    outcome: str,
    critical_success: bool = False,
    dice: tuple[int, ...] = (),
    purchased_ids: frozenset[str] = frozenset(),
) -> tuple[EconomicsState, ResourceState]:
    return bind_social_material_outcome(
        economics,
        resources,
        rules(),
        command_id=command_id,
        trigger_id=trigger_id,
        procedure_id=procedure_id,
        actor_id="actor",
        margin=margin,
        outcome=outcome,
        critical_success=critical_success,
        ht=10,
        purchased_ids=purchased_ids,
        actor_ids=ACTORS,
        rng=RecordedDice(dice),
    )


def test_panhandling_moves_two_dollars_per_margin_for_one_hour() -> None:
    economics, _ = bind(
        state(),
        ResourceState(revision=1),
        command_id="beg",
        trigger_id="panhandling-trigger",
        procedure_id="skill:panhandling",
        margin=4,
        outcome="panhandling-given",
    )
    accounts = {value.id: value.balance for value in economics.accounts}
    assert accounts["actor-cash"] == 28 and accounts["donor-cash"] == 992
    assert economics.social_outcomes[0].amount == 8
    assert economics.social_outcomes[0].ends_at == 3600


def test_performance_pay_and_public_speaking_scale_one_crowd_reaction_by_margin() -> None:
    economics, resources = bind(
        state(),
        ResourceState(revision=1),
        command_id="show",
        trigger_id="performance-trigger",
        procedure_id="skill:performance",
        margin=3,
        outcome="performance-received",
    )
    show = economics.social_outcomes[0]
    assert show.amount == 16 and show.audience_reaction == "good"
    assert show.audience_actor_ids == ("audience-a", "audience-b")
    economics, _ = bind(
        economics,
        resources,
        command_id="speech",
        trigger_id="speech-trigger",
        procedure_id="skill:public-speaking",
        margin=-4,
        outcome="public-speaking-unmoved",
    )
    assert economics.social_outcomes[-1].audience_reaction == "bad"


def test_carousing_charges_the_evening_and_records_a_delayed_hangover() -> None:
    economics, resources = bind(
        state(),
        ResourceState(revision=1),
        command_id="night",
        trigger_id="carousing-trigger",
        procedure_id="skill:carousing",
        margin=-1,
        outcome="carousing-uneventful",
        dice=(6, 6, 6, 2),
        purchased_ids=frozenset({"trait:disadvantage:horrible-hangovers"}),
    )
    accounts = {value.id: value.balance for value in economics.accounts}
    assert accounts["actor-cash"] == 15 and accounts["venue-cash"] == 5
    result = economics.social_outcomes[0]
    assert result.ends_at == 4 * 3600
    assert result.hangover_due == 6 * 3600 and result.hangover_seconds == 16 * 3600
    assert [(value.due, value.amount) for value in resources.scheduled] == [
        (4 * 3600, 0),
        (6 * 3600, 16 * 3600),
    ]
