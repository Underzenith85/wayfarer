"""Source-bound, unresolved outside damage over existing environmental exposures.

B428 acid contact and B433 ambient flames have an independent damage roll;
lower damage is favorable to their exposed owner. This seam does not turn an
actor's resistance check into an outside roll or synthesize a party-wide event.
"""

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace, Outcome, RandomSource, draw_dice
from wayfarer.engine.rules.environment import poison_spec
from wayfarer.engine.rules.environmental_hazards import acid_spec
from wayfarer.engine.rules.gurps_checks import replay_success
from wayfarer.engine.rules.types.hazard import HazardSchedule, RecoveryRestriction
from wayfarer.engine.simulation.health.hazard_records import HazardCommand
from wayfarer.engine.simulation.health.hazard_visibility import (
    SecretHazardResult,
    conceal_hazard_result,
)
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record

Die = Annotated[int, Field(ge=1, le=6)]
PREREQUISITE_PREFIX = "outside-prerequisite:"


class PreparedHazardDamage(Record):
    schedule: HazardSchedule
    hp: Pool
    fp: Pool
    game_time: int = Field(ge=0)
    resistance: CheckTrace | None = Field(default=None, exclude_if=lambda value: value is None)

    @property
    def dice_count(self) -> int:
        if self.resistance is not None and self.resistance.outcome.succeeded:
            return 0
        if self.schedule.spec.kind == "heat":
            return int(
                self.resistance is not None and self.resistance.outcome is Outcome.CRITICAL_FAILURE
            )
        return self.schedule.spec.damage_dice

    @property
    def modifier(self) -> int:
        if self.schedule.spec.kind == "heat" and self.dice_count:
            return 0
        return self.schedule.spec.damage_add


class NaturalExposureDeclaration(Record):
    """A seated GM's causal fact, tied to an actual recorded exposure admission."""

    kind: Literal["natural-environment"] = "natural-environment"
    exposure_command_id: Id
    circumstances: str = Field(min_length=1, max_length=2000)


class OutsidePrerequisite(Record):
    """One settled personal check survives cancellation of its later damage choice."""

    preparation: PreparedHazardDamage
    source: NaturalExposureDeclaration
    exposure_command: HazardCommand
    secret: bool = Field(default=False, exclude_if=lambda value: not value)


class HazardDamageSelection(Record):
    preparation: PreparedHazardDamage
    dice: tuple[Die, ...]

    @model_validator(mode="after")
    def complete_dice(self) -> HazardDamageSelection:
        if len(self.dice) != self.preparation.dice_count:
            raise ValueError("Selected hazard dice do not match the source expression")
        return self

    @property
    def basic_damage(self) -> int:
        if (
            self.preparation.resistance is not None
            and self.preparation.resistance.outcome.succeeded
        ):
            return 0
        # B378's non-crushing floor applies before DR; B428/B433 give no exception.
        return max(1, sum(self.dice) + self.preparation.modifier)


def prerequisite_key(schedule: HazardSchedule) -> str:
    return json.dumps([schedule.id, schedule.cycle, schedule.due], separators=(",", ":"))


def saved_prerequisite(
    state: ResourceState, schedule: HazardSchedule
) -> OutsidePrerequisite | None:
    identifier = (
        PREREQUISITE_PREFIX + hashlib.sha256(prerequisite_key(schedule).encode()).hexdigest()
    )
    event = next((row for row in state.events if row.id == identifier), None)
    return OutsidePrerequisite.model_validate_json(event.kind) if event is not None else None


def resume_saved_hazard_damage(
    state: ResourceState,
    schedule: HazardSchedule,
    selected: HazardDamageSelection | None,
    *,
    operation: str,
    rng: RandomSource,
) -> HazardDamageSelection | None:
    """Every actual resolver honors the already rolled personal prerequisite."""
    if operation != "resolve":
        return selected
    prior = saved_prerequisite(state, schedule)
    if prior is None:
        return selected
    if prior.preparation.schedule != schedule:
        raise ConflictError("The settled hazard prerequisite source changed")
    current = prepare_hazard_damage(
        state,
        actor_id=schedule.actor_id,
        schedule_id=schedule.id,
        resistance=prior.preparation.resistance,
    )
    if selected is not None:
        if selected.preparation != current:
            raise ConflictError("Selected damage cannot replace a settled resistance result")
        return selected
    return HazardDamageSelection(preparation=current, dice=draw_dice(rng, current.dice_count))


def _source(schedule: HazardSchedule) -> None:
    spec = schedule.spec
    if spec.kind == "acid" and spec.variant in {"splash", "immersion"}:
        if spec.protection is None or spec.protection.sealed:
            raise ValidationError("Protected acid exposure has no outside damage roll")
        variant: Literal["splash", "immersion"] = (
            "splash" if spec.variant == "splash" else "immersion"
        )
        if spec != acid_spec(
            variant, id=spec.id, scene_id=spec.scene_id, protection=spec.protection
        ):
            raise ValidationError("Outside acid damage requires the canonical B428 profile")
    elif spec.kind == "fire":
        if (
            (spec.damage_dice, spec.damage_add) not in {(1, -3), (1, -1), (3, 0)}
            or spec.interval != 1
            or spec.resistible
            or spec.damage_type not in {None, "burn"}
            or spec.damage_from_margin
            or spec.affliction != "none"
            or spec.critical_effect != "none"
            or spec.cycles_dice
        ):
            raise ValidationError("Outside flame damage requires a supported B433 profile")
    elif spec.kind == "heat" and spec.variant is None:
        if spec.damage_from_margin or spec.affliction != "none" or spec.cycles_dice:
            raise ValidationError("Outside heat-stroke damage requires the B434 ambient profile")
    elif spec.kind == "poison" and spec.variant in {
        "arsenic",
        "mustard-respiratory",
    }:
        if spec != poison_spec(spec.variant, id=spec.id, scene_id=spec.scene_id):
            raise ValidationError("Outside poison damage requires its canonical B439 profile")
    else:
        raise ValidationError("This exposure has no supported outside damage continuation")
    if schedule.stage != "cycles" or schedule.combat_turn is not None:
        raise ValidationError("Outside damage requires an ordinary environmental cycle")


def prepare_hazard_damage(
    state: ResourceState, *, actor_id: str, schedule_id: str, resistance: CheckTrace | None = None
) -> PreparedHazardDamage:
    """Capture a real current damage boundary without consuming any dice."""
    schedule = next((row for row in state.hazards if row.id == schedule_id), None)
    if schedule is None or not schedule.active or schedule.remaining < 1:
        raise ConflictError("Outside event requires an active environmental exposure")
    if schedule.actor_id != actor_id:
        raise ValidationError("Outside event does not affect the Luck owner")
    if schedule.due != state.game_time:
        raise ConflictError("Prepare outside damage at its shared-clock deadline")
    _source(schedule)
    hp = next((pool for pool in state.pools if pool.id == "hp:" + actor_id), None)
    fp = next((pool for pool in state.pools if pool.id == "fp:" + actor_id), None)
    if (
        hp is None
        or fp is None
        or hp.injury is None
        or fp.fatigue is None
        or hp.injury.profile_id != schedule.spec.profile_id
        or fp.fatigue.profile_id != schedule.spec.profile_id
        or hp.injury.dead
    ):
        raise ValidationError("Outside damage requires its living owner's Basic Set pools")
    if resistance is not None and (
        not schedule.spec.resistible or replay_success(resistance) != resistance
    ):
        raise ValidationError("Outside prerequisite is not a valid captured resistance result")
    return PreparedHazardDamage(
        schedule=schedule, hp=hp, fp=fp, game_time=state.game_time, resistance=resistance
    )


def validate_hazard_selection(
    state: ResourceState,
    selection: HazardDamageSelection | None,
    *,
    actor_id: str,
    schedule_id: str,
    operation: str,
) -> None:
    if selection is None:
        return
    if operation != "resolve" or selection.preparation != prepare_hazard_damage(
        state,
        actor_id=actor_id,
        schedule_id=schedule_id,
        resistance=selection.preparation.resistance,
    ):
        raise ConflictError("Selected outside damage no longer matches its exposure")
    if selection.preparation.schedule.spec.resistible and selection.preparation.resistance is None:
        raise ValidationError("Outside damage cannot skip its personal resistance prerequisite")


def cyclic_poison_restriction(
    state: ResourceState, schedule_id: str, hp_lost: int
) -> ResourceState:
    """B438: damage from a cyclic poison cannot heal until the exposure ends."""
    schedule = next(row for row in state.hazards if row.id == schedule_id)
    if schedule.spec.kind != "poison" or schedule.spec.cycles <= 1:
        return state
    prior = next((row for row in state.illnesses if row.id == schedule_id), None)
    if not hp_lost and prior is None:
        return state
    restriction = RecoveryRestriction(
        id=schedule_id,
        actor_id=schedule.actor_id,
        active=schedule.active,
        hp_debt=hp_lost + (prior.hp_debt if prior is not None else 0),
        blocks_natural_healing=True,
        blocks_physician_healing=True,
    )
    return state.model_copy(
        update={
            "illnesses": tuple(row for row in state.illnesses if row.id != schedule_id)
            + (restriction,)
        }
    )


def finish_hazard_resolution(
    state: ResourceState,
    command: HazardCommand,
    schedule_id: str,
    hp_lost: int,
    prerequisite: OutsidePrerequisite | None,
) -> ResourceState:
    state = cyclic_poison_restriction(state, schedule_id, hp_lost)
    if prerequisite is not None and prerequisite.secret and command.kind == "resolve":
        state = conceal_hazard_result(
            state,
            SecretHazardResult(
                command_id=command.id,
                actor_id=command.actor_id,
                schedule_id=schedule_id,
                exposure_command_id=prerequisite.exposure_command.id,
            ),
            system=True,
        )
    return state
