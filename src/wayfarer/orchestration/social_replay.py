"""Private immutable resolver input for deterministic immediate social replay."""

import json

from pydantic import field_serializer

from wayfarer.engine.rules.skills.mundane.social.attempts import InterrogationCoercion
from wayfarer.engine.rules.skills.mundane.social.specialties import CampaignSocialSpecialties
from wayfarer.engine.rules.social.gurps_social import (
    DEFAULT_INFLUENCE_CONDITIONS,
    InfluenceConditions,
    InfluenceSkill,
    ReactionModifier,
)
from wayfarer.engine.rules.social.social_hooks import Standing
from wayfarer.engine.rules.traits.base import TraitOptions, TraitRules
from wayfarer.engine.rules.traits.mundane.runtime import DEFAULT_AUDIENCE, Audience
from wayfarer.engine.simulation.campaign.propaganda import PropagandaMediaContext
from wayfarer.engine.simulation.social.social import SocialContext, SocialDisclosure
from wayfarer.errors import ValidationError
from wayfarer.models import Record
from wayfarer.orchestration.social_generations import replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest


class CapturedSocialContext(Record):
    """Pre-binding values: approved traits are still derived at command execution."""

    profile_id: str
    target: int
    will: int = 10
    ht: int = 10
    skill: InfluenceSkill = "diplomacy"
    modifiers: tuple[ReactionModifier, ...] = ()
    required_fact_ids: tuple[str, ...] = ()
    trait_base: int = -5
    trait_levels: int = 1
    trait_options: TraitOptions | None = None
    trait_rules: TraitRules | None = None
    standing: Standing | None = None
    audience: Audience = DEFAULT_AUDIENCE
    self_control_modifier: int = 0
    influence_conditions: InfluenceConditions = DEFAULT_INFLUENCE_CONDITIONS
    procedure_id: str | None = None
    skill_level: int = 0
    partner_skill: int | None = None
    conditions: frozenset[str] = frozenset()
    medium_id: str | None = None
    media: PropagandaMediaContext | None = None
    campaign_specialties: CampaignSocialSpecialties | None = None
    coercion: InterrogationCoercion | None = None
    callous: bool = False

    @field_serializer("conditions")
    def ordered_conditions(self, value: frozenset[str]) -> tuple[str, ...]:
        return tuple(sorted(value))

    def resolve(self) -> SocialContext:
        return SocialContext(
            self.profile_id,
            self.target,
            will=self.will,
            ht=self.ht,
            skill=self.skill,
            modifiers=self.modifiers,
            required_fact_ids=self.required_fact_ids,
            trait_base=self.trait_base,
            trait_levels=self.trait_levels,
            trait_options=self.trait_options,
            trait_rules=self.trait_rules,
            standing=self.standing,
            audience=self.audience,
            self_control_modifier=self.self_control_modifier,
            influence_conditions=self.influence_conditions,
            procedure_id=self.procedure_id,
            skill_level=self.skill_level,
            partner_skill=self.partner_skill,
            conditions=self.conditions,
            medium_id=self.medium_id,
            media=self.media,
            campaign_specialties=self.campaign_specialties,
            coercion=self.coercion,
            callous=self.callous,
        )


class CapturedSocialSource(Record):
    context: CapturedSocialContext
    disclosure: SocialDisclosure


def captured_source(saved: CommandInput) -> CapturedSocialSource | None:
    if saved.text is None:
        return None
    if payload_digest({"input": saved.text}) != saved.payload_hash:
        raise ValidationError("Recorded command input does not match its digest")
    payload = replay_payload(saved.text)
    if not isinstance(payload, dict) or "social_source" not in payload:
        return None
    # Parsing through JSON preserves the strict tuple/dataclass field semantics.
    return CapturedSocialSource.model_validate_json(json.dumps(payload["social_source"]))
