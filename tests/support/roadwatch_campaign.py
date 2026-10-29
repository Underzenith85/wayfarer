"""Actual HTTP Roadside Watch driver; no authored world or seeded played state."""

import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Never, cast
from uuid import uuid4

from aiohttp import ContentTypeError, web
from aiohttp.test_utils import TestClient, TestServer

from wayfarer.adventures.roadwatch_runtime import application
from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.events import EngineEvent, document
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.replay import execute_recorded
from wayfarer.orchestration.runtime import CampaignRuntime
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.persistence.events import CommandRecord
from wayfarer.persistence.postgres import AsyncPostgresStore
from wayfarer.persistence.replay import verify_commands
from wayfarer.transport.common import RUNTIME_KEY

PARTY_TOKEN = "roadwatch-party-test-token"
GM_TOKEN = "roadwatch-gm-test-token"
OUTSIDER_TOKEN = "roadwatch-outsider-test-token"


def object_value(value: object) -> dict[str, object]:
    assert isinstance(value, dict) and all(isinstance(key, str) for key in value)
    return cast(dict[str, object], value)


def array_value(value: object) -> list[object]:
    assert isinstance(value, list)
    return cast(list[object], value)


@dataclass
class SeedControl:
    """Trusted test DI selects actual deterministic streams, never dice outcomes."""

    value: int = 1
    captures: int = 0

    def __call__(self) -> str:
        self.captures += 1
        return format(self.value, "064x")


@dataclass
class RoadwatchHTTP:
    client: TestClient[web.Request, web.Application]
    store: Store
    runtime: CampaignRuntime
    seeds: SeedControl
    directory: Path
    campaign_id: str = ""

    async def request(
        self,
        method: str,
        path: str,
        *,
        token: str = PARTY_TOKEN,
        value: dict[str, object] | None = None,
        status: int = 200,
    ) -> object:
        async with self.client.request(
            method,
            path,
            headers={"Authorization": "Bearer " + token},
            json=value,
        ) as response:
            try:
                body = await response.json()
            except ContentTypeError, json.JSONDecodeError:
                body = await response.text()
            assert response.status == status, (response.status, body)
            return body

    async def view(self, *, token: str = PARTY_TOKEN) -> dict[str, object]:
        return object_value(
            await self.request("GET", f"/campaigns/{self.campaign_id}", token=token)
        )

    async def revision(self) -> int:
        value = (await self.view())["revision"]
        assert type(value) is int
        return value

    async def command(
        self,
        kind: str,
        actor: str,
        *,
        token: str = PARTY_TOKEN,
        identifier: str | None = None,
        revision: int | None = None,
        status: int = 200,
        **fields: object,
    ) -> tuple[dict[str, object], dict[str, object]]:
        payload = {
            "kind": kind,
            "id": identifier or str(uuid4()),
            "actor_id": actor,
            "expected_revision": await self.revision() if revision is None else revision,
            **fields,
        }
        result = object_value(
            await self.request(
                "POST",
                f"/campaigns/{self.campaign_id}/commands",
                token=token,
                value=payload,
                status=status,
            )
        )
        return payload, result

    async def create_and_activate(self) -> None:
        templates = array_value(await self.request("GET", "/setups/templates"))
        assert len(templates) == 1
        graph = object_value(templates[0])
        assert graph["id"] == "roadside-watch-1"
        created = object_value(
            await self.request(
                "POST",
                "/setups",
                value={"id": str(uuid4()), "brief": graph["brief"], "graph": graph},
                status=201,
            )
        )
        self.campaign_id = str(created["id"])
        assert created["phase"] == "draft" and created["revision"] == 0
        current = created
        for operation, fields in (
            ("assign", {"principal_id": "party", "actor_ids": ["a", "b"]}),
            ("ready", {}),
            ("activate", {}),
        ):
            current = object_value(
                await self.request(
                    "POST",
                    f"/setups/{self.campaign_id}",
                    value={
                        "id": str(uuid4()),
                        "expected_revision": current["revision"],
                        "operation": operation,
                        **fields,
                    },
                )
            )
        assert current["phase"] == "active" and current["revision"] == 3

    async def character(self, actor: str) -> dict[str, object]:
        return object_value(
            await self.request("GET", f"/api/v1/campaigns/{self.campaign_id}/characters/{actor}")
        )

    async def tactical(self, actor: str, *, token: str = PARTY_TOKEN) -> dict[str, object]:
        return object_value(
            await self.request(
                "GET",
                f"/api/tactical/v2/campaigns/{self.campaign_id}?actor_id={actor}",
                token=token,
            )
        )


@asynccontextmanager
async def campaign_http(directory: Path, backend: str) -> AsyncIterator[RoadwatchHTTP]:
    directory.mkdir(parents=True, exist_ok=True)
    store: Store
    if backend == "postgres":
        url = os.environ.get("WAYFARER_TEST_DATABASE_URL")
        assert url, "Roadwatch requires the selected PostgreSQL backend; skipping is not evidence"
        store = AsyncPostgresStore(url)
    else:
        assert backend == "sqlite"
        store = AsyncSQLiteStore(directory / "campaign.sqlite")
    seeds = SeedControl()
    app = application(
        store,
        {
            PARTY_TOKEN: "party",
            GM_TOKEN: "gm",
            OUTSIDER_TOKEN: "outsider",
        },
        seeds=seeds,
        v1_ledger_path=directory / "v1-ledger.sqlite",
    )
    client = TestClient(TestServer(app))
    await client.start_server()
    try:
        yield RoadwatchHTTP(client, store, app[RUNTIME_KEY], seeds, directory)
    finally:
        await client.close()


async def travel_both(host: RoadwatchHTTP, exit_id: str) -> None:
    for actor_id in ("a", "b"):
        await host.command("travel_scene", actor_id, exit_id=exit_id)


async def recovery_choice(
    host: RoadwatchHTTP, kind: str, *, actor: str = "b", target: str = "a"
) -> dict[str, object]:
    """Use the actual party projection; never synthesize a clinical task choice."""
    return next(
        object_value(raw)
        for raw in array_value((await host.view())["gurps_recovery_choices"])
        if object_value(raw)["kind"] == kind
        and object_value(raw)["actor_id"] == actor
        and object_value(raw)["target_actor_id"] == target
    )


async def begin_care(
    host: RoadwatchHTTP, kind: str, *, actor: str = "b", target: str = "a"
) -> dict[str, object]:
    choice = await recovery_choice(host, kind, actor=actor, target=target)
    command, _ = await host.command("gurps_recovery", actor, choice_id=choice["id"])
    return command


async def finish_care(
    host: RoadwatchHTTP, seconds: int, *, seed: int = 1, actor: str = "b", target: str = "a"
) -> None:
    await host.command("wait", "a", ticks=seconds)
    choice = await recovery_choice(host, "finish-recovery", actor=actor, target=target)
    host.seeds.value = seed
    await host.command("gurps_recovery", actor, choice_id=choice["id"])


async def verify_postactivation_replay(host: RoadwatchHTTP) -> None:
    """Verify authentic snapshots/inputs; this clone never supplies played outcomes."""
    history = await host.store.history(host.campaign_id)
    activation = next(row for row in history if "play_json" in row.state_after)
    commands = [row for row in history if row.resulting_revision > activation.resulting_revision]
    assert commands
    ids = {row.command_id for row in commands}
    events = [row for row in await host.store.stream(host.campaign_id) if row.command_id in ids]
    count = 0

    def refuse_fresh() -> Never:
        raise AssertionError("Replay must use retained entropy and command instants")

    async def replay(
        before: Campaign, command: CommandRecord
    ) -> tuple[Campaign, list[EngineEvent]]:
        nonlocal count
        count += 1
        store = AsyncSQLiteStore(host.directory / "original-seed-replay" / f"{count}.sqlite")
        # This is an isolated verification copy of an authentic activated image,
        # not an initial state used to make any gameplay choice or outcome lawful.
        await store.commit_genesis(
            before, command_id="verification-copy", text="authentic checkpoint"
        )
        play = PlayService(host.store, host.runtime.play.engine).for_campaign(before)
        cloned = PlayService(store, play.engine, seeds=refuse_fresh, instants=refuse_fresh)
        await execute_recorded(cloned, command)
        return await store.read(host.campaign_id), [
            row.event for row in await store.stream(host.campaign_id, after=before["revision"])
        ]

    original = PlayState.model_validate_json(activation.state_after["play_json"])
    final, checks = await verify_commands(
        activation.state_after,
        commands,
        events,
        configuration_digest=original.configuration_digest,
        execute=replay,
    )
    assert len(checks) == len(commands)
    assert all(row.folded and row.reexecuted and row.reason is None for row in checks)
    # The official fold canonically decodes embedded play_json; mapping key
    # order in its re-encoded string is not a gameplay or retained-input byte.
    assert document(final) == document(await host.store.read(host.campaign_id))


async def start_encounter(host: RoadwatchHTTP, encounter_id: str = "roadwatch-fight") -> None:
    await host.command(
        "start_encounter",
        "gm",
        token=GM_TOKEN,
        encounter_id=encounter_id,
        battlefield_id="roadwatch-clearing",
        scene_id="road-scene",
        placements=[
            {"actor_id": "a", "position": {"q": 1, "r": 0}, "hex_facing": 0},
            {"actor_id": "b", "position": {"q": 0, "r": 1}, "hex_facing": 0},
            {"actor_id": "brigand", "position": {"q": 3, "r": 0}, "hex_facing": 3},
        ],
        allegiances=[
            {"actor_id": "a", "side_id": "watch"},
            {"actor_id": "b", "side_id": "watch"},
            {"actor_id": "brigand", "side_id": "brigand"},
        ],
        oppositions=[{"side_ids": ["brigand", "watch"]}],
    )


async def start_guard_attack(host: RoadwatchHTTP) -> None:
    """Enter an authentic pending attack using public scene and combat commands."""
    await travel_both(host, "post-to-road")
    await start_encounter(host)
    await host.command(
        "take_combat_turn",
        "a",
        encounter_id="roadwatch-fight",
        maneuver="move",
        hex_path=[{"q": 2, "r": 0}],
        hex_facing=0,
    )
    await host.command(
        "take_combat_turn",
        "b",
        encounter_id="roadwatch-fight",
        maneuver="do_nothing",
    )
    await host.command(
        "take_combat_turn",
        "brigand",
        token=GM_TOKEN,
        encounter_id="roadwatch-fight",
        maneuver="attack",
        item_id="sword-brigand",
        mode_id="swing",
        target_id="a",
        hit_location="torso",
    )


async def actual_guard_wound(host: RoadwatchHTTP) -> None:
    """Public real movement and rolls: source ST10 swing+1 vs torso DR2 injures4."""
    await start_guard_attack(host)
    # Seed1 has literal d6[1,5,3,4,...]: deferred attack9, damage die4.
    host.seeds.value = 1
    await host.command(
        "choose_defense",
        "a",
        encounter_id="roadwatch-fight",
        defense="none",
    )
    character = await host.character("a")
    assert character["hp"] == {"current": 7, "maximum": 11}
    await host.command(
        "end_encounter",
        "gm",
        token=GM_TOKEN,
        encounter_id="roadwatch-fight",
        reason="The brigand breaks off and the surveyors withdraw to the shelter.",
    )
    await travel_both(host, "road-to-shelter")
