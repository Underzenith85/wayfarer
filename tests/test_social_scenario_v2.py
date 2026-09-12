"""Imported v2 social policies survive publication, activation and SQLite rebinding."""

import json
from pathlib import Path

import pytest
from test_actions import campaign
from test_social_dispatch import prepare

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.campaign.npcs import (
    NPCSocialAction,
    NPCSocialPlan,
    NPCSocialRules,
    NPCSocialTrigger,
)
from wayfarer.engine.simulation.campaign.objectives import Objective, ObjectiveRules, Predicate
from wayfarer.engine.simulation.campaign.party import PartyRules
from wayfarer.engine.simulation.campaign.scenario_document import PublicBrief, ScenarioDocument
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.campaign.social_policy import (
    SocialActionRules,
    SocialScenarioDocument,
    SocialScenarioGraph,
)
from wayfarer.engine.simulation.campaign.studio import GenerationBrief
from wayfarer.engine.simulation.health.fright import effects
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.scenario_documents import (
    ScenarioDocuments,
    adapt_graph,
    bind_party,
    parse_document,
)
from wayfarer.orchestration.studio import ScenarioStudio
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


async def test_import_publish_activate_and_restart_social_occurrence(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid))
    rules = NPCSocialRules(
        id="private-plan",
        plans=(
            NPCSocialPlan(
                id="alarm-plan",
                actor_id="npc",
                goal="Raise the alarm",
                first_due=1,
                interval=1,
                action_budget=1,
                actions=(
                    NPCSocialAction(
                        id="alarm",
                        kind="alarm",
                        social=NPCSocialTrigger(kind="fright", subject_id="a"),
                    ),
                ),
            ),
        ),
    )
    graph = SocialScenarioGraph(
        id="fright-adventure",
        version=1,
        title="An alarming meeting",
        brief=GenerationBrief(
            premise="Wait for the guard",
            genre="mystery",
            tone="tense",
            duration_minutes=30,
            difficulty="standard",
        ),
        opening_scene_id="dock-scene",
        opening_action="Wait on the dock",
        failure_consequence="The guard leaves",
        world=state.world,
        resources=ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        actors=(ActorSetup(actor_id="a", proposal=state.actors[0].proposal, aware_of=("npc",)),),
        actions=SocialActionRules(id="fright-adventure", version=1),
        npcs=rules,
        party=PartyRules(id="party", version=1),
        scenes=SceneRules(
            id="scenes",
            version=1,
            scenes=(Scene(id="dock-scene", version=1, location_id="dock", title="Dock"),),
        ),
        objectives=ObjectiveRules(
            id="endings",
            version=1,
            objectives=(
                Objective(
                    id="wait",
                    title="Wait",
                    predicates=(Predicate(kind="time", subject_id="clock", value="", minimum=50),),
                ),
            ),
            deadline=100,
        ),
    )
    studio = ScenarioStudio(play)
    documents = ScenarioDocuments(studio)
    document = adapt_graph(
        graph,
        studio=studio,
        revision_id="r1",
        author="GM",
        public=PublicBrief(
            title=graph.title,
            summary=graph.brief.premise,
            setup=graph.brief,
            opening_prompt=graph.opening_action,
        ),
    )
    assert isinstance(document, SocialScenarioDocument)
    assert isinstance(parse_document(document.canonical()), SocialScenarioDocument)
    assert isinstance(bind_party(document).runtime_rules().npcs, NPCSocialRules)
    with pytest.raises(ValueError):
        ScenarioDocument.model_validate_json(document.canonical())
    draft = documents.save_draft(document.canonical(), draft_id="draft", principal_id="gm")
    assert draft.report.status == "playable", draft.report
    published = documents.publish(draft, principal_id="gm")
    initial = campaign(studio.engine(graph))
    initial["id"] = "social-import"
    activated = await documents.activate(published, initial, state.members, principal_id="gm")
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "social.sqlite", 10),
        play.engine,
        rng=RecordedDice([4, 5, 5, 1, 1, 1]),
    )
    restarted = restarted.for_campaign(await restarted.store.read(initial["id"]))
    wait = Wait(id="alarm-clock", actor_id="a", expected_revision=0, ticks=1)
    result = await restarted.execute(initial["id"], wait, authenticated_actor_id="a")
    bound = restarted.for_campaign(await restarted.store.read(initial["id"]))
    after = bound._load(await restarted.store.read(initial["id"]))
    assert len(effects(after.resources)) == 1 and effects(after.resources)[0].active
    assert after.npcs.decisions[0].status == "committed"
    restarted.rng = RecordedDice([])
    assert await restarted.execute(initial["id"], wait, authenticated_actor_id="a") == result
    assert await restarted.store.read(initial["id"]) == await restarted.store.replay(initial["id"])
    visible = await CampaignAccess(activated).read(initial["id"], principal_id="alice")
    assert "alarm-plan" not in json.dumps(visible) and "private-plan" not in json.dumps(visible)
