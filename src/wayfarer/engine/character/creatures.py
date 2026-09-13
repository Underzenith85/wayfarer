"""Creature templates compiled through the authoritative character compiler."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.rules.types.creature import (
    Creature,
    CreatureStatistics,
    CreatureTemplate,
    CreatureVariation,
    PointProvenance,
)
from wayfarer.errors import ValidationError


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()


@dataclass(frozen=True, slots=True)
class CreatureCompilation:
    creature: Creature
    draft: CharacterDraft


class CreatureCatalog:
    """Trusted species defaults; individuals may replace only declared facts."""

    def __init__(
        self, templates: tuple[CreatureTemplate, ...], compiler: CharacterCompiler
    ) -> None:
        if compiler.statistics_profile != "gurps-basic-set-4e-2004":
            raise ValidationError("Creatures require the exact Basic Set statistics profile")
        checked = tuple(CreatureTemplate.model_validate(value) for value in templates)
        self.templates = {entry.id: entry for entry in checked}
        if len(self.templates) != len(checked):
            raise ValidationError("Duplicate creature template identifier")
        self.compiler = compiler
        self.digest = _digest(
            [self.templates[key].model_dump(mode="json") for key in sorted(self.templates)]
        )

    @staticmethod
    def _purchases(statistics: CreatureStatistics) -> tuple[Purchase, ...]:
        ground = statistics.move("ground")
        return tuple(
            Purchase(definition_id=identifier, amount=amount)
            for identifier, amount in (
                ("attribute:st", statistics.st),
                ("attribute:dx", statistics.dx),
                ("attribute:iq", statistics.iq),
                ("attribute:ht", statistics.ht),
                ("secondary:hp", statistics.hp),
                ("secondary:will", statistics.will),
                ("secondary:per", statistics.per),
                ("secondary:fp", statistics.fp),
                ("secondary:basic-speed", statistics.basic_speed_quarters),
                ("secondary:basic-move", ground.ordinary_move),
            )
        )

    @staticmethod
    def _vary_statistics(
        base: CreatureStatistics, variation: CreatureVariation
    ) -> CreatureStatistics:
        changes = {
            key: value
            for key in (
                "st",
                "dx",
                "iq",
                "ht",
                "hp",
                "will",
                "per",
                "fp",
                "basic_speed_quarters",
            )
            if (value := getattr(variation, key)) is not None
        }
        if variation.ground_move is not None:
            changes["movement"] = tuple(
                entry.model_copy(update={"ordinary_move": variation.ground_move})
                if entry.mode == "ground"
                else entry
                for entry in base.movement
            )
        return base.model_copy(update=changes)

    def compile(
        self,
        actor_id: str,
        name: str,
        template_id: str,
        variation: CreatureVariation | None = None,
    ) -> CreatureCompilation:
        template = self.templates.get(template_id)
        if template is None:
            raise ValidationError("Unknown creature template")
        variation = (
            CreatureVariation()
            if variation is None
            else CreatureVariation.model_validate(variation)
        )
        statistics = self._vary_statistics(template.statistics, variation)
        draft = CharacterDraft(name=name, purchases=self._purchases(statistics))
        compilation = self.compiler.compile(draft)
        if compilation.build is None:
            codes = ", ".join(sorted({entry.code for entry in compilation.diagnostics}))
            raise ValidationError("Creature character compilation failed: " + codes)
        base_traits = {entry.id: entry for entry in template.traits}
        if set(variation.remove_trait_ids) - base_traits.keys():
            raise ValidationError("Individual variation removes an unknown species trait")
        for identifier in variation.remove_trait_ids:
            del base_traits[identifier]
        for entry in variation.add_traits:
            if entry.id in base_traits:
                raise ValidationError("Individual variation duplicates a species trait")
            base_traits[entry.id] = entry
        skills = {entry.id: entry for entry in template.skills}
        skills.update((entry.id, entry) for entry in variation.skill_levels)
        mentality = variation.mentality or (
            "domestic" if template.mentality == "domestic" else "wild"
        )
        if template.mentality != "either" and variation.mentality not in (
            None,
            template.mentality,
        ):
            raise ValidationError("This species does not permit a mentality interchange")
        varied_fields = {
            key
            for key in (
                "st",
                "dx",
                "iq",
                "ht",
                "hp",
                "will",
                "per",
                "fp",
                "basic_speed_quarters",
            )
            if getattr(variation, key) is not None
        }
        if variation.ground_move is not None:
            varied_fields.add("basic-move")
        field_by_definition = {
            "attribute:st": "st",
            "attribute:dx": "dx",
            "attribute:iq": "iq",
            "attribute:ht": "ht",
            "secondary:hp": "hp",
            "secondary:will": "will",
            "secondary:per": "per",
            "secondary:fp": "fp",
            "secondary:basic-speed": "basic_speed_quarters",
            "secondary:basic-move": "basic-move",
        }
        provenance = tuple(
            PointProvenance(
                definition_id=entry.definition_id,
                amount=entry.amount,
                point_cost=entry.cost,
                origin=(
                    "individual"
                    if field_by_definition[entry.definition_id] in varied_fields
                    else "template"
                ),
                rules_package_id=self.compiler.definition_packages[entry.definition_id][0],
                rules_package_version=self.compiler.definition_packages[entry.definition_id][1],
                reference=template.reference,
            )
            for entry in compilation.build.purchases
        )
        template_digest = _digest(template.model_dump(mode="json"))
        individual_payload = {
            "actor_id": actor_id,
            "name": name,
            "template_digest": template_digest,
            "variation": variation.model_dump(mode="json"),
            "build_revision": compilation.build.revision,
        }
        creature = Creature(
            actor_id=actor_id,
            name=name,
            template_id=template.id,
            template_digest=template_digest,
            individual_digest=_digest(individual_payload),
            build_revision=compilation.build.revision,
            reference=template.reference,
            kind=template.kind,
            mentality=mentality,
            statistics=statistics,
            traits=tuple(base_traits.values()),
            skills=tuple(sorted(skills.values(), key=lambda entry: entry.id)),
            attacks=template.attacks,
            commands=template.commands,
            mount=template.mount,
            point_total=compilation.build.spent,
            point_provenance=provenance,
        )
        return CreatureCompilation(creature, draft)
