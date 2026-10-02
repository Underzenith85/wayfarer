"""Ordinary activity admission at the daily enchanting work boundary."""

from wayfarer.engine.simulation.actions import (
    ActionRules,
    Inspect,
    Move,
    PlayState,
    Social,
    TypedAction,
    UseItem,
    Wait,
)
from wayfarer.engine.simulation.magic.enchanting_calendar import (
    require_enchanting_free,
    require_pending_cast_time,
)
from wayfarer.errors import ValidationError


def action_duration(rules: ActionRules, command: TypedAction) -> int:
    if isinstance(command, Move):
        return rules.movement_ticks
    if isinstance(command, UseItem):
        return rules.item_ticks
    if isinstance(command, Wait):
        return command.ticks
    if isinstance(command, (Inspect, Social)):
        return next(
            rule.duration
            for rule in rules.checks
            if (rule.action, rule.target_id) == (command.kind, command.target_id)
        )
    raise ValidationError("No implemented resolver")


def require_action_time(state: PlayState, actor_id: str, duration: int) -> None:
    """Refuse an overlapping shift before rolls, resource use or a clock change."""
    end = state.resources.game_time + duration
    require_enchanting_free(state.resources, actor_id, through=end)
    require_pending_cast_time(state.resources, end)
