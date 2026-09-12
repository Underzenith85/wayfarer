"""Encoding advanced-care state into durable task markers."""

from __future__ import annotations

from wayfarer.engine.rules.types.hazard import HazardSchedule, HazardSpec
from wayfarer.engine.rules.types.location import LastingInjury
from wayfarer.engine.rules.types.recovery import ProfileId, RecoveryTask
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ValidationError


def surgery_equipment_modifier(tl: int) -> int:
    """Basic-equipment modifier from B424, excluding quality and sterility."""
    if not 1 <= tl <= 12:
        raise ValidationError("Surgery requires TL1 or later")
    if tl == 1:
        return -6
    if tl in (2, 3):
        return -5
    if tl == 4:
        return -4
    if tl == 5:
        return -2
    return tl - 6


_TRAUMA_PREFIX = "variant:trauma:"


_REPAIR_PREFIX = "variant:repair-lasting:"


def _trauma_marker(life_support: bool) -> str:
    return _TRAUMA_PREFIX + ("daily" if life_support else "hourly")


def _repair_marker(injury_id: str, infection_risk: bool, infection_modifier: int) -> str:
    risk = "risk" if infection_risk else "clean"
    return f"{_REPAIR_PREFIX}{risk}:{infection_modifier}:{injury_id}"


def _repair_metadata(marker: str | None) -> tuple[bool, int, str]:
    if marker is None or not marker.startswith(_REPAIR_PREFIX):
        raise ValidationError("Advanced recovery task marker is invalid")
    payload = marker.removeprefix(_REPAIR_PREFIX)
    pieces = payload.split(":", 2)
    if len(pieces) != 3 or pieces[0] not in ("risk", "clean") or not pieces[2]:
        raise ValidationError("Advanced recovery task marker is invalid")
    try:
        modifier = int(pieces[1])
    except ValueError as error:
        raise ValidationError("Advanced recovery task marker is invalid") from error
    if not -30 <= modifier <= 30:
        raise ValidationError("Advanced recovery task marker is invalid")
    return pieces[0] == "risk", modifier, pieces[2]


def _hp(state: ResourceState, actor_id: str, profile_id: ProfileId) -> Pool:
    hp = next((p for p in state.pools if p.id == f"hp:{actor_id}"), None)
    if hp is None or hp.injury is None or hp.injury.profile_id != profile_id:
        raise ValidationError("Advanced recovery requires matching profile HP")
    if profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Advanced recovery variants require the Basic Set profile")
    return hp


def _lasting_injury(state: ResourceState, target: str, injury_id: str | None) -> LastingInjury:
    hp = next(p for p in state.pools if p.id == f"hp:{target}")
    assert hp.injury is not None
    injury = next((w for w in hp.injury.lasting_injuries if w.id == injury_id), None)
    if injury is None:
        raise ValidationError("Surgery requires the recorded crippling injury")
    return injury


def _replace_lasting(hp: Pool, replacement: LastingInjury) -> Pool:
    assert hp.injury is not None
    status = hp.injury.model_copy(
        update={
            "lasting_injuries": tuple(
                replacement if w.id == replacement.id else w for w in hp.injury.lasting_injuries
            )
        }
    )
    return hp.model_copy(update={"injury": status})


def _infection_schedule(task: RecoveryTask, now: int, infection_modifier: int) -> HazardSchedule:
    schedule_id = f"infection:{task.id}"
    spec = HazardSpec(
        id=schedule_id,
        kind="disease",
        scene_id="postoperative-care",
        interval=86400,
        cycles=100000,
        resistance_modifier=infection_modifier,
        damage_dice=0,
        damage_add=1,
        resistible=True,
        reference="Basic Set B444 postoperative infection",
        recovery_successes=1,
    )
    return HazardSchedule(
        id=schedule_id,
        actor_id=task.target_id,
        spec=spec,
        started=now,
        due=now + 86400,
        remaining=spec.cycles,
        ht=task.ht,
        will=task.ht,
        swimming=1,
    )
