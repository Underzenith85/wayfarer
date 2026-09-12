"""Player-facing GURPS recovery choices derived only from authoritative state.

The wire contract intentionally exposes opaque choice IDs instead of medical
parameters. Clients never submit skill levels, TL, equipment quality, healing
amounts, or recovery deadlines; the server reconstructs those values from the
pinned profile, approved builds, trusted care environment, and persisted tasks.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Literal, cast

from pydantic import Field

from wayfarer.engine.rules.types.recovery import ProfileId
from wayfarer.engine.simulation.actions import ActionCommand, PlayState
from wayfarer.engine.simulation.health.injury import InjuryResult
from wayfarer.engine.simulation.health.medical import (
    BeginRecovery,
    CareContext,
    FinishRecovery,
    apply_recovery,
)
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.medical import (
    CareEnvironment,
    EnvironmentResolver,
    MedicalService,
    _build,
    _value,
    care_context,
)
from wayfarer.orchestration.play import PlayService

MedicalKind = Literal[
    "rest",
    "natural",
    "bandage",
    "first-aid",
    "physician",
    "resuscitate",
    "stabilize",
]


class PlayerRecoveryCommand(ActionCommand):
    kind: Literal["gurps_recovery"] = "gurps_recovery"
    choice_id: str = Field(min_length=1, max_length=300)


@dataclass(frozen=True)
class _Choice:
    id: str
    actor_id: str
    target_id: str
    kind: MedicalKind | Literal["finish-recovery"]
    label: str
    wound_id: str | None = None
    task_id: str | None = None


class _NoRoll:
    def randbelow(self, _exclusive_upper_bound: int, /) -> int:
        raise AssertionError("previewing a recovery start must not consume randomness")


def default_environment(play: PlayService, _state: PlayState, _target_id: str) -> CareEnvironment:
    """Conservative fallback when an authored scenario supplies no care resolver."""

    return CareEnvironment(
        technology_level=play.engine.reviewer.compiler.policy.technology_level or 0
    )


def _capable_provider(state: PlayState, actor_id: str) -> bool:
    hp = next((p for p in state.resources.pools if p.id == f"hp:{actor_id}"), None)
    fp = next((p for p in state.resources.pools if p.id == f"fp:{actor_id}"), None)
    if hp is None or hp.injury is None or hp.injury.incapacitated or hp.injury.stunned:
        return False
    return not (
        fp is not None
        and fp.fatigue is not None
        and (fp.fatigue.collapsed or fp.fatigue.unconscious or fp.fatigue.heart_attack)
    )


def _context(
    play: PlayService,
    state: PlayState,
    actor_id: str,
    target_id: str,
    kind: MedicalKind,
    environment: EnvironmentResolver,
) -> CareContext:
    profile = play.engine.reviewer.compiler.statistics_profile
    if profile not in ("gurps-lite-4e-2004", "gurps-basic-set-4e-2004"):
        raise ValidationError("Recovery requires an exact GURPS profile")
    actor = _build(play, state, actor_id)
    target = _build(play, state, target_id)
    env = environment(play, state, target_id)
    entities = {entity.id: entity for entity in state.world.entities}
    if actor_id not in entities or target_id not in entities:
        raise ValidationError("Recovery actor or patient is unavailable")
    if entities[actor_id].location_id != entities[target_id].location_id:
        raise ValidationError("Treatment requires the patient's location")
    if env.physician_id is not None:
        physician_entity = entities.get(env.physician_id)
        if (
            physician_entity is None
            or physician_entity.location_id != entities[target_id].location_id
            or not _capable_provider(state, env.physician_id)
        ):
            raise ValidationError("Physician must be present and capable of providing care")

    physician = (
        _value(_build(play, state, env.physician_id), "skill:physician")
        if env.physician_id
        else None
    )
    return care_context(cast(ProfileId, profile), actor, target, kind, env, physician)


def _choice_id(kind: str, actor_id: str, target_id: str, discriminator: str = "") -> str:
    digest = hashlib.sha256(discriminator.encode()).hexdigest()[:16] if discriminator else ""
    return ":".join(part for part in ("gurps", kind, actor_id, target_id, digest) if part)


def _start_choices(
    play: PlayService,
    state: PlayState,
    controlled: frozenset[str],
    environment: EnvironmentResolver,
) -> list[_Choice]:
    profile = play.engine.reviewer.compiler.statistics_profile
    if profile not in ("gurps-lite-4e-2004", "gurps-basic-set-4e-2004"):
        return []
    entities = {entity.id: entity for entity in state.world.entities}
    wounds: dict[str, list[str]] = {actor_id: [] for actor_id in controlled}
    for event in state.resources.events:
        if event.target_id not in controlled or not event.id.startswith("injury:"):
            continue
        try:
            injury = InjuryResult.model_validate_json(event.kind)
        except ValueError:
            continue
        if injury.injury > 0:
            wounds[event.target_id].append(event.id)

    choices: list[_Choice] = []
    kinds: tuple[MedicalKind, ...] = (
        "rest",
        "natural",
        "bandage",
        "first-aid",
        "physician",
        "resuscitate",
        "stabilize",
    )
    for actor_id in controlled:
        if actor_id not in entities:
            continue
        for target_id in controlled:
            if (
                target_id not in entities
                or entities[actor_id].location_id != entities[target_id].location_id
            ):
                continue
            for kind in kinds:
                if kind in ("rest", "natural") and actor_id != target_id:
                    continue
                if kind not in ("rest", "natural") and not _capable_provider(state, actor_id):
                    continue
                if kind in ("resuscitate", "stabilize") and profile != "gurps-basic-set-4e-2004":
                    continue
                candidates = wounds[target_id] if kind in ("bandage", "first-aid") else [""]
                for wound_id in candidates:
                    command = BeginRecovery(
                        id="preview-player-recovery",
                        actor_id=actor_id,
                        expected_revision=state.revision,
                        kind=kind,
                        target_id=target_id,
                        wound_id=wound_id or None,
                        # Procedure-defined durations overwrite this. Rest is an
                        # intentionally fixed ten-minute player action.
                        seconds=600,
                    )
                    try:
                        context = _context(play, state, actor_id, target_id, kind, environment)
                        apply_recovery(
                            state.resources, command, context, rng=_NoRoll(), system=True
                        )
                    except ValidationError, ConflictError, AssertionError, StopIteration:
                        continue
                    label = {
                        "rest": "Rest for 10 minutes",
                        "natural": "Begin natural recovery",
                        "bandage": "Bandage wound",
                        "first-aid": "Provide first aid",
                        "physician": "Provide physician care",
                        "resuscitate": "Attempt resuscitation",
                        "stabilize": "Attempt stabilization",
                    }[kind]
                    choices.append(
                        _Choice(
                            _choice_id(kind, actor_id, target_id, wound_id),
                            actor_id,
                            target_id,
                            kind,
                            label,
                            wound_id or None,
                        )
                    )
    return choices


def choices(
    play: PlayService,
    state: PlayState,
    controlled_actor_ids: tuple[str, ...],
    environment: EnvironmentResolver | None = None,
) -> tuple[list[dict[str, object]], list[dict[str, object]], dict[str, _Choice]]:
    """Return visibility-safe choices, task summaries, and the private dispatch map."""

    controlled = frozenset(controlled_actor_ids)
    resolver = environment or default_environment
    private: dict[str, _Choice] = {}
    tasks: list[dict[str, object]] = []
    for task in state.resources.recovery_tasks:
        if task.actor_id not in controlled or task.target_id not in controlled:
            continue
        can_finish = not task.settled and (
            task.status == "interrupted"
            or state.resources.game_time >= task.due
            or (task.kind == "rest" and state.resources.game_time > task.start)
        )
        tasks.append(
            {
                "id": task.id,
                "actor_id": task.actor_id,
                "target_actor_id": task.target_id,
                "kind": task.kind,
                "status": task.status,
                "due": task.due,
                "settled": task.settled,
                "can_finish": can_finish,
            }
        )
        if can_finish:
            choice = _Choice(
                _choice_id("finish", task.actor_id, task.target_id, task.id),
                task.actor_id,
                task.target_id,
                "finish-recovery",
                f"Finish {task.kind}",
                task_id=task.id,
            )
            private[choice.id] = choice

    # A pending task blocks conflicting activity. Only settlement controls are
    # advertised until it is settled, preserving the shared-clock invariant.
    if not any(not bool(task["settled"]) for task in tasks):
        for choice in _start_choices(play, state, controlled, resolver):
            private[choice.id] = choice

    public: list[dict[str, object]] = [
        {
            "id": choice.id,
            "actor_id": choice.actor_id,
            "target_actor_id": choice.target_id,
            "kind": choice.kind,
            "label": choice.label,
        }
        for choice in private.values()
    ]
    return public, tasks, private


async def execute(
    play: PlayService,
    state: PlayState,
    command: PlayerRecoveryCommand,
    *,
    controlled_actor_ids: tuple[str, ...],
    environment: EnvironmentResolver | None = None,
) -> None:
    """Resolve one opaque choice into an authoritative medical command."""

    # The medical reducer owns an exact-once receipt. A transport retry can arrive
    # after its choice disappeared because the task already started or settled.
    # Treat the persisted receipt as the authoritative completed replay instead of
    # re-deriving a now-obsolete choice or consuming randomness again.
    if any(receipt.command_id == command.id for receipt in state.resources.receipts):
        return

    resolver = environment or default_environment
    _public, _tasks, private = choices(play, state, controlled_actor_ids, resolver)
    selected = private.get(command.choice_id)
    if selected is None or selected.actor_id != command.actor_id:
        raise ValidationError("Recovery choice is no longer authorized")
    medical: BeginRecovery | FinishRecovery
    if selected.kind == "finish-recovery":
        assert selected.task_id is not None
        medical = FinishRecovery(
            id=command.id,
            actor_id=command.actor_id,
            expected_revision=command.expected_revision,
            task_id=selected.task_id,
        )
    else:
        medical = BeginRecovery(
            id=command.id,
            actor_id=command.actor_id,
            expected_revision=command.expected_revision,
            kind=selected.kind,
            target_id=selected.target_id,
            wound_id=selected.wound_id,
            seconds=600,
        )
    await MedicalService(play, resolver).execute(
        state.campaign_id, medical, authenticated_actor_id=command.actor_id
    )
