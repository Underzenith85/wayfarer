"""Bounded scenario generation and validation against authoritative runtime rules."""

from __future__ import annotations

import json

from pydantic import TypeAdapter

from wayfarer.character.power import PowerReviewer
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.providers import Orchestrator, ProviderRequest
from wayfarer.rules.catalog import CampaignPolicy
from wayfarer.simulation.access import CampaignMember
from wayfarer.simulation.actions import ActionEngine, ActorSetup
from wayfarer.simulation.studio import GenerationBrief, ScenarioGraph, StudioFinding, StudioReport


class ScenarioStudio:
    def __init__(self, play: PlayService, *, npc_reviewer: PowerReviewer | None = None) -> None:
        self.play, self.npc_reviewer = play, npc_reviewer

    def engine(self, graph: ScenarioGraph) -> ActionEngine:
        return ActionEngine(
            self.play.engine.reviewer,
            self.play.engine.resources.for_world(graph.world),
            graph.runtime_rules(),
        )

    def validate(self, graph: ScenarioGraph) -> StudioReport:
        findings: list[StudioFinding] = []

        def error(code: str, reference: str, message: str) -> None:
            findings.append(
                StudioFinding(code=code, severity="error", reference=reference, message=message)
            )

        scenes = {s.id: s for s in graph.scenes.scenes}
        actors = {a.actor_id: a for a in graph.actors}
        entities = {e.id: e for e in graph.world.entities}
        if graph.opening_scene_id not in scenes:
            error("opening.missing", graph.opening_scene_id, "Opening scene does not exist")
        if len(actors) != len(graph.actors) or not set(graph.npc_actor_ids) <= actors.keys():
            error(
                "actors.invalid",
                graph.id,
                "Actor IDs must be distinct and NPC references must resolve",
            )
        if not set(actors) - set(graph.npc_actor_ids):
            error("party.missing", graph.id, "At least one player character is required")
        for actor in graph.actors:
            reviewer = (
                self.npc_reviewer
                if actor.actor_id in graph.npc_actor_ids
                else self.play.engine.reviewer
            )
            if reviewer is None:
                error("npc.policy", actor.actor_id, "NPC creation requires an explicit NPC policy")
                continue
            review = reviewer.review(actor.proposal)
            if review.status != "automatic":
                error(
                    "actor.legality",
                    actor.actor_id,
                    "Actor must be legal and approved by its policy",
                )
            if actor.actor_id not in entities:
                error("actor.entity", actor.actor_id, "Actor world entity is missing")
            elif (
                actor.actor_id not in graph.npc_actor_ids
                and graph.opening_scene_id in scenes
                and entities[actor.actor_id].location_id
                != scenes[graph.opening_scene_id].location_id
            ):
                error(
                    "opening.party", actor.actor_id, "Player must start at the actionable opening"
                )
        try:
            graph.world.validate()
            graph.scenes.validate_world(graph.world)
            engine = self.engine(graph)
            # The activation path is also the validator: identical initial resources and discoveries.
            from wayfarer.rules.catalog import reference

            seed = Campaign(
                id="studio-validation",
                revision=0,
                rules="studio",
                rules_ref=reference(engine.resources.rules),
                character={
                    "name": "studio",
                    "concept": "",
                    "attributes": {},
                    "skills": {},
                    "traits": [],
                },
                scenario={},
                hp=0,
                fp=0,
                minutes=0,
                location="",
                inventory=[],
                discoveries=[],
                flags=[],
                complete=False,
                messages=[],
            )
            PlayService(self.play.store, engine).initial_state(
                seed, graph.world, graph.resources, graph.actors
            )
        except (ValidationError, ValueError, KeyError, StopIteration) as exc:
            error("runtime.invalid", graph.id, str(exc) or "Invalid runtime references")
        escrow = [r.item_id for r in graph.objectives.rewards if r.item_id is not None]
        if len(escrow) != len(set(escrow)):
            error("reward.duplicate", graph.id, "Escrow items cannot fund multiple rewards")
        if graph.objectives.deadline is None and not graph.objectives.failures:
            error(
                "ending.failure_missing",
                graph.id,
                "Failure consequences need a runtime deadline or predicate",
            )
        # Monotone progression closure detects circular prerequisites and missing clues.
        reached = {graph.opening_scene_id} & scenes.keys()
        known = {f for a, f in graph.world.knowledge if a in actors}
        sources: dict[str, set[str]] = {}
        available_checks: set[str] = set()
        for check in graph.actions.checks:
            for actor in graph.actors:
                if actor.actor_id in graph.npc_actor_ids:
                    continue
                build = self.play.engine.reviewer.review(actor.proposal).compilation.build
                if (
                    build
                    and any(v.target == check.definition_id for v in build.sheet.values)
                    and (
                        not check.required_equipment
                        or any(
                            i.owner_id == actor.actor_id
                            and i.definition_id == check.required_equipment
                            for i in graph.resources.items
                        )
                    )
                ):
                    available_checks.add(check.id)
        for _ in range(len(scenes) + len(graph.world.facts) + 1):
            before = (frozenset(reached), frozenset(known))
            for discovery in graph.scenes.discoveries:
                checks = [
                    c
                    for c in graph.actions.checks
                    if c.id in available_checks
                    and c.target_id == discovery.target_id
                    and discovery.fact_id in c.reveal_fact_ids
                ]
                if discovery.scene_id in reached and (discovery.mode == "automatic" or checks):
                    known.add(discovery.fact_id)
                    sources.setdefault(discovery.fact_id, set()).add(
                        "automatic:" + discovery.scene_id
                        if discovery.mode == "automatic"
                        else "check:" + str(discovery.target_id)
                    )
            for trigger in graph.scenes.triggers:
                if trigger.scene_id in reached and trigger.phase == "entry":
                    known.add(trigger.fact_id)
                    sources.setdefault(trigger.fact_id, set()).add("automatic:" + trigger.scene_id)
            for scene in graph.scenes.scenes:
                if scene.id not in reached:
                    continue
                for route in scene.exits:
                    obstacles = [o for o in scene.obstacles if o.exit_id == route.id]
                    if set(route.required_fact_ids) <= known and all(
                        o.bypass_fact_ids and set(o.bypass_fact_ids) <= known for o in obstacles
                    ):
                        if route.destination_id in scenes:
                            reached.add(route.destination_id)
            if before == (frozenset(reached), frozenset(known)):
                break
        for scene_id in scenes.keys() - reached:
            error(
                "graph.unreachable",
                scene_id,
                "No supported route from the opening; check circular prerequisites and mandatory clues",
            )
        required = {
            p.value
            for o in graph.objectives.objectives
            if o.required
            for p in o.predicates
            if p.kind == "known" and not p.negate
        }
        required |= {f for s in graph.scenes.scenes for e in s.exits for f in e.required_fact_ids}
        for fact_id in required:
            if fact_id not in known:
                error("clue.missing", fact_id, "Mandatory clue has no attainable revelation")
            origins = sources.get(fact_id, set())
            if origins and len(origins) == 1 and all(s.startswith("check:") for s in origins):
                error(
                    "clue.bottleneck",
                    fact_id,
                    "Mandatory clue depends on one roll or NPC; add an independent fallback",
                )
        for objective in graph.objectives.objectives:
            positive = [p for p in objective.predicates if not p.negate]
            for i, predicate in enumerate(positive):
                if predicate.kind in ("location", "fact", "custody") and any(
                    p.kind == predicate.kind
                    and p.subject_id == predicate.subject_id
                    and p.value != predicate.value
                    for p in positive[i + 1 :]
                ):
                    error(
                        "ending.contradiction",
                        objective.id,
                        "Terminal conjunction requires incompatible values",
                    )
                if predicate.kind == "location" and not any(
                    s.id in reached and s.location_id == predicate.value
                    for s in graph.scenes.scenes
                ):
                    error(
                        "ending.unreachable",
                        objective.id,
                        "Declared ending requires an unreachable location",
                    )
        for approach in graph.approaches:
            if (
                approach.check_rule_id not in available_checks
                or approach.actor_id not in actors
                or approach.scene_id not in reached
            ):
                error(
                    "approach.unsupported",
                    approach.id,
                    "Advertised approach is incompatible with the actual party or graph",
                )
        if graph.recovery:
            for setback in graph.recovery.setbacks:
                if setback.kind not in ("capture", "surrender"):
                    continue
                destination = setback.destination_scene_id or graph.opening_scene_id
                branches = [
                    o
                    for o in graph.recovery.options
                    if o.kind in ("escape", "rescue") and o.supported and o.scene_id == destination
                ]
                if not branches:
                    error(
                        "capture.fallback",
                        setback.id,
                        "Capture requires an authored supported rescue or escape branch",
                    )
                for branch in branches:
                    if branch.scene_id not in reached or not set(branch.required_fact_ids) <= known:
                        error(
                            "capture.unreachable",
                            branch.id,
                            "Advertised rescue requires unreachable location or undiscoverable evidence",
                        )
        findings.append(
            StudioFinding(
                code="challenge.estimate",
                severity="warning",
                reference=graph.id,
                message="Challenge estimates do not guarantee solvability or fairness; check tactical balance and deadlines in playtests.",
            )
        )
        return StudioReport(
            findings=tuple(findings),
            reachable_scene_ids=tuple(sorted(reached)),
            challenge={
                "scenes": len(scenes),
                "checks": len(graph.actions.checks),
                "supported_party_checks": len(available_checks),
                "deadline_ticks": graph.objectives.deadline or 0,
                "capture_branches": len(graph.recovery.options) if graph.recovery else 0,
            },
        )

    async def generate(
        self,
        brief: GenerationBrief,
        *,
        llm: Orchestrator,
        principal_id: str,
        attempts: int = 2,
        party: tuple[ActorSetup, ...] = (),
    ) -> tuple[ScenarioGraph, StudioReport]:
        if principal_id not in self.play.engine.reviewer.gm_ids:
            raise ValidationError("Scenario generation requires trusted author authority")
        if not 1 <= attempts <= 3:
            raise ValidationError("Invalid repair budget")
        context: dict[str, object] = {
            "brief": brief.model_dump(mode="json"),
            "party": [a.model_dump(mode="json") for a in party],
            "catalog_ids": sorted(self.play.engine.reviewer.compiler.definitions),
            "policy": json.loads(
                TypeAdapter(CampaignPolicy).dump_json(self.play.engine.reviewer.compiler.policy)
            ),
        }
        for _ in range(attempts):
            raw = await llm._call(
                ProviderRequest(
                    operation="scenario_draft",
                    session_id=f"studio:{principal_id}",
                    context_json=json.dumps(context),
                    prompt="Create a runtime-backed adventure with alternate progression routes. Never invent catalog IDs or unsupported mechanics.",
                    output_schema=ScenarioGraph.model_json_schema(),
                )
            )
            graph = ScenarioGraph.model_validate_json(raw)
            report = self.validate(graph)
            if party and {a.actor_id: a.proposal for a in party} != {
                a.actor_id: a.proposal
                for a in graph.actors
                if a.actor_id not in graph.npc_actor_ids
            }:
                report = report.model_copy(
                    update={
                        "findings": report.findings
                        + (
                            StudioFinding(
                                code="party.changed",
                                severity="error",
                                reference=graph.id,
                                message="Generated scenario must preserve the supplied party and its capabilities",
                            ),
                        )
                    }
                )
            if report.valid:
                return graph, report
            context["validation"] = report.model_dump(mode="json")
        return graph, report

    async def activate(
        self,
        graph: ScenarioGraph,
        campaign: Campaign,
        members: tuple[CampaignMember, ...],
        *,
        principal_id: str,
    ) -> PlayService:
        if principal_id not in self.play.engine.reviewer.gm_ids:
            raise ValidationError("Scenario activation requires trusted author authority")
        report = self.validate(graph)
        if not report.valid:
            raise ValidationError("Scenario failed hard playability checks")
        activated = PlayService(self.play.store, self.engine(graph), rng=self.play.rng)
        # A deterministic campaign ID makes retries identify the same starting snapshot.
        from wayfarer.errors import NotFoundError

        try:
            existing = await self.play.store.read(campaign["id"])
        except NotFoundError:
            existing = None
        if existing is not None:
            if existing.get("scenario_graph_json") != graph.model_dump_json():
                raise ConflictError("Campaign identity already belongs to another scenario")
            existing_state = activated._load(existing)
            if existing_state.members != members:
                raise ConflictError("Activation membership changed")
            return activated
        seed = campaign.copy()
        seed["scenario_graph_json"] = graph.model_dump_json()
        await activated.create(seed, graph.world, graph.resources, graph.actors, members)
        return activated
