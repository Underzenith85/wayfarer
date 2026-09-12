"""Typed-action boundary, no-op, trace and durable concurrency contracts."""

import asyncio
import json
import os
import uuid
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from hypothesis import given
from hypothesis import strategies as st

from wayfarer.contracts import Campaign
from wayfarer.engine.character import builder
from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DEFAULT_POLICY,
    DEFAULT_RULES,
    PROTOTYPE_PACKAGE,
    PROTOTYPE_SOURCE,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
    reference,
)
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import (
    ActionResult,
    ActionRules,
    ActorSetup,
    Attack,
    CheckRule,
    Inspect,
    Move,
    PlayActor,
    PlayState,
    Question,
    Social,
    UseItem,
    Wait,
)
from wayfarer.engine.simulation.campaign.scenario import scenario
from wayfarer.engine.simulation.events import action_result
from wayfarer.engine.simulation.resources import (
    EquipmentSpec,
    Item,
    Owner,
    Pool,
    ResourceEngine,
    ResourceState,
    Scheduled,
)
from wayfarer.engine.world import Connection, Entity, EntityKind, Fact, World
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import ApproveCharacter, PlayService
from wayfarer.orchestration.service import GameService, public
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore


class Dice:
    def __init__(self, value: int = 0) -> None:
        self.calls = 0
        self.value = value

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        assert exclusive_upper_bound == 6
        self.calls += 1
        return self.value


def world() -> World:
    return World(
        entities=(
            Entity("dock", EntityKind.LOCATION, "Dock"),
            Entity("alley", EntityKind.LOCATION, "Alley"),
            Entity("far", EntityKind.LOCATION, "Far"),
            Entity("a", EntityKind.ACTOR, "Mira", location_id="dock"),
            Entity("b", EntityKind.ACTOR, "Iven", location_id="dock"),
            Entity("chest", EntityKind.OBJECT, "Chest", location_id="dock"),
            Entity("hidden", EntityKind.OBJECT, "Hidden", location_id="dock"),
        ),
        connections=(Connection("dock", "alley", "path"), Connection("alley", "dock", "return")),
        facts=(Fact("clue", "chest", "contains", "letter"), Fact("promise", "b", "agreed", "help")),
    )


def engine(
    *, automatic: bool = True, not_before: int = 0, equipment: str | None = None
) -> ActionEngine:
    package = replace(
        PROTOTYPE_PACKAGE,
        definitions=PROTOTYPE_PACKAGE.definitions
        + tuple(
            RuleDefinition(
                key,
                DefinitionKind.EQUIPMENT,
                key,
                PROTOTYPE_SOURCE.id,
                0,
                ImplementationStatus.IMPLEMENTED,
            )
            for key in ("potion", "sword", "tool")
        ),
    )
    policy = replace(DEFAULT_POLICY, allowed_equipment=frozenset({"potion", "sword", "tool"}))
    rules = replace(
        DEFAULT_RULES, packages=(PackagePin(package.id, package.version, package.digest),)
    )
    catalog = RulesCatalog((package,))
    reviewer = PowerReviewer(
        CharacterCompiler(catalog, rules, policy),
        PowerPolicy(id="power", version=1, automatic_approval=automatic),
        frozenset({"gm"}),
    )
    resources = ResourceEngine(
        world(),
        catalog,
        rules,
        policy,
        (
            EquipmentSpec(definition_id="potion", unit_weight=1),
            EquipmentSpec(definition_id="sword", unit_weight=5, stackable=False, slot="hand"),
            EquipmentSpec(definition_id="tool", unit_weight=1, stackable=False, slot="eye"),
        ),
    )
    check_inputs: tuple[tuple[Literal["inspect", "social"], str, str, str], ...] = (
        ("inspect", "chest", "skill:observation", "clue"),
        ("social", "b", "skill:diplomacy", "promise"),
    )
    checks = tuple(
        CheckRule(
            id=kind,
            action=kind,
            target_id=target,
            definition_id=skill,
            package_id=package.id,
            package_version=package.version,
            duration=2,
            modifier=-1,
            reveal_fact_ids=(fact,),
            not_before=not_before,
            required_equipment=equipment,
        )
        for kind, target, skill, fact in check_inputs
    )
    return ActionEngine(
        reviewer,
        resources,
        ActionRules(id="actions", version=1, checks=checks, consumables=("potion",)),
    )


def actor_setup() -> ActorSetup:
    draft = CharacterDraft(
        name="Mira",
        purchases=tuple(
            Purchase(definition_id=f"attribute:{key}", amount=10)
            for key in ("st", "dx", "iq", "ht")
        )
        + (
            Purchase(definition_id="skill:observation", amount=4),
            Purchase(definition_id="skill:diplomacy", amount=4),
        ),
    )
    return ActorSetup(
        actor_id="a",
        proposal=CharacterProposal(draft=draft),
        aware_of=("chest", "b", "alley", "far"),
    )


def resource_seed() -> ResourceState:
    return ResourceState(
        items=(
            Item(id="potions", definition_id="potion", owner_id="a", quantity=5),
            Item(id="sword", definition_id="sword", owner_id="a", equipped=True, ready=True),
            Item(id="tool", definition_id="tool", owner_id="a"),
        ),
        owners=(Owner(actor_id="a", capacity=100),),
        active_effect_ids=("poison",),
        scheduled=(Scheduled(id="expiry", due=2, kind="expire", target_id="poison"),),
    )


def seed(reducer: ActionEngine) -> PlayState:
    setup = actor_setup()
    approval = (
        reducer.reviewer.approve(setup.proposal, campaign_id="c", actor_id="a", revision=0)
        if reducer.reviewer.policy.automatic_approval
        else None
    )
    actor = PlayActor(**setup.model_dump(), approval=approval)
    resources = resource_seed().model_copy(
        update={
            "owners": (
                Owner(
                    actor_id="a",
                    capacity=100,
                    definitions=tuple(p.definition_id for p in setup.proposal.draft.purchases),
                ),
            ),
            "pools": (
                Pool(id="hp:a", current=10, maximum=10),
                Pool(id="fp:a", current=10, maximum=10),
            ),
        }
    )
    return PlayState(
        campaign_id="c",
        configuration_digest=reducer.digest,
        world=world(),
        resources=resources,
        actors=(actor,),
        approvals=(approval,) if approval else (),
    )


def campaign(reducer: ActionEngine) -> Campaign:
    return Campaign(
        id=str(uuid.uuid4()),
        revision=0,
        rules="wave7-test",
        rules_ref=reference(reducer.resources.rules),
        character=builder.character(),
        scenario=scenario(),
        hp=10,
        fp=10,
        minutes=0,
        location="dock",
        inventory=[],
        discoveries=[],
        flags=[],
        complete=False,
        messages=[],
    )


def test_questions_hypotheticals_clarifications_and_abandoned_preview_spend_nothing() -> None:
    reducer = engine()
    state, dice = seed(reducer), Dice()
    commands = (
        Question(id="q", actor_id="a", expected_revision=0, text="Could I move?"),
        Move(
            id="hypothetical",
            actor_id="a",
            expected_revision=0,
            destination_id="alley",
            hypothetical=True,
        ),
        Move(id="missing", actor_id="a", expected_revision=0),
        Inspect(id="target", actor_id="a", expected_revision=0),
        UseItem(id="item", actor_id="a", expected_revision=0),
    )
    for command in commands:
        updated, resolved_events = reducer.resolve(state, command, rng=dice)
        result = action_result(resolved_events)
        assert result.status in {"question", "clarification"} and updated == state
    planned = reducer.assess(
        state, Move(id="plan", actor_id="a", expected_revision=0, destination_id="alley")
    )
    assert planned.status == "feasible" and state.resources.game_time == 0 and dice.calls == 0


def test_check_rule_failures_name_the_offending_check() -> None:
    """Engine invariants localise the fault so authors need not read the engine (#365)."""
    base = engine()
    package_id, package_version = base.reviewer.compiler.definition_packages["skill:observation"]

    def check(check_id: str, target: str, definition_id: str) -> CheckRule:
        return CheckRule(
            id=check_id,
            action="inspect",
            target_id=target,
            definition_id=definition_id,
            package_id=package_id,
            package_version=package_version,
            reveal_fact_ids=("clue",),
        )

    with pytest.raises(ValidationError, match="check-vault requires an implemented catalog") as e:
        ActionEngine(
            base.reviewer,
            base.resources,
            ActionRules(id="a", version=1, checks=(check("check-vault", "chest", "attribute:dx"),)),
        )
    assert e.value.reference == "check-vault"
    assert "attribute:dx is attribute, not a skill" in str(e.value)
    with pytest.raises(ValidationError, match="check-a and check-b both inspect chest") as e:
        ActionEngine(
            base.reviewer,
            base.resources,
            ActionRules(
                id="a",
                version=1,
                checks=(
                    check("check-a", "chest", "skill:observation"),
                    check("check-b", "chest", "skill:observation"),
                ),
            ),
        )
    assert e.value.reference == "check-b"
    with pytest.raises(ValidationError, match="check-a is declared twice") as e:
        ActionEngine(
            base.reviewer,
            base.resources,
            ActionRules(
                id="a",
                version=1,
                checks=(
                    check("check-a", "chest", "skill:observation"),
                    check("check-a", "b", "skill:observation"),
                ),
            ),
        )
    assert e.value.reference == "check-a"


def test_movement_range_awareness_and_unsupported_combat() -> None:
    reducer, dice = engine(), Dice()
    state = seed(reducer)
    for target in ("hidden", "missing"):
        result = reducer.assess(
            state, Inspect(id="x", actor_id="a", expected_revision=0, target_id=target)
        )
        assert result.code == "target.unavailable"
    assert (
        reducer.assess(
            state, Move(id="far", actor_id="a", expected_revision=0, destination_id="far")
        ).code
        == "move.no_connection"
    )
    attack = Attack(
        id="attack", actor_id="a", expected_revision=0, target_id="b", weapon_id="sword"
    )
    updated, resolved_events = reducer.resolve(state, attack, rng=dice)
    result = action_result(resolved_events)
    assert updated == state and result.status == "unsupported" and dice.calls == 0
    assert (
        reducer.assess(state, attack.model_copy(update={"weapon_id": "tool"})).code
        == "attack.weapon_not_ready"
    )
    moved, resolved_events = reducer.resolve(
        state, Move(id="move", actor_id="a", expected_revision=0, destination_id="alley")
    )
    result = action_result(resolved_events)
    assert result.status == "committed" and moved.resources.game_time == 1
    assert next(e.location_id for e in moved.world.entities if e.id == "a") == "alley"
    assert (
        reducer.assess(
            moved, Social(id="social", actor_id="a", expected_revision=1, target_id="b")
        ).code
        == "target.out_of_range"
    )


def test_supported_checks_record_actual_rolls_and_reveal_only_on_success() -> None:
    reducer, dice = engine(), Dice()
    initial = seed(reducer)
    inspected, resolved_events = reducer.resolve(
        initial,
        Inspect(id="inspect", actor_id="a", expected_revision=0, target_id="chest"),
        rng=dice,
    )
    result = action_result(resolved_events)
    assert dice.calls == 3 and result.check is not None and result.check.dice == (1, 1, 1)
    assert (
        result.check.modifiers[0].reason == "inspect"
        and result.check.rules_package == PROTOTYPE_PACKAGE.id
    )
    assert result.derived is not None and result.rules_digest == reducer.digest
    assert inspected.world.knowledge == (("a", "clue"),)
    assert inspected.resources.game_time == 2 and inspected.resources.fired == ("expiry",)
    social, resolved_events = reducer.resolve(
        inspected, Social(id="social", actor_id="a", expected_revision=1, target_id="b"), rng=dice
    )
    result = action_result(resolved_events)
    assert result.check is not None and ("a", "promise") in social.world.knowledge
    failed, resolved_events = reducer.resolve(
        initial,
        Inspect(id="failed", actor_id="a", expected_revision=0, target_id="chest"),
        rng=Dice(5),
    )
    result = action_result(resolved_events)
    assert result.status == "committed" and result.revealed_fact_ids == ()
    assert failed.world.knowledge == () and failed.resources.game_time == 2
    restored = PlayState.model_validate_json(social.model_dump_json())
    reducer.validate(restored)
    assert restored == social


def test_conditions_equipment_timing_approval_and_adjudication_gates() -> None:
    reducer = engine()
    state = seed(reducer)
    command = Inspect(id="check", actor_id="a", expected_revision=0, target_id="chest")
    for actor in (
        state.actors[0].model_copy(update={"conditions": ("stunned",)}),
        state.actors[0].model_copy(update={"available_at": 5}),
    ):
        blocked = state.model_copy(update={"actors": (actor,)})
        assert reducer.assess(blocked, command).status == "rejected"
    delayed = engine(not_before=3)
    assert delayed.assess(seed(delayed), command).code == "check.not_ready"
    equipped = engine(equipment="tool")
    assert equipped.assess(seed(equipped), command).code == "check.equipment_required"
    pending = engine(automatic=False)
    assert pending.assess(seed(pending), command).code == "character.approval_required"
    assert (
        reducer.assess(
            state,
            Social(id="x", actor_id="a", expected_revision=0, target_id="b", approach="deception"),
        ).status
        == "adjudication_required"
    )
    assert (
        reducer.assess(state, Wait(id="wait", actor_id="a", expected_revision=0, ticks=101)).code
        == "wait.limit"
    )


@given(st.integers(min_value=1, max_value=10))
def test_item_use_conservation_and_invalid_actions_are_atomic(amount: int) -> None:
    reducer = engine()
    state = seed(reducer)
    updated, resolved_events = reducer.resolve(
        state,
        UseItem(id="use", actor_id="a", expected_revision=0, item_id="potions", quantity=amount),
    )
    result = action_result(resolved_events)
    remaining = sum(i.quantity for i in updated.resources.items if i.definition_id == "potion")
    if amount > 5:
        assert updated == state and result.status == "rejected" and remaining == 5
    else:
        assert result.status == "committed" and remaining == 5 - amount
        assert (
            updated.revision == updated.resources.revision == 1 and updated.resources.game_time == 1
        )


@pytest.mark.parametrize(
    "field,value",
    [("roll", [1, 1, 1]), ("target", 999), ("approval", True), ("effects", []), ("quantity", True)],
)
def test_forged_fields_and_integer_coercions_are_rejected(field: str, value: object) -> None:
    payload: dict[str, object] = dict(
        kind="use_item", id="x", actor_id="a", expected_revision=0, item_id="potions", quantity=1
    )
    payload[field] = value
    with pytest.raises(ValidationError, match="proposal"):
        PlayService.propose(payload)


@pytest.mark.asyncio
@pytest.mark.integration
async def test_receipt_miss_racing_an_identical_commit_returns_original_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = AsyncSQLiteStore(tmp_path / "receipt-race.sqlite", 10)
    reducer, dice = engine(), Dice()
    service = PlayService(store, reducer, rng=dice)
    initial = campaign(reducer)
    await service.create(initial, world(), resource_seed(), (actor_setup(),))
    command = Inspect(id="same-request", actor_id="a", expected_revision=0, target_id="chest")
    original_duplicate = store.duplicate
    raced = False

    async def racing_duplicate(cid: str, command_id: str, payload: str) -> Campaign | None:
        nonlocal raced
        receipt = await original_duplicate(cid, command_id, payload)
        if not raced and receipt is None:
            raced = True
            # Another caller commits after this lookup misses, before it returns.
            await service.execute(cid, command, authenticated_actor_id="a")
        return receipt

    monkeypatch.setattr(store, "duplicate", racing_duplicate)
    result = await service.execute(initial["id"], command, authenticated_actor_id="a")
    assert result.status == "committed" and result.revision == 1
    assert dice.calls == 3 and len(await store.history(initial["id"])) == 1
    assert await service.execute(initial["id"], command, authenticated_actor_id="a") == result
    with pytest.raises(ConflictError):
        await service.execute(
            initial["id"], command.model_copy(update={"target_id": "b"}), authenticated_actor_id="a"
        )


@pytest.mark.asyncio
@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_action_transactions_retry_concurrency_restart_and_snapshot(
    tmp_path: Path, backend: str
) -> None:
    store: AsyncSQLiteStore | AsyncPostgresStore
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if url is None:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        store = AsyncPostgresStore(url, 10)
    else:
        store = AsyncSQLiteStore(tmp_path / "play.sqlite", 10)
    reducer, dice = engine(), Dice()
    service = PlayService(store, reducer, rng=dice)
    initial = campaign(reducer)
    await service.create(initial, world(), resource_seed(), (actor_setup(),))
    raw_before = await store.read(initial["id"])
    command = Inspect(id="inspect", actor_id="a", expected_revision=0, target_id="chest")
    assert (
        await service.preview(initial["id"], command, authenticated_actor_id="a")
    ).status == "feasible"
    assert dice.calls == 0 and await store.read(initial["id"]) == raw_before
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(initial["id"], command, authenticated_actor_id="b")
    question = Question(id="q", actor_id="a", expected_revision=0, text="What if I attack?")
    assert (
        await service.execute(initial["id"], question, authenticated_actor_id="a")
    ).status == "question"
    assert await store.history(initial["id"]) == []
    results = await asyncio.gather(
        *(service.execute(initial["id"], command, authenticated_actor_id="a") for _ in range(8))
    )
    assert all(r == results[0] for r in results) and dice.calls == 3
    assert len(await store.history(initial["id"])) == 1
    competing = await asyncio.gather(
        *(
            service.execute(
                initial["id"],
                UseItem(
                    id=f"race-{i}", actor_id="a", expected_revision=1, item_id="potions", quantity=4
                ),
                authenticated_actor_id="a",
            )
            for i in range(2)
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(r, ActionResult) and r.status == "committed" for r in competing) == 1
    assert sum(isinstance(r, ConflictError) for r in competing) == 1
    restarted = PlayService(store, reducer, rng=Dice(5))
    assert await restarted.execute(initial["id"], command, authenticated_actor_id="a") == results[0]
    assert "play_json" not in public(await store.read(initial["id"]))
    with pytest.raises(ConflictError):
        await restarted.execute(
            initial["id"], command.model_copy(update={"target_id": "b"}), authenticated_actor_id="a"
        )
    for revision in range(2, 12):
        await restarted.execute(
            initial["id"],
            Wait(id=f"wait-{revision}", actor_id="a", expected_revision=revision, ticks=1),
            authenticated_actor_id="a",
        )
    assert await store.replay(initial["id"]) == await store.read(initial["id"])
    history = await store.history(initial["id"])
    assert (
        len(history) == 12
        and history[0].actor_id == "a"
        and json.loads(history[0].event["outcome"])["check"] is not None
    )
    state = PlayState.model_validate_json((await store.read(initial["id"]))["play_json"])
    assert state.resources.fired == ("expiry",) and len(state.resources.events) == 1


@pytest.mark.asyncio
@pytest.mark.integration
async def test_pending_approval_is_durable_authorized_and_revalidated(tmp_path: Path) -> None:
    store = AsyncSQLiteStore(tmp_path / "approvals.sqlite", 10)
    reducer = engine(automatic=False)
    initial = campaign(reducer)
    service = PlayService(store, reducer)
    state = await service.create(initial, world(), resource_seed(), (actor_setup(),))
    assert state.actors[0].approval is None
    blocked = await service.execute(
        initial["id"],
        Wait(id="pending", actor_id="a", expected_revision=0, ticks=1),
        authenticated_actor_id="a",
    )
    assert (
        blocked.code == "character.approval_required" and await store.history(initial["id"]) == []
    )
    approval = ApproveCharacter(
        id="approval",
        actor_id="gm",
        target_actor_id="a",
        expected_revision=0,
        reason="Reviewed for this scenario",
    )
    with pytest.raises(ValidationError, match="authority"):
        await service.approve(initial["id"], approval, authenticated_gm_id="a")
    record = await service.approve(initial["id"], approval, authenticated_gm_id="gm")
    assert record.recorded_revision == 1 and record.approver_id == "gm"
    restarted = PlayService(store, reducer)
    assert await restarted.approve(initial["id"], approval, authenticated_gm_id="gm") == record
    result = await restarted.execute(
        initial["id"],
        Wait(id="active", actor_id="a", expected_revision=1, ticks=1),
        authenticated_actor_id="a",
    )
    assert result.status == "committed"
    assert await store.replay(initial["id"]) == await store.read(initial["id"])
    assert (await store.history(initial["id"]))[0].event["action"] == "power-approval"
    with pytest.raises(ConflictError):
        await restarted.approve(
            initial["id"], approval.model_copy(update={"id": "stale"}), authenticated_gm_id="gm"
        )
    migrated = engine(automatic=True)
    with pytest.raises(ValidationError, match="migration"):
        await PlayService(store, migrated).preview(
            initial["id"],
            Wait(id="x", actor_id="a", expected_revision=2, ticks=1),
            authenticated_actor_id="a",
        )


@pytest.mark.asyncio
async def test_legacy_turns_cannot_mutate_typed_campaigns(service: GameService) -> None:
    reducer = engine()
    initial = campaign(reducer)
    await PlayService(service.store, reducer).create(
        initial, world(), resource_seed(), (actor_setup(),)
    )
    assert not hasattr(service, "turn")
    assert (await service.read(initial["id"]))["revision"] == 0


def test_compiled_resources_cannot_be_forged_and_fatigue_is_charged_once() -> None:
    initial_engine = engine()
    reducer = ActionEngine(
        initial_engine.reviewer,
        initial_engine.resources,
        initial_engine.rules.model_copy(update={"fatigue_cost": 2}),
    )
    state = seed(reducer)
    command = Move(id="move", actor_id="a", expected_revision=0, destination_id="alley")
    depleted = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": (
                        Pool(id="hp:a", current=10, maximum=10),
                        Pool(id="fp:a", current=1, maximum=10),
                    )
                }
            )
        }
    )
    updated, resolved_events = reducer.resolve(depleted, command)
    result = action_result(resolved_events)
    assert updated == depleted and result.code == "resource.fatigue"
    moved, resolved_events = reducer.resolve(state, command)
    result = action_result(resolved_events)
    assert (
        result.status == "committed"
        and next(p.current for p in moved.resources.pools if p.id == "fp:a") == 8
    )
    forged = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"owners": (Owner(actor_id="a", capacity=100, definitions=("forged",)),)}
            )
        }
    )
    with pytest.raises(ValidationError, match="prerequisites"):
        reducer.assess(forged, command)
    with pytest.raises(ConflictError):
        reducer.resolve(moved, command)


def test_equipment_attribute_effects_feed_skill_target_with_provenance() -> None:
    from decimal import Decimal

    from wayfarer.engine.rules.effects import Effect, Operation

    reducer = engine()
    # Trusted equipment binding; construct a new engine digest after configuration.
    spec = reducer.resources.specs["tool"]
    reducer.resources.specs["tool"] = spec.model_copy(
        update={
            "effects": (
                Effect(
                    "lens",
                    "attribute:iq",
                    Operation.ADD,
                    Decimal(2),
                    "tool",
                    PROTOTYPE_PACKAGE.version,
                ),
            )
        }
    )
    reducer = ActionEngine(reducer.reviewer, reducer.resources, reducer.rules)
    state = seed(reducer)
    resources = state.resources.model_copy(
        update={
            "items": tuple(
                i.model_copy(update={"equipped": True, "ready": True}) if i.id == "tool" else i
                for i in state.resources.items
            )
        }
    )
    state = state.model_copy(update={"resources": resources})
    _, resolved_events = reducer.resolve(
        state,
        Inspect(id="inspect", actor_id="a", expected_revision=0, target_id="chest"),
        rng=Dice(),
    )
    result = action_result(resolved_events)
    assert result.check is not None and result.check.base_target == 13
    assert result.dependencies[0].value == 12
    assert result.dependencies[0].explanations[0].source_id == "tool"
