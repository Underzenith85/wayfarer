"""Immutable B414-415 facts between one actor's blast and fragment injury."""

from __future__ import annotations

from pydantic import Field, model_validator

from wayfarer.engine.rules.checks import CheckTrace
from wayfarer.engine.rules.types.explosion import BlastResponse
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.combat.attack_roll import AttackRollSpec
from wayfarer.engine.simulation.combat.commands import ResolveWeaponExplosion, TakeCombatTurn
from wayfarer.engine.simulation.combat.explosions import BlastRecord
from wayfarer.models import Id, Record


class ExposedBlastObject(Record):
    item_id: str
    point: GroundPosition


class BlastProgress(Record):
    blast: BlastRecord
    command_id: str
    center: GroundPosition
    responses: tuple[BlastResponse, ...]
    actor_ids: tuple[str, ...]
    actor_index: int = Field(default=0, ge=0)
    objects: tuple[ExposedBlastObject, ...]
    object_cover: tuple[tuple[str, int], ...]
    object_sizes: tuple[tuple[str, int], ...]
    environment: str
    contact_actor_id: str | None
    internal_actor_id: str | None
    interception: tuple[tuple[str, CheckTrace], ...]
    evidence: tuple[str, ...] = ()
    stop_for: tuple[str, ...] = ()
    incendiary_objects: bool = Field(default=False, exclude_if=lambda value: not value)


class FragmentTarget(Record):
    actor_id: str
    response: BlastResponse
    cover: int
    covered: tuple[str, ...]
    direct: bool
    target: int | None


class PreparedFragmentAttack(Record):
    progress: BlastProgress
    target: FragmentTarget
    spec: AttackRollSpec

    @model_validator(mode="after")
    def exact_actor_roll(self) -> PreparedFragmentAttack:
        progress, target = self.progress, self.target
        if (
            progress.actor_index >= len(progress.actor_ids)
            or progress.actor_ids[progress.actor_index] != target.actor_id
            or target.direct
            or target.target is None
            or target.response not in progress.responses
            or target.response.actor_id != target.actor_id
            or self.spec
            != AttackRollSpec(profile_id="gurps-basic-set-4e-2004", target=target.target)
        ):
            raise ValueError("Fragment preparation differs from its exact actor and source target")
        return self


class RecordedFragmentLaunch(Record):
    campaign_id: Id
    command: TakeCombatTurn
    payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    attack_id: Id
    source_item_id: Id
    producer_command_id: Id
    producer_payload_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


class FragmentContinuation(Record):
    launch: RecordedFragmentLaunch
    resolution: ResolveWeaponExplosion
    preparation: PreparedFragmentAttack | None
    original: CheckTrace | None = None
    secret: bool = False
    cancelled: bool = False

    @property
    def active(self) -> bool:
        return self.preparation is not None

    @model_validator(mode="after")
    def exact_source(self) -> FragmentContinuation:
        phase = self.preparation
        if phase is not None and (
            phase.progress.blast.id != self.resolution.blast_id
            or phase.progress.blast.encounter_id != self.resolution.encounter_id
            or phase.progress.command_id != self.resolution.id
            or phase.target.actor_id == self.launch.command.actor_id
        ):
            raise ValueError("Fragment continuation differs from its actual source")
        if self.cancelled and (not self.secret or self.original is not None):
            raise ValueError("Only an unrolled secret fragment choice can be cancelled")
        if self.original is not None and (
            phase is None or phase.spec.score(self.original.dice) != self.original
        ):
            raise ValueError("Fragment continuation lost its captured original")
        return self
