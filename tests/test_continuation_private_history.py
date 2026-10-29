"""Strict authored genesis and authoritative native continuation remain distinct."""

import json
from dataclasses import replace
from pathlib import Path
from typing import Never
from uuid import uuid4

import pytest
from support.runtime import build_play, build_runtime
from support.setup import play_game
from test_actions import campaign
from test_scenes import configured
from test_wave11 import graph_fixture

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.scenario_references import verify
from wayfarer.engine.simulation.campaign.setup import CreateSetup, SetupCommand
from wayfarer.engine.simulation.campaign.skill_context_state import PREFIX as CONTEXT_PREFIX
from wayfarer.engine.simulation.campaign.skill_context_state import CapturedSkillContext
from wayfarer.engine.simulation.campaign.skill_context_state import identifier as context_identifier
from wayfarer.engine.simulation.campaign.studio import ScenarioGraph
from wayfarer.engine.simulation.health.battle_fatigue import BattleFatigueLedger, write
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ValidationError
from wayfarer.orchestration.party import PartyCommand
from wayfarer.orchestration.setup import SetupService
from wayfarer.orchestration.studio import ScenarioStudio


def service(path: Path, backend: str) -> SetupService:
    engine, _ = configured()
    return SetupService(build_runtime(build_play(path, engine, backend=backend)))


async def native_completed(path: Path, backend: str) -> tuple[SetupService, str, ScenarioGraph]:
    setup = service(path, backend)
    graph = graph_fixture().model_copy(
        update={"party": PartyRules(id="continuing-party", version=1)}
    )
    cid = await play_game(setup, graph, command_id="native-" + uuid4().hex)
    play = setup.play.for_campaign(await setup.play.store.read(cid))
    for identifier, activity in (
        ("native-wait", {"kind": "wait", "ticks": 1}),
        ("native-travel", {"kind": "travel_scene", "exit_id": "to-alley"}),
    ):
        state = play._load(await play.store.read(cid))
        await setup.access.submit_json(
            cid,
            PartyCommand(
                id=identifier,
                actor_id="a",
                expected_revision=state.revision,
                kind="queue_activity",
                activity_json=json.dumps(
                    {
                        "id": "inner",
                        "actor_id": "a",
                        "expected_revision": state.revision,
                        **activity,
                    }
                ),
            ).model_dump(mode="json"),
            principal_id="alice",
        )
    state = play._load(await play.store.read(cid))
    assert state.objectives.outcome == "success"
    await setup.execute(
        cid,
        SetupCommand(id="complete", expected_revision=state.revision, operation="complete"),
        principal_id="alice",
    )
    return setup, cid, graph


def successor(graph: ScenarioGraph) -> ScenarioGraph:
    return graph.model_copy(
        update={
            "id": "private-history-successor",
            "objectives": graph.objectives.model_copy(
                update={"id": "next-objectives", "deadline": 300}
            ),
        }
    )


def unavailable() -> Never:
    raise AssertionError("Accepted retry requested fresh entropy or time")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_consumed_schedule_and_party_clock_survive_clean_saved_preview_and_continue(
    tmp_path: Path, backend: str
) -> None:
    setup, cid, graph = await native_completed(tmp_path, backend)
    before = await setup.play.store.read(cid)
    old = PlayState.model_validate_json(before["play_json"])
    history, events = await setup.play.store.history(cid), await setup.play.store.stream(cid)
    assert old.resources.game_time == 3 and "expiry" in old.resources.fired
    assert not old.resources.scheduled
    # These facts are already authoritative in the completed campaign. A clean
    # successor may reference them without reauthoring their old world records.
    proposal = successor(graph).model_copy(update={"world": replace(graph.world, facts=())})
    preview = SetupCommand(
        id="preview", expected_revision=old.revision, operation="preview", graph=proposal
    )
    await setup.execute(cid, preview, principal_id="alice")
    saved = await setup.play.store.read(cid)
    clean = setup.load(saved).next_graph
    assert clean is not None and clean.resources == proposal.resources
    assert not any(e.id.startswith(CONTEXT_PREFIX) for e in clean.resources.events)
    at_preview = PlayState.model_validate_json(saved["play_json"])
    assert (
        at_preview.resources.model_copy(update={"revision": old.resources.revision})
        == old.resources
    )
    setup = service(tmp_path, backend)
    continuation = SetupCommand(
        id="continue", expected_revision=at_preview.revision, operation="continue"
    )
    await setup.execute(cid, continuation, principal_id="alice")
    current = await setup.play.store.read(cid)
    state = setup.play.for_campaign(current)._load(current)
    assert state.resources.model_copy(update={"revision": old.resources.revision}) == old.resources
    assert state.actors == old.actors and state.approvals == old.approvals
    assert state.recovery == old.recovery and state.advancement == old.advancement
    assert setup.load(current).adventures == setup.load(before).adventures
    assert (await setup.play.store.history(cid))[: len(history)] == history
    assert (await setup.play.store.stream(cid))[: len(events)] == events
    assert state.configuration_digest == setup.play.for_campaign(current).engine.digest
    for checkpoint, _ in await setup.play.store.stream_states(cid):
        verify(checkpoint)
    assert await setup.play.store.replay(cid) == current
    setup.play.seeds = unavailable
    setup.play.instants = unavailable
    final_history, final_events = (
        await setup.play.store.history(cid),
        await setup.play.store.stream(cid),
    )
    await setup.execute(cid, continuation, principal_id="alice")
    assert await setup.play.store.read(cid) == current
    assert await setup.play.store.history(cid) == final_history
    assert await setup.play.store.stream(cid) == final_events
    # Native play after continuation must use the carried clock, without
    # replaying the already consumed starting expiry schedule.
    setup = service(tmp_path, backend)
    await setup.access.submit_json(
        cid,
        PartyCommand(
            id="post-continuation-wait",
            actor_id="a",
            expected_revision=state.revision,
            kind="queue_activity",
            activity_json=json.dumps(
                {
                    "id": "inner",
                    "actor_id": "a",
                    "expected_revision": state.revision,
                    "kind": "wait",
                    "ticks": 1,
                }
            ),
        ).model_dump(mode="json"),
        principal_id="alice",
    )
    advanced = setup.play.for_campaign(await setup.play.store.read(cid))._load(
        await setup.play.store.read(cid)
    )
    assert advanced.resources.game_time == old.resources.game_time + 1
    assert advanced.resources.fired == old.resources.fired and not advanced.resources.scheduled
    assert all(g.ready_through == advanced.resources.game_time for g in advanced.party.groups)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("private_kind", ["battle-fatigue", "skill-context"])
async def test_authored_private_receipts_cannot_enter_genesis_or_successor(
    tmp_path: Path, backend: str, private_kind: str
) -> None:
    setup, cid, graph = await native_completed(tmp_path, backend)
    if private_kind == "battle-fatigue":
        resources = write(
            graph.resources,
            BattleFatigueLedger(
                encounter_id="forged-encounter",
                binding="a" * 64,
                initial_round=1,
                initial_index=0,
                round=1,
                index=0,
                seconds=0,
                roster=("a",),
                rolled=("a",),
                charged=True,
            ),
        )
    else:
        context = CapturedSkillContext(
            producer="queued-activity",
            command_id="forged-queue",
            actor_id="a",
            generation=1,
        )
        event = ResourceEvent(
            id=context_identifier("queued-activity", "forged-queue"),
            at=0,
            target_id="a",
            kind=context.model_dump_json(),
        )
        resources = graph.resources.model_copy(update={"events": (event,)})
    forged = graph.model_copy(update={"resources": resources})
    with pytest.raises(ValidationError, match="Initial resources cannot seed"):
        setup.play.initial_state(
            campaign(setup.play.engine), forged.world, resources, forged.actors
        )
    assert (
        not ScenarioStudio(setup.play, npc_reviewer=setup.play.engine.reviewer)
        .validate(forged)
        .valid
    )
    created = await setup.create(
        CreateSetup(id="forged-" + uuid4().hex, brief=graph.brief, graph=forged),
        principal_id="alice",
    )
    draft_id = str(created["id"])
    await setup.execute(
        draft_id,
        SetupCommand(
            id="assign",
            expected_revision=0,
            operation="assign",
            principal_id="alice",
            actor_ids=("a",),
        ),
        principal_id="alice",
    )
    draft = await setup.play.store.read(draft_id)
    draft_history, draft_events = (
        await setup.play.store.history(draft_id),
        await setup.play.store.stream(draft_id),
    )
    with pytest.raises(ValidationError, match="Initial resources cannot seed"):
        await setup.execute(
            draft_id,
            SetupCommand(id="ready", expected_revision=draft["revision"], operation="ready"),
            principal_id="alice",
        )
    assert await setup.play.store.read(draft_id) == draft
    assert await setup.play.store.history(draft_id) == draft_history
    assert await setup.play.store.stream(draft_id) == draft_events
    assert "play_json" not in draft
    before = await setup.play.store.read(cid)
    history, events = await setup.play.store.history(cid), await setup.play.store.stream(cid)
    with pytest.raises(ValidationError, match="Initial resources cannot seed"):
        await setup.execute(
            cid,
            SetupCommand(
                id="forged-preview",
                expected_revision=before["revision"],
                operation="preview",
                graph=successor(forged),
            ),
            principal_id="alice",
        )
    assert await setup.play.store.read(cid) == before
    assert await setup.play.store.history(cid) == history
    assert await setup.play.store.stream(cid) == events
