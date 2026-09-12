"""Non-authoritative snapshot encoding and verified stream materialisation."""

import json
from copy import deepcopy

from wayfarer import validation
from wayfarer.engine.simulation.campaign.scenario_references import verify
from wayfarer.engine.simulation.events import StatePatched, digest, document, fold
from wayfarer.errors import NotFoundError, ValidationError
from wayfarer.models import Campaign
from wayfarer.persistence.events import StoredEvent

EVENT_PROJECTIONS = ("last_result", "last_combat_result", "scene_events")


def encode(state: Campaign) -> str:
    """Schema 2 separates event projections from the play checkpoint cache.

    Schema 1 (the old bare Campaign JSON) remains readable. Neither format is
    authoritative; the event stream authenticates every candidate checkpoint.
    """
    checkpoint = deepcopy(state)
    projections: dict[str, object] = {}
    if "play_json" in checkpoint:
        play = validation.mapping(validation.decode(checkpoint["play_json"]))
        for key in EVENT_PROJECTIONS:
            if key in play:
                projections[key] = play.pop(key)
        checkpoint["play_json"] = json.dumps(play, separators=(",", ":"), ensure_ascii=False)
    return json.dumps(
        {"snapshot_schema": 2, "campaign": checkpoint, "event_projections": projections}
    )


def decode(raw: object) -> Campaign:
    value = validation.mapping(validation.decode(raw) if isinstance(raw, str | bytes) else raw)
    if "snapshot_schema" not in value:
        return validation.campaign(value)
    if value["snapshot_schema"] != 2:
        raise ValueError("Unknown snapshot cache format")
    state = validation.campaign(value["campaign"])
    if "play_json" in state:
        play = validation.mapping(validation.decode(state["play_json"]))
        play.update(validation.mapping(value["event_projections"]))
        # Re-encode with the canonical model field order for legacy API consumers.
        from wayfarer.engine.simulation.actions import PlayState

        state["play_json"] = PlayState.model_validate_json(json.dumps(play)).model_dump_json()
    return state


def materialize(
    initial: Campaign,
    events: list[StoredEvent],
    snapshots: list[tuple[int, object]],
    *,
    through: int | None = None,
) -> Campaign:
    maximum = through if through is not None else 2**63 - 1
    if initial["revision"] > maximum:
        raise NotFoundError("Revision precedes retained stream genesis")
    events = [event for event in events if event.revision <= maximum]
    expected = {initial["revision"]: digest(document(initial))}
    for row in events:
        if isinstance(row.event, StatePatched) and row.event.scope == "campaign":
            expected[row.revision] = row.event.after_digest
    state, cutoff = initial, None
    for revision, raw in sorted(snapshots, key=lambda row: row[0], reverse=True):
        if revision > maximum or revision not in expected:
            continue
        try:
            candidate = decode(raw)
            if (
                candidate["id"] != initial["id"]
                or candidate["revision"] != revision
                or digest(document(candidate)) != expected[revision]
            ):
                continue
            verify(candidate)
        except ValueError, KeyError, TypeError, ValidationError:
            continue
        state, cutoff = candidate, revision
        break
    result = fold(state, [row.event for row in events if cutoff is None or row.revision > cutoff])
    verify(result)
    return result


def select_checkpoint(
    initial: Campaign, caches: list[tuple[int, object]], digests: dict[int, str], maximum: int
) -> tuple[Campaign, int | None]:
    """Select a cache authenticated by a durable stream checkpoint digest."""
    for revision, raw in sorted(caches, key=lambda row: row[0], reverse=True):
        if revision > maximum or revision not in digests:
            continue
        try:
            state = decode(raw)
            if (
                state["id"] != initial["id"]
                or state["revision"] != revision
                or digest(document(state)) != digests[revision]
            ):
                continue
            verify(state)
        except ValueError, KeyError, TypeError, ValidationError:
            continue
        return state, revision
    return initial, None
