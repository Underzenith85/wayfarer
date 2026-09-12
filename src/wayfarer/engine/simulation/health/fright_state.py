"""Recorded fright state and read-only projections."""

import hashlib
import json

from wayfarer.engine.rules.checks import CheckTrace, Modifier
from wayfarer.engine.rules.fright import FrightEffect
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.models import Record

PREFIX = "fright-runtime:"


class TimedFright(Record):
    id: str
    actor_id: str
    trigger_id: str
    effect: FrightEffect
    started: int
    due: int | None
    active: bool
    recovery_target: int
    aftermath_until: int | None = None
    recovery_due: int | None = None
    care: bool = False
    neglect_days: int = 0
    next_care_due: int | None = None
    panic_responses: tuple[str, ...] = ()
    recovery_checks: tuple[CheckTrace, ...] = ()
    proposed_draft: str | None = None
    proposal_id: str | None = None
    proposal_build_revision: str | None = None
    proposed_by: str | None = None
    proposed_related_trait: str | None = None
    adjudicated_build_revision: str | None = None


def effects(state: ResourceState) -> tuple[TimedFright, ...]:
    """Fold the fright event ledger into its latest episode states."""
    latest: dict[str, TimedFright] = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            item = TimedFright.model_validate_json(event.kind)
            latest[item.id] = item
    return tuple(latest.values())


def public_id(item: TimedFright) -> str:
    """Return an opaque reference that cannot disclose an authored NPC occurrence ID."""
    return hashlib.sha256(item.id.encode()).hexdigest()


def projection(
    state: ResourceState, actor_ids: tuple[str, ...], *, director: bool = False
) -> tuple[dict[str, object], ...]:
    """Expose consequences and required decisions, never their hidden cause or rolls."""
    result: list[dict[str, object]] = []
    for item in effects(state):
        if not director and item.actor_id not in actor_ids:
            continue
        effect = item.effect
        choices: list[dict[str, object]] = []
        if effect.trait_choice != "none" and item.adjudicated_build_revision is None:
            choices.append({"kind": effect.trait_choice, "points": effect.trait_points})
        for attribute, loss in (("ht", effect.permanent_ht_loss), ("iq", effect.permanent_iq_loss)):
            if loss and item.adjudicated_build_revision is None:
                choices.append(
                    {"kind": "permanent-attribute-loss", "attribute": attribute, "loss": loss}
                )
        aftermath = item.aftermath_until is not None and state.game_time < item.aftermath_until
        if not (item.active or choices or aftermath):
            continue
        value: dict[str, object] = {
            "id": public_id(item),
            "actor_id": item.actor_id,
            "condition": effect.condition if item.active else "none",
            "active": item.active,
            "choices": tuple(choices),
            "build_approval_required": bool(choices),
            "aftermath_until": item.aftermath_until if aftermath else None,
            "aftermath_penalty": effect.aftermath_penalty if aftermath else 0,
            "panic_response_required": item.active and effect.condition == "panic",
            "care_required": item.active and effect.neglect_progression,
            "proposal_id": item.proposal_id,
            "proposed_draft": json.loads(item.proposed_draft) if item.proposed_draft else None,
        }
        if director:
            value.update(
                {
                    "care": item.care,
                    "panic_severity": effect.panic_severity,
                    "panic_responses": item.panic_responses,
                    "decision_kinds": ("care",)
                    if item.active and effect.neglect_progression
                    else ("panic-response",)
                    if item.active and effect.table_total == 33
                    else (),
                }
            )
        result.append(value)
    return tuple(result)


def blocked(state: ResourceState, actor_id: str, *, kind: str | None = None) -> bool:
    return any(
        item.actor_id == actor_id
        and item.active
        and item.effect.condition != "retching"
        and not (kind == "move" and item.effect.condition == "panic")
        for item in effects(state)
    )


def stunned(state: ResourceState, actor_id: str) -> bool:
    return any(
        item.actor_id == actor_id and item.active and item.effect.condition == "stunned"
        for item in effects(state)
    )


def can_defend(state: ResourceState, actor_id: str) -> bool:
    return all(
        item.effect.condition in ("stunned", "retching", "panic")
        for item in effects(state)
        if item.actor_id == actor_id and item.active
    )


def aftermath_penalty(state: ResourceState, actor_id: str) -> int:
    """Return the B361 penalty without changing purchased statistics."""
    return sum(modifier.value for modifier in aftermath_modifiers(state, actor_id))


def aftermath_modifiers(state: ResourceState, actor_id: str) -> tuple[Modifier, ...]:
    return tuple(
        Modifier(
            item.effect.aftermath_penalty,
            "Fright aftermath",
            item.id,
            "Basic Set Campaigns 4e B361",
        )
        for item in effects(state)
        if item.actor_id == actor_id
        and not item.active
        and item.aftermath_until is not None
        and state.game_time < item.aftermath_until
    )


def requires_adjudication(
    state: ResourceState, actor_id: str, *, handles_aftermath: bool = True
) -> bool:
    return any(
        item.actor_id == actor_id
        and (
            (
                item.adjudicated_build_revision is None
                and (item.effect.permanent_ht_loss or item.effect.permanent_iq_loss)
            )
            or (
                not handles_aftermath
                and item.aftermath_until is not None
                and state.game_time < item.aftermath_until
            )
        )
        for item in effects(state)
    )
