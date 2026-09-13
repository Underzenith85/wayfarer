"""Selected-source creature definitions and finite training tables."""

from __future__ import annotations

from wayfarer.engine.rules.types.creature import (
    CreatureAttack,
    CreatureMovement,
    CreatureSkill,
    CreatureStatistics,
    CreatureTemplate,
    CreatureTrait,
    MountCapabilities,
    TrainableCommand,
)
from wayfarer.errors import ValidationError


def training_days(animal_iq: int, target_level: int) -> int:
    """Return the B459 general-training duration, rejecting impossible cells."""
    table = {
        2: {2: 60},
        3: {2: 30, 3: 360},
        4: {2: 7, 3: 180, 4: 360},
        5: {2: 2, 3: 90, 4: 180, 5: 720},
    }
    try:
        return table[animal_iq][target_level]
    except KeyError as exc:
        raise ValidationError("Unsupported or impossible animal training level") from exc


def command_training_days(animal_iq: int) -> int:
    """Return the B458 duration for one separately taught trick."""
    try:
        return {3: 90, 4: 30, 5: 14}[animal_iq]
    except KeyError as exc:
        raise ValidationError("This creature cannot learn a separate trained command") from exc


def _stats(
    st: int,
    dx: int,
    iq: int,
    ht: int,
    will: int,
    per: int,
    speed: int,
    dodge: int,
    move: int,
    sm: int,
    weight: int,
    *,
    dr: int = 0,
    hexes: int = 1,
    enhanced: int | None = None,
) -> CreatureStatistics:
    return CreatureStatistics(
        st=st,
        dx=dx,
        iq=iq,
        ht=ht,
        hp=st,
        will=will,
        per=per,
        fp=ht,
        basic_speed_quarters=speed,
        dodge=dodge,
        size_modifier=sm,
        weight_lb=weight,
        hexes=hexes,
        dr=dr,
        movement=(CreatureMovement(mode="ground", ordinary_move=move, enhanced_move=enhanced),),
    )


def representative_creatures() -> tuple[CreatureTemplate, ...]:
    """A bounded source-backed sample, not an unlimited bestiary claim."""
    recall = TrainableCommand(id="come", required_training_level=3, task="recall")
    return (
        CreatureTemplate(
            id="creature:house-cat",
            name="House Cat",
            reference="B456",
            kind="animal",
            mentality="domestic",
            statistics=_stats(4, 14, 4, 10, 11, 12, 24, 10, 10, -3, 10),
            traits=(CreatureTrait(id="quadruped"), CreatureTrait(id="sharp-claws")),
            skills=(
                CreatureSkill(id="skill:brawling", level=16),
                CreatureSkill(id="skill:stealth", level=14),
            ),
            attacks=(
                CreatureAttack(
                    id="bite", form="bite", damage_basis="thrust-1", damage_type="cutting"
                ),
            ),
            commands=(
                TrainableCommand(
                    id="trick",
                    required_training_level=4,
                    task="trick",
                    included_in_general_training=False,
                ),
            ),
        ),
        CreatureTemplate(
            id="creature:large-guard-dog",
            name="Large Guard Dog",
            reference="B457",
            kind="animal",
            mentality="domestic",
            statistics=_stats(9, 11, 4, 12, 10, 12, 23, 8, 10, 0, 90),
            traits=(CreatureTrait(id="quadruped"), CreatureTrait(id="sharp-teeth")),
            skills=(
                CreatureSkill(id="skill:brawling", level=13),
                CreatureSkill(id="skill:tracking", level=13),
            ),
            attacks=(
                CreatureAttack(
                    id="bite", form="bite", damage_basis="thrust-1", damage_type="cutting"
                ),
            ),
            commands=(
                recall,
                TrainableCommand(id="guard", required_training_level=4, task="guard"),
            ),
        ),
        CreatureTemplate(
            id="creature:timber-wolf",
            name="Timber Wolf",
            reference="B458",
            kind="animal",
            mentality="wild",
            statistics=_stats(10, 12, 4, 12, 11, 14, 24, 9, 9, 0, 120, dr=1),
            traits=(CreatureTrait(id="quadruped"), CreatureTrait(id="sharp-teeth")),
            skills=(
                CreatureSkill(id="skill:brawling", level=14),
                CreatureSkill(id="skill:tracking", level=14),
            ),
            attacks=(
                CreatureAttack(
                    id="bite", form="bite", damage_basis="thrust-1", damage_type="cutting"
                ),
            ),
            commands=(recall,),
        ),
        CreatureTemplate(
            id="creature:cavalry-horse",
            name="Cavalry Horse",
            reference="B459",
            kind="animal",
            mentality="domestic",
            statistics=_stats(22, 9, 3, 11, 11, 12, 20, 9, 8, 1, 1400, hexes=3, enhanced=16),
            traits=(CreatureTrait(id="quadruped"), CreatureTrait(id="hooves")),
            skills=(
                CreatureSkill(id="skill:brawling", level=10),
                CreatureSkill(id="skill:mount", level=12),
            ),
            attacks=(
                CreatureAttack(
                    id="kick", form="kick", damage_basis="thrust", damage_type="crushing"
                ),
            ),
            commands=(
                recall,
                TrainableCommand(id="ride", required_training_level=3, task="riding"),
            ),
            mount=MountCapabilities(riding=True, war_trained=True, combat_training_years=1),
        ),
        CreatureTemplate(
            id="creature:draft-horse",
            name="Draft Horse",
            reference="B459",
            kind="animal",
            mentality="domestic",
            statistics=_stats(25, 9, 3, 12, 10, 11, 21, 8, 6, 1, 2000, hexes=3, enhanced=12),
            traits=(CreatureTrait(id="quadruped"), CreatureTrait(id="hooves")),
            attacks=(
                CreatureAttack(
                    id="kick", form="kick", damage_basis="thrust", damage_type="crushing"
                ),
            ),
            commands=(
                recall,
                TrainableCommand(id="pull", required_training_level=3, task="draft"),
            ),
            mount=MountCapabilities(draft=True),
        ),
        CreatureTemplate(
            id="creature:basilisk",
            name="Basilisk",
            reference="B460",
            kind="monster",
            mentality="wild",
            statistics=_stats(2, 12, 3, 12, 10, 10, 24, 9, 4, -3, 2, dr=1),
            traits=(CreatureTrait(id="vermiform"),),
            attacks=(
                CreatureAttack(
                    id="death-gaze", form="gaze", damage_basis="special", damage_type="toxic"
                ),
            ),
        ),
        CreatureTemplate(
            id="creature:gryphon",
            name="Gryphon",
            reference="B460",
            kind="monster",
            mentality="wild",
            statistics=CreatureStatistics(
                st=17,
                dx=12,
                iq=5,
                ht=12,
                hp=17,
                will=11,
                per=12,
                fp=12,
                basic_speed_quarters=24,
                dodge=10,
                size_modifier=1,
                weight_lb=600,
                hexes=2,
                dr=2,
                movement=(
                    CreatureMovement(mode="ground", ordinary_move=6),
                    CreatureMovement(mode="air", ordinary_move=12, enhanced_move=24),
                ),
            ),
            traits=(CreatureTrait(id="quadruped"), CreatureTrait(id="flight")),
            skills=(CreatureSkill(id="skill:brawling", level=14),),
            attacks=(
                CreatureAttack(
                    id="claw", form="claw", damage_basis="thrust-1", damage_type="cutting"
                ),
            ),
            commands=(recall,),
        ),
    )
