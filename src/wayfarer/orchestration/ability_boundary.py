"""Early fail-closed admission for newly enrolled one-second continuations."""

from dataclasses import dataclass

from wayfarer.contracts import Campaign
from wayfarer.engine.simulation.ability_offshift import PREFIX, pending
from wayfarer.engine.simulation.ability_types import AbilityCommand
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.magic.enchanting_calendar import require_enchanting_free
from wayfarer.engine.simulation.magic.enchanting_transitions import AdvanceEnchanting
from wayfarer.errors import ConflictError, ValidationError


@dataclass(frozen=True)
class AbilityAdmission:
    """Server-constructed native command, never a public interval assertion."""

    command: AbilityCommand | Wait | AdvanceEnchanting


def require_boundary(
    campaign: Campaign,
    admission: AbilityAdmission | None,
    *,
    command_id: str | None = None,
    revision: int | None = None,
) -> None:
    raw = campaign.get("play_json")
    if raw is None or PREFIX not in raw:
        return
    state = PlayState.model_validate_json(raw)
    outstanding = pending(state.resources)
    if not outstanding:
        return
    if admission is None:
        raise ConflictError("Resolve or cancel pending off-shift concentration before this command")
    command = admission.command
    if (command.id, command.expected_revision) != (command_id, revision):
        raise ValidationError("Off-shift admission does not match its command plan")
    if isinstance(command, AbilityCommand):
        if command.kind not in ("resolve", "cancel") or not all(
            (p.effect.actor_id, p.effect.ability_id) == (command.actor_id, command.ability_id)
            and (
                command.channel_id == p.effect.channel_id
                or command.kind == "cancel"
                and command.channel_id is None
            )
            for p in outstanding
        ):
            raise ConflictError("Ability command does not continue pending concentration")
        return
    if state.party.groups or any(e.status == "active" for e in state.encounters):
        raise ConflictError("Off-shift concentration does not support grouped or combat clocks")
    end = state.resources.game_time + command.ticks if isinstance(command, Wait) else command.to
    for policy in outstanding:
        if not state.resources.game_time <= end <= policy.effect.ready_at:
            raise ConflictError("Resolve or cancel concentration before advancing beyond readiness")
        require_enchanting_free(state.resources, policy.effect.actor_id, through=end)
