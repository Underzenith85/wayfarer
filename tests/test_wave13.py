"""Grounded conclusions, durable previews and exactly-once campaign continuation."""

import asyncio
import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_wave11 import graph_fixture
from test_wave12 import ready, service

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.objectives import Objective, Predicate, Reward
from wayfarer.engine.simulation.campaign.setup import SetupCommand
from wayfarer.engine.simulation.campaign.studio import ScenarioGraph
from wayfarer.engine.world import Commitment, CommitmentKind, Fact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.objectives import checkpoint
from wayfarer.orchestration.setup import SetupService


async def finish(setup: SetupService, outcome: str = "success") -> str:
    cid = await ready(setup)
    await setup.execute(
        cid,
        SetupCommand(id="start", expected_revision=3, operation="activate"),
        principal_id="alice",
    )

    # Seed authored outcomes and prior lasting changes through the real transaction store.
    def resolve(campaign: Campaign) -> CommandReceipt:
        state = PlayState.model_validate_json(campaign["play_json"])
        graph = graph_fixture()
        rules = graph.objectives.model_copy(
            update={
                "rewards": (
                    Reward(
                        id="points", actor_id="a", points=3, outcomes=("success", "partial-success")
                    ),
                )
            }
        )
        if outcome == "partial-success":
            rules = rules.model_copy(
                update={
                    "objectives": rules.objectives
                    + (
                        Objective(
                            id="other",
                            title="Other",
                            predicates=(
                                Predicate(kind="time", subject_id="clock", value="", minimum=1000),
                            ),
                        ),
                    )
                }
            )
        graph = graph.model_copy(update={"objectives": rules})
        campaign["scenario_graph_json"] = graph.model_dump_json()
        # This fixture authors a different rules graph; pin that graph explicitly.
        from wayfarer.engine.simulation.campaign.scenario_document import digest_json
        from wayfarer.engine.simulation.campaign.scenario_references import boundary
        from wayfarer.orchestration.studio import ScenarioStudio

        pin = boundary(campaign)
        assert pin is not None
        graph_digest = digest_json(graph.model_dump(mode="json"))
        runtime_digest = (
            ScenarioStudio(setup.play, npc_reviewer=setup.play.engine.reviewer).engine(graph).digest
        )
        pin = pin.model_copy(
            update={
                "graph_digest": graph_digest,
                "runtime_digest": runtime_digest,
                "reference": pin.reference.model_copy(
                    update={"content_digest": graph_digest, "engine_digest": runtime_digest}
                ),
            }
        )
        campaign["scenario_reference_json"] = pin.model_dump_json()
        lobby = setup.load(campaign).model_copy(update={"graph": graph})
        campaign["setup_json"] = lobby.model_dump_json()
        world = state.world.learn("a", "clue") if outcome != "failure" else state.world
        world = replace(
            world,
            facts=world.facts + (Fact("private", "a", "secret", "unseen betrayal"),),
            commitments=world.commitments
            + (Commitment("debt", CommitmentKind.DEBT, "a", None, "Repay the ferryman"),),
        )
        state = state.model_copy(
            update={
                "revision": 5,
                "world": world,
                "resources": state.resources.model_copy(
                    update={
                        "revision": 5,
                        "game_time": 100 if outcome != "success" else 3,
                        "scheduled": (),
                        "pools": tuple(
                            p.model_copy(update={"current": 2}) if p.id == "hp:a" else p
                            for p in state.resources.pools
                        ),
                    }
                ),
            }
        )
        runtime = setup.play.for_campaign(campaign)
        state = state.model_copy(update={"configuration_digest": runtime.engine.digest})
        state = checkpoint(runtime, state)
        campaign["revision"] = 5
        campaign["play_json"] = state.model_dump_json()
        return CommandReceipt(action="setup", outcome="settled")

    await setup.play.store.commit_turn(cid, "fixture", 4, "fixture", resolve)
    await setup.execute(
        cid,
        SetupCommand(id="complete", expected_revision=5, operation="complete"),
        principal_id="alice",
    )
    return cid


def successor() -> ScenarioGraph:
    graph = graph_fixture()
    return graph.model_copy(
        update={
            "id": "sequel",
            "title": "The debt",
            "objectives": graph.objectives.model_copy(
                update={"id": "sequel-objectives", "deadline": 300}
            ),
        }
    )


@pytest.mark.parametrize("outcome", ["success", "partial-success", "failure"])
async def test_endings_and_continuation(tmp_path: Path, outcome: str) -> None:
    setup = service(tmp_path)
    cid = await finish(setup, outcome)
    before = await setup.play.store.read(cid)
    ending = setup.load(before).adventures[0]
    assert ending.state.objectives.outcome == outcome
    view = await setup.read(cid, principal_id="alice")
    encoded = json.dumps(view["adventures"])
    assert "unseen betrayal" not in encoded
    assert "Repay the ferryman" in encoded
    command = SetupCommand(
        id="preview", expected_revision=6, operation="preview", graph=successor()
    )
    await setup.execute(cid, command, principal_id="alice")
    assert (
        PlayState.model_validate_json((await setup.play.store.read(cid))["play_json"]).objectives
        == ending.state.objectives
    )
    setup = service(tmp_path)
    continued = SetupCommand(id="continue", expected_revision=7, operation="continue")
    await asyncio.gather(*(setup.execute(cid, continued, principal_id="alice") for _ in range(2)))
    campaign = await setup.play.store.read(cid)
    state = setup.play.for_campaign(campaign)._load(campaign)
    assert state.lifecycle == "active"
    assert state.objectives.outcome == "ongoing"
    assert state.advancement == ending.state.advancement
    assert state.resources.pools == ending.state.resources.pools
    assert state.resources.items == ending.state.resources.items
    assert state.resources.game_time == ending.state.resources.game_time
    assert state.world.commitments == ending.state.world.commitments
    assert "private" in {f.id for f in state.world.facts}
    assert setup.load(campaign).adventures == (ending,)
    with pytest.raises(ConflictError):
        await setup.execute(
            cid,
            continued.model_copy(update={"id": "again", "expected_revision": 8}),
            principal_id="alice",
        )


async def test_archive_restore_and_invalid_preview(tmp_path: Path) -> None:
    setup = service(tmp_path)
    cid = await finish(setup)
    before = await setup.play.store.read(cid)
    with pytest.raises(ValidationError):
        await setup.execute(
            cid,
            SetupCommand(
                id="repeat", expected_revision=6, operation="preview", graph=graph_fixture()
            ),
            principal_id="alice",
        )
    assert await setup.play.store.read(cid) == before
    await setup.execute(
        cid,
        SetupCommand(id="archive", expected_revision=6, operation="archive"),
        principal_id="alice",
    )
    with pytest.raises(ConflictError):
        await setup.execute(
            cid,
            SetupCommand(id="next", expected_revision=7, operation="preview", graph=successor()),
            principal_id="alice",
        )
    restored = await setup.execute(
        cid,
        SetupCommand(id="restore", expected_revision=7, operation="unarchive"),
        principal_id="alice",
    )
    assert restored["phase"] == "completed"
    assert setup.load(await setup.play.store.read(cid)).adventures == setup.load(before).adventures


async def test_generation_is_resumable_and_does_not_mutate_play(tmp_path: Path) -> None:
    from test_wave9 import FakeProvider

    from wayfarer.orchestration.providers import Orchestrator

    setup = service(tmp_path)
    cid = await finish(setup)
    before = PlayState.model_validate_json((await setup.play.store.read(cid))["play_json"])
    provider = FakeProvider(successor().model_dump_json())
    llm = Orchestrator(setup.access, provider)
    command = SetupCommand(id="generate-next", expected_revision=6, operation="preview")
    first = await setup.generate(cid, command, llm, principal_id="alice")
    second = await setup.generate(cid, command, llm, principal_id="alice")
    assert first == second and len(provider.requests) == 1
    after = PlayState.model_validate_json((await setup.play.store.read(cid))["play_json"])
    assert before.actors == after.actors
    assert before.world == after.world
    assert before.objectives == after.objectives
    assert before.advancement == after.advancement


async def test_public_conclusion_authorization_and_reward_reuse(tmp_path: Path) -> None:
    from aiohttp.test_utils import TestClient, TestServer

    from wayfarer.transport.campaign_api import create_campaign_app

    setup = service(tmp_path)
    cid = await finish(setup)
    app = create_campaign_app(
        setup.access, {"host": "alice", "outsider": "eve"}, legacy_routes=True
    )
    async with TestClient(TestServer(app)) as client:
        response = await client.get(f"/setups/{cid}", headers={"Authorization": "Bearer host"})
        assert response.status == 200
        value = await response.json()
        assert value["adventures"][0]["outcome"] == "success"
        assert "unseen betrayal" not in json.dumps(value["adventures"])
        response = await client.post(
            f"/setups/{cid}",
            headers={"Authorization": "Bearer outsider"},
            json={"id": "steal", "expected_revision": 6, "operation": "archive"},
        )
        assert response.status == 404
    graph = successor()
    graph = graph.model_copy(
        update={
            "objectives": graph.objectives.model_copy(
                update={"rewards": (Reward(id="points", actor_id="a", points=3),)}
            )
        }
    )
    with pytest.raises(ValidationError, match="Previously settled"):
        await setup.execute(
            cid,
            SetupCommand(
                id="duplicate-reward", expected_revision=6, operation="preview", graph=graph
            ),
            principal_id="alice",
        )
