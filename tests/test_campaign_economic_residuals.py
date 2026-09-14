"""Acceptance evidence for #690 (B515 and B518)."""

from decimal import Decimal

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.campaign.administration import AdministrationState
from wayfarer.engine.simulation.campaign.economics import (
    AcquireBondedLabor,
    BondedLaborRule,
    CraftRule,
    EconomicsRules,
    EconomicsState,
    MakeGoods,
    MoneyAccount,
    ReleaseBondedLabor,
    apply_economics,
)
from wayfarer.engine.simulation.resources import Item, ResourceState
from wayfarer.engine.world import Entity, EntityKind, World

WORLD = World(
    entities=(
        Entity("buyer", EntityKind.ACTOR, "Buyer"),
        Entity("seller", EntityKind.ACTOR, "Seller"),
        Entity("subject", EntityKind.ACTOR, "Subject"),
        Entity("town", EntityKind.LOCATION, "Town"),
    )
)


def clock(state: ResourceState, to: int, _parent: str) -> ResourceState:
    return state.model_copy(update={"revision": state.revision + 1, "game_time": to})


def unused_transfer(state: ResourceState, *_args: object) -> ResourceState:
    return state


def test_making_goods_conserves_parts_tracks_time_and_hidden_defect() -> None:
    rules = EconomicsRules(
        id="economics",
        version=1,
        crafts=(
            CraftRule(
                id="chair",
                target_id="skill:carpentry",
                output_definition_id="equipment:chair",
                output_price=100,
                work_seconds=8 * 3600,
                materials=(("material:wood", 2),),
                material_value=20,
                required_tool_definition_ids=("equipment:carpentry-kit",),
            ),
        ),
    )
    resources = ResourceState(
        items=(
            Item(id="wood", definition_id="material:wood", owner_id="buyer", quantity=2),
            Item(id="tools", definition_id="equipment:carpentry-kit", owner_id="buyer"),
        )
    )
    command = MakeGoods(
        id="make-chair",
        actor_id="buyer",
        expected_revision=0,
        craft_rule_id="chair",
        output_item_id="chair-1",
        material_item_ids=("wood",),
        tool_item_ids=("tools",),
    )
    economy, updated, outcome = apply_economics(
        EconomicsState(),
        resources,
        AdministrationState(),
        command,
        rules,
        world=WORLD,
        build_values={"skill:carpentry": Decimal(10)},
        purchased_ids=frozenset(),
        rng=RecordedDice((6, 6, 6)),
        transfer=unused_transfer,
        advance=clock,
        system=True,
    )
    assert updated.game_time == 8 * 3600
    assert {value.id for value in updated.items} == {"tools", "chair-1"}
    assert tuple(value.id for value in updated.expended_items) == ("wood",)
    assert outcome.consequence == "catastrophic-hidden-defect"
    assert economy.crafted_goods[0].catastrophic_defect
    assert apply_economics(
        economy,
        updated,
        AdministrationState(),
        command,
        rules,
        world=WORLD,
        build_values={"skill:carpentry": Decimal(10)},
        purchased_ids=frozenset(),
        rng=RecordedDice(()),
        transfer=unused_transfer,
        advance=clock,
        system=True,
    ) == (economy, updated, outcome)


def test_bonded_labor_is_legal_actor_custody_not_inventory() -> None:
    rules = EconomicsRules(
        id="economics",
        version=1,
        bonded_labor=(
            BondedLaborRule(
                id="contract",
                subject_actor_id="subject",
                seller_account_id="seller-cash",
                buyer_account_id="buyer-cash",
                monthly_pay=100,
                jurisdiction_id="town",
                legal=True,
                kind_of_slavery="debt bondage",
                private_disposition="plans escape",
                visible_to_actor_ids=("buyer", "subject"),
            ),
        ),
    )
    economy = EconomicsState(
        accounts=(
            MoneyAccount(
                id="buyer-cash",
                owner_id="buyer",
                currency_id="coin",
                location_id="town",
                balance=10_000,
            ),
            MoneyAccount(
                id="seller-cash",
                owner_id="seller",
                currency_id="coin",
                location_id="town",
                balance=0,
            ),
        )
    )
    resources = ResourceState()
    economy, resources, outcome = apply_economics(
        economy,
        resources,
        AdministrationState(),
        AcquireBondedLabor(
            id="buy-contract", actor_id="buyer", expected_revision=0, rule_id="contract"
        ),
        rules,
        world=WORLD,
        build_values={},
        purchased_ids=frozenset(),
        rng=RecordedDice((3, 4)),
        transfer=unused_transfer,
        system=True,
    )
    assert outcome.amount == 60 * 100
    assert [value.balance for value in economy.accounts] == [4_000, 6_000]
    contract = economy.bonded_labor[0]
    assert contract.subject_actor_id == "subject" and not resources.items
    assert "plans escape" not in resources.events[-1].kind

    economy, resources, outcome = apply_economics(
        economy,
        resources,
        AdministrationState(),
        ReleaseBondedLabor(
            id="release", actor_id="buyer", expected_revision=1, contract_id=contract.id
        ),
        rules,
        world=WORLD,
        build_values={},
        purchased_ids=frozenset(),
        rng=RecordedDice(()),
        transfer=unused_transfer,
        system=True,
    )
    assert outcome.status == "released"
    assert economy.bonded_labor[0].status == "released"
