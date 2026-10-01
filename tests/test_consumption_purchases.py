"""Independent B80/B139 supply totals and approval-bound survival consequences."""

from fractions import Fraction
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import seed_campaign
from test_actions import campaign, world
from test_consumption_service import compiler, rule_package
from test_statistics import gurps_draft
from trait_support import approved_build

from wayfarer.contracts import Campaign
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.character.traits.physiology import physiology_traits
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.physiology import PROFILE
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.recovery import FatigueStatus
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.health.survival import (
    BeginSurvival,
    SettleSurvival,
    SurvivalContext,
    begin_survival,
    settle_survival,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Item, Owner, Pool, ResourceState
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.survival import SurvivalEnvironment, SurvivalService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


@pytest.mark.parametrize(
    ("identifier", "level", "meals", "water"),
    [
        (None, 0, 9, 6),
        ("trait:advantage:reduced-consumption", 1, 6, 4),
        ("trait:advantage:reduced-consumption", 2, 3, 2),
        ("trait:disadvantage:increased-consumption", 1, 18, 6),
        ("trait:disadvantage:increased-consumption", 2, 36, 6),
        ("trait:disadvantage:increased-consumption", 3, 72, 6),
    ],
)
def test_three_day_source_consumption_conserves_actual_inventory(
    identifier: str | None, level: int, meals: int, water: int
) -> None:
    purchases = () if identifier is None else (Purchase(definition_id=identifier, amount=level),)
    build, compiled = approved_build(compiler(), *purchases)
    ctx = SurvivalContext(
        PROFILE,
        10,
        10,
        meal_item_ids=("rations",),
        water_item_ids=("water",),
        does_not_sleep=True,
        physiology=physiology_traits(build, compiled.definitions),
    )
    state = ResourceState(
        owners=(Owner(actor_id="a", capacity=1000),),
        items=(
            Item(id="rations", definition_id="rations", owner_id="a", quantity=100),
            Item(id="water", definition_id="water", owner_id="a", quantity=100),
        ),
        pools=(
            Pool(id="hp:a", current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE)),
            Pool(id="fp:a", current=10, maximum=10, fatigue=FatigueStatus(profile_id=PROFILE)),
        ),
    )
    state, _ = begin_survival(
        state, BeginSurvival(id="begin", actor_id="a", expected_revision=0), ctx, system=True
    )
    count = 0
    while state.survival[0].next_due <= 3 * 86400:
        count += 1
        state = state.model_copy(update={"game_time": state.survival[0].next_due})
        command = SettleSurvival(id=f"meal:{count}", actor_id="a", expected_revision=state.revision)
        state, result = settle_survival(state, command, ctx, rng=RecordedDice([]), system=True)
        restored = ResourceState.model_validate_json(state.model_dump_json())
        assert settle_survival(restored, command, ctx, rng=RecordedDice([]), system=True) == (
            restored,
            result,
        )
        assert result.fp_lost == result.hp_lost == 0
    remaining = {item.id: item.quantity for item in state.items}
    assert remaining == {"rations": 100 - meals, "water": 100 - water}
    assert (
        sum(item.quantity for item in state.expended_items if item.definition_id == "rations")
        == meals
    )
    assert (
        sum(item.quantity for item in state.expended_items if item.definition_id == "water")
        == water
    )
    assert state.survival[0].water_quarts_consumed == Fraction(0)
    assert all(pool.current == 10 for pool in state.pools)


async def due_service(
    tmp_path: Path,
    *,
    approval: Literal["valid", "missing", "stale"] = "valid",
    principal_id: str = "a",
) -> tuple[PlayService, Campaign]:
    compiled = compiler()
    engine = ActionEngine(
        PowerReviewer(compiled, PowerPolicy(id="test", version=1), frozenset({"gm"})),
        ResourceEngine(
            world(), RulesCatalog((rule_package(),)), compiled.rules, compiled.policy, ()
        ),
        ActionRules(id="consumption-test", version=1, fatigue_cost=0),
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "due.sqlite", 10), engine, rng=RecordedDice([]))
    initial = campaign(engine)
    proposal = CharacterProposal(
        draft=gurps_draft(Purchase(definition_id="trait:disadvantage:increased-consumption"))
    )
    state = play.initial_state(
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=proposal, aware_of=("alley",)),),
        members=(
            CampaignMember(principal_id=principal_id, role="player", actor_ids=("a",)),
            CampaignMember(principal_id="spectator", role="spectator"),
        ),
    )
    build = compiled.compile(proposal.draft).build
    assert build is not None
    resources, _ = begin_survival(
        state.resources,
        BeginSurvival(id="initial-clock", actor_id="a", expected_revision=0),
        SurvivalContext(PROFILE, 10, 10, physiology=physiology_traits(build, compiled.definitions)),
        system=True,
    )
    # Trusted scenario genesis starts at its first owed meal, before any command.
    resources = resources.model_copy(
        update={"game_time": 14400, "revision": 0, "events": (), "receipts": ()}
    )
    state = state.model_copy(update={"resources": resources})
    if approval == "missing":
        state = state.model_copy(
            update={
                "actors": tuple(
                    actor.model_copy(update={"approval": None}) for actor in state.actors
                )
            }
        )
    elif approval == "stale":
        state = state.model_copy(
            update={
                "actors": tuple(
                    actor.model_copy(update={"proposal": CharacterProposal(draft=gurps_draft())})
                    for actor in state.actors
                )
            }
        )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    return play, initial


async def test_service_applies_actual_starvation_once_after_reload(tmp_path: Path) -> None:
    play, initial = await due_service(tmp_path)
    command = SettleSurvival(id="hungry", actor_id="a", expected_revision=0)
    service = SurvivalService(play, lambda *_: SurvivalEnvironment())
    result = await service.execute(initial["id"], command, principal_id="a")
    state = play._load(await play.store.read(initial["id"]))
    fp = next(pool for pool in state.resources.pools if pool.id == "fp:a")
    assert fp.current == 9 and fp.fatigue is not None and fp.fatigue.starvation == 1
    assert state.resources.survival[0].next_meal_due == 28800
    assert result.fp_lost == 1
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "due.sqlite", 10), play.engine, rng=RecordedDice([])
    )
    replay = SurvivalService(restarted, lambda *_: SurvivalEnvironment())
    assert await replay.execute(initial["id"], command, principal_id="a") == result
    assert restarted._load(await restarted.store.read(initial["id"])) == state
    with pytest.raises(ConflictError):
        await replay.execute(
            initial["id"], command.model_copy(update={"id": "stale"}), principal_id="a"
        )


@pytest.mark.parametrize("approval", ["missing", "stale"])
async def test_service_rejects_revoked_or_changed_approval_without_consumption(
    tmp_path: Path, approval: Literal["missing", "stale"]
) -> None:
    play, initial = await due_service(tmp_path, approval=approval)
    before = await play.store.read(initial["id"])
    with pytest.raises(ValidationError, match="approval|compiled build"):
        await SurvivalService(play, lambda *_: SurvivalEnvironment()).execute(
            initial["id"],
            SettleSurvival(id="denied", actor_id="a", expected_revision=0),
            principal_id="a",
        )
    assert await play.store.read(initial["id"]) == before


def test_source_level_bound_and_duplicate_identity_rejection() -> None:
    compiled = compiler()
    assert (
        compiled.compile(
            gurps_draft(Purchase(definition_id="trait:advantage:reduced-consumption", amount=5))
        ).build
        is None
    )
    build, compiled = approved_build(
        compiled,
        Purchase(definition_id="advantage:reduced-consumption"),
        Purchase(definition_id="trait:advantage:reduced-consumption"),
    )
    with pytest.raises(ValidationError, match="both catalog identities"):
        physiology_traits(build, compiled.definitions)


async def test_survival_honors_campaign_control_for_different_principal(tmp_path: Path) -> None:
    play, initial = await due_service(tmp_path, principal_id="owner")
    service = SurvivalService(play, lambda *_: SurvivalEnvironment())
    command = SettleSurvival(id="controlled", actor_id="a", expected_revision=0)
    before = await play.store.read(initial["id"])
    with pytest.raises(AuthorizationError):
        await service.execute(initial["id"], command, principal_id="spectator")
    assert await play.store.read(initial["id"]) == before
    result = await service.execute(initial["id"], command, principal_id="owner")
    assert result.fp_lost == 1


def test_unsupported_combination_and_subsecond_meals_reject_explicitly() -> None:
    build, compiled = approved_build(
        compiler(),
        Purchase(definition_id="trait:advantage:reduced-consumption"),
        Purchase(definition_id="trait:disadvantage:increased-consumption"),
    )
    with pytest.raises(ValidationError, match="requires source review"):
        physiology_traits(build, compiled.definitions)
    build, compiled = approved_build(
        compiler(), Purchase(definition_id="trait:disadvantage:increased-consumption", amount=8)
    )
    with pytest.raises(ValidationError, match="subsecond"):
        physiology_traits(build, compiled.definitions).consumption_period("food")
