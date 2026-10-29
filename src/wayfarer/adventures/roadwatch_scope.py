"""Immutable HTTP admission for the fixed Roadside Watch campaign.

This is a deployment boundary, not another rules engine. It never reads current
campaign state: native authorization, CAS, saved receipts and captured command
facts remain authoritative, including retries after their choices disappear.
"""

import json
import re
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass

from aiohttp import web
from aiohttp.typedefs import Middleware

from wayfarer.adventures.roadwatch_rules import GM_ID, PC_ACTOR_IDS, PLAYER_ID, require_pregen
from wayfarer.engine.simulation.actions import ACTION_ADAPTER, Inspect, Move, Wait
from wayfarer.engine.simulation.campaign.setup import CreateSetup, SetupCommand
from wayfarer.engine.simulation.campaign.studio import ScenarioGraph
from wayfarer.engine.simulation.combat.commands import (
    COMBAT_ADAPTER,
    ChooseDefense,
    ContinueCriticalMiss,
    EndEncounter,
    ResumeInterruptedTurn,
    RetrieveEquipment,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.engine.world import EntityKind
from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.noncombat import NoncombatCommand
from wayfarer.orchestration.objectives import ObjectiveCommand
from wayfarer.orchestration.player_medical import PlayerRecoveryCommand
from wayfarer.orchestration.scenes import SCENE_ADAPTER, ObserveScene, TravelScene
from wayfarer.transport.common import _identity, _json
from wayfarer.transport.v1.common import Fault

REFUSAL = "This action is outside Roadside Watch's admitted campaign scope"
_IDENTITY = {"kind", "id", "actor_id", "expected_revision"}
_TURN_FIELDS = _IDENTITY | {
    "encounter_id",
    "maneuver",
    "destination",
    "facing",
    "posture",
    "item_id",
    "target_id",
    "mode_id",
    "hit_location",
    "ready_hand",
    "ready_reach",
    "hex_path",
    "hex_facing",
    "step_timing",
}
_DEFENSE_FIELDS = _IDENTITY | {"encounter_id", "defense", "item_id", "retreat", "parry_mode_id"}
_MANEUVERS = {"attack", "move", "ready", "do_nothing", "change_posture"}


def _require(condition: bool) -> None:
    if not condition:
        raise ValidationError(REFUSAL)


def _shape(command: Record, allowed: set[str]) -> None:
    _require(set(command.model_dump(exclude_defaults=True)) <= allowed)


@dataclass(frozen=True)
class RoadwatchScope:
    graph: ScenarioGraph

    def command(self, raw: Mapping[str, object]) -> None:
        """Validate supported payloads without observing mutable game state."""
        kind = raw.get("kind")
        actors = {actor.actor_id for actor in self.graph.actors}
        items = {item.id for item in self.graph.resources.items}
        if kind in {"move", "inspect", "wait"}:
            action = ACTION_ADAPTER.validate_json(json.dumps(raw))
            _require(action.actor_id in PC_ACTOR_IDS and not action.hypothetical)
            if isinstance(action, Move):
                locations = {
                    e.id for e in self.graph.world.entities if e.kind is EntityKind.LOCATION
                }
                _require(action.destination_id in locations)
            elif isinstance(action, Inspect):
                targets = {
                    check.target_id
                    for check in self.graph.actions.checks
                    if check.action == "inspect" and check.definition_id == "skill:observation"
                }
                _require(action.target_id in targets)
            else:
                _require(isinstance(action, Wait))
            return
        if kind in {"start_noncombat", "approach_noncombat", "withdraw_noncombat"}:
            noncombat = NoncombatCommand.model_validate_json(json.dumps(raw))
            _require(noncombat.actor_id in PC_ACTOR_IDS and not noncombat.hypothetical)
            expected_selection = {
                "start_noncombat": "quiet-path",
                "approach_noncombat": "move-quietly",
                "withdraw_noncombat": None,
            }
            _require(noncombat.selection_id == expected_selection[noncombat.kind])
            return
        if kind in {"observe_scene", "travel_scene"}:
            scene = SCENE_ADAPTER.validate_json(json.dumps(raw))
            _require(scene.actor_id in PC_ACTOR_IDS and not scene.hypothetical)
            if isinstance(scene, TravelScene):
                _require(
                    scene.exit_id in {exit.id for s in self.graph.scenes.scenes for exit in s.exits}
                )
            else:
                _require(isinstance(scene, ObserveScene))
            return
        if kind in {"evaluate_objectives", "abandon_scenario"}:
            objective = ObjectiveCommand.model_validate_json(json.dumps(raw))
            _require(objective.actor_id in {*PC_ACTOR_IDS, GM_ID} and not objective.hypothetical)
            return
        if kind == "gurps_recovery":
            medical = PlayerRecoveryCommand.model_validate_json(json.dumps(raw))
            _require(medical.actor_id in PC_ACTOR_IDS and not medical.hypothetical)
            fields = medical.choice_id.split(":")
            _require(len(fields) in (4, 5) and fields[0] == "gurps")
            _require(fields[2] == medical.actor_id and fields[3] in PC_ACTOR_IDS)
            _require(fields[1] in {"bandage", "first-aid", "rest", "finish"})
            if fields[1] in {"bandage", "first-aid"}:
                _require(medical.actor_id == "b")
            if fields[1] == "rest":
                _require(fields[2] == fields[3])
            return
        _require(
            kind
            in {
                "start_encounter",
                "take_combat_turn",
                "choose_defense",
                "end_encounter",
                "continue_critical_miss",
                "resume_interrupted_turn",
                "retrieve_equipment",
            }
        )
        combat = COMBAT_ADAPTER.validate_json(json.dumps(raw))
        _require(combat.actor_id in actors | {GM_ID})
        if isinstance(combat, TakeCombatTurn):
            _shape(combat, _TURN_FIELDS)
            _require(combat.maneuver in _MANEUVERS)
            _require(combat.item_id is None or combat.item_id in items)
            _require(combat.target_id is None or combat.target_id in actors)
            _require(combat.hit_location in (None, "torso"))
            _require(combat.mode_id in (None, "swing", "thrust"))
            _require(combat.ready_reach in (None, 1))
        elif isinstance(combat, ChooseDefense):
            _shape(combat, _DEFENSE_FIELDS)
            _require(combat.defense in {"dodge", "parry", "none"})
            _require(combat.item_id is None or combat.item_id in items)
            _require(combat.parry_mode_id in (None, "swing", "thrust"))
            if combat.defense == "parry":
                _require(combat.parry_mode_id in {"swing", "thrust"})
        elif isinstance(combat, StartEncounter):
            _require(combat.actor_id == GM_ID)
            _require(combat.battlefield_id == "roadwatch-clearing")
            _require(combat.scene_id == "road-scene")
            _require(
                len(combat.placements) == 3 and {p.actor_id for p in combat.placements} == actors
            )
            _require(
                len(combat.allegiances) == 3
                and {team.actor_id: team.side_id for team in combat.allegiances}
                == {"a": "watch", "b": "watch", "brigand": "brigand"}
            )
            _require(tuple(pair.side_ids for pair in combat.oppositions) == (("brigand", "watch"),))
            _require(not combat.automatic_completion)
            _require(not combat.ranged_situations and not combat.reinforcements_expected)
        elif isinstance(combat, EndEncounter | ContinueCriticalMiss):
            _require(combat.actor_id == GM_ID)
        elif isinstance(combat, RetrieveEquipment):
            _require(combat.item_id in items)
        else:
            _require(isinstance(combat, ResumeInterruptedTurn))

    def mutation(self, path: str, raw: dict[str, object], principal_id: str) -> None:
        if path == "/setups":
            setup = CreateSetup.model_validate_json(json.dumps(raw))
            _require(principal_id == PLAYER_ID and setup.rules_profile is None)
            _require(setup.graph is not None and setup.graph == self.graph)
            _require(setup.brief == self.graph.brief)
            assert setup.graph is not None
            for actor in setup.graph.actors:
                require_pregen(actor.actor_id, actor.proposal)
            return
        if re.fullmatch(r"/setups/[^/]+", path):
            setup_command = SetupCommand.model_validate_json(json.dumps(raw))
            _require(
                setup_command.operation
                in {"invite", "join", "assign", "ready", "activate", "pause", "resume"}
            )
            _require(setup_command.graph is None and setup_command.brief is None)
            _require(setup_command.principal_id in (None, PLAYER_ID))
            _require(set(setup_command.actor_ids) <= set(PC_ACTOR_IDS))
            _require(len(set(setup_command.actor_ids)) == len(setup_command.actor_ids))
            return
        if re.fullmatch(r"/campaigns/[^/]+/commands", path):
            self.command(raw)
            return
        if re.fullmatch(r"/api/tactical/v2/campaigns/[^/]+/commands", path):
            _require(set(raw) == {"command"} and isinstance(raw.get("command"), dict))
            command = raw["command"]
            assert isinstance(command, dict)
            self.command(command)
            return
        raise ValidationError(REFUSAL)


def admission(scope: RoadwatchScope) -> Middleware:
    """Install after existing authentication middleware, before every route."""

    @web.middleware
    async def enforce(
        request: web.Request, handler: Callable[[web.Request], Awaitable[web.StreamResponse]]
    ) -> web.StreamResponse:
        if request.path == "/api/v1/live":
            # Stateful live messages can interrupt narration; initial release
            # deliberately exposes neither that write surface nor a browser claim.
            raise Fault(403, "forbidden")
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            try:
                _require(request.method == "POST")
                # V1 consumes its stream directly; do not drain/rewrite its body.
                # This deployment deliberately offers V1 reads only.
                _require(not request.path.startswith("/api/v1"))
                scope.mutation(request.path, await _json(request), _identity(request))
            except ValidationError:
                if request.path.startswith("/api/v1"):
                    raise Fault(400, "invalid_request") from None
                raise
        return await handler(request)

    return enforce
