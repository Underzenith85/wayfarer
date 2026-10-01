"""Canonical sleep facts shared by waking and activity consumers."""

from wayfarer.engine.simulation.resources import ResourceState


def asleep(state: ResourceState, actor_id: str) -> bool:
    return (
        any(s.actor_id == actor_id and s.forced_asleep for s in state.survival)
        or any(
            t.actor_id == actor_id and t.kind == "sleep" and t.status == "pending" and not t.settled
            for t in state.survival_tasks
        )
        or any(
            t.target_id == actor_id and t.sleep and t.status == "pending" and not t.settled
            for t in state.recovery_tasks
        )
    )


def wake_sleep(state: ResourceState, actor_id: str) -> ResourceState:
    """Interrupt sleep without granting an uncompleted night's recovery (B426-427)."""
    return state.model_copy(
        update={
            "survival": tuple(
                s.model_copy(update={"forced_asleep": False}) if s.actor_id == actor_id else s
                for s in state.survival
            ),
            "survival_tasks": tuple(
                t.model_copy(update={"status": "interrupted", "interrupted_at": state.game_time})
                if t.actor_id == actor_id
                and t.kind == "sleep"
                and t.status == "pending"
                and not t.settled
                else t
                for t in state.survival_tasks
            ),
            "recovery_tasks": tuple(
                t.model_copy(update={"status": "interrupted", "interrupted_at": state.game_time})
                if t.target_id == actor_id and t.sleep and t.status == "pending" and not t.settled
                else t
                for t in state.recovery_tasks
            ),
        }
    )
