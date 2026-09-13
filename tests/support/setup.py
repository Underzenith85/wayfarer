"""Create and activate a game through the one surviving creation path.

`ScenarioStudio.activate` was a parallel creation path that wrote a starting
snapshot directly. `_activate_setup` does the same work — hard playability checks,
document and party revalidation against the current engine, one controller per
player character, `pin_scenario` — so tests that used to activate a graph host a
setup here instead, exactly as `ScenarioCatalog.instantiate` does in production.
"""

from __future__ import annotations

from wayfarer.engine.simulation.campaign.scenario_loading import PublishedRevision
from wayfarer.engine.simulation.campaign.setup import CreateSetup, SetupCommand
from wayfarer.engine.simulation.campaign.studio import ScenarioGraph
from wayfarer.orchestration.setup import SetupService


async def host_game(
    setup: SetupService,
    graph: ScenarioGraph,
    *,
    command_id: str = "create",
    principal_id: str = "alice",
    document_json: str | None = None,
    published: PublishedRevision | None = None,
) -> str:
    """Create a setup for this graph and return its campaign id."""
    value = await setup.create(
        CreateSetup(id=command_id, brief=graph.brief, graph=graph),
        principal_id=principal_id,
        document_json=document_json,
        published=published,
    )
    return str(value["id"])


async def activate_game(
    setup: SetupService, cid: str, graph: ScenarioGraph, *, principal_id: str = "alice"
) -> None:
    """Seat every player character with the host, confirm readiness and activate."""
    actors = tuple(a.actor_id for a in graph.actors if a.actor_id not in graph.npc_actor_ids)
    commands = (
        SetupCommand(
            id="assign",
            expected_revision=0,
            operation="assign",
            principal_id=principal_id,
            actor_ids=actors,
        ),
        SetupCommand(id="ready", expected_revision=1, operation="ready"),
        SetupCommand(id="activate", expected_revision=2, operation="activate"),
    )
    for command in commands:
        await setup.execute(cid, command, principal_id=principal_id)


async def play_game(
    setup: SetupService,
    graph: ScenarioGraph,
    *,
    command_id: str = "create",
    principal_id: str = "alice",
    document_json: str | None = None,
    published: PublishedRevision | None = None,
) -> str:
    """Host and activate in one step; returns the active campaign id."""
    cid = await host_game(
        setup,
        graph,
        command_id=command_id,
        principal_id=principal_id,
        document_json=document_json,
        published=published,
    )
    await activate_game(setup, cid, graph, principal_id=principal_id)
    return cid
