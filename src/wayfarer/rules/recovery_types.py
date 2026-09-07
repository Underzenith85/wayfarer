"""Persisted, profile-opted fatigue and timed medical work (B424-427)."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

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
    kind: Literal["rest", "natural", "bandage", "first-aid", "physician"]
    start: int = Field(ge=0)
    due: int = Field(ge=0)
    status: Literal["pending", "completed", "interrupted"] = "pending"
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


def interrupt_tasks(
    tasks: tuple[RecoveryTask, ...], actor_ids: frozenset[str], at: int
) -> tuple[RecoveryTask, ...]:
    """Activity after the due time cannot erase already earned uninterrupted rest."""
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
