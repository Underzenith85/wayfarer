"""Wave 10 recovery and NPC contracts over restartable authoritative storage."""

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import TypedDict

import pytest
from test_actions import Dice, actor_setup, campaign, resource_seed
from test_scenes import configured

from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.npcs import NPCAction, NPCPlan, NPCRules
from wayfarer.engine.simulation.campaign.objectives import Objective, ObjectiveRules, Predicate
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.health.recovery import RecoveryOption, RecoveryRules, SetbackRule
from wayfarer.engine.simulation.resources import Item, Owner
from wayfarer.engine.world import Entity, EntityKind, Fact
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.npcs import NPCProposal, NPCService
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.recovery import RecoveryCommand, RecoveryService, captive
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore


class OptionScope(TypedDict):
    scene_id: str
    actor_ids: tuple[str, ...]
    target_actor_ids: tuple[str, ...]


def policies() -> RecoveryRules:
    common: OptionScope = {
        "scene_id": "dock-scene",
        "actor_ids": ("a", "b"),
        "target_actor_ids": ("a", "b"),
    }
    return RecoveryRules(
        id="recovery",
        version=1,
        setbacks=(
            SetbackRule(
                id="capture",
                kind="capture",
                actor_ids=("a", "b"),
                captor_id="guard",
                custody_owner_id="guard",
                restraints=("rope",),
            ),
            SetbackRule(
                id="transfer",
                kind="capture",
                actor_ids=("a",),
                captor_id="guard",
                custody_owner_id="guard",
                destination_scene_id="alley-scene",
            ),
            SetbackRule(
                id="retreat", kind="retreat", actor_ids=("a",), destination_scene_id="dock-scene"
            ),
            SetbackRule(id="hurt", kind="incapacitation", actor_ids=("a",)),
            SetbackRule(id="death", kind="death", actor_ids=("a",)),
            SetbackRule(
                id="mission-lost",
                kind="retreat",
                actor_ids=("a",),
                impossible_objective_ids=("mission",),
                failure_fact_id="lost",
            ),
        ),
        options=(
            RecoveryOption(id="observe", kind="observe", success_fact_ids=("captured",), **common),
            RecoveryOption(id="assist", kind="assist", success_fact_ids=("clue",), **common),
            RecoveryOption(id="escape", kind="escape", check_rule_id="inspect", **common),
            RecoveryOption(id="rescue", kind="rescue", required_fact_ids=("captured",), **common),
            RecoveryOption(id="gear", kind="recover_items", **common),
            RecoveryOption(
                id="rest",
                kind="rest",
                ticks=2,
                cost_definition_id="potion",
                cost=1,
                recovery_hp=2,
                recovery_fp=2,
                **common,
            ),
            RecoveryOption(
                id="replace", kind="replace", replacement=actor_setup().proposal, **common
            ),
            RecoveryOption(id="unsupported", kind="escape", supported=False, **common),
            RecoveryOption(
                id="resupply", kind="resupply", stock_item_id="stock", stock_quantity=1, **common
            ),
        ),
    )


async def prepare(
    tmp_path: Path,
    *,
    dice: Dice | None = None,
    recovery: RecoveryRules | None = None,
    transfer: bool = False,
    postgres: bool = False,
    combat: bool = False,
    guard_capacity: int = 100,
) -> tuple[str, PlayService]:
    base, world = configured()
    world = replace(
        world,
        entities=world.entities + (Entity("guard", EntityKind.ACTOR, "Guard", location_id="dock"),),
        facts=world.facts
        + (Fact("captured", "a", "detained", "dock"), Fact("lost", "a", "mission", "lost")),
        knowledge=(("guard", "captured"),),
    )
    plan = NPCPlan(
        id="patrol",
        actor_id="guard",
        goal="Maintain detention",
        disposition="hostile",
        first_due=1,
        interval=1,
        action_budget=4,
        clock_limit=4,
        actions=(
            NPCAction(
                id="unknown",
                kind="alarm",
                required_fact_ids=("clue",),
                reveal_fact_ids=("clue",),
                recipient_ids=("b",),
            ),
            NPCAction(
                id="move",
                kind="transfer_prisoner",
                required_fact_ids=("captured",),
                setback_rule_id="transfer",
                target_actor_id="a",
            )
            if transfer
            else NPCAction(
                id="patrol",
                kind="patrol",
                required_fact_ids=("captured",),
                reveal_fact_ids=("captured",),
                recipient_ids=("b",),
                cost_definition_id="potion",
                cost=1,
            ),
        ),
    )
    from test_combat import combat_engine

    rules = base.rules.model_copy(
        update={
            "combat": combat_engine().rules.combat if combat else None,
            "party": PartyRules(id="party", version=1),
            "npcs": NPCRules(id="npcs", version=1, plans=(plan,)),
            "recovery": recovery or policies(),
            "objectives": ObjectiveRules(
                id="mission",
                version=1,
                objectives=(
                    Objective(
                        id="mission",
                        title="Solve it",
                        predicates=(Predicate(kind="known", subject_id="a", value="promise"),),
                    ),
                ),
                failures=(Predicate(kind="known", subject_id="a", value="lost"),),
            ),
        }
    )
    base.resources.actors = frozenset(e.id for e in world.entities if e.kind == EntityKind.ACTOR)
    engine = ActionEngine(base.reviewer, base.resources, rules)
    store: AsyncSQLiteStore | AsyncPostgresStore
    if postgres:
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if url is None:
            pytest.skip("PostgreSQL is not configured")
        store = AsyncPostgresStore(url, 10)
    else:
        store = AsyncSQLiteStore(tmp_path / "wave10.sqlite", 10)
    play = PlayService(store, engine, rng=dice or Dice())
    seed = resource_seed().model_copy(
        update={
            "owners": resource_seed().owners
            + (Owner(actor_id="b", capacity=100), Owner(actor_id="guard", capacity=guard_capacity)),
            "items": resource_seed().items
            + (Item(id="stock", definition_id="potion", owner_id="guard", quantity=2),),
        }
    )
    if guard_capacity == 0:
        seed = seed.model_copy(
            update={"items": tuple(i for i in seed.items if i.owner_id != "guard")}
        )
    initial = campaign(engine)
    await play.create(
        initial,
        world,
        seed,
        (actor_setup(), actor_setup().model_copy(update={"actor_id": "b"})),
        (
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    return initial["id"], play


async def read(cid: str, play: PlayService) -> PlayState:
    return play._load(await play.store.read(cid))


async def setback(cid: str, play: PlayService, rule: str, actor: str = "a") -> PlayState:
    state = await read(cid, play)
    return await RecoveryService(play).execute(
        cid,
        RecoveryCommand(
            id=f"{rule}:{state.revision}",
            kind="apply_setback",
            actor_id="gm",
            expected_revision=state.revision,
            rule_id=rule,
            target_actor_id=actor,
        ),
        authenticated_actor_id="gm",
    )


async def choose(
    cid: str, play: PlayService, actor: str, rule: str, target: str = "a"
) -> PlayState:
    state = await read(cid, play)
    return await RecoveryService(play).execute(
        cid,
        RecoveryCommand(
            id=f"{rule}:{state.revision}",
            kind="choose_recovery",
            actor_id=actor,
            expected_revision=state.revision,
            rule_id=rule,
            target_actor_id=target,
        ),
        authenticated_actor_id=actor,
    )


async def wait(cid: str, play: PlayService, actor: str, ticks: int = 1) -> PlayState:
    state = await read(cid, play)
    return await PartyService(play).execute(
        cid,
        PartyCommand(
            kind="queue_activity",
            id=f"wait:{state.revision}",
            actor_id=actor,
            expected_revision=state.revision,
            activity_json=Wait(
                id="inner", actor_id=actor, expected_revision=state.revision, ticks=ticks
            ).model_dump_json(),
        ),
        authenticated_actor_id=actor,
    )


@pytest.mark.parametrize("postgres", [False, True])
async def test_capture_captive_action_rescue_reunion_restart_and_custody(
    tmp_path: Path, postgres: bool
) -> None:
    cid, play = await prepare(tmp_path, postgres=postgres)
    state = await setback(cid, play, "capture")
    assert captive(state, "a") and state.objectives.outcome == "ongoing"
    assert all(i.owner_id != "a" for i in state.resources.items)
    assert len(state.party.groups) == 2
    access = CampaignAccess(play)
    assert (await access.read(cid, principal_id="bob"))["captivity"] == ()
    with pytest.raises(ValidationError, match="evidence"):
        await choose(cid, play, "b", "rescue")
    state = await choose(cid, play, "a", "observe")
    assert state.resources.game_time == 0 and state.recovery.decisions[-1].status == "pending"
    play = PlayService(play.store, play.engine, rng=Dice())
    state = await wait(cid, play, "b")
    assert state.resources.game_time == 1
    assert ("b", "captured") in state.world.knowledge
    state = await choose(cid, play, "b", "rescue")
    assert captive(state, "a") is not None
    state = await choose(cid, play, "a", "assist")
    assert captive(state, "a") is None and state.resources.game_time == 2
    assert all(i.owner_id != "a" for i in state.resources.items)
    assert ("b", "clue") not in state.world.knowledge
    state = await choose(cid, play, "a", "gear")
    state = await wait(cid, play, "b")
    assert any(i.owner_id == "a" for i in state.resources.items)
    group = next(g for g in state.party.groups if "b" in g.actor_ids)
    await access.execute(
        cid,
        PartyCommand(
            kind="rejoin_party",
            id="reunion",
            actor_id="a",
            expected_revision=state.revision,
            target_id=group.id,
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    state = await read(cid, play)
    assert len(state.party.groups) == 1
    assert ("b", "clue") not in state.world.knowledge
    history = await play.store.history(cid)
    assert history[-1].state_after["play_json"] == state.model_dump_json()
    for event in history:
        play.engine.validate(PlayState.model_validate_json(event.state_after["play_json"]))


async def test_duplicate_concurrent_setback_and_illegal_captive_bypass(tmp_path: Path) -> None:
    import asyncio

    cid, play = await prepare(tmp_path)
    service = RecoveryService(play)
    command = RecoveryCommand(
        kind="apply_setback",
        id="capture",
        actor_id="gm",
        expected_revision=0,
        rule_id="capture",
        target_actor_id="a",
    )
    results = await asyncio.gather(
        *(service.execute(cid, command, authenticated_actor_id="gm") for _ in range(2))
    )
    assert results[0] == results[1] and results[0].revision == 1
    with pytest.raises(ConflictError):
        await service.execute(
            cid, command.model_copy(update={"target_actor_id": "b"}), authenticated_actor_id="gm"
        )
    with pytest.raises(ValidationError):
        await wait(cid, play, "a")
    with pytest.raises(ValidationError):
        await play.execute(
            cid,
            Wait(id="bypass", actor_id="a", expected_revision=1, ticks=1),
            authenticated_actor_id="a",
        )
    with pytest.raises(AuthorizationError):
        await CampaignAccess(play).execute(
            cid, command.model_dump(mode="json"), principal_id="alice"
        )
    assert (await read(cid, play)).revision == 1


async def test_failed_escape_adjudication_and_partial_rescue(tmp_path: Path) -> None:
    dice = Dice(5)
    cid, play = await prepare(tmp_path, dice=dice)
    await setback(cid, play, "capture")
    state = await choose(cid, play, "a", "unsupported")
    assert (
        state.recovery.decisions[-1].status == "adjudication_required"
        and state.resources.game_time == 0
    )
    await choose(cid, play, "a", "escape")
    state = await wait(cid, play, "b")
    assert state.recovery.decisions[-1].status == "failed"
    assert dice.calls == 3 and captive(state, "a")
    assert json.loads(state.recovery.decisions[-1].check_json or "{}")["dice"] == [6, 6, 6]
    state = await setback(cid, play, "capture", "b")
    # One captive can escape without releasing the other.
    play.rng = Dice()
    await choose(cid, play, "a", "escape")
    state = await choose(cid, play, "b", "observe", "b")
    assert captive(state, "a") is None and captive(state, "b") is not None
    assert state.objectives.outcome == "ongoing"


async def test_retreat_rest_costs_npc_budget_and_replacement_legality(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    await setback(cid, play, "hurt")
    await choose(cid, play, "a", "rest")
    state = await wait(cid, play, "b", 2)
    assert "unconscious" not in state.actors[0].conditions
    assert state.resources.game_time == 2
    assert state.npcs.progress[0].spent_actions == 2
    before_items = sum(i.quantity for i in state.resources.items if i.definition_id == "potion")
    await setback(cid, play, "death")
    await choose(cid, play, "a", "replace")
    state = await wait(cid, play, "b")
    assert not state.recovery.dead_actor_ids and len(state.recovery.replacements) == 1
    assert (
        sum(i.quantity for i in state.resources.items if i.definition_id == "potion")
        == before_items
    )
    state = await setback(cid, play, "mission-lost")
    assert state.objectives.outcome == "failure"
    assert state.recovery.impossible_objective_ids == ("mission",)


async def test_npc_unknown_proposal_falls_back_and_finite_loop(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    await NPCService(play).propose(
        cid,
        NPCProposal(
            id="proposal", actor_id="gm", expected_revision=0, plan_id="patrol", action_id="unknown"
        ),
        authenticated_gm_id="gm",
    )
    state = await wait(cid, play, "a", 100)
    assert len([d for d in state.npcs.decisions if d.status != "proposed"]) == 4
    assert state.npcs.progress[0].spent_actions == 4
    assert ("b", "clue") not in state.world.knowledge
    assert state.npcs.decisions[0].action_id == "patrol"
    assert state.npcs.decisions[-1].status == "rejected"
    original = state
    assert play.checkpoint(state) == original


async def test_captor_transfer_shared_time_and_old_custody(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path, transfer=True)
    await setback(cid, play, "capture")
    await choose(cid, play, "a", "observe")
    state = await wait(cid, play, "b")
    assert any(d.status == "committed" for d in state.npcs.decisions)
    held = captive(state, "a")
    assert held is not None and held.scene_id == "alley-scene"
    assert len(state.recovery.captivity) == 2
    assert state.recovery.decisions[-1].status == "committed"
    assert (
        state.recovery.captivity[-1].confiscated_item_ids
        == state.recovery.captivity[0].confiscated_item_ids
    )


async def test_two_authenticated_players_capture_rescue_and_private_stream(tmp_path: Path) -> None:
    import aiohttp
    from aiohttp import web

    from wayfarer.transport.campaign_api import create_campaign_app

    cid, play = await prepare(tmp_path)
    app = create_campaign_app(
        CampaignAccess(play),
        {"alice-secret": "alice", "bob-secret": "bob", "gm-secret": "gm"},
        legacy_routes=True,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}/campaigns/{cid}"

    async def send(
        client: aiohttp.ClientSession, principal: str, body: dict[str, object]
    ) -> dict[str, object]:
        async with client.post(
            url + "/commands", json=body, headers={"Authorization": f"Bearer {principal}-secret"}
        ) as response:
            assert response.status == 200, await response.text()
            from wayfarer.validation import mapping

            return mapping(await response.json())

    try:
        async with aiohttp.ClientSession() as client:
            command = RecoveryCommand(
                kind="apply_setback",
                id="capture",
                actor_id="gm",
                expected_revision=0,
                rule_id="capture",
                target_actor_id="a",
            ).model_dump(mode="json")
            await send(client, "gm", command)
            await send(client, "gm", command)
            await send(
                client,
                "alice",
                RecoveryCommand(
                    kind="choose_recovery",
                    id="look",
                    actor_id="a",
                    expected_revision=1,
                    rule_id="observe",
                    target_actor_id="a",
                ).model_dump(mode="json"),
            )
            await send(
                client,
                "bob",
                PartyCommand(
                    kind="queue_activity",
                    id="wait",
                    actor_id="b",
                    expected_revision=2,
                    activity_json=Wait(
                        id="inner", actor_id="b", expected_revision=2, ticks=1
                    ).model_dump_json(),
                ).model_dump(mode="json"),
            )
            await send(
                client,
                "bob",
                RecoveryCommand(
                    kind="choose_recovery",
                    id="rescue",
                    actor_id="b",
                    expected_revision=3,
                    rule_id="rescue",
                    target_actor_id="a",
                ).model_dump(mode="json"),
            )
            await send(
                client,
                "alice",
                RecoveryCommand(
                    kind="choose_recovery",
                    id="inside",
                    actor_id="a",
                    expected_revision=4,
                    rule_id="assist",
                    target_actor_id="a",
                ).model_dump(mode="json"),
            )
            assert captive(await read(cid, play), "a") is None
            async with client.get(
                url + "/events?after=0", headers={"Authorization": "Bearer bob-secret"}
            ) as response:
                text = await response.text()
                assert '"restraints"' not in text and '"inside"' not in text
    finally:
        await runner.cleanup()


async def test_illegal_replacement_rejected_and_resupply_finite(tmp_path: Path) -> None:
    from wayfarer.engine.character.compiler import Purchase

    policy = policies()
    proposal = actor_setup().proposal
    illegal = proposal.model_copy(
        update={
            "draft": proposal.draft.model_copy(
                update={"purchases": (Purchase(definition_id="attribute:st", amount=100),)}
            )
        }
    )
    policy = policy.model_copy(
        update={
            "options": tuple(
                o.model_copy(update={"replacement": illegal}) if o.kind == "replace" else o
                for o in policy.options
            )
        }
    )
    cid, play = await prepare(tmp_path, recovery=policy)
    await setback(cid, play, "death")
    await choose(cid, play, "a", "replace")
    state = await wait(cid, play, "b")
    assert state.recovery.dead_actor_ids == ("a",) and not state.recovery.replacements
    assert state.recovery.decisions[-1].status == "rejected"
    # Another player can buy from finite stock while the dead character's choice remains resumable.
    await choose(cid, play, "b", "resupply", "b")
    state = await choose(cid, play, "a", "replace")
    assert state.recovery.decisions[-2].status == "rejected"  # NPC consumed remaining stock first
    assert not any(i.id == "stock" for i in state.resources.items)


async def test_capture_and_rescue_during_resolved_combat_boundary(tmp_path: Path) -> None:
    from test_combat import start

    from wayfarer.orchestration.combat import CombatService

    cid, play = await prepare(tmp_path, combat=True)
    await CombatService(play).execute(cid, start(), authenticated_actor_id="gm")
    state = await setback(cid, play, "capture")
    assert state.encounters[0].status == "completed"
    assert state.encounters[0].completion_reason == "setback:capture"
    assert not any(i.ready for i in state.resources.items if i.owner_id == "a")
    await choose(cid, play, "a", "assist")
    await choose(cid, play, "b", "assist")
    await choose(cid, play, "b", "rescue")
    state = await choose(cid, play, "a", "assist")
    assert captive(state, "a") is None
    assert state.encounters[0].status == "completed"
    play.engine.validate(state)


async def test_resupply_transfers_stock_once_without_minting(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    state = await choose(cid, play, "b", "resupply", "b")
    assert state.recovery.decisions[-1].status == "committed"
    assert sum(i.quantity for i in state.resources.items if i.owner_id == "b") == 1
    before = sum(i.quantity for i in state.resources.items)
    state = await choose(cid, play, "b", "resupply", "b")
    assert state.recovery.decisions[-1].status == "rejected"
    assert sum(i.quantity for i in state.resources.items) == before


async def test_capture_failure_rolls_back_custody_and_revision(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path, guard_capacity=0)
    before = await read(cid, play)
    with pytest.raises(ValidationError):
        await setback(cid, play, "capture")
    assert await read(cid, play) == before
    assert await play.store.history(cid) == []


async def test_incapacitation_does_not_release_captive_or_allow_escape(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    await setback(cid, play, "capture")
    state = await setback(cid, play, "hurt")
    assert captive(state, "a") is not None
    with pytest.raises(ValidationError, match="Incapacitated"):
        await choose(cid, play, "a", "escape")


async def test_authored_captive_communication_only_sends_known_facts(tmp_path: Path) -> None:
    policy = policies()
    policy = policy.model_copy(
        update={
            "options": policy.options
            + (
                RecoveryOption(
                    id="message",
                    kind="communicate",
                    scene_id="dock-scene",
                    actor_ids=("a",),
                    target_actor_ids=("a",),
                    recipient_actor_ids=("b",),
                    communication_fact_ids=("clue",),
                ),
            )
        }
    )
    cid, play = await prepare(tmp_path, recovery=policy)
    await setback(cid, play, "capture")
    await choose(cid, play, "a", "message")
    state = await wait(cid, play, "b")
    assert state.recovery.decisions[-1].status == "rejected"
    assert ("b", "clue") not in state.world.knowledge
    await choose(cid, play, "a", "assist")
    await wait(cid, play, "b")
    await choose(cid, play, "a", "message")
    state = await wait(cid, play, "b")
    assert ("b", "clue") in state.world.knowledge
    assert captive(state, "a") is not None


async def test_downtime_advancement_reuses_compiler_and_earned_point_balance(
    tmp_path: Path,
) -> None:
    from wayfarer.engine.character.compiler import Purchase
    from wayfarer.orchestration.advancement import AdvancementService, GrantPoints, _balance

    proposal = actor_setup().proposal
    draft = proposal.draft.model_copy(
        update={
            "purchases": tuple(
                Purchase(definition_id=p.definition_id, amount=8)
                if p.definition_id == "skill:observation"
                else p
                for p in proposal.draft.purchases
            )
        }
    )
    policy = policies()
    policy = policy.model_copy(
        update={
            "options": policy.options
            + (
                RecoveryOption(
                    id="train",
                    kind="advance",
                    scene_id="dock-scene",
                    actor_ids=("a",),
                    target_actor_ids=("a",),
                    advancement=draft,
                    ticks=2,
                ),
            )
        }
    )
    cid, play = await prepare(tmp_path, recovery=policy)
    state = await choose(cid, play, "a", "train")
    assert state.recovery.decisions[-1].status == "rejected"
    await AdvancementService(play).grant(
        cid,
        GrantPoints(
            id="earn",
            actor_id="gm",
            target_actor_id="a",
            expected_revision=state.revision,
            points=4,
            reason="Earned adventure reward",
        ),
        authenticated_gm_id="gm",
    )
    state = await choose(cid, play, "a", "train")
    assert state.recovery.decisions[-1].status == "committed"
    assert state.actors[0].proposal.draft == draft
    assert _balance(state, "a") == 0
    assert state.resources.game_time == 4
    play.engine.validate(state)
