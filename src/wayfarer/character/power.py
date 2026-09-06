"""Campaign suitability review, kept separate from point legality."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wayfarer.character.compiler import (
    CharacterCompiler,
    CharacterDraft,
    Compilation,
    RuntimeState,
    ValidatedBuild,
)
from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import DefinitionKind


class ReviewRecord(BaseModel):
    model_config = ConfigDict(
        extra="forbid", frozen=True, strict=True, revalidate_instances="always"
    )


class ForbiddenCombination(ReviewRecord):
    id: str = Field(min_length=1)
    definitions: tuple[str, ...] = Field(min_length=1)


class ConcentrationLimit(ReviewRecord):
    id: str = Field(min_length=1)
    definitions: tuple[str, ...] = Field(min_length=1)
    maximum_points: int = Field(ge=0)


class CapabilityBenchmark(ReviewRecord):
    id: str = Field(min_length=1)
    target: str
    maximum: int = Field(ge=0)


class PowerPolicy(ReviewRecord):
    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    automatic_approval: bool = True
    allow_gm_overrides: bool = False
    forbidden: tuple[ForbiddenCombination, ...] = ()
    concentration: tuple[ConcentrationLimit, ...] = ()
    benchmarks: tuple[CapabilityBenchmark, ...] = ()

    @property
    def digest(self) -> str:
        return hashlib.sha256(self.model_dump_json().encode()).hexdigest()


class CustomConstruction(ReviewRecord):
    """A named grouping of purchased catalog components, never new mechanics."""

    id: str = Field(min_length=1, max_length=100)
    components: tuple[str, ...] = Field(min_length=1, max_length=100, strict=False)


class CharacterProposal(ReviewRecord):
    draft: CharacterDraft
    custom: tuple[CustomConstruction, ...] = Field(default=(), strict=False, max_length=100)


class PowerFinding(ReviewRecord):
    code: str
    rule_id: str
    disposition: Literal["blocked", "review"]
    message: str
    overridable: bool = False


@dataclass(frozen=True, slots=True)
class PowerReview:
    compilation: Compilation
    findings: tuple[PowerFinding, ...]
    digest: str
    status: Literal["illegal", "blocked", "review", "automatic"]


class Approval(ReviewRecord):
    campaign_id: str
    actor_id: str
    build_revision: str
    review_digest: str
    rules_revision: str
    policy_digest: str
    approver_id: str
    decision: Literal["automatic", "gm", "override"]
    reason: str = Field(min_length=1, max_length=2000)
    recorded_revision: int = Field(ge=0)
    findings: tuple[str, ...] = ()


class PowerReviewer:
    def __init__(
        self, compiler: CharacterCompiler, policy: PowerPolicy, gm_ids: frozenset[str] = frozenset()
    ) -> None:
        self.compiler, self.policy, self.gm_ids = compiler, policy, gm_ids
        rules: tuple[ForbiddenCombination | ConcentrationLimit | CapabilityBenchmark, ...] = (
            *policy.forbidden,
            *policy.concentration,
            *policy.benchmarks,
        )
        if len({r.id for r in rules}) != len(rules):
            raise ValidationError("Duplicate power policy rule ID")
        groups: tuple[ForbiddenCombination | ConcentrationLimit, ...] = (
            *policy.forbidden,
            *policy.concentration,
        )
        for group in groups:
            if len(set(group.definitions)) != len(group.definitions) or any(
                key not in compiler.definitions for key in group.definitions
            ):
                raise ValidationError("Invalid power policy definition references")
        targets = {
            key
            for key, d in compiler.definitions.items()
            if d.kind in (DefinitionKind.ATTRIBUTE, DefinitionKind.SKILL)
        } | {e.target for _, e in compiler.effects}
        if any(b.target not in targets for b in policy.benchmarks):
            raise ValidationError("Unknown capability benchmark target")
        if "engine:auto" in gm_ids or any(not key.strip() for key in gm_ids):
            raise ValidationError("Invalid GM identity")

    def review(self, proposal: CharacterProposal) -> PowerReview:
        compilation = self.compiler.compile(proposal.draft)
        findings: list[PowerFinding] = []
        build = compilation.build
        if build is not None:
            selected = {p.definition_id for p in build.purchases}
            for rule in self.policy.forbidden:
                if set(rule.definitions) <= selected:
                    findings.append(
                        PowerFinding(
                            code="power.forbidden_combination",
                            rule_id=rule.id,
                            disposition="blocked",
                            message="Campaign forbids this combination",
                            overridable=True,
                        )
                    )
            for limit in self.policy.concentration:
                # Gross positive investment avoids discounts hiding concentration.
                points = sum(
                    max(0, p.cost) for p in build.purchases if p.definition_id in limit.definitions
                )
                if points > limit.maximum_points:
                    findings.append(
                        PowerFinding(
                            code="power.concentration",
                            rule_id=limit.id,
                            disposition="review",
                            message=f"Investment {points} exceeds {limit.maximum_points}",
                        )
                    )
            values = {v.target: v.value for v in build.sheet.values}
            for benchmark in self.policy.benchmarks:
                if values.get(benchmark.target, 0) > benchmark.maximum:
                    findings.append(
                        PowerFinding(
                            code="power.capability",
                            rule_id=benchmark.id,
                            disposition="review",
                            message="Capability exceeds the campaign benchmark",
                        )
                    )
            components: set[str] = set()
            names: set[str] = set()
            bound_effects = {key for key, _ in self.compiler.effects}
            for custom in proposal.custom:
                invalid = (
                    custom.id in names
                    or len(set(custom.components)) != len(custom.components)
                    or bool(components & set(custom.components))
                    or not set(custom.components) <= selected
                    or not set(custom.components) <= bound_effects
                )
                if invalid:
                    findings.append(
                        PowerFinding(
                            code="custom.invalid_components",
                            rule_id=custom.id,
                            disposition="blocked",
                            message="Custom components require distinct purchased catalog costs and effects",
                        )
                    )
                else:
                    findings.append(
                        PowerFinding(
                            code="custom.approval_required",
                            rule_id=custom.id,
                            disposition="review",
                            message="Custom constructions require recorded GM approval",
                        )
                    )
                names.add(custom.id)
                components.update(custom.components)
            for key in sorted(selected):
                if "custom" in self.compiler.definitions[key].hooks:
                    if key not in bound_effects:
                        findings.append(
                            PowerFinding(
                                code="custom.missing_effects",
                                rule_id=key,
                                disposition="blocked",
                                message="Custom definition has no catalog-backed effects",
                            )
                        )
                    else:
                        findings.append(
                            PowerFinding(
                                code="custom.approval_required",
                                rule_id=key,
                                disposition="review",
                                message="Custom definition requires GM approval",
                            )
                        )
            if not self.policy.automatic_approval:
                findings.append(
                    PowerFinding(
                        code="power.gm_required",
                        rule_id=self.policy.id,
                        disposition="review",
                        message="Campaign requires explicit GM approval",
                    )
                )
        status: Literal["illegal", "blocked", "review", "automatic"] = (
            "illegal"
            if build is None
            else "blocked"
            if any(f.disposition == "blocked" for f in findings)
            else "review"
            if findings
            else "automatic"
        )
        digest = hashlib.sha256(
            json.dumps(
                {
                    "proposal": proposal.model_dump(mode="json"),
                    "build": build.revision if build else None,
                    "policy": self.policy.digest,
                    "findings": [f.model_dump() for f in findings],
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return PowerReview(compilation, tuple(findings), digest, status)

    def approve(
        self,
        proposal: CharacterProposal,
        *,
        campaign_id: str,
        actor_id: str,
        revision: int,
        approver_id: str | None = None,
        reason: str = "",
    ) -> Approval:
        """Called only with trusted identity; approval records are persisted by the service."""
        review = self.review(proposal)
        build = review.compilation.build
        if build is None:
            raise ValidationError("Point-illegal characters cannot be approved")
        decision: Literal["automatic", "gm", "override"] = "automatic"
        if approver_id is None:
            if review.status != "automatic":
                raise ValidationError("Campaign power approval required")
            approver_id, reason = "engine:auto", "Within automatic campaign limits"
        else:
            if approver_id not in self.gm_ids or not reason.strip():
                raise ValidationError("GM approval requires an authorized approver and reason")
            blocked = tuple(f for f in review.findings if f.disposition == "blocked")
            if blocked and (
                not self.policy.allow_gm_overrides or any(not f.overridable for f in blocked)
            ):
                raise ValidationError("Campaign policy does not permit this override")
            decision = "override" if blocked else "gm"
        return Approval(
            campaign_id=campaign_id,
            actor_id=actor_id,
            build_revision=build.revision,
            review_digest=review.digest,
            rules_revision=hashlib.sha256(repr(build.rules).encode()).hexdigest(),
            policy_digest=self.policy.digest,
            approver_id=approver_id,
            decision=decision,
            reason=reason,
            recorded_revision=revision,
            findings=tuple(f"{f.code}:{f.rule_id}" for f in review.findings),
        )

    def activate(
        self,
        proposal: CharacterProposal,
        approval: Approval | None,
        *,
        campaign_id: str,
        actor_id: str,
    ) -> tuple[ValidatedBuild, RuntimeState]:
        """Approval must come from canonical storage, never the player command."""
        if approval is None:
            raise ValidationError("Character has no recorded power approval")
        expected = self.approve(
            proposal,
            campaign_id=campaign_id,
            actor_id=actor_id,
            revision=approval.recorded_revision,
            approver_id=None if approval.decision == "automatic" else approval.approver_id,
            reason=approval.reason,
        )
        if expected != approval:
            raise ValidationError("Power approval is stale or does not match this character")

        def authorize(build: ValidatedBuild) -> None:
            if build.revision != approval.build_revision:
                raise ValidationError("Approved build changed")

        return self.compiler.activate(proposal.draft, authorize=authorize)
