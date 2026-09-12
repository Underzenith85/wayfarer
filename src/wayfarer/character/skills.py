"""Profile-selected GURPS skill compilation (B168-173, B229-232; Lite 13-14).

No request supplies mechanics. Specs come from exact pinned catalog definitions.
Default-only skills cannot be links in a default chain (B173). Purchased links
are resolved to a stable best level, including reciprocal catalog defaults;
acquisition prerequisites and parent-relative techniques remain acyclic.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from graphlib import CycleError, TopologicalSorter

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.rules.conformance import profile, require_capabilities
from wayfarer.rules.skill_types import (
    ControllingAttribute,
    DefaultCondition,
    DefaultConditionKind,
    Difficulty,
    PrerequisiteKind,
    SkillDefault,
    SkillPrerequisite,
    SkillSpec,
)

BASIC = "gurps-basic-set-4e-2004"
LITE = "gurps-lite-4e-2004"
OFFSETS = {
    Difficulty.EASY: 0,
    Difficulty.AVERAGE: -1,
    Difficulty.HARD: -2,
    Difficulty.VERY_HARD: -3,
}


class SkillError(ValidationError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True, slots=True)
class SkillLevel:
    target: str
    level: int
    points: int
    default_from: str | None = None
    default_credit: int = 0
    unmodified: int | None = None
    default_conditions: tuple[DefaultCondition, ...] = ()


@dataclass(frozen=True, slots=True)
class DefaultContext:
    """Authoritative state used to decide whether a conditional default exists.

    A missing fact fails closed. Technology levels are keyed by skill definition;
    equipment IDs are pinned rule-definition IDs. Matching-specialty needs no
    caller input because the selected catalog owns both specialty records.
    """

    technology_levels: Mapping[str, int]
    equipment_ids: frozenset[str]
    purchased_definition_ids: frozenset[str] = frozenset()
    capabilities: frozenset[str] = frozenset()
    campaign_technology_level: int | None = None

    @classmethod
    def empty(cls) -> DefaultContext:
        return cls({}, frozenset())


def relative_level(difficulty: Difficulty, points: int) -> int:
    if not isinstance(difficulty, Difficulty) or type(points) is not int or points < 1:
        raise SkillError("skill.points", "Skill points must be a positive integer")
    return OFFSETS[difficulty] + (0 if points == 1 else 1 if points < 4 else 2 + (points - 4) // 4)


def _credit(difficulty: Difficulty, relative: int) -> int:
    steps = relative - OFFSETS[difficulty]
    return 0 if steps < 0 else 1 if steps == 0 else 2 if steps == 1 else 4 * (steps - 1)


class SkillCompiler:
    def __init__(
        self,
        profile_id: str,
        definitions: Mapping[str, RuleDefinition],
        available: frozenset[str] | None = None,
    ) -> None:
        profile(profile_id)
        if profile_id not in (BASIC, LITE):
            raise SkillError("skill.profile", "Unsupported skill profile")
        require_capabilities(
            profile_id, ("gurps.character.skill_difficulty", "gurps.character.skill_defaults")
        )
        if profile_id == BASIC:
            require_capabilities(
                profile_id, ("gurps.character.specialties", "gurps.character.techniques")
            )
        self.profile_id = profile_id
        self.specs: dict[str, SkillSpec] = {}
        for key, definition in definitions.items():
            spec = definition.skill
            if spec is None:
                continue
            if (
                definition.kind is not DefinitionKind.SKILL
                or definition.status is not ImplementationStatus.IMPLEMENTED
                or "character.gurps-skill" not in definition.hooks
            ):
                raise SkillError("skill.definition", f"Invalid skill binding: {key}")
            if (
                not isinstance(spec.attribute, ControllingAttribute)
                or not isinstance(spec.difficulty, Difficulty)
                or not spec.reference
            ):
                raise SkillError("skill.definition", f"Unsupported skill definition: {key}")
            if profile_id == LITE and (
                spec.difficulty is Difficulty.VERY_HARD
                or spec.technique is not None
                or (spec.specialty is not None and spec.specialty.optional_parent is not None)
            ):
                raise SkillError("skill.profile", f"Skill mechanic is outside Lite: {key}")
            self.specs[key] = spec
        self.available = (
            frozenset(self.specs) if available is None else available & self.specs.keys()
        )
        graph: dict[str, set[str]] = {}
        for key, spec in self.specs.items():
            default_refs = {d.target for d in spec.defaults if d.target not in ControllingAttribute}
            refs = {
                p.target for p in spec.prerequisites if p.kind is PrerequisiteKind.TRAINED_SKILL
            }
            refs.update(
                p.target
                for group in spec.prerequisite_groups
                for p in group.alternatives
                if p.kind is PrerequisiteKind.TRAINED_SKILL
            )
            if any(type(d.modifier) is not int or d.modifier > 0 for d in spec.defaults):
                raise SkillError("skill.definition", "Defaults need integer nonpositive modifiers")
            for default in spec.defaults:
                if len(set(default.conditions)) != len(default.conditions):
                    raise SkillError("skill.definition", "Duplicate default condition")
                for condition in default.conditions:
                    if not isinstance(condition.kind, DefaultConditionKind):
                        raise SkillError("skill.definition", "Unsupported default condition")
                    needs_value = condition.kind is DefaultConditionKind.REQUIRED_EQUIPMENT
                    if needs_value != (condition.value is not None):
                        raise SkillError("skill.definition", "Invalid default condition value")
            alternatives = tuple(p for g in spec.prerequisite_groups for p in g.alternatives)
            if any(
                type(p.minimum) is not int
                or p.minimum < 1
                or not isinstance(p.kind, PrerequisiteKind)
                or (
                    p.minimum_technology_level is not None
                    and (
                        type(p.minimum_technology_level) is not int
                        or p.minimum_technology_level < 0
                    )
                )
                for p in tuple(spec.prerequisites) + alternatives
            ):
                raise SkillError("skill.definition", "Invalid acquisition prerequisite")
            if any(len(group.alternatives) < 2 for group in spec.prerequisite_groups):
                raise SkillError(
                    "skill.definition", "An alternative set needs at least two alternatives"
                )
            if len({(d.target, d.conditions) for d in spec.defaults}) != len(spec.defaults):
                raise SkillError("skill.definition", "Duplicate skill defaults")
            if spec.specialty is not None:
                specialty = spec.specialty
                if not specialty.family or not specialty.name or spec.technique is not None:
                    raise SkillError("skill.specialty", "Invalid specialty")
                if specialty.optional_parent is not None:
                    parent = self.specs.get(specialty.optional_parent)
                    if (
                        parent is None
                        or parent.specialty is not None
                        or parent.technique is not None
                        or parent.attribute is not ControllingAttribute.IQ
                        or parent.difficulty not in (Difficulty.HARD, Difficulty.VERY_HARD)
                        or spec.attribute != parent.attribute
                        or OFFSETS[spec.difficulty] != OFFSETS[parent.difficulty] + 1
                    ):
                        raise SkillError("skill.specialty", "Invalid optional specialty parent")
                    refs.add(specialty.optional_parent)
            if spec.technique is not None:
                technique = spec.technique
                parent = self.specs.get(technique.parent)
                if (
                    parent is None
                    or parent.technique is not None
                    or spec.defaults
                    or spec.difficulty not in (Difficulty.AVERAGE, Difficulty.HARD)
                    or type(technique.default_modifier) is not int
                    or type(technique.maximum_modifier) is not int
                    or technique.default_modifier > technique.maximum_modifier
                    or spec.attribute != parent.attribute
                ):
                    raise SkillError("skill.technique", "Unsupported technique definition")
                refs.add(technique.parent)
            if any(ref not in self.specs or self.specs[ref].technique is not None for ref in refs):
                raise SkillError(
                    "skill.reference", f"Missing or unsupported skill reference: {key}"
                )
            if any(
                ref in self.specs and self.specs[ref].technique is not None for ref in default_refs
            ):
                raise SkillError(
                    "skill.reference", f"Missing or unsupported skill reference: {key}"
                )
            # Defaults are deliberately absent from this graph. Basic Set
            # specialties commonly default to one another; their nonpositive
            # modifiers are resolved to a stable level during compilation.
            graph[key] = refs
        try:
            self.order = tuple(TopologicalSorter(graph).static_order())
        except CycleError as exc:
            raise SkillError("skill.cycle", "Cyclic skill definitions are unsupported") from exc
        default_graph = {
            key: {
                default.target
                for default in spec.defaults
                if default.target not in ControllingAttribute and default.target in self.specs
            }
            for key, spec in self.specs.items()
        }

        def reaches(start: str, goal: str) -> bool:
            pending = [start]
            visited: set[str] = set()
            while pending:
                current = pending.pop()
                if current == goal:
                    return True
                if current in visited:
                    continue
                visited.add(current)
                pending.extend(default_graph.get(current, set()) - visited)
            return False

        # Within a reciprocal component, only the purchased native level is a
        # default source. Otherwise B173 point credit could feed around the
        # cycle forever. Final learned levels still propagate out of the
        # component to ordinary one-way defaults.
        self.reciprocal_defaults = frozenset(
            (key, target)
            for key, targets in default_graph.items()
            for target in targets
            if reaches(target, key)
        )

    def _conditions_satisfied(
        self,
        skill_id: str,
        spec: SkillSpec,
        default: SkillDefault,
        context: DefaultContext,
    ) -> bool:
        for condition in default.conditions:
            if condition.kind is DefaultConditionKind.MATCHING_TECHNOLOGY_LEVEL:
                own_tl = context.technology_levels.get(skill_id)
                target_tl = context.technology_levels.get(default.target)
                if own_tl is None or own_tl != target_tl:
                    return False
            elif condition.kind is DefaultConditionKind.MATCHING_SPECIALTY:
                target = self.specs.get(default.target)
                if (
                    spec.specialty is None
                    or target is None
                    or target.specialty is None
                    or spec.specialty.name != target.specialty.name
                ):
                    return False
            elif condition.kind is DefaultConditionKind.REQUIRED_EQUIPMENT:
                if condition.value not in context.equipment_ids:
                    return False
            else:
                raise SkillError("skill.definition", "Unsupported default condition")
        return True

    def compile(
        self,
        points: Mapping[str, int],
        values: Mapping[str, Decimal],
        adjust: Callable[[str, int], int] | None = None,
        default_context: DefaultContext | None = None,
    ) -> tuple[SkillLevel, ...]:
        if any(
            key not in self.available or type(p) is not int or p < 1 for key, p in points.items()
        ):
            raise SkillError("skill.points", "Unknown skill or invalid point allocation")
        attributes: dict[str, int] = {}
        for attribute_id in ControllingAttribute:
            value = values.get(attribute_id)
            if value is None or not value.is_finite() or value != value.to_integral_value():
                raise SkillError(
                    "skill.attribute", f"Missing whole-number attribute: {attribute_id}"
                )
            attributes[attribute_id] = int(value)
        levels: dict[str, SkillLevel] = {}
        context = default_context or DefaultContext.empty()

        if any(
            not isinstance(key, str) or type(level) is not int or level < 0
            for key, level in context.technology_levels.items()
        ):
            raise SkillError("skill.context", "Technology levels need nonnegative integers")
        if any(
            not isinstance(identifier, str) or not identifier
            for identifier in context.equipment_ids
        ):
            raise SkillError("skill.context", "Equipment context needs definition IDs")
        if any(
            not isinstance(identifier, str) or not identifier
            for identifier in context.purchased_definition_ids | context.capabilities
        ):
            raise SkillError("skill.context", "Acquisition context needs nonempty identifiers")
        if context.campaign_technology_level is not None and (
            type(context.campaign_technology_level) is not int
            or context.campaign_technology_level < 0
        ):
            raise SkillError("skill.context", "Campaign technology level must be nonnegative")

        def adjusted(result: SkillLevel) -> SkillLevel:
            level = result.level if adjust is None else adjust(result.target, result.level)
            if type(level) is not int:
                raise SkillError("skill.level", "Skill effects must produce whole-number levels")
            return replace(result, level=level, unmodified=result.level)

        # Optional specialties have an implicit reverse default. Use purchased
        # native levels for that edge so it cannot feed its own default back.
        native = {
            key: attributes[self.specs[key].attribute]
            + relative_level(self.specs[key].difficulty, p)
            for key, p in points.items()
            if self.specs[key].technique is None
        }
        default_native = {
            key: level if adjust is None else adjust(key, level) for key, level in native.items()
        }

        def ordinary(key: str) -> SkillLevel | None:
            spec = self.specs[key]
            paid = points.get(key, 0)
            candidates: list[tuple[int, str, bool, tuple[DefaultCondition, ...]]] = []
            for default in spec.defaults:
                if not self._conditions_satisfied(key, spec, default, context):
                    continue
                if default.target in attributes:
                    candidates.append(
                        (
                            min(20, attributes[default.target]) + default.modifier,
                            default.target,
                            False,
                            default.conditions,
                        )
                    )
                elif default.target in points and default.target in levels:
                    source_level = (
                        default_native[default.target]
                        if (key, default.target) in self.reciprocal_defaults
                        else levels[default.target].level
                    )
                    candidates.append(
                        (
                            source_level + default.modifier,
                            default.target,
                            True,
                            default.conditions,
                        )
                    )
            if spec.specialty is not None and spec.specialty.optional_parent in native:
                parent_id = spec.specialty.optional_parent
                assert parent_id is not None
                candidates.append((default_native[parent_id] - 2, parent_id, True, ()))
            for other, other_spec in self.specs.items():
                if (
                    other in native
                    and other_spec.specialty is not None
                    and other_spec.specialty.optional_parent == key
                ):
                    candidates.append((default_native[other] - 2, other, True, ()))
            attribute = attributes[spec.attribute]
            # B173: skill defaults grant point-equivalent credit; attribute
            # defaults do not. Partial investment remains recorded without rounding up.
            options = [
                (level, target, 0, conditions) for level, target, _, conditions in candidates
            ]
            if paid:
                options.append((native[key], "", 0, ()))
                if self.profile_id == BASIC:
                    for level, target, skill_default, conditions in candidates:
                        credit = _credit(spec.difficulty, level - attribute) if skill_default else 0
                        if credit:
                            options.append(
                                (
                                    attribute + relative_level(spec.difficulty, paid + credit),
                                    target,
                                    credit,
                                    conditions,
                                )
                            )
            if options:
                level, target, credit, conditions = max(
                    options, key=lambda x: (x[0], x[1] == "", x[1])
                )
                return adjusted(
                    SkillLevel(key, level, paid, target or None, credit, None, conditions)
                )
            return None

        def satisfied(requirement: SkillPrerequisite) -> bool:
            threshold = requirement.minimum_technology_level
            if threshold is not None:
                if context.campaign_technology_level is None:
                    return False
                if context.campaign_technology_level < threshold:
                    return True
            target = requirement.target
            if requirement.kind is PrerequisiteKind.TRAINED_SKILL:
                return (
                    target in points
                    and target in levels
                    and levels[target].level >= requirement.minimum
                )
            if requirement.kind is PrerequisiteKind.PURCHASED_DEFINITION:
                return target in context.purchased_definition_ids
            if requirement.kind is PrerequisiteKind.CAPABILITY:
                return target in context.capabilities
            raise SkillError("skill.definition", "Unsupported acquisition prerequisite")

        # Reciprocal defaults are legitimate source data. Start with native and
        # attribute-default anchors, then propagate purchased-skill defaults to
        # a fixed point. Nonpositive modifiers make levels bounded; provenance
        # uses the same deterministic tie-break as an acyclic compilation.
        ordinary_keys = tuple(
            key for key in self.order if key in self.available and self.specs[key].technique is None
        )
        for _ in range(len(ordinary_keys) + 1):
            changed = False
            for key in ordinary_keys:
                spec = self.specs[key]
                prerequisites = all(satisfied(p) for p in spec.prerequisites) and all(
                    any(satisfied(p) for p in group.alternatives)
                    for group in spec.prerequisite_groups
                )
                if not prerequisites:
                    continue
                result = ordinary(key)
                if result is not None and levels.get(key) != result:
                    levels[key] = result
                    changed = True
            if not changed:
                break
        else:
            raise SkillError("skill.cycle", "Skill defaults did not resolve to a stable level")

        for key in ordinary_keys:
            if key in points and key not in levels:
                raise SkillError("skill.prerequisite", f"Missing trained prerequisite: {key}")

        # Techniques cannot be default sources or prerequisites. Their parent
        # levels are stable after the ordinary-skill fixed point.
        for key in self.order:
            if key not in self.available or self.specs[key].technique is None:
                continue
            spec = self.specs[key]
            paid = points.get(key, 0)
            technique = spec.technique
            assert technique is not None
            parent = levels.get(technique.parent)
            if parent is None or technique.parent not in points:
                if paid:
                    raise SkillError(
                        "skill.prerequisite", f"Technique needs a trained parent: {key}"
                    )
                continue
            if paid == 1 and spec.difficulty is Difficulty.HARD:
                raise SkillError("technique.points", "Hard techniques start at two points")
            improvement = paid - (1 if paid and spec.difficulty is Difficulty.HARD else 0)
            modifier = technique.default_modifier + improvement
            if modifier > technique.maximum_modifier:
                raise SkillError(
                    "technique.cap", f"Technique exceeds its parent-relative cap: {key}"
                )
            levels[key] = adjusted(SkillLevel(key, parent.level + modifier, paid, technique.parent))
            if levels[key].level > parent.level + technique.maximum_modifier:
                raise SkillError("technique.cap", f"Technique effects exceed its cap: {key}")
        return tuple(levels[key] for key in sorted(levels))
