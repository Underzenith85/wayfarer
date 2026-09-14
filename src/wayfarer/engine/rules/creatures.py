"""Selected-source creature definitions and finite training tables."""

from __future__ import annotations

from wayfarer.engine.rules.traits import (
    attack_defense,
    movement_forms,
    physical,
    psi_powers,
    sensory,
)
from wayfarer.engine.rules.traits.modifiers import (
    AttackProfile,
    LimitationParameters,
    ModifierApproval,
    ModifierRuntimeReceipt,
    ModifierSelection,
    apply_attack_modifiers,
)
from wayfarer.engine.rules.traits.mundane import complete as complete_mundane
from wayfarer.engine.rules.types.creature import (
    CreatureAttack,
    CreatureAttackEffect,
    CreatureCombatBehavior,
    CreatureModifierBinding,
    CreatureMovement,
    CreatureSkill,
    CreatureStatistics,
    CreatureTemplate,
    CreatureTrait,
    MountCapabilities,
    SwarmAttack,
    SwarmSpec,
    TrainableCommand,
)
from wayfarer.errors import ValidationError

_STRUCTURAL_TRAIT_ADAPTERS = frozenset(
    {"domestic-animal", "hooves", "quadruped", "vermiform", "wild-animal"}
)
_CANONICAL_TRAIT_ADAPTERS = {
    "catfall": "advantage:catfall" in movement_forms.BINDING_BY_ID,
    "combat-reflexes": "trait:combat-reflexes" in physical.PHYSICAL_BINDINGS,
    "night-vision": "trait:night-vision" in physical.PHYSICAL_BINDINGS,
    "temperature-tolerance": "trait:temperature-tolerance" in physical.PHYSICAL_BINDINGS,
    "acute-vision": "trait:acute-vision" in physical.PHYSICAL_BINDINGS,
    "discriminatory-smell": "advantage:discriminatory-smell" in sensory.BINDING_BY_ID,
    "peripheral-vision": "trait:advantage:peripheral-vision" in complete_mundane.SPEC_BY_ID,
    "chummy": "trait:disadvantage:chummy" in complete_mundane.SPEC_BY_ID,
    "sharp-claws": "advantage:claws" in attack_defense.BINDING_BY_ID,
    "sharp-teeth": "advantage:teeth" in attack_defense.BINDING_BY_ID,
    "sharp-beak": "advantage:teeth" in attack_defense.BINDING_BY_ID,
    "weak-bite": "disadvantage:weak-bite" in attack_defense.BINDING_BY_ID,
    "winged-flight": "advantage:flight" in movement_forms.BINDING_BY_ID,
}


def _death_gaze() -> CreatureAttackEffect:
    return CreatureAttackEffect(
        definition_id="advantage:innate-attack",
        damage_dice=3,
        modifiers=(
            CreatureModifierBinding(definition_id="modifier:enhancement:malediction", option="1"),
            CreatureModifierBinding(
                definition_id="modifier:limitation:sense-based",
                option="vision",
                sense="vision",
            ),
        ),
        power_id="power:psychokinesis",
    )


def creature_attack_effect(attack: CreatureAttack) -> ModifierRuntimeReceipt:
    """Compile a special attack through the registered ability and modifier adapters."""
    effect = attack.effect
    if effect is None or effect.definition_id != "advantage:innate-attack":
        raise ValidationError("Special creature attack has no registered effect adapter")
    power = psi_powers.BINDING_BY_ID.get(effect.power_id or "")
    if power is None or effect.definition_id not in power.members:
        raise ValidationError("Special creature attack has no registered power adapter")
    if attack.damage_type != "toxic":
        raise ValidationError("Death gaze requires its registered toxic effect adapter")
    receipt = apply_attack_modifiers(
        AttackProfile(damage_kind="toxic"),
        "innate-attack",
        tuple(
            ModifierSelection(
                definition_id=entry.definition_id,
                option=entry.option,
                limitation=(
                    LimitationParameters(sense=entry.sense) if entry.sense is not None else None
                ),
            )
            for entry in effect.modifiers
        ),
        (
            ModifierApproval(
                "modifier:limitation:sense-based",
                "vision",
                -20,
                frozenset({"innate-attack"}),
            ),
        ),
    )
    if (
        receipt.modified.malediction_range != "yards"
        or receipt.modified.penetration_modifier != "sense-based"
        or receipt.modified.penetration_sense != "vision"
    ):
        raise ValidationError("Death gaze requires Malediction 1 and Vision-Based adapters")
    return receipt


def validate_creature_template(template: CreatureTemplate) -> None:
    """Reject drift between selected creature facts and canonical trait adapters."""
    traits = {entry.id: entry for entry in template.traits}
    unknown = set(traits) - _STRUCTURAL_TRAIT_ADAPTERS - _CANONICAL_TRAIT_ADAPTERS.keys()
    unavailable = {
        identifier
        for identifier in set(traits) & _CANONICAL_TRAIT_ADAPTERS.keys()
        if not _CANONICAL_TRAIT_ADAPTERS[identifier]
    }
    if unknown or unavailable:
        raise ValidationError("Creature trait has no registered canonical or structural adapter")
    if (template.mentality == "domestic") != ("domestic-animal" in traits):
        raise ValidationError("Domestic creature mentality requires its meta-trait adapter")
    if (template.mentality == "wild") != ("wild-animal" in traits):
        raise ValidationError("Wild creature mentality requires its meta-trait adapter")
    night_vision = traits.get("night-vision")
    if night_vision is not None and night_vision.levels > 9:
        raise ValidationError("Night Vision exceeds its canonical trait adapter")
    flight = traits.get("winged-flight")
    if flight is not None:
        binding = movement_forms.BINDING_BY_ID.get("advantage:flight")
        if binding is None or "winged" not in {entry.id for entry in binding.modifiers}:
            raise ValidationError("Winged flight has no registered movement adapter")
        if dict(flight.parameters) != {"air-move": "12"}:
            raise ValidationError("Winged flight must retain its approved air movement")
    for attack in template.attacks:
        if attack.effect is not None:
            creature_attack_effect(attack)
    if any(entry.id == "sharp-beak" for entry in template.traits):
        teeth = attack_defense.BINDING_BY_ID.get("advantage:teeth")
        if teeth is None or "sharp-beak" not in teeth.parameters[0].choices:
            raise ValidationError("Sharp beak has no registered natural-weapon adapter")


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


def representative_swarms() -> tuple[SwarmSpec, ...]:
    """The three finite B461 examples, with explicit protection/countermeasure facts."""
    return (
        SwarmSpec(
            id="swarm:bats",
            kind="bats",
            airborne=True,
            move=8,
            dispersal_hp=8,
            attack=SwarmAttack(dice=1, damage_type="cut", armor="normal-dr"),
            immune_countermeasures=("stomp",),
        ),
        SwarmSpec(
            id="swarm:bees",
            kind="bees",
            airborne=True,
            move=6,
            dispersal_hp=12,
            attack=SwarmAttack(fixed_injury=1, damage_type="tox", armor="sealed-only"),
            immune_countermeasures=("stomp",),
            vulnerable_countermeasures=("insecticide", "immersion"),
            ordinary_clothing_seconds=2,
            low_tech_armor_seconds=5,
            disengage_distance_from_origin=50,
        ),
        SwarmSpec(
            id="swarm:rats",
            kind="rats",
            airborne=False,
            move=4,
            dispersal_hp=6,
            attack=SwarmAttack(dice=1, damage_type="cut", armor="normal-dr"),
            vulnerable_countermeasures=("stomp",),
        ),
    )


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
            traits=(
                CreatureTrait(id="catfall"),
                CreatureTrait(id="combat-reflexes"),
                CreatureTrait(id="domestic-animal"),
                CreatureTrait(id="night-vision", levels=5),
                CreatureTrait(id="quadruped"),
                CreatureTrait(id="sharp-claws"),
                CreatureTrait(id="sharp-teeth"),
            ),
            skills=(
                CreatureSkill(id="skill:brawling", level=16),
                CreatureSkill(id="skill:jumping", level=14),
                CreatureSkill(id="skill:stealth", level=14),
            ),
            attacks=(
                CreatureAttack(
                    id="bite", form="bite", damage_basis="thrust-1", damage_type="cutting"
                ),
            ),
            combat_behavior=CreatureCombatBehavior(
                maneuvers=("attack", "move", "move-and-attack", "do-nothing"),
                attack_motivations=("defensive", "panic"),
                preferred_attack_ids=("bite",),
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
            traits=(
                CreatureTrait(id="chummy"),
                CreatureTrait(id="discriminatory-smell"),
                CreatureTrait(id="domestic-animal"),
                CreatureTrait(id="quadruped"),
                CreatureTrait(id="sharp-teeth"),
            ),
            skills=(
                CreatureSkill(id="skill:brawling", level=13),
                CreatureSkill(id="skill:tracking", level=13),
            ),
            attacks=(
                CreatureAttack(
                    id="bite", form="bite", damage_basis="thrust-1", damage_type="cutting"
                ),
            ),
            combat_behavior=CreatureCombatBehavior(
                maneuvers=("attack", "all-out-attack", "move", "move-and-attack", "do-nothing"),
                attack_motivations=("defensive", "territorial", "commanded"),
                preferred_attack_ids=("bite",),
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
            traits=(
                CreatureTrait(id="discriminatory-smell"),
                CreatureTrait(id="night-vision", levels=2),
                CreatureTrait(id="quadruped"),
                CreatureTrait(id="sharp-teeth"),
                CreatureTrait(id="temperature-tolerance"),
                CreatureTrait(id="wild-animal"),
            ),
            skills=(
                CreatureSkill(id="skill:brawling", level=14),
                CreatureSkill(id="skill:tracking", level=14),
            ),
            attacks=(
                CreatureAttack(
                    id="bite", form="bite", damage_basis="thrust-1", damage_type="cutting"
                ),
            ),
            combat_behavior=CreatureCombatBehavior(
                maneuvers=("attack", "all-out-attack", "move", "move-and-attack", "do-nothing"),
                attack_motivations=("predatory", "defensive", "territorial"),
                preferred_attack_ids=("bite",),
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
            traits=(
                CreatureTrait(id="combat-reflexes"),
                CreatureTrait(id="domestic-animal"),
                CreatureTrait(id="hooves"),
                CreatureTrait(id="peripheral-vision"),
                CreatureTrait(id="quadruped"),
                CreatureTrait(id="weak-bite"),
            ),
            skills=(
                CreatureSkill(id="skill:brawling", level=10),
                CreatureSkill(id="skill:mount", level=12),
            ),
            attacks=(
                CreatureAttack(
                    id="kick", form="kick", damage_basis="thrust", damage_type="crushing"
                ),
            ),
            combat_behavior=CreatureCombatBehavior(
                maneuvers=("attack", "move", "move-and-attack", "do-nothing"),
                attack_motivations=("defensive", "commanded", "panic"),
                preferred_attack_ids=("kick",),
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
            traits=(
                CreatureTrait(id="domestic-animal"),
                CreatureTrait(id="hooves"),
                CreatureTrait(id="peripheral-vision"),
                CreatureTrait(id="quadruped"),
                CreatureTrait(id="weak-bite"),
            ),
            attacks=(
                CreatureAttack(
                    id="kick", form="kick", damage_basis="thrust", damage_type="crushing"
                ),
            ),
            combat_behavior=CreatureCombatBehavior(
                maneuvers=("attack", "move", "do-nothing"),
                attack_motivations=("defensive", "panic"),
                preferred_attack_ids=("kick",),
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
            traits=(CreatureTrait(id="vermiform"), CreatureTrait(id="wild-animal")),
            attacks=(
                CreatureAttack(
                    id="death-gaze",
                    form="gaze",
                    damage_basis="special",
                    damage_type="toxic",
                    effect=_death_gaze(),
                ),
            ),
            combat_behavior=CreatureCombatBehavior(
                maneuvers=("concentrate", "move", "do-nothing"),
                attack_motivations=("predatory", "defensive"),
                preferred_attack_ids=("death-gaze",),
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
            traits=(
                CreatureTrait(id="acute-vision", levels=3),
                CreatureTrait(id="combat-reflexes"),
                CreatureTrait(id="quadruped"),
                CreatureTrait(id="sharp-beak"),
                CreatureTrait(id="sharp-claws"),
                CreatureTrait(id="wild-animal"),
                CreatureTrait(id="winged-flight", parameters=(("air-move", "12"),)),
            ),
            skills=(CreatureSkill(id="skill:brawling", level=14),),
            attacks=(
                CreatureAttack(
                    id="claw", form="claw", damage_basis="thrust-1", damage_type="cutting"
                ),
                CreatureAttack(
                    id="beak",
                    form="beak",
                    damage_basis="thrust-1",
                    damage_type="large-piercing",
                ),
            ),
            combat_behavior=CreatureCombatBehavior(
                maneuvers=("attack", "all-out-attack", "move", "move-and-attack", "do-nothing"),
                attack_motivations=("predatory", "defensive", "territorial", "commanded"),
                preferred_attack_ids=("claw", "beak"),
            ),
            commands=(recall,),
        ),
    )
