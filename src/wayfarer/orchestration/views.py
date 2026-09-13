"""How a campaign looks to the person reading it (#637).

One builder per audience. Every caller — the HTTP read, the live stream, the
model's context and the v1 views — resolves through `projections`, so the rule
about what a player may not see is written once, here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, replace
from typing import TYPE_CHECKING

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.engine import hex_template
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.health.fright_state import projection as fright_projection
from wayfarer.engine.simulation.resources import wire_weight
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.player_medical import choices as medical_choices
from wayfarer.orchestration.recovery import RecoveryCommand, RecoveryService
from wayfarer.orchestration.tactical_view import legacy_encounter

if TYPE_CHECKING:
    from wayfarer.orchestration.runtime import CampaignRuntime


def campaign_view(
    state: PlayState, member: CampaignMember, rules: CombatRules | None = None
) -> dict[str, object]:
    """What this member may read of the campaign: the director's world, or their own."""

    if member.role == "gm":
        return {
            "campaign_id": state.campaign_id,
            "lifecycle": state.lifecycle,
            "revision": state.revision,
            "game_time": state.resources.game_time,
            "role": member.role,
            "actors": tuple(actor.actor_id for actor in state.actors),
            "world": asdict(state.world),
            "fright": fright_projection(state.resources, (), director=True),
        }
    perspectives: dict[str, object] = {}
    for actor_id in member.actor_ids:
        own = next(e for e in state.world.entities if e.id == actor_id)
        perspective = state.world.perspective(actor_id)
        known = {f.id for f in perspective.facts}
        perspective = replace(
            perspective,
            commitments=tuple(
                c
                for c in perspective.commitments
                if c.reveal_fact_id in known
                or (c.reveal_fact_id is None and actor_id in (c.debtor_id, c.creditor_id))
            ),
        )
        perspective = replace(
            perspective,
            entities=tuple(
                e
                if e.id == actor_id or e.location_id == own.location_id
                else replace(e, location_id=None, owner_id=None)
                for e in perspective.entities
            ),
        )
        perspectives[actor_id] = asdict(perspective)
    groups = tuple(g for g in state.party.groups if set(g.actor_ids) & set(member.actor_ids))
    visible_objectives = state.objectives.model_dump(
        mode="json", exclude={"evidence", "settled_reward_ids"}
    )
    visible_objectives["progress"] = tuple(
        {"objective_id": e.objective_id, "satisfied": e.satisfied}
        for e in state.objectives.evidence
        if not e.visible_to or set(e.visible_to) & set(member.actor_ids)
    )
    return {
        "campaign_id": state.campaign_id,
        "lifecycle": state.lifecycle,
        "principal_id": member.principal_id,
        "shared_time": len(state.party.groups) > 1,
        "rulings": tuple(
            r.model_dump(
                mode="json",
                include={"id", "actor_id", "status", "alternatives", "selected_id", "reason"},
            )
            for r in state.rulings
            if r.actor_id in member.actor_ids
        ),
        "director": tuple(
            t.model_dump(
                mode="json",
                exclude={
                    "command_json",
                    "request_json",
                    "session_id",
                    "principal_id",
                    "outcome_json",
                },
            )
            for t in state.director
            if t.actor_id in member.actor_ids
        ),
        "journal": tuple(
            e.model_dump(mode="json") for e in state.journal if e.actor_id in member.actor_ids
        ),
        "scene_cursors": tuple(
            e.model_dump(mode="json") for e in state.actor_scenes if e.actor_id in member.actor_ids
        ),
        "encounters": tuple(
            legacy_encounter(
                state,
                e,
                member,
                board=hex_template(e, rules) if e.spatial_kind == "hex" else None,
            )
            for e in state.encounters
            if set(e.turn_order) & set(member.actor_ids)
        ),
        "resolution": state.last_result.model_dump(mode="json")
        if state.last_result
        and any(
            t.actor_id in member.actor_ids
            and t.command_json
            and json.loads(t.command_json).get("id") == state.last_result.command_id
            for t in state.director
        )
        else None,
        "revision": state.revision,
        "game_time": state.resources.game_time,
        "role": member.role,
        "actors": member.actor_ids,
        "fright": fright_projection(state.resources, member.actor_ids),
        "inventory": tuple(
            i.model_dump(mode="json")
            for i in state.resources.items
            if i.owner_id in member.actor_ids
        ),
        "status": tuple(
            a.model_dump(mode="json", include={"actor_id", "conditions", "available_at"})
            for a in state.actors
            if a.actor_id in member.actor_ids
        ),
        "pools": tuple(
            p.model_dump(mode="json")
            for p in state.resources.pools
            if any(p.id == f"{kind}:{a}" for a in member.actor_ids for kind in ("hp", "fp"))
        ),
        "perspectives": perspectives,
        "subgroups": tuple(g.model_dump(mode="json") for g in groups),
        "pending_activities": tuple(
            q.model_dump(mode="json", exclude={"command_json"})
            for q in state.party.queue
            if q.actor_id in member.actor_ids
        ),
        "activity_receipts": tuple(
            r.model_dump(mode="json")
            for r in state.party.receipts
            if r.actor_id in member.actor_ids
        ),
        "noncombat": tuple(
            e.model_dump(mode="json") for e in state.noncombat if e.actor_id in member.actor_ids
        ),
        "objectives": visible_objectives,
        "captivity": tuple(
            c.model_dump(mode="json")
            for c in state.recovery.captivity
            if c.actor_id in member.actor_ids
        ),
        "recovery_decisions": tuple(
            d.model_dump(mode="json")
            for d in state.recovery.decisions
            if d.actor_id in member.actor_ids
        ),
        "dead_actor_ids": tuple(a for a in state.recovery.dead_actor_ids if a in member.actor_ids),
    }


def player_detail(
    runtime: CampaignRuntime,
    state: PlayState,
    member: CampaignMember,
    projection: dict[str, object],
) -> dict[str, object]:
    """Everything a player's own view adds from the bound engine and its rules."""
    compiler = runtime.play.engine.reviewer.compiler
    projection["characters"] = tuple(
        {
            "actor_id": a.actor_id,
            "name": a.proposal.draft.name,
            "values": tuple(
                {"target": v.target, "value": str(v.value)} for v in build.sheet.values
            ),
            "spent": build.spent,
        }
        for a in state.actors
        if a.actor_id in member.actor_ids
        if (build := compiler.compile(a.proposal.draft).build) is not None
    )
    projection["equipment"] = tuple(
        {
            "id": i.id,
            "name": compiler.definitions[i.definition_id].name,
            "unit_weight": wire_weight(
                runtime.play.engine.resources.specs[i.definition_id].unit_weight
            ),
        }
        for i in state.resources.items
        if i.owner_id in member.actor_ids
    )
    projection["scenes"] = tuple(
        {
            "actor_id": cursor.actor_id,
            "id": scene.id,
            "title": scene.title,
            "exits": tuple(
                {"id": e.id, "destination_id": e.destination_id}
                for e in scene.exits
                if set(e.required_fact_ids)
                <= {f.id for f in state.world.perspective(cursor.actor_id).facts}
            ),
        }
        for cursor in state.actor_scenes
        if cursor.actor_id in member.actor_ids
        for scene in (
            runtime.play.engine.rules.scenes.scenes if runtime.play.engine.rules.scenes else ()
        )
        if scene.id == cursor.scene_id
    )

    choices: list[dict[str, object]] = []
    recovery = RecoveryService(runtime.play)
    for option in (
        runtime.play.engine.rules.recovery.options if runtime.play.engine.rules.recovery else ()
    ):
        for actor_id in member.actor_ids:
            visible = {e.id for e in state.world.perspective(actor_id).entities}
            for target_id in option.target_actor_ids:
                if target_id not in visible or not option.supported:
                    continue
                candidate = RecoveryCommand(
                    id="preview",
                    actor_id=actor_id,
                    expected_revision=state.revision,
                    kind="choose_recovery",
                    rule_id=option.id,
                    target_actor_id=target_id,
                )
                try:
                    recovery.assess(state, candidate)
                except ValidationError, ConflictError:
                    continue
                choices.append(
                    {
                        "id": option.id,
                        "kind": option.kind,
                        "actor_id": actor_id,
                        "target_actor_id": target_id,
                    }
                )
    projection["recovery_choices"] = choices

    medical, medical_tasks, _private = medical_choices(
        runtime.play, state, member.actor_ids, runtime.medical_environment
    )
    projection["gurps_recovery_choices"] = medical
    projection["gurps_recovery_tasks"] = medical_tasks

    scene_choices: list[dict[str, object]] = []
    entities = {e.id: e for e in state.world.entities}
    for actor in state.actors:
        if actor.actor_id not in member.actor_ids:
            continue
        for check in runtime.play.engine.rules.checks:
            target = entities[check.target_id]
            if (
                check.action == "inspect"
                and check.target_id in actor.aware_of
                and target.location_id == entities[actor.actor_id].location_id
            ):
                scene_choices.append(
                    {
                        "id": actor.actor_id + ":" + check.id,
                        "actor_id": actor.actor_id,
                        "label": "Inspect: " + target.name,
                        "command": {"kind": "inspect", "target_id": target.id},
                    }
                )
        if runtime.play.engine.rules.noncombat:
            cursor = next(c for c in state.actor_scenes if c.actor_id == actor.actor_id)
            for rule in runtime.play.engine.rules.noncombat.encounters:
                encounter_id = actor.actor_id + ":" + rule.id
                if rule.scene_id == cursor.scene_id and not any(
                    e.id == encounter_id for e in state.noncombat
                ):
                    scene_choices.append(
                        {
                            "id": encounter_id,
                            "actor_id": actor.actor_id,
                            "label": "Begin: " + rule.id,
                            "command": {
                                "kind": "start_noncombat",
                                "selection_id": rule.id,
                                "encounter_id": encounter_id,
                            },
                        }
                    )
    projection["scene_choices"] = scene_choices
    return projection
