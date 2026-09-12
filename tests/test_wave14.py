"""Reference adventure acceptance: every mutation uses authenticated HTTP."""

import asyncio
import json
from pathlib import Path

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from wayfarer.adventures.lantern import adventure
from wayfarer.adventures.runtime import application
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.studio import ScenarioGraph
from wayfarer.orchestration.setup import SetupService
from wayfarer.transport.campaign_api import ACCESS_KEY
from wayfarer.transport.setup_api import SETUP_KEY


class Dice:
    """Server-owned deterministic randomness; clients never supply roll outcomes."""

    def __init__(self, value: int = 0) -> None:
        self.value, self.calls = value, 0

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        self.calls += 1
        return self.value % exclusive_upper_bound


class Table:
    def __init__(self, path: Path, dice: Dice | None = None) -> None:
        self.path, self.dice = path, dice or Dice()
        self.client: TestClient[web.Request, web.Application] | None = None
        self.cid, self.sequence = "", 0

    async def open(self) -> None:
        app = application(self.path, {"alice-token": "alice", "bob-token": "bob", "gm-token": "gm"})
        app[ACCESS_KEY].play.rng = self.dice
        self.client = TestClient(TestServer(app))
        await self.client.start_server()

    async def close(self) -> None:
        assert self.client
        await self.client.close()

    async def restart(self) -> None:
        await self.close()
        await self.open()

    async def request(
        self, path: str, body: dict[str, object] | None = None, principal: str = "alice"
    ) -> dict[str, object]:
        assert self.client
        headers = {"Authorization": f"Bearer {principal}-token"}
        response = (
            await self.client.post(path, json=body, headers=headers)
            if body is not None
            else await self.client.get(path, headers=headers)
        )
        assert response.status in (200, 201), await response.text()
        result: dict[str, object] = await response.json()
        return result

    async def lobby(self, operation: str, **fields: object) -> dict[str, object]:
        current = await self.request(f"/setups/{self.cid}")
        self.sequence += 1
        return await self.request(
            f"/setups/{self.cid}",
            {
                "id": f"lobby-{self.sequence}",
                "expected_revision": current["revision"],
                "operation": operation,
                **fields,
            },
        )

    async def start(self) -> None:
        graph = adventure()
        created = await self.request(
            "/setups", {"id": "reference", "brief": graph.brief.model_dump(mode="json")}
        )
        self.cid = str(created["id"])
        await self.lobby("edit", graph=graph.model_dump(mode="json"))
        await self.lobby("invite", principal_id="bob")
        current = await self.request(f"/setups/{self.cid}", principal="bob")
        await self.request(
            f"/setups/{self.cid}",
            {"id": "join-bob", "expected_revision": current["revision"], "operation": "join"},
            "bob",
        )
        await self.lobby("assign", principal_id="alice", actor_ids=["a"])
        await self.lobby("assign", principal_id="bob", actor_ids=["b"])
        await self.lobby("ready")
        current = await self.request(f"/setups/{self.cid}", principal="bob")
        await self.request(
            f"/setups/{self.cid}",
            {"id": "ready-bob", "expected_revision": current["revision"], "operation": "ready"},
            "bob",
        )
        await self.lobby("activate")

    async def command(self, actor: str, kind: str, **fields: object) -> dict[str, object]:
        principal = {"a": "alice", "b": "bob"}.get(actor, "gm")
        current = await self.request(f"/campaigns/{self.cid}", principal=principal)
        self.sequence += 1
        return await self.request(
            f"/campaigns/{self.cid}/commands",
            {
                "id": f"command-{self.sequence}",
                "actor_id": actor,
                "expected_revision": current["revision"],
                "kind": kind,
                **fields,
            },
            principal,
        )

    async def queued(self, actor: str, kind: str = "wait", **fields: object) -> dict[str, object]:
        state = await self.state()
        return await self.command(
            actor,
            "queue_activity",
            activity_json=json.dumps(
                {
                    "id": "inner",
                    "actor_id": actor,
                    "expected_revision": state.revision,
                    "kind": kind,
                    **fields,
                }
            ),
        )

    async def state(self) -> PlayState:
        assert self.client
        campaign = await self.client.app[ACCESS_KEY].play.store.read(self.cid)
        return PlayState.model_validate_json(campaign["play_json"])

    async def finish(self, expected: str) -> None:
        state = await self.state()
        assert state.objectives.outcome == expected
        await self.lobby("complete")
        assert self.client
        snapshot = (
            self.client.app[SETUP_KEY]
            .load(await self.client.app[ACCESS_KEY].play.store.read(self.cid))
            .adventures[0]
        )
        assert snapshot.state.objectives.outcome == expected
        rewards = state.advancement
        await self.lobby("preview", graph=adventure(sequel=True).model_dump(mode="json"))
        await self.restart()
        current = await self.request(f"/setups/{self.cid}")
        body = {
            "id": "continue-once",
            "expected_revision": current["revision"],
            "operation": "continue",
        }
        await asyncio.gather(*(self.request(f"/setups/{self.cid}", body) for _ in range(2)))
        after = await self.state()
        assert after.objectives.outcome == "ongoing"
        assert after.advancement == rewards
        assert after.resources.pools == state.resources.pools
        assert self.client
        campaign = await self.client.app[ACCESS_KEY].play.store.read(self.cid)
        assert SetupService.load(campaign).adventures == (snapshot,)
        store = self.client.app[ACCESS_KEY].play.store
        assert await store.replay(self.cid) == campaign
        from wayfarer.engine.simulation.events import document

        folded = await store.stream_states(self.cid)
        from wayfarer.engine.simulation.scenario_references import verify

        for checkpoint, _ in folded:
            verify(checkpoint)
        history = await store.history(self.cid)
        assert [document(s) for s, _ in folded[1:]] == [document(e.state_after) for e in history]


@pytest.mark.parametrize(
    ("outcome", "clue"),
    [
        ("success", "manifest"),
        ("success", "records"),
        ("partial-success", "manifest"),
        ("failure", None),
    ],
)
async def test_reference_setup_social_endings_and_continuation(
    tmp_path: Path, outcome: str, clue: str | None
) -> None:
    table = Table(tmp_path / "reference.sqlite")
    await table.open()
    try:
        await table.start()
        if clue == "records":
            await table.command("a", "split_party", target_id="research")
            await table.queued("a", "travel_scene", exit_id="customs-path")
            await table.queued("b", ticks=1)
            assert ("a", "manifest-found") in (await table.state()).world.knowledge
            await table.queued("a", "travel_scene", exit_id="return-harbor")
            await table.queued("b", ticks=1)
            await table.command("a", "rejoin_party", target_id="group:harbor-scene")
            before_rest = (await table.state()).resources.game_time
            await table.command("a", "choose_recovery", rule_id="camp-rest", target_actor_id="a")
            assert (await table.state()).resources.game_time == before_rest + 2
        elif clue == "manifest":
            await table.command("a", "inspect", target_id="manifest")
        if outcome == "success":
            await table.command(
                "a", "start_noncombat", encounter_id="discussion", selection_id="parley"
            )
            before = await table.state()
            await table.restart()
            assert (await table.state()).noncombat == before.noncombat
            await table.command(
                "a", "approach_noncombat", encounter_id="discussion", selection_id="appeal-to-duty"
            )
        else:
            await table.command("a", "wait", ticks=12)
        await table.finish(outcome)
    finally:
        await table.close()


async def test_reference_artifact_roundtrip_and_generated_validation(tmp_path: Path) -> None:
    from wayfarer.adventures.lantern import build_adventure
    from wayfarer.orchestration.studio import ScenarioStudio

    table = Table(tmp_path / "reference.sqlite")
    await table.open()
    try:
        assert table.client
        studio = ScenarioStudio(
            table.client.app[ACCESS_KEY].play,
            npc_reviewer=table.client.app[ACCESS_KEY].play.engine.reviewer,
        )
        graph = ScenarioGraph.model_validate_json(adventure().model_dump_json())
        assert graph == build_adventure()
        assert adventure(sequel=True) == build_adventure(sequel=True)
        assert studio.validate(graph).valid
        combat = graph.runtime_rules().combat
        assert combat and combat.attacks
        illegal = graph.model_copy(update={"opening_scene_id": "invented"})
        assert not studio.validate(illegal).valid
        broken_consequence = graph.model_copy(
            update={
                "combat_consequences": (
                    graph.combat_consequences[0].model_copy(update={"fact_ids": ("invented",)}),
                )
            }
        )
        assert not studio.validate(broken_consequence).valid
        await table.start()
        view = await table.request(f"/campaigns/{table.cid}", principal="bob")
        assert "secretly owes" not in json.dumps(view)
        assert isinstance(view["actors"], list)
        assert "warden" not in view["actors"]
    finally:
        await table.close()


async def test_reference_combat_route_resumes_defense_and_never_rerolls(tmp_path: Path) -> None:
    table = Table(tmp_path / "combat.sqlite")
    await table.open()
    try:
        await table.start()
        await table.command("a", "inspect", target_id="manifest")
        assert table.client
        forged = await table.client.post(
            f"/campaigns/{table.cid}/commands",
            headers={"Authorization": "Bearer gm-token"},
            json={
                "id": "forged-player",
                "actor_id": "a",
                "expected_revision": (await table.state()).revision,
                "kind": "take_combat_turn",
                "encounter_id": "yard",
                "maneuver": "wait",
            },
        )
        assert forged.status == 403
        await table.command(
            "gm",
            "start_encounter",
            encounter_id="yard",
            battlefield_id="guardhouse-yard",
            placements=[
                {"actor_id": "a", "position": {"x": 0, "y": 0}, "facing": "east"},
                {"actor_id": "warden", "position": {"x": 1, "y": 0}, "facing": "west"},
            ],
        )
        for strike in range(4):
            await table.command(
                "a",
                "take_combat_turn",
                encounter_id="yard",
                maneuver="attack",
                target_id="warden",
                item_id="blade-a",
            )
            pending = await table.state()
            assert pending.encounters[0].pending_defense
            if strike == 0:
                await table.restart()
                assert (await table.state()).encounters == pending.encounters
            body = {
                "id": f"defense-{strike}",
                "actor_id": "warden",
                "expected_revision": pending.revision,
                "kind": "choose_defense",
                "encounter_id": "yard",
                "defense": "none",
            }
            await table.request(f"/campaigns/{table.cid}/commands", body, "gm")
            calls = table.dice.calls
            await table.request(f"/campaigns/{table.cid}/commands", body, "gm")
            assert table.dice.calls == calls
            if (await table.state()).encounters[0].status == "active":
                await table.command(
                    "warden", "take_combat_turn", encounter_id="yard", maneuver="wait"
                )
        state = await table.state()
        assert state.encounters[0].status == "completed"
        assert len(state.encounters[0].wounds) == 4
        assert state.objectives.outcome == "success"
        await table.finish("success")
    finally:
        await table.close()


@pytest.mark.parametrize("route", ["quiet-rescue", "negotiate-release"])
async def test_reference_split_capture_rescue_disconnect_gear_and_reunion(
    tmp_path: Path, route: str
) -> None:
    table = Table(tmp_path / "rescue.sqlite")
    await table.open()
    try:
        await table.start()
        await table.command("a", "split_party", target_id="scouts")
        await table.command("gm", "apply_setback", rule_id="harbor-capture", target_actor_id="a")
        state = await table.state()
        assert state.objectives.outcome == "ongoing"
        assert all(i.owner_id != "a" for i in state.resources.items)
        view = await table.request(f"/campaigns/{table.cid}", principal="bob")
        assert not view["captivity"]
        assert route not in json.dumps(view["recovery_choices"])
        await table.command("a", "choose_recovery", rule_id="study-cell", target_actor_id="a")
        await table.queued("b", ticks=1)
        await table.command("a", "choose_recovery", rule_id="signal-outside", target_actor_id="a")
        await table.queued("b", ticks=1)
        view = await table.request(f"/campaigns/{table.cid}", principal="bob")
        assert route in json.dumps(view["recovery_choices"])
        assert "loose shutter" not in json.dumps(view)
        await table.command("b", "choose_recovery", rule_id=route, target_actor_id="a")
        pending = await table.state()
        await table.restart()  # captive disconnected; no timeout chooses for them
        assert (await table.state()).recovery.decisions == pending.recovery.decisions
        assert (await table.state()).resources.game_time == 2
        await table.command("a", "choose_recovery", rule_id="loosen-bars", target_actor_id="a")
        state = await table.state()
        assert state.recovery.captivity[0].released_at == 3
        assert all(i.owner_id != "a" for i in state.resources.items)
        await table.command("a", "choose_recovery", rule_id="recover-gear", target_actor_id="a")
        await table.queued("b", ticks=1)
        await table.command("a", "rejoin_party", target_id="group:harbor-scene")
        state = await table.state()
        assert len(state.party.groups) == 1
        assert next(i for i in state.resources.items if i.id == "blade-a").owner_id == "a"
        assert ("b", "inside-route") not in state.world.knowledge
        await table.finish("success")
    finally:
        await table.close()


@pytest.mark.parametrize("expected", ["partial-success", "failure"])
async def test_reference_failed_escape_deadline_and_captive_continuation(
    tmp_path: Path, expected: str
) -> None:
    table = Table(tmp_path / "failed-rescue.sqlite", Dice(5))
    await table.open()
    try:
        await table.start()
        await table.command("a", "split_party", target_id="scout")
        await table.command("gm", "apply_setback", rule_id="harbor-capture", target_actor_id="a")
        for tick in range(12):
            choice = "study-cell" if expected == "partial-success" and tick == 0 else "slip-bonds"
            await table.command("a", "choose_recovery", rule_id=choice, target_actor_id="a")
            await table.queued("b", ticks=1)
        state = await table.state()
        assert state.objectives.outcome == expected
        assert state.recovery.captivity[0].released_at is None
        assert state.recovery.decisions[-1].status == "failed"
        assert state.resources.game_time == 12
        await table.finish(expected)
        assert (await table.state()).recovery.captivity == state.recovery.captivity
    finally:
        await table.close()


async def test_reference_reinforcement_arrives_only_after_resolved_defense(tmp_path: Path) -> None:
    table = Table(tmp_path / "arrival.sqlite")
    await table.open()
    try:
        await table.start()
        await table.command("a", "inspect", target_id="manifest")
        await table.command("b", "split_party", target_id="rescuers")
        await table.command(
            "gm",
            "start_encounter",
            encounter_id="yard",
            battlefield_id="guardhouse-yard",
            placements=[
                {"actor_id": "a", "position": {"x": 0, "y": 0}},
                {"actor_id": "warden", "position": {"x": 1, "y": 0}},
            ],
        )
        await table.command(
            "a",
            "take_combat_turn",
            encounter_id="yard",
            maneuver="attack",
            target_id="warden",
            item_id="blade-a",
        )
        state = await table.state()
        assert table.client
        response = await table.client.post(
            f"/campaigns/{table.cid}/commands",
            headers={"Authorization": "Bearer bob-token"},
            json={
                "id": "early-arrival",
                "actor_id": "b",
                "kind": "join_encounter",
                "expected_revision": state.revision,
                "encounter_id": "yard",
                "position": {"x": 2, "y": 0},
            },
        )
        assert response.status == 409
        await table.restart()
        await table.command("warden", "choose_defense", encounter_id="yard", defense="none")
        await table.command("b", "join_encounter", encounter_id="yard", position={"x": 2, "y": 0})
        state = await table.state()
        assert set(state.encounters[0].turn_order) == {"a", "b", "warden"}
        assert len(state.party.groups) == 1
        assert ("b", "warden-secret") not in state.world.knowledge
        await table.command("gm", "apply_setback", rule_id="harbor-capture", target_actor_id="a")
        await table.command("a", "choose_recovery", rule_id="signal-outside", target_actor_id="a")
        await table.command("b", "choose_recovery", rule_id="loosen-bars", target_actor_id="a")
        await table.command("b", "choose_recovery", rule_id="quiet-rescue", target_actor_id="a")
        await table.command("a", "choose_recovery", rule_id="loosen-bars", target_actor_id="a")
        rescued = await table.state()
        assert rescued.encounters[0].status == "completed"
        assert rescued.encounters[0].completion_reason == "setback:capture"
        assert rescued.recovery.captivity[0].released_at is not None
        await table.command("a", "rejoin_party", target_id="group:harbor-scene")
        await table.command("a", "choose_recovery", rule_id="recover-gear", target_actor_id="a")
        await table.finish("success")
    finally:
        await table.close()


async def test_reference_generated_fixture_uses_public_generation_and_persists(
    tmp_path: Path,
) -> None:
    from reference_provider import ReferenceProvider

    from wayfarer.orchestration.providers import Orchestrator
    from wayfarer.transport.campaign_api import ORCHESTRATOR_KEY
    from wayfarer.transport.setup_api import generate

    app = application(tmp_path / "generated.sqlite", {"alice-token": "alice"})
    provider = ReferenceProvider()
    app[ORCHESTRATOR_KEY] = Orchestrator(app[ACCESS_KEY], provider)
    app.router.add_post("/setups/{cid}/generate", generate)
    async with TestClient(TestServer(app)) as client:
        headers = {"Authorization": "Bearer alice-token"}
        response = await client.post(
            "/setups",
            json={"id": "generated", "brief": adventure().brief.model_dump(mode="json")},
            headers=headers,
        )
        cid = (await response.json())["id"]
        body = {"id": "generate", "expected_revision": 0, "operation": "edit"}
        first = await client.post(f"/setups/{cid}/generate", json=body, headers=headers)
        assert first.status == 200, await first.text()
        second = await client.post(f"/setups/{cid}/generate", json=body, headers=headers)
        assert await first.json() == await second.json()
        assert len(provider.requests) == 1
        saved = await app[ACCESS_KEY].play.store.read(cid)
        assert "play_json" not in saved
        assert SetupService.load(saved).graph == adventure()
