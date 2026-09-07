"""Profile-selected GURPS skill compilation (B168-173, B229-232; Lite 13-14).

No request supplies mechanics. Specs come from exact pinned catalog definitions.
Default-only skills cannot be links in a default chain (B173). Purchased links
are evaluated in dependency order; cycles fail closed, including reciprocal
catalog defaults, which require a separately reviewed default-direction model.
"""

from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from decimal import Decimal
from graphlib import CycleError, TopologicalSorter

from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import DefinitionKind, ImplementationStatus, RuleDefinition
from wayfarer.rules.conformance import profile, require_capabilities
from wayfarer.rules.skill_types import ControllingAttribute, Difficulty, SkillSpec

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
            refs = {d.target for d in spec.defaults if d.target not in ControllingAttribute}
            refs.update(p.target for p in spec.prerequisites)
            if any(type(d.modifier) is not int or d.modifier > 0 for d in spec.defaults):
                raise SkillError("skill.definition", "Defaults need integer nonpositive modifiers")
            if any(type(p.minimum) is not int or p.minimum < 1 for p in spec.prerequisites):
                raise SkillError("skill.definition", "Prerequisites need positive integer levels")
            if len({d.target for d in spec.defaults}) != len(spec.defaults):
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
            graph[key] = refs
        try:
            self.order = tuple(TopologicalSorter(graph).static_order())
        except CycleError as exc:
            raise SkillError("skill.cycle", "Cyclic skill definitions are unsupported") from exc

    def compile(
        self,
        points: Mapping[str, int],
        values: Mapping[str, Decimal],
        adjust: Callable[[str, int], int] | None = None,
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

        def record(result: SkillLevel) -> None:
            level = result.level if adjust is None else adjust(result.target, result.level)
            if type(level) is not int:
                raise SkillError("skill.level", "Skill effects must produce whole-number levels")
            levels[result.target] = replace(result, level=level, unmodified=result.level)

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
        for key in self.order:
            if key not in self.available:
                continue
            spec = self.specs[key]
            paid = points.get(key, 0)
            prerequisites = all(
                p.target in points and p.target in levels and levels[p.target].level >= p.minimum
                for p in spec.prerequisites
            )
            if not prerequisites:
                if paid:
                    raise SkillError("skill.prerequisite", f"Missing trained prerequisite: {key}")
                continue
            if spec.technique is not None:
                technique = spec.technique
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
                record(SkillLevel(key, parent.level + modifier, paid, technique.parent))
                if levels[key].level > parent.level + technique.maximum_modifier:
                    raise SkillError("technique.cap", f"Technique effects exceed its cap: {key}")
                continue
            candidates: list[tuple[int, str, bool]] = []
            for default in spec.defaults:
                if default.target in attributes:
                    candidates.append(
                        (
                            min(20, attributes[default.target]) + default.modifier,
                            default.target,
                            False,
                        )
                    )
                elif default.target in points and default.target in levels:
                    candidates.append(
                        (levels[default.target].level + default.modifier, default.target, True)
                    )
            if spec.specialty is not None and spec.specialty.optional_parent in native:
                parent_id = spec.specialty.optional_parent
                assert parent_id is not None
                candidates.append((default_native[parent_id] - 2, parent_id, True))
            for other, other_spec in self.specs.items():
                if (
                    other in native
                    and other_spec.specialty is not None
                    and other_spec.specialty.optional_parent == key
                ):
                    candidates.append((default_native[other] - 2, other, True))
            attribute = attributes[spec.attribute]
            # B173: skill defaults grant point-equivalent credit; attribute
            # defaults do not. Partial investment remains recorded without rounding up.
            options = [(level, target, 0) for level, target, _ in candidates]
            if paid:
                options.append((native[key], "", 0))
                if self.profile_id == BASIC:
                    for level, target, skill_default in candidates:
                        credit = _credit(spec.difficulty, level - attribute) if skill_default else 0
                        if credit:
                            options.append(
                                (
                                    attribute + relative_level(spec.difficulty, paid + credit),
                                    target,
                                    credit,
                                )
                            )
            if options:
                level, target, credit = max(options, key=lambda x: (x[0], x[1] == "", x[1]))
                record(SkillLevel(key, level, paid, target or None, credit))
        return tuple(levels[key] for key in sorted(levels))
