"""Wave 9 acceptance fixtures through durable service and authenticated boundaries."""

import asyncio
import json
import os
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from test_actions import Dice, actor_setup, campaign, resource_seed
from test_combat import combat_engine, resources, start
from test_gurps_melee import setup as melee_setup
from test_reinforcements import escalation
from test_scenes import configured

from wayfarer.engine.simulation.action_engine import ActionEngine
from wayfarer.engine.simulation.actions import Inspect, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.objectives import (
    Objective,
    ObjectiveRules,
    Predicate,
    Reward,
)
from wayfarer.engine.simulation.campaign.party import CrossSceneEffect, PartyRules
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.profiles import AttackProfile, ProtectionProfile
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.resources import Item, Owner, Scheduled
from wayfarer.engine.simulation.social.noncombat import Approach, NoncombatRule, NoncombatRules
from wayfarer.errors import (
    AuthorizationError,
    ConflictError,
    ProviderError,
    ProviderTimeoutError,
    ValidationError,
)
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    HexJoinPlacement,
    JoinEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.noncombat import NoncombatCommand, NoncombatService
from wayfarer.orchestration.objectives import ObjectiveCommand, ObjectiveService
from wayfarer.orchestration.party import PartyCommand, PartyService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.providers import Orchestrator, ProviderReply, ProviderRequest, Usage
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.postgres import AsyncPostgresStore


async def prepare(
    tmp_path: Path,
    *,
    objectives: ObjectiveRules | None = None,
    noncombat: NoncombatRules | None = None,
    party: bool = False,
    dice: Dice | None = None,
    backend: str = "sqlite",
) -> tuple[str, PlayService]:
    base, world = configured()
    combat = combat_engine().rules.combat
    assert combat is not None
    rules = base.rules.model_copy(
        update={
            "objectives": objectives,
            "noncombat": noncombat,
            "combat": combat,
            "party": PartyRules(
                id="party",
                version=1,
                effects=(
                    CrossSceneEffect(
                        id="alarm",
                        source_scene_id="dock-scene",
                        recipient_actor_ids=("b",),
                        fact_id="clue",
                        delay=1,
                    ),
                ),
            )
            if party
            else None,
        }
    )
    engine = ActionEngine(base.reviewer, base.resources, rules)
    store: AsyncSQLiteStore | AsyncPostgresStore
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        if url is None:
            pytest.skip("WAYFARER_TEST_DATABASE_URL is not configured")
        store = AsyncPostgresStore(url, 10)
    else:
        store = AsyncSQLiteStore(tmp_path / "wave9.sqlite", 10)
    play = PlayService(store, engine, rng=dice or Dice())
    seed = resource_seed().model_copy(
        update={
            "owners": resource_seed().owners + (Owner(actor_id="b", capacity=100),),
            "items": resource_seed().items
            + (Item(id="escrow", definition_id="potion", owner_id="b", quantity=1),),
            "scheduled": (
                Scheduled(id="deadline-event", due=2, kind="consequence", target_id="a"),
            ),
        }
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


def goals(*, deadline: int | None = None) -> ObjectiveRules:
    return ObjectiveRules(
        id="rescue",
        version=1,
        deadline=deadline,
        objectives=(
            Objective(
                id="rescue",
                title="Discover the rescue route",
                visible_to=("a",),
                predicates=(Predicate(kind="known", subject_id="a", value="clue"),),
            ),
        ),
        rewards=(
            Reward(id="points", actor_id="a", points=4),
            Reward(id="supply", actor_id="a", item_id="escrow"),
        ),
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_objective_settlement_is_atomic_exactly_once_and_terminal(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, objectives=goals(), backend=backend)
    action = Inspect(id="discover", actor_id="a", expected_revision=0, target_id="chest")
    results = await asyncio.gather(
        *(play.execute(cid, action, authenticated_actor_id="a") for _ in range(4))
    )
    assert len(set(results)) == 1
    state = play._load(await play.store.read(cid))
    assert state.objectives.outcome == "success"
    assert sum(e.points for e in state.advancement) == 4
    assert next(i for i in state.resources.items if i.id == "escrow").owner_id == "a"
    terminal = state.objectives
    await ObjectiveService(play).execute(
        cid,
        ObjectiveCommand(kind="abandon_scenario", id="late", actor_id="gm", expected_revision=1),
        authenticated_actor_id="gm",
    )
    assert play._load(await play.store.read(cid)).objectives == terminal
    assert await play.store.replay(cid) == await play.store.read(cid)


@pytest.mark.parametrize("deadline,expected", [(2, "failure"), (1, "failure"), (3, "success")])
async def test_deadline_boundary_precedes_simultaneous_success(
    tmp_path: Path, deadline: int, expected: str
) -> None:
    cid, play = await prepare(tmp_path, objectives=goals(deadline=deadline))
    await play.execute(
        cid,
        Inspect(id="inspect", actor_id="a", expected_revision=0, target_id="chest"),
        authenticated_actor_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert state.objectives.outcome == expected
    assert bool(state.advancement) == (expected == "success")


async def test_failure_abandonment_and_invalid_references(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path, objectives=goals(deadline=1))
    await play.execute(
        cid, Wait(id="wait", actor_id="a", expected_revision=0, ticks=1), authenticated_actor_id="a"
    )
    assert play._load(await play.store.read(cid)).objectives.outcome == "failure"
    with pytest.raises(ValueError, match="contradictory"):
        objective = goals().objectives[0]
        ObjectiveRules(
            id="bad",
            version=1,
            objectives=(
                objective.model_copy(
                    update={
                        "predicates": objective.predicates
                        + (objective.predicates[0].model_copy(update={"negate": True}),)
                    }
                ),
            ),
        )
    bad = goals().model_copy(update={"rewards": (Reward(id="bad", actor_id="missing", points=3),)})
    with pytest.raises(ValidationError, match="Unresolved"):
        await prepare(tmp_path / "bad", objectives=bad)


@pytest.mark.parametrize(
    "category", ["negotiation", "investigation", "infiltration", "pursuit", "hazard"]
)
async def test_noncombat_categories_failure_progress_restart_withdraw(
    tmp_path: Path,
    category: Literal["negotiation", "investigation", "infiltration", "pursuit", "hazard"],
) -> None:
    rules = NoncombatRules(
        id="encounters",
        version=1,
        encounters=(
            NoncombatRule(
                id="challenge",
                scene_id="dock-scene",
                category=category,
                stakes="Reach the captive before the guard arrives",
                required_progress=2,
                approaches=(
                    Approach(
                        id="careful",
                        check_rule_id="inspect",
                        failure_progress=1,
                        failure_fact_ids=("clue",),
                        fatigue_cost=1,
                    ),
                ),
            ),
        ),
    )
    cid, play = await prepare(tmp_path, noncombat=rules, dice=Dice(5))
    service = NoncombatService(play)
    await service.execute(
        cid,
        NoncombatCommand(
            kind="start_noncombat",
            id="start",
            actor_id="a",
            expected_revision=0,
            encounter_id="one",
            selection_id="challenge",
        ),
        authenticated_actor_id="a",
    )
    first = await service.execute(
        cid,
        NoncombatCommand(
            kind="approach_noncombat",
            id="first",
            actor_id="a",
            expected_revision=1,
            encounter_id="one",
            selection_id="careful",
        ),
        authenticated_actor_id="a",
    )
    assert first.progress == 1 and first.failures == 1 and first.status == "choice"
    assert first.pending_choices == ("careful",)
    restarted = NoncombatService(PlayService(play.store, play.engine, rng=Dice(5)))
    second = await restarted.execute(
        cid,
        NoncombatCommand(
            kind="approach_noncombat",
            id="second",
            actor_id="a",
            expected_revision=2,
            encounter_id="one",
            selection_id="careful",
        ),
        authenticated_actor_id="a",
    )
    assert second.status == "success" and second.failures == 2
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_combat_damage_retry_armor_incapacitation_and_replay(tmp_path: Path) -> None:
    base = combat_engine()
    rules = base.rules.combat
    assert rules is not None
    rules = rules.model_copy(
        update={
            "attacks": (AttackProfile(definition_id="sword", damage_bonus=20),),
            "protection": (ProtectionProfile(definition_id="sword", resistance=2),),
        }
    )
    engine = ActionEngine(
        base.reviewer, base.resources, base.rules.model_copy(update={"combat": rules})
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "injury.sqlite", 10), engine, rng=Dice())
    initial = campaign(engine)
    from test_actions import world

    await play.create(
        initial,
        world(),
        resources(),
        (actor_setup(), actor_setup().model_copy(update={"actor_id": "b"})),
    )
    service = CombatService(play)
    opening = start().model_copy(
        update={
            "placements": (
                Placement(actor_id="a", position=GridPoint(x=0, y=0)),
                Placement(actor_id="b", position=GridPoint(x=1, y=0)),
            )
        }
    )
    await service.execute(initial["id"], opening, authenticated_actor_id="gm")
    await service.execute(
        initial["id"],
        TakeCombatTurn(
            id="strike",
            actor_id="a",
            expected_revision=1,
            encounter_id="fight",
            maneuver="attack",
            target_id="b",
            item_id="sword-a",
        ),
        authenticated_actor_id="a",
    )
    defense = ChooseDefense(
        id="defend", actor_id="b", expected_revision=2, encounter_id="fight", defense="none"
    )
    results = await asyncio.gather(
        *(service.execute(initial["id"], defense, authenticated_actor_id="b") for _ in range(4))
    )
    assert results[0] == results[-1]
    trace = results[0].injury
    assert (
        trace is not None and trace.resistance == 2 and trace.injury == 19 and trace.hp_after == 0
    )
    state = play._load(await play.store.read(initial["id"]))
    assert state.encounters[0].status == "completed"
    assert state.actors[1].conditions == ("unconscious",)
    assert len(state.encounters[0].wounds) == 1
    assert await play.store.replay(initial["id"]) == await play.store.read(initial["id"])


async def split(cid: str, access: CampaignAccess) -> None:
    await access.execute(
        cid,
        PartyCommand(
            kind="split_party", id="split", actor_id="a", expected_revision=0, target_id="scouts"
        ).model_dump(mode="json"),
        principal_id="alice",
    )


def queued(actor: str, revision: int, ticks: int, key: str) -> PartyCommand:
    return PartyCommand(
        kind="queue_activity",
        id=key,
        actor_id=actor,
        expected_revision=revision,
        activity_json=Wait(
            id=key, actor_id=actor, expected_revision=revision, ticks=ticks
        ).model_dump_json(),
    )


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_split_shared_time_alarm_rejoin_and_scoped_streams(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, party=True, backend=backend)
    access = CampaignAccess(play)
    await split(cid, access)
    with pytest.raises(AuthorizationError):
        await access.execute(
            cid, queued("a", 1, 2, "forged").model_dump(mode="json"), principal_id="bob"
        )
    await access.execute(
        cid,
        PartyCommand(
            kind="signal_scene", id="signal", actor_id="a", expected_revision=1, target_id="alarm"
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    await access.execute(
        cid, queued("a", 2, 2, "a-wait").model_dump(mode="json"), principal_id="alice"
    )
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 0 and ("b", "clue") not in state.world.knowledge
    await access.execute(
        cid, queued("b", 3, 2, "b-wait").model_dump(mode="json"), principal_id="bob"
    )
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 2
    assert ("b", "clue") in state.world.knowledge and ("a", "clue") not in state.world.knowledge
    assert state.resources.fired.count("deadline-event") == 1
    assert len(state.party.receipts) == 2 and all(
        r.status == "committed" for r in state.party.receipts
    )
    await access.execute(
        cid,
        PartyCommand(
            kind="rejoin_party",
            id="rejoin",
            actor_id="a",
            expected_revision=4,
            target_id="group:dock-scene",
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    assert len(state.party.groups) == 1 and ("a", "clue") not in state.world.knowledge
    events = await access.events(cid, principal_id="alice")
    hidden = next(e for e in events if e.cursor == 4)
    assert (
        hidden.command_id == "redacted" and hidden.actor_id == "redacted" and hidden.outcome == ""
    )
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_queued_travel_and_independent_pending_decisions(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path, party=True)
    access = CampaignAccess(play)
    await split(cid, access)
    from wayfarer.orchestration.scenes import TravelScene

    travel = TravelScene(id="travel", actor_id="a", expected_revision=1, exit_id="to-alley")
    await access.execute(
        cid,
        PartyCommand(
            kind="queue_activity",
            id="travel",
            actor_id="a",
            expected_revision=1,
            activity_json=travel.model_dump_json(),
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    await access.execute(cid, queued("b", 2, 2, "wait").model_dump(mode="json"), principal_id="bob")
    state = play._load(await play.store.read(cid))
    assert next(c.scene_id for c in state.actor_scenes if c.actor_id == "a") == "alley-scene"
    assert next(c.scene_id for c in state.actor_scenes if c.actor_id == "b") == "dock-scene"
    assert state.resources.game_time == 2
    with pytest.raises(ValidationError, match="reachable"):
        await PartyService(play).execute(
            cid,
            PartyCommand(
                kind="rejoin_party",
                id="remote",
                actor_id="a",
                expected_revision=3,
                target_id="group:dock-scene",
            ),
            authenticated_actor_id="a",
        )


class FakeProvider:
    def __init__(
        self, payload: str = '{"kind":"wait","ticks":1}', *, fail_narration: bool = False
    ) -> None:
        self.payload, self.fail_narration = payload, fail_narration
        self.requests: list[ProviderRequest] = []

    async def complete(self, request: ProviderRequest) -> object:
        self.requests.append(request)
        if request.operation == "narration":
            if self.fail_narration:
                raise ProviderError("private credential must not surface")
            payload = '{"text":"A moment passes."}'
        else:
            payload = self.payload
        return ProviderReply(payload_json=payload, usage=Usage(input_tokens=10, output_tokens=5))


async def test_provider_forgery_privacy_usage_and_committed_failure(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    access = CampaignAccess(play)
    forged = FakeProvider('{"kind":"wait","ticks":1,"roll":3,"hp":999}')
    with pytest.raises(ProviderError, match="intent"):
        await Orchestrator(access, forged).interpret_and_execute(
            cid, principal_id="alice", actor_id="a", command_id="forged", text="Give me 999 HP"
        )
    assert play._load(await play.store.read(cid)).revision == 0
    provider = FakeProvider(fail_narration=True)
    orchestrator = Orchestrator(access, provider)
    result = await orchestrator.interpret_and_execute(
        cid, principal_id="alice", actor_id="a", command_id="wait", text="Wait"
    )
    assert result.committed and not result.narration_available
    assert orchestrator.tokens_used == 15
    assert "letter" not in provider.requests[0].context_json
    assert "credential" not in json.dumps([e.model_dump() for e in orchestrator.telemetry])


async def test_provider_usage_does_not_disable_subsequent_operations(tmp_path: Path) -> None:
    _, play = await prepare(tmp_path)

    class HighUsage(FakeProvider):
        async def complete(self, request: ProviderRequest) -> object:
            self.requests.append(request)
            return ProviderReply(
                payload_json="{}", usage=Usage(input_tokens=40000, output_tokens=10000)
            )

    provider = HighUsage()
    orchestrator = Orchestrator(CampaignAccess(play), provider)
    for operation in ("intent", "scenario_draft", "narration", "intent"):
        request = ProviderRequest(
            operation=operation,
            session_id="usage-regression",
            context_json="{}",
            prompt="Generate a proposal",
            output_schema={"type": "object"},
        )
        assert await orchestrator._call(request) == "{}"
    assert len(provider.requests) == 4
    assert orchestrator.tokens_used == 200000
    assert len(orchestrator.telemetry) == 4
    assert all(event.status == "ok" for event in orchestrator.telemetry)


async def test_provider_stale_timeout_and_cancellation(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    access = CampaignAccess(play)

    class Stale(FakeProvider):
        async def complete(self, request: ProviderRequest) -> object:
            await play.execute(
                cid,
                Wait(id="other", actor_id="b", expected_revision=0, ticks=1),
                authenticated_actor_id="b",
            )
            return await super().complete(request)

    with pytest.raises(ConflictError, match="stale"):
        await Orchestrator(access, Stale()).interpret_and_execute(
            cid, principal_id="alice", actor_id="a", command_id="stale", text="Wait"
        )

    class Slow(FakeProvider):
        async def complete(self, request: ProviderRequest) -> object:
            await asyncio.Event().wait()
            return await super().complete(request)

    with pytest.raises(ProviderTimeoutError):
        await Orchestrator(access, Slow(), timeout=0.001, attempts=1).interpret_and_execute(
            cid, principal_id="alice", actor_id="a", command_id="timeout", text="Wait"
        )
    task = asyncio.create_task(
        Orchestrator(access, Slow()).interpret_and_execute(
            cid, principal_id="alice", actor_id="a", command_id="cancel", text="Wait"
        )
    )
    await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert play._load(await play.store.read(cid)).revision == 1


async def test_combat_barrier_long_investigation_and_reinforcement_arrival(tmp_path: Path) -> None:
    from wayfarer.engine.rules.catalog import PROTOTYPE_PACKAGE, RulesCatalog
    from wayfarer.engine.simulation.resources import ResourceEngine, ResourceState
    from wayfarer.engine.world import Entity, EntityKind
    from wayfarer.orchestration.combat import JoinEncounter

    base, original = configured()
    expanded = replace(
        original,
        entities=original.entities + (Entity("c", EntityKind.ACTOR, "Scout", location_id="dock"),),
    )
    package = replace(
        PROTOTYPE_PACKAGE, definitions=tuple(base.reviewer.compiler.definitions.values())
    )
    resource_engine = ResourceEngine(
        expanded,
        RulesCatalog((package,)),
        base.resources.rules,
        base.reviewer.compiler.policy,
        tuple(base.resources.specs.values()),
    )
    engine = ActionEngine(
        base.reviewer,
        resource_engine,
        base.rules.model_copy(
            update={
                "combat": combat_engine().rules.combat,
                "party": PartyRules(id="party", version=1),
            }
        ),
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "concurrent.sqlite", 10), engine, rng=Dice())
    initial = campaign(engine)
    seed = ResourceState(
        items=resources().items, owners=resources().owners + (Owner(actor_id="c", capacity=100),)
    )
    await play.create(
        initial,
        expanded,
        seed,
        tuple(actor_setup().model_copy(update={"actor_id": a}) for a in ("a", "b", "c")),
        (
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="charlie", role="player", actor_ids=("c",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    cid = initial["id"]
    access = CampaignAccess(play)
    await access.execute(
        cid,
        PartyCommand(
            kind="split_party", id="split", actor_id="c", expected_revision=0, target_id="scouts"
        ).model_dump(mode="json"),
        principal_id="charlie",
    )
    combat = CombatService(play)
    opening = start(revision=1).model_copy(
        update={
            "placements": (
                Placement(actor_id="a", position=GridPoint(x=0, y=0)),
                Placement(actor_id="b", position=GridPoint(x=1, y=0)),
            )
        }
    )
    await combat.execute(cid, opening, authenticated_actor_id="gm")
    await access.execute(
        cid,
        PartyCommand(
            kind="queue_activity",
            id="investigation",
            actor_id="c",
            expected_revision=2,
            activity_json=Inspect(
                id="investigate", actor_id="c", expected_revision=2, target_id="chest"
            ).model_dump_json(),
        ).model_dump(mode="json"),
        principal_id="charlie",
    )
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=3,
            encounter_id="fight",
            maneuver="attack",
            target_id="b",
            item_id="sword-a",
        ),
        authenticated_actor_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 0 and ("c", "clue") not in state.world.knowledge
    assert state.encounters[0].pending_defense is not None
    await combat.execute(
        cid,
        ChooseDefense(
            id="defend", actor_id="b", expected_revision=4, encounter_id="fight", defense="none"
        ),
        authenticated_actor_id="b",
    )
    for revision, actor in ((5, "b"), (6, "a"), (7, "b")):
        await combat.execute(
            cid,
            TakeCombatTurn(
                id=f"turn-{revision}",
                actor_id=actor,
                expected_revision=revision,
                encounter_id="fight",
                maneuver="wait",
            ),
            authenticated_actor_id=actor,
        )
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 2 and ("c", "clue") in state.world.knowledge
    assert state.party.receipts[0].status == "committed"
    await access.execute(
        cid,
        JoinEncounter(
            id="rescue-arrival",
            actor_id="c",
            expected_revision=8,
            encounter_id="fight",
            position=GridPoint(x=2, y=0),
        ).model_dump(mode="json"),
        principal_id="charlie",
    )
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].turn_order == ("a", "b", "c") and len(state.party.groups) == 1
    assert ("a", "clue") not in state.world.knowledge
    assert await play.store.replay(cid) == await play.store.read(cid)


async def test_authored_basic_hex_investigation_travel_restart_and_arrival(
    tmp_path: Path,
) -> None:
    """#328 cross-system path uses creation and authenticated commands only."""
    from test_basic_combat import start_basic

    from wayfarer.orchestration.scenes import TravelScene

    authored, authored_world = configured()
    investigation_check = authored.rules.checks[0].model_copy(
        update={
            "definition_id": "skill:broadsword",
            "package_id": "package:gurps-basic-set-4e-2004-characters",
            "package_version": "1.0.0",
        }
    )
    runtime_rules = authored.rules.model_copy(
        update={
            "party": PartyRules(id="mixed-party", version=1),
            "checks": (investigation_check,),
            "consumables": (),
        }
    )
    cid, play = await melee_setup(
        tmp_path,
        "gurps-basic-set-4e-2004",
        human=True,
        third_actor=True,
        runtime_rules=runtime_rules,
        runtime_world=authored_world,
        extra_scheduled=(Scheduled(id="mixed-deadline", due=2, kind="consequence", target_id="a"),),
        start_encounter=False,
        aware_of=("chest",),
    )
    access = CampaignAccess(play)
    await access.execute(
        cid,
        PartyCommand(
            kind="split_party",
            id="independent-c",
            actor_id="c",
            expected_revision=0,
            target_id="investigators",
        ).model_dump(mode="json"),
        principal_id="c",
    )
    opening_template = start_basic(1)
    opening = opening_template.model_copy(
        update={
            "id": "basic-opening",
            "expected_revision": 1,
            "facts": tuple(
                fact.model_copy(
                    update={
                        "provenance": fact.provenance.model_copy(update={"declared_revision": 1})
                    }
                )
                for fact in opening_template.facts
            ),
        }
    )
    await access.execute(cid, opening.model_dump(mode="json"), principal_id="gm")
    attack = TakeCombatTurn(
        id="basic-attack",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="attack",
        target_id="b",
        item_id="sword-a",
        mode_id="swing",
    )
    first = await access.execute(cid, attack.model_dump(mode="json"), principal_id="a")

    # A disconnected defender reloads the exact pending decision; an attacker retry
    # receives the durable response rather than another roll or turn.
    assert isinstance(play.store, AsyncSQLiteStore)
    restarted_play = PlayService(AsyncSQLiteStore(play.store.path), play.engine, rng=Dice())
    restarted = CampaignAccess(restarted_play)
    pending = restarted_play._load(await restarted_play.store.read(cid))
    assert pending.encounters[0].pending_defense is not None
    assert await restarted.execute(cid, attack.model_dump(mode="json"), principal_id="a") == first

    # Competing commands at one revision preserve CAS. The committed pause does not
    # resolve or discard the other group's combat decision.
    pauses = await asyncio.gather(
        *(
            restarted.execute(
                cid,
                PartyCommand(
                    kind="pause_group",
                    id=command_id,
                    actor_id="c",
                    expected_revision=3,
                ).model_dump(mode="json"),
                principal_id="c",
            )
            for command_id in ("pause-c", "pause-c-race")
        ),
        return_exceptions=True,
    )
    assert sum(isinstance(result, ConflictError) for result in pauses) == 1
    still_pending = restarted_play._load(await restarted_play.store.read(cid))
    assert still_pending.encounters[0].pending_defense is not None
    await restarted.execute(
        cid,
        PartyCommand(
            kind="resume_group", id="resume-c", actor_id="c", expected_revision=4
        ).model_dump(mode="json"),
        principal_id="c",
    )

    investigation = Inspect(
        id="inspect-letter", actor_id="c", expected_revision=5, target_id="chest"
    )
    await restarted.execute(
        cid,
        PartyCommand(
            kind="queue_activity",
            id="investigation",
            actor_id="c",
            expected_revision=5,
            activity_json=investigation.model_dump_json(),
        ).model_dump(mode="json"),
        principal_id="c",
    )
    await restarted.execute(
        cid,
        ChooseDefense(
            id="basic-defense",
            actor_id="b",
            expected_revision=6,
            encounter_id="fight",
            defense="none",
        ).model_dump(mode="json"),
        principal_id="b",
    )
    await restarted.execute(
        cid,
        TakeCombatTurn(
            id="basic-b-1",
            actor_id="b",
            expected_revision=7,
            encounter_id="fight",
            maneuver="do_nothing",
        ).model_dump(mode="json"),
        principal_id="b",
    )
    at_one = restarted_play._load(await restarted_play.store.read(cid))
    assert at_one.resources.game_time == 1
    assert ("c", "clue") not in at_one.world.knowledge
    for revision, actor in ((8, "a"), (9, "b")):
        await restarted.execute(
            cid,
            TakeCombatTurn(
                id=f"basic-{actor}-2",
                actor_id=actor,
                expected_revision=revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ).model_dump(mode="json"),
            principal_id=actor,
        )
    at_two = restarted_play._load(await restarted_play.store.read(cid))
    assert at_two.resources.game_time == 2
    assert at_two.resources.fired.count("mixed-deadline") == 1
    assert ("c", "clue") in at_two.world.knowledge
    assert ("a", "clue") not in at_two.world.knowledge

    # The explicit representation migration preserves round/turn state and uses
    # the exact selected Basic profile; hiding or showing a map is not the switch.
    before_migration = at_two.encounters[0]
    await restarted.execute(
        cid,
        escalation(revision=10, b_position=Hex(q=1, r=0)).model_dump(mode="json"),
        principal_id="gm",
    )
    restarted_play = restarted_play.for_campaign(await restarted_play.store.read(cid))
    restarted = CampaignAccess(restarted_play)
    migrated = restarted_play._load(await restarted_play.store.read(cid)).encounters[0]
    assert migrated.spatial_kind == "hex"
    assert (migrated.round, migrated.current_actor_id) == (
        before_migration.round,
        before_migration.current_actor_id,
    )

    travel = TravelScene(id="travel-c", actor_id="c", expected_revision=11, exit_id="to-alley")
    await restarted.execute(
        cid,
        PartyCommand(
            kind="queue_activity",
            id="travel-c",
            actor_id="c",
            expected_revision=11,
            activity_json=travel.model_dump_json(),
        ).model_dump(mode="json"),
        principal_id="c",
    )
    for revision, actor in ((12, "a"), (13, "b")):
        await restarted.execute(
            cid,
            TakeCombatTurn(
                id=f"hex-{actor}-3",
                actor_id=actor,
                expected_revision=revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ).model_dump(mode="json"),
            principal_id=actor,
        )
    at_three = restarted_play._load(await restarted_play.store.read(cid))
    assert at_three.resources.game_time == 3
    assert next(s.scene_id for s in at_three.actor_scenes if s.actor_id == "c") == "dock-scene"
    for revision, actor in ((14, "a"), (15, "b")):
        await restarted.execute(
            cid,
            TakeCombatTurn(
                id=f"hex-{actor}-4",
                actor_id=actor,
                expected_revision=revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ).model_dump(mode="json"),
            principal_id=actor,
        )
    arrived = restarted_play._load(await restarted_play.store.read(cid))
    assert arrived.resources.game_time == 4
    assert next(s.scene_id for s in arrived.actor_scenes if s.actor_id == "c") == "alley-scene"

    return_trip = TravelScene(id="return-c", actor_id="c", expected_revision=16, exit_id="return")
    await restarted.execute(
        cid,
        PartyCommand(
            kind="queue_activity",
            id="return-c",
            actor_id="c",
            expected_revision=16,
            activity_json=return_trip.model_dump_json(),
        ).model_dump(mode="json"),
        principal_id="c",
    )
    for revision, actor in ((17, "a"), (18, "b")):
        await restarted.execute(
            cid,
            TakeCombatTurn(
                id=f"hex-{actor}-5",
                actor_id=actor,
                expected_revision=revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ).model_dump(mode="json"),
            principal_id=actor,
        )
    synchronized = restarted_play._load(await restarted_play.store.read(cid))
    assert synchronized.resources.game_time == 5
    assert next(s.scene_id for s in synchronized.actor_scenes if s.actor_id == "c") == "dock-scene"
    inventory = tuple(
        (item.id, item.owner_id, item.quantity) for item in synchronized.resources.items
    )
    await restarted.execute(
        cid,
        JoinEncounter(
            id="join-c",
            actor_id="c",
            expected_revision=19,
            encounter_id="fight",
            placement=HexJoinPlacement(position=Hex(q=0, r=1), facing=3),
        ).model_dump(mode="json"),
        principal_id="c",
    )
    final = restarted_play._load(await restarted_play.store.read(cid))
    assert final.encounters[0].turn_order == ("a", "b", "c")
    assert (
        tuple((item.id, item.owner_id, item.quantity) for item in final.resources.items)
        == inventory
    )
    assert final.resources.fired.count("mixed-deadline") == 1
    assert ("c", "clue") in final.world.knowledge and ("a", "clue") not in final.world.knowledge
    assert await restarted_play.store.replay(cid) == await restarted_play.store.read(cid)
    from wayfarer.engine.rules.conformance import CAPABILITIES, CoverageStatus

    assert restarted_play.engine.reviewer.compiler.statistics_profile == ("gurps-basic-set-4e-2004")
    assert all(
        CAPABILITIES[identifier].status is CoverageStatus.PARTIAL
        for identifier in (
            "gurps.combat.melee_attack",
            "gurps.combat.active_defense",
            "gurps.combat.maneuvers",
            "gurps.combat.turn_timing",
            "gurps.tactical.hex_movement",
            "gurps.tactical.facing",
            "gurps.tactical.visibility",
        )
    )


async def test_independent_noncombat_choices_pause_resume_and_rejected_choice(
    tmp_path: Path,
) -> None:
    rules = NoncombatRules(
        id="encounters",
        version=1,
        encounters=(
            NoncombatRule(
                id="challenge",
                scene_id="dock-scene",
                category="hazard",
                stakes="Avoid the flood",
                required_progress=5,
                approaches=(Approach(id="careful", check_rule_id="inspect"),),
            ),
        ),
    )
    cid, play = await prepare(tmp_path, party=True, noncombat=rules)
    access = CampaignAccess(play)
    await split(cid, access)
    for revision, actor, principal in ((1, "a", "alice"), (2, "b", "bob")):
        await access.execute(
            cid,
            NoncombatCommand(
                kind="start_noncombat",
                id=f"start-{actor}",
                actor_id=actor,
                expected_revision=revision,
                encounter_id=actor,
                selection_id="challenge",
            ).model_dump(mode="json"),
            principal_id=principal,
        )
    state = play._load(await play.store.read(cid))
    assert len([e for e in state.noncombat if e.status == "choice"]) == 2
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await access.execute(
            cid,
            PartyCommand(
                kind="queue_activity",
                id="bad",
                actor_id="a",
                expected_revision=3,
                activity_json=NoncombatCommand(
                    kind="approach_noncombat",
                    id="bad",
                    actor_id="a",
                    expected_revision=3,
                    encounter_id="a",
                    selection_id="invented",
                ).model_dump_json(),
            ).model_dump(mode="json"),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before
    await access.execute(
        cid,
        PartyCommand(kind="pause_group", id="pause", actor_id="a", expected_revision=3).model_dump(
            mode="json"
        ),
        principal_id="alice",
    )
    # Another actor can withdraw without a consequential automatic choice for Alice.
    await access.execute(
        cid,
        NoncombatCommand(
            kind="withdraw_noncombat",
            id="withdraw",
            actor_id="b",
            expected_revision=4,
            encounter_id="b",
        ).model_dump(mode="json"),
        principal_id="bob",
    )
    await access.execute(
        cid,
        PartyCommand(
            kind="resume_group", id="resume", actor_id="a", expected_revision=5
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    assert next(e for e in state.noncombat if e.actor_id == "a").pending_choices == ("careful",)
    assert next(e for e in state.noncombat if e.actor_id == "b").status == "withdrawn"


async def test_partial_success_abandonment_predicates_and_reward_rollback(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.campaign.objectives import evaluate

    cid, play = await prepare(tmp_path)
    initial = play._load(await play.store.read(cid))
    authored = ObjectiveRules(
        id="rescue",
        version=3,
        deadline=5,
        objectives=(
            Objective(
                id="rescue-a",
                title="Free A",
                predicates=(Predicate(kind="custody", subject_id="a", value=""),),
            ),
            Objective(
                id="rescue-b",
                title="Find B",
                predicates=(Predicate(kind="known", subject_id="a", value="clue"),),
            ),
        ),
    )
    at_deadline = initial.model_copy(
        update={"resources": initial.resources.model_copy(update={"game_time": 5})}
    )
    assert evaluate(at_deadline, authored).outcome == "partial-success"
    assert evaluate(initial, authored, abandon=True).outcome == "abandoned"
    assert Predicate(kind="fact", subject_id="clue", value="letter").evaluate(initial)
    assert Predicate(kind="location", subject_id="a", value="dock").evaluate(initial)
    assert Predicate(kind="item", subject_id="a", value="potion", minimum=5).evaluate(initial)
    assert Predicate(kind="condition", subject_id="a", value="restrained", negate=True).evaluate(
        initial
    )
    assert Predicate(kind="time", subject_id="clock", value="", minimum=5).evaluate(at_deadline)
    # Explicit loss has priority even when another objective is satisfied.
    losing = authored.model_copy(
        update={"failures": (Predicate(kind="location", subject_id="a", value="dock"),)}
    )
    assert evaluate(initial, losing).outcome == "failure"

    # A reward which cannot transfer must roll back both progress and earned points.
    base, expanded = configured()
    rules = goals().model_copy(
        update={
            "rewards": (
                Reward(id="points", actor_id="a", points=5),
                Reward(id="bad-item", actor_id="a", item_id="sword-b"),
            )
        }
    )
    engine = ActionEngine(
        base.reviewer, base.resources, base.rules.model_copy(update={"objectives": rules})
    )
    failing = PlayService(AsyncSQLiteStore(tmp_path / "rollback.sqlite", 10), engine, rng=Dice())
    seed = campaign(engine)
    await failing.create(
        seed,
        expanded,
        resources(),
        (actor_setup(), actor_setup().model_copy(update={"actor_id": "b"})),
    )
    before = await failing.store.read(seed["id"])
    with pytest.raises(ValidationError, match="Unequip"):
        await failing.execute(
            seed["id"],
            Inspect(id="try", actor_id="a", expected_revision=0, target_id="chest"),
            authenticated_actor_id="a",
        )
    assert await failing.store.read(seed["id"]) == before


async def test_two_bearer_players_concurrent_split_commands(tmp_path: Path) -> None:
    import aiohttp
    from aiohttp import web

    from wayfarer.transport.campaign_api import create_campaign_app

    cid, play = await prepare(tmp_path, party=True)
    runner = web.AppRunner(
        create_campaign_app(
            CampaignAccess(play), {"alice-key": "alice", "bob-key": "bob"}, legacy_routes=True
        )
    )
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    url = f"http://127.0.0.1:{runner.addresses[0][1]}/campaigns/{cid}/commands"
    try:
        async with aiohttp.ClientSession() as client:

            async def send(command: PartyCommand, token: str) -> int:
                async with client.post(
                    url,
                    json=command.model_dump(mode="json"),
                    headers={"Authorization": f"Bearer {token}"},
                ) as response:
                    await response.read()
                    return response.status

            assert (
                await send(
                    PartyCommand(
                        kind="split_party",
                        id="split",
                        actor_id="a",
                        expected_revision=0,
                        target_id="scouts",
                    ),
                    "alice-key",
                )
                == 200
            )
            statuses = await asyncio.gather(
                send(queued("a", 1, 2, "a"), "alice-key"), send(queued("b", 1, 2, "b"), "bob-key")
            )
            assert sorted(statuses) == [200, 409]
            loser = "a" if statuses[0] == 409 else "b"
            assert (
                await send(
                    queued(loser, 2, 2, f"{loser}-refreshed"),
                    f"{'alice' if loser == 'a' else 'bob'}-key",
                )
                == 200
            )
            state = play._load(await play.store.read(cid))
            assert state.resources.game_time == 2 and len(state.party.receipts) == 2
    finally:
        await runner.cleanup()
