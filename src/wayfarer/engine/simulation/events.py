"""Pure event production and folding for a complete command checkpoint.

Mechanical facts and private state changes share an ordered stream. State changes
are explicit path operations, not replacement campaign snapshots. Only the latter
are folded; facts are independently consumable and declare their own audience.

The campaign envelope reaches this module as a JSON mapping. Its typed shape is an
application contract that persistence and orchestration own; the engine only
digests it and reads the play checkpoint it carries.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from copy import deepcopy
from typing import Annotated, Literal

from pydantic import Field, JsonValue, TypeAdapter

from wayfarer import validation
from wayfarer.engine.simulation.ability_types import AbilityEvent
from wayfarer.engine.simulation.actions import ActionResult, PlayState
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.campaign.scenes import SceneEvent
from wayfarer.engine.simulation.combat.encounter import CombatResult
from wayfarer.engine.simulation.health.fright_state import TimedFright
from wayfarer.engine.simulation.health.hazards import HazardResult
from wayfarer.engine.simulation.health.injury import InjuryResult
from wayfarer.engine.simulation.magic.spell_state import SpellEvent
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ValidationError
from wayfarer.models import Record


class CampaignAudience(Record):
    kind: Literal["campaign"] = "campaign"


class ActorAudience(Record):
    kind: Literal["actors"] = "actors"
    actor_ids: tuple[str, ...] = Field(min_length=1)


class GMAudience(Record):
    kind: Literal["gm"] = "gm"


EventAudience = Annotated[
    CampaignAudience | ActorAudience | GMAudience, Field(discriminator="kind")
]


class PathChange(Record):
    operation: Literal["set", "remove"]
    path: tuple[str | int, ...] = Field(min_length=1)
    value: JsonValue = None


class StatePatched(Record):
    kind: Literal["state.patched"] = "state.patched"
    audience: GMAudience = GMAudience()
    scope: Literal["campaign", "play"] = "campaign"
    before_digest: str
    after_digest: str
    changes: tuple[PathChange, ...]


class CommandApplied(Record):
    kind: Literal["command.applied"] = "command.applied"
    audience: EventAudience
    actor_id: str
    action: str


class ProjectionRefresh(Record):
    """A payload-free hint; delivery still requires a changed authorized projection."""

    kind: Literal["projection.refresh"] = "projection.refresh"
    audience: CampaignAudience = CampaignAudience()


class ActionResolved(Record):
    kind: Literal["action.resolved"] = "action.resolved"
    audience: EventAudience
    result: ActionResult


class ResourceChanged(Record):
    kind: Literal["resource.changed"] = "resource.changed"
    audience: EventAudience
    fact: ResourceEvent


class SceneChanged(Record):
    kind: Literal["scene.changed"] = "scene.changed"
    audience: EventAudience
    fact: SceneEvent


class SpellResolved(Record):
    kind: Literal["spell.resolved"] = "spell.resolved"
    audience: GMAudience = GMAudience()
    fact: SpellEvent


class AbilityResolved(Record):
    kind: Literal["ability.resolved"] = "ability.resolved"
    audience: GMAudience = GMAudience()
    fact: AbilityEvent


class CombatResolved(Record):
    kind: Literal["combat.resolved"] = "combat.resolved"
    audience: GMAudience = GMAudience()
    result: CombatResult


class FrightResolved(Record):
    kind: Literal["fright.resolved"] = "fright.resolved"
    audience: GMAudience = GMAudience()
    fact: TimedFright


class HazardResolved(Record):
    kind: Literal["hazard.resolved"] = "hazard.resolved"
    audience: GMAudience = GMAudience()
    result: HazardResult


class InjuryResolved(Record):
    kind: Literal["injury.resolved"] = "injury.resolved"
    audience: GMAudience = GMAudience()
    result: InjuryResult


EngineEvent = Annotated[
    StatePatched
    | CommandApplied
    | ProjectionRefresh
    | ActionResolved
    | ResourceChanged
    | SceneChanged
    | SpellResolved
    | AbilityResolved
    | CombatResolved
    | FrightResolved
    | HazardResolved
    | InjuryResolved,
    Field(discriminator="kind"),
]
EVENT_ADAPTER: TypeAdapter[EngineEvent] = TypeAdapter(EngineEvent)
JSON_ADAPTER: TypeAdapter[JsonValue] = TypeAdapter(JsonValue)


def visible(event: EngineEvent, member: CampaignMember) -> bool:
    audience = event.audience
    return (
        member.role == "gm"
        or isinstance(audience, CampaignAudience)
        or (
            isinstance(audience, ActorAudience)
            and bool(set(audience.actor_ids) & set(member.actor_ids))
        )
    )


def digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
    ).hexdigest()


def document(campaign: Mapping[str, object]) -> dict[str, JsonValue]:
    value = JSON_ADAPTER.validate_json(json.dumps(campaign))
    assert isinstance(value, dict)
    # These are encoded records in the legacy campaign envelope, not opaque text.
    for key in ("play_json", "resources_json"):
        if isinstance(value.get(key), str):
            value[key] = JSON_ADAPTER.validate_json(str(value[key]))
    return value


def changes(
    before: JsonValue, after: JsonValue, path: tuple[str | int, ...] = ()
) -> list[PathChange]:
    if before == after:
        return []
    result: list[PathChange] = []
    if isinstance(before, dict) and isinstance(after, dict):
        for key in sorted(before.keys() - after.keys()):
            result.append(PathChange(operation="remove", path=(*path, key)))
        for key in sorted(after):
            if key in before:
                result.extend(changes(before[key], after[key], (*path, key)))
            else:
                result.append(PathChange(operation="set", path=(*path, key), value=after[key]))
    elif isinstance(before, list) and isinstance(after, list):
        for index in range(min(len(before), len(after))):
            result.extend(changes(before[index], after[index], (*path, index)))
        for index in range(len(before) - 1, len(after) - 1, -1):
            result.append(PathChange(operation="remove", path=(*path, index)))
        for index in range(len(before), len(after)):
            result.append(PathChange(operation="set", path=(*path, index), value=after[index]))
    else:
        result.append(PathChange(operation="set", path=path, value=after))
    return result


def fold_document(
    value: dict[str, JsonValue], events: list[EngineEvent], *, scope: Literal["campaign", "play"]
) -> dict[str, JsonValue]:
    value = deepcopy(value)
    for event in events:
        if not isinstance(event, StatePatched) or event.scope != scope:
            continue
        if digest(value) != event.before_digest:
            raise ValidationError("Event fold base digest mismatch")
        for change in event.changes:
            target: JsonValue = value
            for part in change.path[:-1]:
                if isinstance(target, dict) and isinstance(part, str):
                    target = target[part]
                elif isinstance(target, list) and isinstance(part, int):
                    target = target[part]
                else:
                    raise ValidationError("Invalid event path")
            key = change.path[-1]
            if isinstance(target, dict) and isinstance(key, str):
                if change.operation == "remove":
                    del target[key]
                else:
                    target[key] = deepcopy(change.value)
            elif isinstance(target, list) and isinstance(key, int) and 0 <= key <= len(target):
                if change.operation == "remove":
                    del target[key]
                elif key == len(target):
                    target.append(deepcopy(change.value))
                else:
                    target[key] = deepcopy(change.value)
            else:
                raise ValidationError("Invalid event path")
        if digest(value) != event.after_digest:
            raise ValidationError("Event fold result digest mismatch")
    return value


def fold_play(state: PlayState, events: list[EngineEvent]) -> PlayState:
    value = JSON_ADAPTER.validate_json(state.model_dump_json())
    assert isinstance(value, dict)
    return PlayState.model_validate_json(json.dumps(fold_document(value, events, scope="play")))


def play_events(before: PlayState, after: PlayState, actor_id: str) -> list[EngineEvent]:
    first = JSON_ADAPTER.validate_json(before.model_dump_json())
    last = JSON_ADAPTER.validate_json(after.model_dump_json())
    return [
        StatePatched(
            scope="play",
            before_digest=digest(first),
            after_digest=digest(last),
            changes=tuple(changes(first, last)),
        ),
        *play_facts(before, after, actor_id),
    ]


def play_facts(before: PlayState, after: PlayState, actor_id: str) -> list[EngineEvent]:
    """Choose audiences where the composed reducer still has both world views.

    Full combat, spell and ability traces can carry hidden target facts and are GM
    only. Individual scene discoveries are private to their observer. Encoded
    resource procedure records are GM only until they have a scoped typed view.
    """
    result: list[EngineEvent] = []
    if after.last_result != before.last_result and after.last_result is not None:
        result.append(
            ActionResolved(audience=ActorAudience(actor_ids=(actor_id,)), result=after.last_result)
        )
    if (
        after.last_combat_result != before.last_combat_result
        and after.last_combat_result is not None
    ):
        result.append(CombatResolved(result=after.last_combat_result))
    old_scenes = {e.model_dump_json() for e in before.scene_events}
    result.extend(
        SceneChanged(audience=ActorAudience(actor_ids=(e.actor_id,)), fact=e)
        for e in after.scene_events
        if e.model_dump_json() not in old_scenes
    )
    old_resources = {e.model_dump_json() for e in before.resources.events}
    for event in after.resources.events:
        if event.model_dump_json() in old_resources:
            continue
        if event.id.startswith("fright-runtime:"):
            result.append(FrightResolved(fact=TimedFright.model_validate_json(event.kind)))
        elif event.id.startswith("hazard:"):
            result.append(HazardResolved(result=HazardResult.model_validate_json(event.kind)))
        elif event.id.startswith("injury:"):
            result.append(InjuryResolved(result=InjuryResult.model_validate_json(event.kind)))
        elif event.id.startswith("spell:"):
            result.append(SpellResolved(fact=SpellEvent.model_validate_json(event.kind)))
        elif event.id.startswith("ability:"):
            result.append(AbilityResolved(fact=AbilityEvent.model_validate_json(event.kind)))
        else:
            result.append(ResourceChanged(audience=GMAudience(), fact=event))
    return result


def command_events(
    before: Mapping[str, object], after: Mapping[str, object], action: str, actor_id: str
) -> list[EngineEvent]:
    old, new = document(before), document(after)
    result: list[EngineEvent] = [
        StatePatched(
            before_digest=digest(old), after_digest=digest(new), changes=tuple(changes(old, new))
        )
    ]
    audience: EventAudience = GMAudience()
    legacy_maps = False
    if "play_json" in before:
        legacy = validation.mapping(validation.decode(validation.string(before["play_json"])))
        legacy_maps = any(
            isinstance(item, dict) and item.get("hex_battlefield") is not None
            for item in validation.sequence(legacy.get("encounters", []))
        )
    if "play_json" in before and "play_json" in after and not legacy_maps:
        first, last = (
            PlayState.model_validate_json(validation.string(before["play_json"])),
            PlayState.model_validate_json(validation.string(after["play_json"])),
        )
        if actor_id in {a.actor_id for a in last.actors}:
            audience = ActorAudience(actor_ids=(actor_id,))
        result.extend(play_facts(first, last, actor_id))
    result.append(CommandApplied(audience=audience, actor_id=actor_id, action=action))
    result.append(ProjectionRefresh())
    return result


def action_result(events: list[EngineEvent]) -> ActionResult:
    for event in reversed(events):
        if isinstance(event, ActionResolved):
            return event.result
    raise ValidationError("Action resolution omitted its result event")
