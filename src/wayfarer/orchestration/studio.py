"""Bounded scenario generation and validation against authoritative runtime rules."""

from __future__ import annotations

import json
from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from pydantic import TypeAdapter
from pydantic import ValidationError as SchemaError

from wayfarer.contracts import Campaign
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import CampaignPolicy, reference
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, CheckRule
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneExit
from wayfarer.engine.simulation.campaign.studio import (
    GenerationBrief,
    ScenarioGraph,
    StudioFinding,
    StudioReport,
)
from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.providers import Orchestrator, ProviderRequest
from wayfarer.orchestration.scenario_references import pin_scenario


def _listed(values: Iterable[object]) -> str:
    return ", ".join(sorted(str(value) for value in values)) or "none"


def _error(code: str, reference: str, message: str) -> StudioFinding:
    """An error finding names the node to edit and what would satisfy the rule (#365)."""
    return StudioFinding(code=code, severity="error", reference=reference, message=message)


@dataclass(frozen=True, slots=True)
class _Progression:
    """What the monotone closure reached, what it learned, and why it stopped.

    The closure is also the diagnosis: each finding below quotes the reason a
    candidate route or revelation was rejected instead of restating its rule.
    """

    graph: ScenarioGraph
    scenes: dict[str, Scene]
    declared_checks: dict[str, CheckRule]
    available_checks: set[str]
    actor_checks: set[tuple[str, str]]
    unsupported_checks: dict[str, str]
    reached: set[str]
    known: set[str]
    initially_known: set[str]
    sources: dict[str, set[str]]

    @classmethod
    def analyse(cls, graph: ScenarioGraph, reviewer: PowerReviewer) -> _Progression:
        player_ids = {a.actor_id for a in graph.actors} - set(graph.npc_actor_ids)
        analysis = cls(
            graph=graph,
            scenes={s.id: s for s in graph.scenes.scenes},
            declared_checks={c.id: c for c in graph.actions.checks},
            available_checks=set(),
            actor_checks=set(),
            unsupported_checks={},
            reached={graph.opening_scene_id} & {s.id for s in graph.scenes.scenes},
            known={f for a, f in graph.world.knowledge if a in player_ids},
            initially_known={f for a, f in graph.world.knowledge if a in player_ids},
            sources={},
        )
        analysis._index_party_checks(reviewer, player_ids)
        analysis._close()
        return analysis

    def _index_party_checks(self, reviewer: PowerReviewer, player_ids: set[str]) -> None:
        """Record which authored checks the actual party can run, and why not."""
        builds = {
            a.actor_id: reviewer.review(a.proposal).compilation.build
            for a in self.graph.actors
            if a.actor_id in player_ids
        }
        for check in self.graph.actions.checks:
            skilled = {
                actor_id
                for actor_id, build in builds.items()
                if build and any(v.target == check.definition_id for v in build.sheet.values)
            }
            for actor_id in skilled:
                if not check.required_equipment or any(
                    i.owner_id == actor_id and i.definition_id == check.required_equipment
                    for i in self.graph.resources.items
                ):
                    self.available_checks.add(check.id)
                    self.actor_checks.add((actor_id, check.id))
            if check.id not in self.available_checks:
                self.unsupported_checks[check.id] = (
                    f"no player character has {check.definition_id}; buy it for a player or point "
                    f"the check at a definition the party holds"
                    if not skilled
                    else f"{_listed(skilled)} has {check.definition_id} but no player character "
                    f"carries the required {check.required_equipment}; grant that item in "
                    f"resources.items"
                )

    def _close(self) -> None:
        """Monotone progression closure: circular prerequisites simply never open."""
        graph = self.graph
        for _ in range(len(self.scenes) + len(graph.world.facts) + 1):
            before = (frozenset(self.reached), frozenset(self.known))
            for discovery in graph.scenes.discoveries:
                checks = [
                    c
                    for c in graph.actions.checks
                    if c.id in self.available_checks
                    and c.target_id == discovery.target_id
                    and discovery.fact_id in c.reveal_fact_ids
                ]
                if discovery.scene_id in self.reached and (discovery.mode == "automatic" or checks):
                    self._learn(
                        discovery.fact_id,
                        "automatic:" + discovery.scene_id
                        if discovery.mode == "automatic"
                        else "check:" + str(discovery.target_id),
                    )
            for trigger in graph.scenes.triggers:
                if trigger.scene_id in self.reached and trigger.phase == "entry":
                    self._learn(trigger.fact_id, "automatic:" + trigger.scene_id)
            # The same reachability closure includes supported authored encounter and
            # recovery outcomes, rather than treating all clues as scene discoveries.
            if graph.noncombat:
                for encounter in graph.noncombat.encounters:
                    if encounter.scene_id in self.reached:
                        for approach in encounter.approaches:
                            if approach.check_rule_id in self.available_checks:
                                for fact_id in (
                                    *approach.success_fact_ids,
                                    *encounter.completion_fact_ids,
                                ):
                                    self._learn(fact_id, "encounter:" + encounter.id)
            if graph.recovery:
                for option in graph.recovery.options:
                    if (
                        option.scene_id in self.reached
                        and option.supported
                        and set(option.required_fact_ids) <= self.known
                        and (
                            option.check_rule_id is None
                            or option.check_rule_id in self.available_checks
                        )
                    ):
                        for fact_id in option.success_fact_ids:
                            self._learn(fact_id, "recovery:" + option.id)
            for scene in graph.scenes.scenes:
                if scene.id not in self.reached:
                    continue
                for route in scene.exits:
                    if not self._route_block(scene, route) and route.destination_id in self.scenes:
                        self.reached.add(route.destination_id)
            if before == (frozenset(self.reached), frozenset(self.known)):
                break

    def _learn(self, fact_id: str, origin: str) -> None:
        self.known.add(fact_id)
        self.sources.setdefault(fact_id, set()).add(origin)

    def _route_block(self, scene: Scene, route: SceneExit) -> tuple[str, ...]:
        """The prerequisites of one exit that the closure has not satisfied."""
        causes: list[str] = []
        missing = set(route.required_fact_ids) - self.known
        blocked = [
            o.id
            for o in scene.obstacles
            if o.exit_id == route.id
            and not (o.bypass_fact_ids and set(o.bypass_fact_ids) <= self.known)
        ]
        if missing:
            causes.append(f"unattainable facts {_listed(missing)}")
        if blocked:
            causes.append(f"obstacles {_listed(blocked)} without an attainable bypass fact")
        return tuple(causes)

    def route_report(self, scene: Scene, route: SceneExit) -> str:
        """Say why a declared exit never opened during the closure."""
        if scene.id not in self.reached:
            return f"exit {route.id} starts in unreachable scene {scene.id}"
        return (
            f"exit {route.id} from {scene.id} needs {' and '.join(self._route_block(scene, route))}"
        )

    def check_support(self, check_id: str) -> str | None:
        """Explain an unusable check reference, or None when the party can run it."""
        if check_id not in self.declared_checks:
            return f"check {check_id} is not declared in actions.checks"
        reason = self.unsupported_checks.get(check_id)
        return None if reason is None else f"check {check_id} is unsupported: {reason}"

    def unattainable(self, fact_id: str) -> str:
        """State what the reachability closure concluded about a mandatory clue."""
        graph = self.graph
        reasons: list[str] = []
        for discovery in graph.scenes.discoveries:
            if discovery.fact_id != fact_id:
                continue
            if discovery.scene_id not in self.reached:
                reasons.append(
                    f"discovery {discovery.id} sits in unreachable scene {discovery.scene_id}"
                )
            elif discovery.mode == "check":
                revealing = [
                    c
                    for c in graph.actions.checks
                    if c.target_id == discovery.target_id and fact_id in c.reveal_fact_ids
                ]
                reasons.extend(
                    f"discovery {discovery.id} has {support}"
                    for support in (self.check_support(c.id) for c in revealing)
                    if support is not None
                )
                if not revealing:
                    reasons.append(
                        f"discovery {discovery.id} is a check on {discovery.target_id}, but no "
                        f"check rule targets it and lists {fact_id} in reveal_fact_ids"
                    )
        for trigger in graph.scenes.triggers:
            if trigger.fact_id != fact_id:
                continue
            if trigger.phase != "entry":
                reasons.append(
                    f"trigger {trigger.id} fires on exit, which no route is guaranteed to take"
                )
            elif trigger.scene_id not in self.reached:
                reasons.append(f"trigger {trigger.id} sits in unreachable scene {trigger.scene_id}")
        for encounter in graph.noncombat.encounters if graph.noncombat else ():
            candidates = [
                a
                for a in encounter.approaches
                if fact_id in a.success_fact_ids or fact_id in encounter.completion_fact_ids
            ]
            if not candidates:
                continue
            if encounter.scene_id not in self.reached:
                reasons.append(
                    f"encounter {encounter.id} sits in unreachable scene {encounter.scene_id}"
                )
                continue
            reasons.extend(
                f"encounter approach {approach.id} has {support}"
                for approach in candidates
                for support in (self.check_support(approach.check_rule_id),)
                if support is not None
            )
        for option in graph.recovery.options if graph.recovery else ():
            if fact_id not in option.success_fact_ids:
                continue
            support = (
                None if option.check_rule_id is None else self.check_support(option.check_rule_id)
            )
            if not option.supported:
                reasons.append(f"recovery option {option.id} is marked unsupported")
            elif option.scene_id not in self.reached:
                reasons.append(
                    f"recovery option {option.id} sits in unreachable scene {option.scene_id}"
                )
            elif not set(option.required_fact_ids) <= self.known:
                reasons.append(
                    f"recovery option {option.id} requires unattainable "
                    f"{_listed(set(option.required_fact_ids) - self.known)}"
                )
            elif support is not None:
                reasons.append(f"recovery option {option.id} has {support}")
        if not reasons:
            return (
                "no discovery, entry trigger, encounter approach or recovery option reveals it; "
                "author one in a reachable scene, or give a player character the fact as "
                "starting knowledge"
            )
        return "; ".join(reasons)

    @staticmethod
    def origin_label(origin: str) -> str:
        kind, _, value = origin.partition(":")
        return {
            "automatic": f"an automatic revelation in scene {value}",
            "check": f"one check against {value}",
            "encounter": f"noncombat encounter {value}",
            "recovery": f"recovery option {value}",
        }[kind]


def _progression_findings(analysis: _Progression) -> list[StudioFinding]:
    """Unreachable scenes and mandatory clues, each with the closure's conclusion."""
    graph, findings = analysis.graph, []
    for scene_id in sorted(analysis.scenes.keys() - analysis.reached):
        routes = [
            (scene, route)
            for scene in graph.scenes.scenes
            for route in scene.exits
            if route.destination_id == scene_id
        ]
        detail = (
            "no declared exit leads here; add one from a reachable scene"
            if not routes
            else "; ".join(analysis.route_report(scene, route) for scene, route in routes)
        )
        findings.append(
            _error(
                "graph.unreachable",
                scene_id,
                f"Scene {scene_id} has no supported route from opening scene "
                f"{graph.opening_scene_id}: {detail}",
            )
        )
    required = {
        p.value
        for o in graph.objectives.objectives
        if o.required
        for p in o.predicates
        if p.kind == "known" and not p.negate
    }
    required |= {f for s in graph.scenes.scenes for e in s.exits for f in e.required_fact_ids}
    for fact_id in sorted(required):
        if fact_id not in analysis.known:
            findings.append(
                _error(
                    "clue.missing",
                    fact_id,
                    f"Mandatory clue {fact_id} has no attainable revelation: "
                    f"{analysis.unattainable(fact_id)}",
                )
            )
        origins = analysis.sources.get(fact_id, set())
        if (
            fact_id not in analysis.initially_known
            and origins
            and len(origins) == 1
            and all(s.startswith(("check:", "encounter:")) for s in origins)
        ):
            findings.append(
                _error(
                    "clue.bottleneck",
                    fact_id,
                    f"Mandatory clue {fact_id} depends only on "
                    f"{analysis.origin_label(next(iter(origins)))}; add an independent fallback "
                    f"such as an automatic discovery or entry trigger in another reachable scene",
                )
            )
    return findings


def _ending_findings(analysis: _Progression) -> list[StudioFinding]:
    """Terminal states that cannot all hold, and endings no route can reach."""
    graph, findings = analysis.graph, []
    # Required objectives are a conjunction even when incompatible predicates
    # were placed in separate objective records by the author or generator.
    required_predicates = [
        (o, p) for o in graph.objectives.objectives if o.required for p in o.predicates
    ]
    for i, (objective, predicate) in enumerate(required_predicates):
        for other_objective, other in required_predicates[i + 1 :]:
            if (predicate.kind, predicate.subject_id) != (other.kind, other.subject_id):
                continue
            opposite = (
                predicate.value == other.value
                and predicate.minimum == other.minimum
                and predicate.negate != other.negate
            )
            exclusive = (
                predicate.kind in ("location", "fact", "custody")
                and not predicate.negate
                and not other.negate
                and predicate.value != other.value
            )
            if opposite or exclusive:
                findings.append(
                    _error(
                        "ending.contradiction",
                        graph.objectives.id,
                        f"Required objectives {objective.id} and {other_objective.id} in "
                        f"{graph.objectives.id} demand mutually incompatible terminal states: "
                        f"{predicate.kind} {predicate.subject_id} must be "
                        f"{'not ' if predicate.negate else ''}{predicate.value} and "
                        f"{'not ' if other.negate else ''}{other.value}; drop one predicate or "
                        f"mark one objective optional",
                    )
                )
    for objective in graph.objectives.objectives:
        positive = [p for p in objective.predicates if not p.negate]
        for i, predicate in enumerate(positive):
            clashing = [
                p.value
                for p in positive[i + 1 :]
                if p.kind == predicate.kind
                and p.subject_id == predicate.subject_id
                and p.value != predicate.value
            ]
            if predicate.kind in ("location", "fact", "custody") and clashing:
                findings.append(
                    _error(
                        "ending.contradiction",
                        objective.id,
                        f"Objective {objective.id} requires {predicate.subject_id} to be "
                        f"{predicate.kind} {predicate.value} and {_listed(clashing)} at once; a "
                        f"{predicate.kind} predicate holds one value, so keep a single value",
                    )
                )
            if predicate.kind == "location" and not any(
                s.id in analysis.reached and s.location_id == predicate.value
                for s in graph.scenes.scenes
            ):
                findings.append(
                    _error(
                        "ending.unreachable",
                        objective.id,
                        f"Objective {objective.id} ends at location {predicate.value}, which no "
                        f"reachable scene occupies; reachable scenes cover "
                        f"{_listed(analysis.scenes[s].location_id for s in analysis.reached)}",
                    )
                )
    return findings


def _approach_findings(analysis: _Progression) -> list[StudioFinding]:
    """Advertised approaches and capture fallbacks, with every unmet condition."""
    graph, findings = analysis.graph, []
    entities = {e.id: e for e in graph.world.entities}
    for approach in graph.approaches:
        check = analysis.declared_checks.get(approach.check_rule_id)
        causes: list[str] = []
        if check is None:
            causes.append(f"check {approach.check_rule_id} is not declared in actions.checks")
        elif (approach.actor_id, approach.check_rule_id) not in analysis.actor_checks:
            runners = _listed(a for a, c in analysis.actor_checks if c == approach.check_rule_id)
            causes.append(
                analysis.check_support(approach.check_rule_id)
                or f"{approach.actor_id} is not one of the player characters that can run check "
                f"{approach.check_rule_id} ({runners})"
            )
        if approach.scene_id not in analysis.scenes:
            causes.append(f"scene {approach.scene_id} is not declared")
        elif approach.scene_id not in analysis.reached:
            causes.append(f"scene {approach.scene_id} is unreachable from the opening")
        if check is not None and check.target_id not in entities:
            causes.append(f"check {check.id} targets unknown entity {check.target_id}")
        elif check is not None and approach.scene_id in analysis.scenes:
            location = entities[check.target_id].location_id or check.target_id
            if location != analysis.scenes[approach.scene_id].location_id:
                causes.append(
                    f"target {check.target_id} is at {location}, but scene {approach.scene_id} is "
                    f"at {analysis.scenes[approach.scene_id].location_id}"
                )
        if causes:
            findings.append(
                _error(
                    "approach.unsupported",
                    approach.id,
                    f"Advertised approach {approach.id} is incompatible with the actual party or "
                    f"graph: {'; '.join(causes)}",
                )
            )
    if graph.recovery is None:
        return findings
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
            findings.append(
                _error(
                    "capture.fallback",
                    setback.id,
                    f"Setback {setback.id} sends the party to {destination} with no supported "
                    f"rescue or escape; author a recovery option of kind escape or rescue in "
                    f"scene {destination} with supported set",
                )
            )
        for branch in branches:
            missing = set(branch.required_fact_ids) - analysis.known
            if branch.scene_id not in analysis.reached or missing:
                findings.append(
                    _error(
                        "capture.unreachable",
                        branch.id,
                        f"Recovery branch {branch.id} for setback {setback.id} is not usable: "
                        + (
                            f"scene {branch.scene_id} is unreachable from the opening"
                            if branch.scene_id not in analysis.reached
                            else f"it requires unattainable {_listed(missing)}"
                        ),
                    )
                )
    return findings


def _objective_shape_findings(graph: ScenarioGraph) -> list[StudioFinding]:
    """Escrow funding and the declared failure path."""
    findings: list[StudioFinding] = []
    funded: dict[str, list[str]] = {}
    for reward in graph.objectives.rewards:
        if reward.item_id is not None:
            funded.setdefault(reward.item_id, []).append(reward.id)
    for item_id, reward_ids in sorted(funded.items()):
        if len(reward_ids) > 1:
            findings.append(
                _error(
                    "reward.duplicate",
                    item_id,
                    f"Escrow item {item_id} funds rewards {_listed(reward_ids)}; an escrow item is "
                    f"transferred once, so give each reward its own item",
                )
            )
    if graph.objectives.deadline is None and not graph.objectives.failures:
        findings.append(
            _error(
                "ending.failure_missing",
                graph.objectives.id,
                f"Objectives {graph.objectives.id} declare no failure path; set "
                f"objectives.deadline or add at least one entry to objectives.failures",
            )
        )
    return findings


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
        """Report every hard playability defect at the node that has to change.

        A finding an author cannot act on is a defect of the validator (#365), so
        each one names the offending check, scene, clue, objective or approach and
        says what would satisfy the rule without reading the engine.
        """
        analysis = _Progression.analyse(graph, self.play.engine.reviewer)
        findings = [
            *self._cast_findings(graph),
            *self._runtime_findings(graph),
            *_objective_shape_findings(graph),
            *_progression_findings(analysis),
            *_ending_findings(analysis),
            *_approach_findings(analysis),
            StudioFinding(
                code="challenge.estimate",
                severity="warning",
                reference=graph.id,
                message="Challenge estimates do not guarantee solvability or fairness; check tactical balance and deadlines in playtests.",
            ),
        ]
        return StudioReport(
            findings=tuple(findings),
            reachable_scene_ids=tuple(sorted(analysis.reached)),
            challenge={
                "scenes": len(analysis.scenes),
                "checks": len(graph.actions.checks),
                "supported_party_checks": len(analysis.available_checks),
                "deadline_ticks": graph.objectives.deadline or 0,
                "capture_branches": len(graph.recovery.options) if graph.recovery else 0,
            },
        )

    def _cast_findings(self, graph: ScenarioGraph) -> list[StudioFinding]:
        """The opening, the party roster and every actor's legality and placement."""
        findings: list[StudioFinding] = []
        scenes = {s.id: s for s in graph.scenes.scenes}
        actors = {a.actor_id: a for a in graph.actors}
        entities = {e.id: e for e in graph.world.entities}
        if graph.opening_scene_id not in scenes:
            findings.append(
                _error(
                    "opening.missing",
                    graph.opening_scene_id,
                    f"Opening scene {graph.opening_scene_id} does not exist; opening_scene_id must "
                    f"name a declared scene ({_listed(scenes)})",
                )
            )
        for actor_id, count in sorted(Counter(a.actor_id for a in graph.actors).items()):
            if count > 1:
                findings.append(
                    _error(
                        "actors.invalid",
                        actor_id,
                        f"Actor ID {actor_id} is declared {count} times; give every actor a "
                        f"distinct actor_id",
                    )
                )
        for npc_id in sorted(set(graph.npc_actor_ids) - actors.keys()):
            findings.append(
                _error(
                    "actors.invalid",
                    npc_id,
                    f"npc_actor_ids names {npc_id}, which is not a declared actor; add an actor "
                    f"with that ID or drop the reference",
                )
            )
        if not set(actors) - set(graph.npc_actor_ids):
            findings.append(
                _error(
                    "party.missing",
                    graph.id,
                    f"Scenario {graph.id} has no player character: every declared actor "
                    f"({_listed(actors)}) is listed in npc_actor_ids; leave at least one actor "
                    f"out of npc_actor_ids",
                )
            )
        for actor in graph.actors:
            reviewer = (
                self.npc_reviewer
                if actor.actor_id in graph.npc_actor_ids
                else self.play.engine.reviewer
            )
            if reviewer is None:
                findings.append(
                    _error(
                        "npc.policy",
                        actor.actor_id,
                        f"NPC {actor.actor_id} requires an explicit NPC policy; build the studio "
                        f"with an npc_reviewer or remove {actor.actor_id} from npc_actor_ids",
                    )
                )
                continue
            review = reviewer.review(actor.proposal)
            if review.status != "automatic":
                blocking = [f"{f.code} ({f.rule_id})" for f in review.findings] or [
                    f"{d.code} at {'.'.join(str(part) for part in d.path)}"
                    for d in review.compilation.diagnostics
                ]
                findings.append(
                    _error(
                        "actor.legality",
                        actor.actor_id,
                        f"Actor {actor.actor_id} must be legal and approved by its policy; its "
                        f"review is {review.status} because of "
                        f"{_listed(blocking) if blocking else 'its build'}",
                    )
                )
            if actor.actor_id not in entities:
                findings.append(
                    _error(
                        "actor.entity",
                        actor.actor_id,
                        f"Actor {actor.actor_id} has no world entity; add an actor entity with "
                        f"id {actor.actor_id} to world.entities",
                    )
                )
            elif (
                actor.actor_id not in graph.npc_actor_ids
                and graph.opening_scene_id in scenes
                and entities[actor.actor_id].location_id
                != scenes[graph.opening_scene_id].location_id
            ):
                opening = scenes[graph.opening_scene_id]
                findings.append(
                    _error(
                        "opening.party",
                        actor.actor_id,
                        f"Player {actor.actor_id} starts at "
                        f"{entities[actor.actor_id].location_id or 'no location'}, but opening "
                        f"scene {opening.id} is at {opening.location_id}; move the entity to "
                        f"{opening.location_id} or open at the player's location",
                    )
                )
        return findings

    def _runtime_findings(self, graph: ScenarioGraph) -> list[StudioFinding]:
        """Run the activation path itself; engine invariants carry their own locus."""
        try:
            graph.world.validate()
            graph.scenes.validate_world(graph.world)
            engine = self.engine(graph)
            # The activation path is also the validator: identical initial resources and discoveries.

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
            # Engine invariants name the offending node when they can (#365); fall
            # back to the scenario only for failures with no better locus.
            locus = exc.reference if isinstance(exc, ValidationError) else None
            detail = str(exc) or "Invalid runtime references"
            if isinstance(exc, KeyError):
                detail = f"Unresolved runtime reference {detail}; declare it or stop naming it"
            elif isinstance(exc, StopIteration):
                detail = (
                    "A runtime lookup found no matching record; check that every rule, actor and "
                    "resource the scenario names is declared"
                )
            return [_error("runtime.invalid", locus or graph.id, detail)]
        return []

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
            "npc_policy": json.loads(
                TypeAdapter(CampaignPolicy).dump_json(self.npc_reviewer.compiler.policy)
            )
            if self.npc_reviewer
            else None,
            "npc_catalog_ids": sorted(self.npc_reviewer.compiler.definitions)
            if self.npc_reviewer
            else [],
        }
        graph: ScenarioGraph | None = None
        report: StudioReport | None = None
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
            try:
                graph = ScenarioGraph.model_validate_json(raw)
            except SchemaError:
                context["validation"] = {
                    "code": "schema.invalid",
                    "message": "Return a complete scenario matching the supplied output schema.",
                }
                continue
            report = self.validate(graph)
            supplied = {a.actor_id: a.proposal for a in party}
            generated = {
                a.actor_id: a.proposal
                for a in graph.actors
                if a.actor_id not in graph.npc_actor_ids
            }
            if party and supplied != generated:
                changed = sorted(
                    supplied.keys() ^ generated.keys()
                    | {k for k in supplied.keys() & generated.keys() if supplied[k] != generated[k]}
                )
                report = report.model_copy(
                    update={
                        "findings": report.findings
                        + (
                            StudioFinding(
                                code="party.changed",
                                severity="error",
                                reference=changed[0],
                                message=(
                                    "Generated scenario must preserve the supplied party and its "
                                    f"capabilities; {', '.join(changed)} was added, dropped or "
                                    "rebuilt. Copy the supplied player actors verbatim and put "
                                    "new characters in npc_actor_ids."
                                ),
                            ),
                        )
                    }
                )
            if report.valid:
                return graph, report
            context["validation"] = report.model_dump(mode="json")
        if graph is None or report is None:
            raise ValidationError("Scenario generation exhausted its schema repair budget")
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

        try:
            existing = await self.play.store.read(campaign["id"])
        except NotFoundError:
            existing = None
        if existing is not None:
            existing_graph = existing.get("scenario_graph_json")
            if existing_graph is None or json.loads(existing_graph) != graph.model_dump(
                mode="json"
            ):
                raise ConflictError("Campaign identity already belongs to another scenario")
            if existing.get("scenario_document_json") != campaign.get("scenario_document_json"):
                raise ConflictError("Campaign identity already pins another document revision")
            existing_state = activated._load(existing)
            if existing_state.members != members:
                raise ConflictError("Activation membership changed")
            return activated
        seed = campaign.copy()

        published = None
        if "scenario_document_json" in seed:
            # deferred: scenario_documents -> studio -> scenario_documents.  Activation saves a
            # draft through the documents service, which wraps this studio.
            from wayfarer.orchestration.scenario_documents import ScenarioDocuments

            documents = ScenarioDocuments(self)
            draft = documents.save_draft(
                seed["scenario_document_json"], draft_id="activation", principal_id=principal_id
            )
            published = documents.publish(draft, principal_id=principal_id)
        pin_scenario(
            seed,
            graph,
            runtime_digest=activated.engine.digest,
            command_id="activate",
            campaign_revision=0,
            published=published,
        )
        await activated.create(seed, graph.world, graph.resources, graph.actors, members)
        return activated
