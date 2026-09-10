"""Persisted, profile-opted fatigue and timed medical work (B424-427)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wayfarer.errors import ConflictError

ProfileId = Literal["gurps-lite-4e-2004", "gurps-basic-set-4e-2004"]
FatigueCause = Literal["ordinary", "starvation", "dehydration", "sleep"]


class FatigueStatus(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    profile_id: ProfileId
    collapsed: bool = False
    unconscious: bool = False
    heart_attack: bool = False
    heart_attack_deadline: int | None = Field(default=None, ge=0)
    power: int = Field(default=0, ge=0, exclude_if=lambda v: v == 0)
    half_paid: bool = Field(default=False, exclude_if=lambda v: not v)
    starvation: int = Field(default=0, ge=0)
    dehydration: int = Field(default=0, ge=0)
    sleep: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_heart_attack(self) -> FatigueStatus:
        if self.heart_attack != (self.heart_attack_deadline is not None):
            raise ValueError("Heart attack requires its authoritative deadline")
        return self


class RecoveryTask(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )
    id: str = Field(min_length=1, max_length=200)
    actor_id: str
    target_id: str
    profile_id: ProfileId
    kind: Literal[
        "rest", "natural", "bandage", "first-aid", "physician", "resuscitate", "stabilize"
    ]
    start: int = Field(ge=0)
    due: int = Field(ge=0)
    status: Literal["pending", "completed", "interrupted", "cancelled"] = "pending"
    interrupted_at: int | None = Field(default=None, ge=0)
    settled: bool = False
    wound_id: str | None = None
    bandaged_hp: int = Field(default=0, ge=0)
    technology_level: int = Field(default=8, ge=0, le=12)
    food: bool = False
    water: bool = False
    sleep: bool = False
    physician_skill: int | None = None
    physician_id: str | None = None
    ht: int = Field(default=10, ge=1)
    skill: int | None = Field(default=None, ge=1)
    treatment_modifier: int = 0
    fp_interval: Literal[300, 600] = Field(default=600, exclude_if=lambda v: v == 600)
    power_entitlement: int = Field(default=0, ge=0, exclude_if=lambda v: v == 0)
    healing_bonus: int = Field(default=0, ge=0, exclude_if=lambda v: v == 0)
    healing_rate: Literal[1, 2] = Field(default=1, exclude_if=lambda v: v == 1)
    ordinary_entitlement: int = Field(default=0, ge=0)
    starvation_entitlement: int = Field(default=0, ge=0)
    dehydration_entitlement: int = Field(default=0, ge=0)
    sleep_entitlement: int = Field(default=0, ge=0)
    ordinary_granted: int = Field(default=0, ge=0)
    starvation_granted: int = Field(default=0, ge=0)
    dehydration_granted: int = Field(default=0, ge=0)
    sleep_granted: int = Field(default=0, ge=0)
    hp_entitlement: int = Field(default=0, ge=0)
    fp_recovered_total: int = Field(default=0, ge=0)


def rest_entitlement(task: RecoveryTask, at: int | None = None) -> tuple[int, int, int, int]:
    end = min(task.due, task.interrupted_at if task.interrupted_at is not None else task.due)
    seconds = max(0, min(end, at if at is not None else end) - task.start)
    return (
        min(task.ordinary_entitlement - task.power_entitlement, seconds // task.fp_interval)
        + min(
            task.power_entitlement,
            max(
                0, seconds - (task.ordinary_entitlement - task.power_entitlement) * task.fp_interval
            )
            // 600,
        ),
        min(task.starvation_entitlement, 3 * (seconds // 86400)) if task.food else 0,
        task.dehydration_entitlement if task.water and seconds >= 86400 else 0,
        min(task.sleep_entitlement, 1 + (seconds - 28800) // 3600)
        if task.sleep and seconds >= 28800
        else 0,
    )


def require_settled(tasks: tuple[RecoveryTask, ...], actor_ids: frozenset[str], at: int) -> None:
    """Finish earned recovery before using resources or taking further actions."""
    for task in tasks:
        if task.settled or not actor_ids & {task.actor_id, task.target_id}:
            continue
        earned = rest_entitlement(task, at)
        granted = (
            task.ordinary_granted,
            task.starvation_granted,
            task.dehydration_granted,
            task.sleep_granted,
        )
        if (task.status == "pending" and task.due <= at) or (
            task.kind == "rest" and any(e > g for e, g in zip(earned, granted, strict=True))
        ):
            raise ConflictError("Settle earned recovery before further activity")


def interrupt_tasks(
    tasks: tuple[RecoveryTask, ...], actor_ids: frozenset[str], at: int
) -> tuple[RecoveryTask, ...]:
    """Activity after the due time cannot erase already earned uninterrupted rest."""
    require_settled(tasks, actor_ids, at)
    return tuple(
        task.model_copy(update={"status": "interrupted", "interrupted_at": at})
        if task.status == "pending"
        and at < task.due
        and actor_ids & {task.actor_id, task.target_id}
        else task.model_copy(update={"physician_id": None, "physician_skill": None})
        if task.status == "pending" and at < task.due and task.physician_id in actor_ids
        else task
        for task in tasks
    )


def retire_tasks(
    tasks: tuple[RecoveryTask, ...], actor_ids: frozenset[str], at: int
) -> tuple[RecoveryTask, ...]:
    """Death cancels unfinished work; it must never hold the shared clock open."""
    return tuple(
        task.model_copy(update={"status": "cancelled", "settled": True, "interrupted_at": at})
        if not task.settled and actor_ids & {task.actor_id, task.target_id}
        else task.model_copy(update={"physician_id": None, "physician_skill": None})
        if task.physician_id in actor_ids
        else task
        for task in tasks
    )
