"""Viewpoint projections and opaque, content-scoped optimistic concurrency tokens."""

from __future__ import annotations

import hashlib
import hmac
from dataclasses import dataclass

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.resources import wire_weight
from wayfarer.models import Campaign
from wayfarer.orchestration.play import PlayService

from .common import Fault, Obj, encoded, validate


@dataclass
class View:
    runtime: PlayService
    state: PlayState
    member: CampaignMember
    campaign: Obj
    scenes: dict[str, Obj]
    characters: dict[str, Obj]
    inventories: dict[str, Obj]
    actor_scenes: dict[str, str]
    policy: str


class Projector:
    def __init__(
        self, play: PlayService, secret: str, *, tick_ms: int = 1000, weight_grams: int = 1
    ) -> None:
        self.play, self.secret = play, secret.encode()
        self.tick_ms, self.weight_grams = tick_ms, weight_grams
        self.text_enabled = False

    def token(self, principal: str, cid: str, kind: str, value: object) -> str:
        return hmac.new(
            self.secret, encoded([principal, cid, kind, value]).encode(), hashlib.sha256
        ).hexdigest()

    def versioned(self, principal: str, cid: str, kind: str, value: Obj) -> Obj:
        return {**value, "version": self.token(principal, cid, kind, value)}

    def make(
        self, raw: Campaign, principal: str, stamp: str, *, viewpoint: str | None = None
    ) -> View:
        if "play_json" not in raw:
            raise Fault(404, "not_found")
        play = self.play.for_campaign(raw)
        state = play._load(raw)
        member = next((m for m in state.members if m.principal_id == principal), None)
        if member is None:
            raise Fault(404, "not_found")
        actors = {a.actor_id: a for a in state.actors}
        permitted = (
            set(actors)
            if member.role == "gm"
            else set(member.actor_ids)
            if member.role == "player"
            else set()
        )
        if viewpoint is not None:
            if viewpoint not in permitted:
                raise Fault(404, "not_found")
            permitted = {viewpoint}
        entities = {e.id: e for e in state.world.entities}
        # Only these targets have an authored check; inspecting anything else is
        # answered with unsupported_action, so it is never advertised.
        inspectable = {c.target_id for c in play.engine.rules.checks if c.action == "inspect"}
        offered: set[str] = set()
        scenes: dict[str, Obj] = {}
        characters: dict[str, Obj] = {}
        inventories: dict[str, Obj] = {}
        actor_scenes: dict[str, str] = {}
        policy: list[object] = [member.model_dump(mode="json")]
        ticks: list[int] = []
        for aid in sorted(permitted):
            actor = actors[aid]
            entity = entities[aid]
            scene = next(
                (s.scene_id for s in state.actor_scenes if s.actor_id == aid), entity.location_id
            )
            if scene is None:
                raise Fault(503, "service_unavailable")
            actor_scenes[aid] = scene
            group = next((g for g in state.party.groups if aid in g.actor_ids), None)
            tick = group.ready_through if group else actor.available_at
            ticks.append(tick)
            time = {"ticks": tick, "tick_duration_ms": self.tick_ms}
            known = {f for a, f in state.world.knowledge if a == aid}
            visible = {aid, *actor.aware_of} | {
                f.subject_id for f in state.world.facts if f.id in known
            }
            visible = {
                x
                for x in visible
                if x in entities
                and (
                    x == aid
                    or entities[x].location_id == entity.location_id
                    or x == entity.location_id
                )
            }
            observations: list[Obj] = [
                {"id": x, "label": entities[x].name, "description": entities[x].name}
                for x in sorted(visible - {aid})
            ]
            observations += [
                {"id": f.id, "label": f.predicate, "description": f.value}
                for f in state.world.facts
                if f.id in known and f.subject_id in visible
            ]
            offered |= {str(o["id"]) for o in observations} & inspectable
            if play.engine.rules.scenes:
                configured = next(s for s in play.engine.rules.scenes.scenes if s.id == scene)
                by_id = {s.id: s for s in play.engine.rules.scenes.scenes}
                destinations: set[str] = set()
                for exit in configured.exits:
                    if (
                        set(exit.required_fact_ids) <= known
                        and exit.destination_id not in destinations
                    ):
                        destinations.add(exit.destination_id)
                        observations.append(
                            {
                                "id": exit.destination_id,
                                "label": by_id[exit.destination_id].title,
                                "description": "Known scene exit",
                            }
                        )
            title = entities[entity.location_id].name if entity.location_id in entities else scene
            scenes.setdefault(
                scene,
                self.versioned(
                    principal,
                    state.campaign_id,
                    f"scene:{scene}:{aid}",
                    {
                        "id": scene,
                        "title": title,
                        "description": title,
                        "game_time": time,
                        "observations": observations,
                        "visible_actor_ids": sorted(x for x in visible if x in actors),
                    },
                ),
            )
            build = play.engine.reviewer.review(actor.proposal).compilation.build
            if build is None:
                raise Fault(503, "service_unavailable")
            stats = [
                {"id": v.target, "label": v.target, "value": float(v.value)}
                for v in build.sheet.values
            ]
            pools = {p.id: p for p in state.resources.pools}
            owned = [i for i in state.resources.items if i.owner_id == aid]
            captivity = next(
                (
                    c
                    for c in state.recovery.captivity
                    if c.actor_id == aid and c.released_at is None
                ),
                None,
            )
            confiscated = set(captivity.confiscated_item_ids) if captivity else set()
            items: list[Obj] = []
            for i in state.resources.items:
                if i.owner_id != aid and i.id not in confiscated:
                    continue
                spec = play.engine.resources.specs[i.definition_id]
                held = i.id in confiscated
                items.append(
                    {
                        "id": i.id,
                        "name": i.definition_id,
                        "description": i.definition_id,
                        "quantity": i.quantity,
                        "unit_weight_grams": wire_weight(spec.unit_weight * self.weight_grams),
                        "location": "confiscated"
                        if held
                        else "equipped"
                        if i.equipped
                        else "stored"
                        if i.container_id
                        else "carried",
                        "container_id": None if held else i.container_id,
                        "allowed_actions": ["inspect"]
                        + (
                            ["use_item"]
                            if not held
                            and not i.equipped
                            and i.definition_id in play.engine.rules.consumables
                            else []
                        ),
                    }
                )
            inventories[aid] = self.versioned(
                principal,
                state.campaign_id,
                f"inventory:{aid}",
                {
                    "actor_id": aid,
                    "items": sorted(items, key=lambda i: str(i["id"])),
                    "total_weight_grams": wire_weight(
                        play.engine.resources.carried_weight(state.resources, aid)
                        * self.weight_grams
                    ),
                    "encumbrance": "Not supplied by the current engine projection",
                },
            )
            characters[aid] = self.versioned(
                principal,
                state.campaign_id,
                f"character:{aid}",
                {
                    "id": aid,
                    "campaign_id": state.campaign_id,
                    "name": entity.name,
                    "hp": {
                        "current": pools[f"hp:{aid}"].current,
                        "maximum": pools[f"hp:{aid}"].maximum,
                    },
                    "fp": {
                        "current": pools[f"fp:{aid}"].current,
                        "maximum": pools[f"fp:{aid}"].maximum,
                    },
                    "attributes": [v for v in stats if str(v["id"]).startswith("attribute:")],
                    "skills": [v for v in stats if str(v["id"]).startswith("skill:")],
                    "defenses": [v for v in stats if str(v["id"]).startswith("defense:")],
                    "movement": [v for v in stats if str(v["id"]).startswith("movement:")],
                    "conditions": [
                        {"id": c, "label": c, "description": c} for c in actor.conditions
                    ],
                    "equipped_item_ids": sorted(i.id for i in owned if i.equipped),
                },
            )
            policy.append(
                [aid, scene, sorted(known), group.generation if group else None, sorted(visible)]
            )
        if member.role == "gm" and viewpoint is None:
            # HTTP inspection is explicitly privileged; WebSocket GM subscriptions
            # still request one actor's restricted viewpoint above.
            locations = {e.id: e for e in state.world.entities if e.kind.value == "location"}
            scene_ids = (
                {x.location_id: x.id for x in play.engine.rules.scenes.scenes}
                if play.engine.rules.scenes
                else {}
            )
            for lid, location in locations.items():
                sid = scene_ids.get(lid, lid)
                gm_observations: list[Obj] = [
                    {"id": f.id, "label": f.predicate, "description": f.value}
                    for f in state.world.facts
                    if f.subject_id == lid or entities[f.subject_id].location_id == lid
                ]
                offered |= {str(o["id"]) for o in gm_observations} & inspectable
                scenes[sid] = self.versioned(
                    principal,
                    state.campaign_id,
                    f"gm-scene:{sid}",
                    {
                        "id": sid,
                        "title": location.name,
                        "description": location.name,
                        "game_time": {
                            "ticks": state.resources.game_time,
                            "tick_duration_ms": self.tick_ms,
                        },
                        "observations": gm_observations,
                        "visible_actor_ids": sorted(
                            a for a in actors if entities[a].location_id == lid
                        ),
                    },
                )
        # An advertised capability must conform: the UI renders active controls
        # for exactly these, so a kind the engine would answer with
        # unsupported_action is never claimed. Inspection is offered only where
        # this viewpoint can already see a target carrying an authored check, and
        # the target-scoped entries below name exactly those.
        capabilities = [
            *(["actions.inspect"] if offered else []),
            "actions.move",
            *(["actions.use_item"] if play.engine.rules.consumables else []),
            "actions.wait",
            *(["actions.text"] if self.text_enabled else []),
        ]
        scoped = [f"actions.inspect:{target}" for target in sorted(offered)]
        if len(capabilities) + len(scoped) <= 100 and all(len(x) <= 100 for x in scoped):
            capabilities += scoped
        membership = self.versioned(
            principal,
            state.campaign_id,
            "membership",
            {
                "principal_id": principal,
                "campaign_id": state.campaign_id,
                "role": member.role,
                "actor_ids": list(member.actor_ids),
            },
        )
        campaign = self.versioned(
            principal,
            state.campaign_id,
            "campaign",
            {
                "id": state.campaign_id,
                "name": raw["scenario"].get("title", "Campaign"),
                "premise": raw["scenario"].get("premise", "Explore the current scene.")[:2000],
                "status": state.lifecycle
                if state.lifecycle != "active"
                else "completed"
                if state.objectives.outcome != "ongoing"
                else "active",
                "game_time": {"ticks": min(ticks, default=0), "tick_duration_ms": self.tick_ms},
                "membership": membership,
                "capabilities": capabilities,
                "updated_at": stamp,
            },
        )
        for name, values in [
            ("Scene", scenes.values()),
            ("Character", characters.values()),
            ("Inventory", inventories.values()),
        ]:
            for value in values:
                validate(name, value)
        validate("Campaign", campaign)
        return View(
            play,
            state,
            member,
            campaign,
            scenes,
            characters,
            inventories,
            actor_scenes,
            self.token(principal, state.campaign_id, "policy", policy),
        )
