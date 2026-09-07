"""Validate a next-adventure proposal without letting it rewrite campaign history."""

from dataclasses import replace

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.studio import ScenarioStudio
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.setup import Setup
from wayfarer.simulation.studio import ScenarioGraph


def prepare(
    play: PlayService, campaign: Campaign, setup: Setup, graph: ScenarioGraph
) -> tuple[ScenarioGraph, PlayState]:
    old = PlayState.model_validate_json(campaign["play_json"])
    if graph.id in {s.graph.id for s in setup.adventures}:
        raise ValidationError("Choose a distinct next adventure identity")
    if graph.objectives.id in {s.graph.objectives.id for s in setup.adventures}:
        raise ValidationError("Next adventure requires a fresh objective/reward namespace")
    settled_rewards = {r for a in setup.adventures for r in a.state.objectives.settled_reward_ids}
    if settled_rewards & {r.id for r in graph.objectives.rewards}:
        raise ValidationError("Previously settled rewards cannot be awarded again")
    if old.party.queue:
        raise ConflictError("Resolve shared-time decisions before continuing")
    actors = {a.actor_id: a for a in old.actors}
    if {a.actor_id: a.proposal for a in graph.actors} != {
        a.actor_id: a.proposal for a in old.actors
    }:
        raise ValidationError("Next adventure must preserve the existing character builds")
    # Existing world records win. Only the opening location is a proposed transition.
    entities = {e.id: e for e in graph.world.entities}
    for entity in old.world.entities:
        if entity.id in actors:
            target = entities.get(entity.id)
            if target is None:
                raise ValidationError("Missing continuing actor")
            if (
                any(
                    c.actor_id == entity.id and c.released_at is None
                    for c in old.recovery.captivity
                )
                and target.location_id != entity.location_id
            ):
                raise ValidationError("A next adventure cannot release or relocate a captive")
            entities[entity.id] = replace(entity, location_id=target.location_id)
        else:
            entities[entity.id] = entity
    facts = {f.id: f for f in graph.world.facts}
    facts.update({f.id: f for f in old.world.facts})
    commitments = {c.id: c for c in graph.world.commitments}
    commitments.update({c.id: c for c in old.world.commitments})
    old_fact_ids = {f.id for f in old.world.facts}
    world = replace(
        old.world,
        entities=tuple(entities.values()),
        facts=tuple(facts.values()),
        commitments=tuple(commitments.values()),
        connections=tuple(dict.fromkeys((*old.world.connections, *graph.world.connections))),
        knowledge=tuple(
            dict.fromkeys(
                (
                    *old.world.knowledge,
                    *((a, f) for a, f in graph.world.knowledge if f not in old_fact_ids),
                )
            )
        ),
    )
    # Never recreate spent starting inventory or refill pools from a scenario draft.
    historical_items = {
        i.id for s in setup.adventures for i in (*s.graph.resources.items, *s.state.resources.items)
    }
    items = tuple(i for i in graph.resources.items if i.id not in historical_items)
    if any(i.owner_id in actors for i in items):
        raise ValidationError("New party equipment requires an authoritative reward or resupply")
    owners = {o.actor_id: o for o in graph.resources.owners}
    owners.update({o.actor_id: o for o in old.resources.owners})
    resources = old.resources.model_copy(
        update={
            "revision": 0,
            "items": old.resources.items + items,
            "owners": tuple(owners.values()),
        }
    )
    graph = graph.model_copy(update={"world": world, "resources": resources})
    studio = ScenarioStudio(play, npc_reviewer=play.engine.reviewer)
    # Validate full runtime graph. The seed compiler creates pools; we restore them below.
    report = studio.validate(graph)
    if not report.valid:
        raise ValidationError(
            "; ".join(f.message for f in report.findings if f.severity == "error")
        )
    active = PlayService(play.store, studio.engine(graph), rng=play.rng)
    seed = campaign.copy()
    seed.pop("play_json", None)
    seed.pop("resources_json", None)
    seed["revision"] = 0
    state = active.initial_state(seed, world, resources, graph.actors, old.members)
    # NPC schedules use absolute shared time. Reusing a plan could replay its actions.
    old_plans = {p.id for s in setup.adventures if s.graph.npcs for p in s.graph.npcs.plans}
    if graph.npcs and any(
        p.id in old_plans or p.first_due < old.resources.game_time for p in graph.npcs.plans
    ):
        raise ValidationError("Next NPC plans need fresh IDs and future shared-time deadlines")
    if (
        graph.objectives.deadline is not None
        and graph.objectives.deadline <= old.resources.game_time
    ):
        raise ValidationError("Next adventure deadline must follow current shared time")
    if old.recovery != type(old.recovery)() and graph.recovery is None:
        raise ValidationError("Carry recovery policy forward to preserve lasting setbacks")
    revision = campaign["revision"] + 1
    state = state.model_copy(
        update={
            "revision": revision,
            "resources": resources.model_copy(update={"revision": revision}),
            "actors": old.actors,
            "approvals": old.approvals,
            "advancement": old.advancement,
            "migrations": old.migrations,
            "recovery": old.recovery,
        }
    )
    active.engine.validate(state)
    return graph, state
