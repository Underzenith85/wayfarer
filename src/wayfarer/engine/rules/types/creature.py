"""Typed creature construction and persistent training facts.

Campaigns fourth printing B455-459.  These records contain paraphrased facts;
natural-attack resolution remains owned by #522.
"""

from __future__ import annotations

from typing import Final, Literal, Self

from pydantic import Field, model_validator

from wayfarer.models import Id, Record

PROFILE: Final[Literal["gurps-basic-set-4e-2004"]] = "gurps-basic-set-4e-2004"
SOURCE: Final[Literal["gurps-basic-set-campaigns-4e-fourth-printing"]] = (
    "gurps-basic-set-campaigns-4e-fourth-printing"
)


class CreatureMovement(Record):
    mode: Literal["ground", "air", "water"]
    ordinary_move: int = Field(ge=0, le=1000)
    enhanced_move: int | None = Field(default=None, ge=1, le=10000)

    @model_validator(mode="after")
    def enhanced_is_faster(self) -> Self:
        if self.enhanced_move is not None and self.enhanced_move < self.ordinary_move:
            raise ValueError("Enhanced creature movement cannot be slower than ordinary movement")
        return self


class CreatureStatistics(Record):
    st: int = Field(ge=1, le=1000)
    dx: int = Field(ge=1, le=100)
    iq: int = Field(ge=1, le=100)
    ht: int = Field(ge=1, le=100)
    hp: int = Field(ge=1, le=1000)
    will: int = Field(ge=1, le=100)
    per: int = Field(ge=1, le=100)
    fp: int = Field(ge=1, le=1000)
    basic_speed_quarters: int = Field(ge=0, le=400)
    dodge: int = Field(ge=0, le=100)
    size_modifier: int = Field(ge=-10, le=20)
    weight_lb: int = Field(ge=0, le=10000000)
    hexes: int = Field(default=1, ge=1, le=100)
    dr: int = Field(default=0, ge=0, le=1000)
    movement: tuple[CreatureMovement, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def movement_modes_are_unique(self) -> Self:
        if len({entry.mode for entry in self.movement}) != len(self.movement):
            raise ValueError("Creature movement modes must be unique")
        return self

    def move(self, mode: Literal["ground", "air", "water"] = "ground") -> CreatureMovement:
        value = next((entry for entry in self.movement if entry.mode == mode), None)
        if value is None:
            raise ValueError(f"Creature has no {mode} movement mode")
        return value


class CreatureTrait(Record):
    id: Id
    levels: int = Field(default=1, ge=1, le=100)
    parameters: tuple[tuple[str, str], ...] = ()


class CreatureSkill(Record):
    id: Id
    level: int = Field(ge=1, le=100)


class CreatureAttack(Record):
    id: Id
    form: Literal["bite", "claw", "kick", "striker", "gaze", "special"]
    damage_basis: Literal["thrust-1", "thrust", "special"]
    damage_type: Literal["crushing", "cutting", "impaling", "toxic", "special"]
    reach: int = Field(default=1, ge=0, le=100)


class TrainableCommand(Record):
    id: Id
    required_training_level: int = Field(ge=2, le=5)
    task: Literal["recall", "tolerance", "riding", "draft", "hunt", "guard", "trick"]
    included_in_general_training: bool = True


class MountCapabilities(Record):
    riding: bool = False
    draft: bool = False
    war_trained: bool = False
    combat_training_years: int = Field(default=0, ge=0, le=4)

    @model_validator(mode="after")
    def war_training_requires_a_riding_mount(self) -> Self:
        if (self.war_trained or self.combat_training_years) and not self.riding:
            raise ValueError("War training requires a riding mount")
        if self.war_trained != (self.combat_training_years > 0):
            raise ValueError("War-training status and years disagree")
        return self


class CreatureTemplate(Record):
    id: Id
    name: str = Field(min_length=1, max_length=200)
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    source_id: Literal["gurps-basic-set-campaigns-4e-fourth-printing"] = SOURCE
    reference: str = Field(pattern=r"^B(?:45[5-9]|460)(?:-[0-9]+)?$")
    kind: Literal["animal", "monster"]
    mentality: Literal["domestic", "wild", "either"]
    statistics: CreatureStatistics
    traits: tuple[CreatureTrait, ...] = ()
    skills: tuple[CreatureSkill, ...] = ()
    attacks: tuple[CreatureAttack, ...] = ()
    commands: tuple[TrainableCommand, ...] = ()
    mount: MountCapabilities | None = None

    @model_validator(mode="after")
    def facts_are_unique_and_coherent(self) -> Self:
        for values in (self.traits, self.skills, self.attacks, self.commands):
            if len({entry.id for entry in values}) != len(values):
                raise ValueError("Creature fact identifiers must be unique within their family")
        if self.mount is not None and self.mount.riding:
            ground = next((m for m in self.statistics.movement if m.mode == "ground"), None)
            if ground is None or ground.ordinary_move <= 0:
                raise ValueError("A riding mount requires ordinary ground movement")
        return self


class CreatureVariation(Record):
    st: int | None = Field(default=None, ge=1, le=1000)
    dx: int | None = Field(default=None, ge=1, le=100)
    iq: int | None = Field(default=None, ge=1, le=100)
    ht: int | None = Field(default=None, ge=1, le=100)
    hp: int | None = Field(default=None, ge=1, le=1000)
    will: int | None = Field(default=None, ge=1, le=100)
    per: int | None = Field(default=None, ge=1, le=100)
    fp: int | None = Field(default=None, ge=1, le=1000)
    basic_speed_quarters: int | None = Field(default=None, ge=0, le=400)
    ground_move: int | None = Field(default=None, ge=0, le=1000)
    add_traits: tuple[CreatureTrait, ...] = ()
    remove_trait_ids: tuple[Id, ...] = ()
    skill_levels: tuple[CreatureSkill, ...] = ()
    mentality: Literal["domestic", "wild"] | None = None

    @model_validator(mode="after")
    def variation_is_unambiguous(self) -> Self:
        if len({entry.id for entry in self.add_traits}) != len(self.add_traits):
            raise ValueError("Duplicate individual trait")
        if len(set(self.remove_trait_ids)) != len(self.remove_trait_ids):
            raise ValueError("Duplicate removed trait")
        if {entry.id for entry in self.add_traits} & set(self.remove_trait_ids):
            raise ValueError("A trait cannot be both added and removed")
        if len({entry.id for entry in self.skill_levels}) != len(self.skill_levels):
            raise ValueError("Duplicate individual skill")
        return self


class PointProvenance(Record):
    definition_id: Id
    amount: int
    point_cost: int
    origin: Literal["template", "individual"]
    rules_package_id: Id
    rules_package_version: str
    reference: str


class LearnedCommand(Record):
    id: Id
    required_training_level: int = Field(ge=2, le=5)
    competence: int = Field(ge=1, le=100)
    learned_from_handler_id: Id


class CreatureTraining(Record):
    id: Id
    kind: Literal["general", "command", "war-mount"]
    handler_id: Id
    handler_skill_id: Id
    competence: int = Field(ge=1, le=100)
    handling_modifier: Literal[-5, 0]
    started_at: int = Field(ge=0)
    due_at: int = Field(ge=1)
    required_days: int = Field(ge=1)
    target_level: int | None = Field(default=None, ge=2, le=5)
    command_id: Id | None = None

    @model_validator(mode="after")
    def program_shape(self) -> Self:
        if self.due_at != self.started_at + self.required_days * 86400:
            raise ValueError("Creature training deadline must use the shared campaign clock")
        if self.kind == "general" and (self.target_level is None or self.command_id is not None):
            raise ValueError("General training requires only a target level")
        if self.kind == "command" and (self.command_id is None or self.target_level is not None):
            raise ValueError("Command training requires only a command identifier")
        if self.kind == "war-mount" and (
            self.command_id is not None or self.target_level is not None
        ):
            raise ValueError("War-mount training has no command or level selector")
        return self


class Creature(Record):
    actor_id: Id
    name: str = Field(min_length=1, max_length=200)
    template_id: Id
    template_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    individual_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    build_revision: str = Field(pattern=r"^[0-9a-f]{64}$")
    profile_id: Literal["gurps-basic-set-4e-2004"] = PROFILE
    source_id: Literal["gurps-basic-set-campaigns-4e-fourth-printing"] = SOURCE
    reference: str
    kind: Literal["animal", "monster"]
    mentality: Literal["domestic", "wild"]
    statistics: CreatureStatistics
    traits: tuple[CreatureTrait, ...] = ()
    skills: tuple[CreatureSkill, ...] = ()
    attacks: tuple[CreatureAttack, ...] = ()
    commands: tuple[TrainableCommand, ...] = ()
    mount: MountCapabilities | None = None
    point_total: int
    point_provenance: tuple[PointProvenance, ...]
    owner_id: Id | None = None
    handler_id: Id | None = None
    training_level: int | None = Field(default=None, ge=2, le=5)
    learned_commands: tuple[LearnedCommand, ...] = ()
    training: CreatureTraining | None = None
    last_command_id: Id | None = None

    @model_validator(mode="after")
    def persistent_facts_are_coherent(self) -> Self:
        if self.training_level is not None and self.training_level > self.statistics.iq:
            raise ValueError("Creature training cannot exceed intelligence")
        if len({entry.id for entry in self.learned_commands}) != len(self.learned_commands):
            raise ValueError("Creature learned commands must be unique")
        if self.training is not None and self.handler_id != self.training.handler_id:
            raise ValueError("Active creature training belongs to the current handler")
        return self
