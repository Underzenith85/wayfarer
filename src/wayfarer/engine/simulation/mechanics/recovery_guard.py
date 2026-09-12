"""Pure guards for authored recovery and pending mechanic continuations."""

from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.recovery import Captivity
from wayfarer.errors import ConflictError, ValidationError


def captive(state: PlayState, actor_id: str) -> Captivity | None:
    return next(
        (
            c
            for c in reversed(state.recovery.captivity)
            if c.actor_id == actor_id and c.released_at is None
        ),
        None,
    )


def guard(state: PlayState, actor_id: str, kind: str, *, allow_fright: bool = False) -> None:
    if kind not in ("resolve_weapon_explosion", "declare_thrown_landing"):
        from wayfarer.engine.simulation.explosions import guard as blast_guard

        blast_guard(state.resources)
    from wayfarer.engine.simulation.mechanics.equipment_retrieval import tasks as retrievals
    from wayfarer.engine.simulation.object_repairs import tasks

    if kind not in ("question", "wait", "retrieve_equipment") and any(
        t.actor_id == actor_id and t.status == "pending" for t in retrievals(state.resources)
    ):
        raise ConflictError("Finish or cancel equipment retrieval before acting")

    if kind not in ("question", "wait", "repair_equipment") and any(
        t.actor_id == actor_id and t.status == "pending" for t in tasks(state.resources)
    ):
        raise ConflictError("Finish or cancel the repair attempt before acting")
    from wayfarer.engine.rules.recovery_types import require_settled
    from wayfarer.engine.simulation.fright import blocked, requires_adjudication

    if kind not in ("question", "wait") and (
        (blocked(state.resources, actor_id, kind=kind) and not allow_fright)
        or requires_adjudication(state.resources, actor_id)
    ):
        raise ValidationError("Resolve the actor's fright condition before acting")
    from wayfarer.engine.simulation.spell_backfires import backfires

    if kind != "question":
        if any(b.pending for b in backfires(state.resources)):
            raise ConflictError("Resolve the recorded spell backfire before advancing play")
    require_settled(
        state.resources.recovery_tasks, frozenset({actor_id}), state.resources.game_time
    )
    if kind not in ("question", "wait", "take_combat_turn", "choose_defense"):
        from wayfarer.engine.simulation.spell_effects import require_not_dazed

        require_not_dazed(state.resources, actor_id)
    if actor_id in state.recovery.dead_actor_ids and kind not in ("choose_recovery", "question"):
        raise ValidationError("Dead characters require a policy-governed replacement")
    if captive(state, actor_id) is not None and kind not in (
        "choose_recovery",
        "question",
        "pause_group",
        "resume_group",
    ):
        raise ValidationError("Captive actions must use authored recovery choices or adjudication")
