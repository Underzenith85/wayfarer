"""Rest accrued between commands, on the shared clock."""

from __future__ import annotations

from wayfarer.engine.rules.types.hazard import blocked_fp
from wayfarer.engine.rules.types.recovery import RecoveryTask, rest_entitlement
from wayfarer.engine.simulation.resources import ResourceState


def accrue_rest(state: ResourceState, at: int) -> ResourceState:
    """Deterministic clock accrual, preserving continuous restricted-fatigue rest."""
    pools = {p.id: p for p in state.pools}
    tasks: list[RecoveryTask] = []
    for task in state.recovery_tasks:
        if task.kind != "rest" or task.settled:
            tasks.append(task)
            continue
        fp = pools.get(f"fp:{task.target_id}")
        if fp is None or fp.fatigue is None or fp.fatigue.heart_attack:
            tasks.append(task)
            continue
        status = fp.fatigue
        granted = (
            task.ordinary_granted,
            task.starvation_granted,
            task.dehydration_granted,
            task.sleep_granted,
        )
        earned = rest_entitlement(task, at)
        available = (
            max(
                0,
                fp.maximum
                - fp.current
                - status.starvation
                - status.dehydration
                - status.sleep
                - blocked_fp(state.illnesses, task.target_id),
            ),
            status.starvation,
            status.dehydration,
            status.sleep,
        )
        award = tuple(
            min(max(0, e - g), remaining)
            for e, g, remaining in zip(earned, granted, available, strict=True)
        )
        current = fp.current + sum(award)
        status = status.model_copy(
            update={
                "power": max(
                    0, status.power - max(0, award[0] - max(0, available[0] - status.power))
                ),
                "starvation": status.starvation - award[1],
                "dehydration": status.dehydration - award[2],
                "sleep": status.sleep - award[3],
                "collapsed": status.collapsed and current <= 0,
                "unconscious": status.unconscious and current <= 0,
            }
        )
        pools[fp.id] = fp.model_copy(update={"current": current, "fatigue": status})
        # Mark earned units consumed even if another healing source filled FP.
        # They can never become credit against a future fatigue cost.
        task = task.model_copy(
            update={
                "ordinary_granted": earned[0],
                "starvation_granted": earned[1],
                "dehydration_granted": earned[2],
                "sleep_granted": earned[3],
                "fp_recovered_total": task.fp_recovered_total + sum(award),
            }
        )
        tasks.append(task)
    return state.model_copy(update={"pools": tuple(pools.values()), "recovery_tasks": tuple(tasks)})
