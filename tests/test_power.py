"""Point legality, suitability and approvals are independent gates."""

from dataclasses import replace
from decimal import Decimal

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.character.power import (
    Approval,
    CapabilityBenchmark,
    CharacterProposal,
    ConcentrationLimit,
    CustomConstruction,
    ForbiddenCombination,
    PowerPolicy,
    PowerReviewer,
)
from wayfarer.errors import ValidationError
from wayfarer.rules.catalog import (
    DEFAULT_POLICY,
    DEFAULT_RULES,
    PROTOTYPE_PACKAGE,
    PROTOTYPE_SOURCE,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.rules.effects import Effect, Operation


def reviewer(policy: PowerPolicy | None = None) -> PowerReviewer:
    gifts = tuple(
        RuleDefinition(
            key,
            DefinitionKind.TRAIT,
            key,
            PROTOTYPE_SOURCE.id,
            5,
            ImplementationStatus.IMPLEMENTED,
            hooks=("custom",) if key == "custom" else (),
        )
        for key in ("gift", "other", "custom", "unbound")
    )
    package = replace(PROTOTYPE_PACKAGE, definitions=PROTOTYPE_PACKAGE.definitions + gifts)
    rules = replace(
        DEFAULT_RULES, packages=(PackagePin(package.id, package.version, package.digest),)
    )
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        rules,
        DEFAULT_POLICY,
        effects=tuple(
            (key, Effect(key, "attribute:st", Operation.ADD, Decimal(1), key, package.version))
            for key in ("gift", "other", "custom")
        ),
    )
    return PowerReviewer(compiler, policy or PowerPolicy(id="test", version=1), frozenset({"gm"}))


def proposal(
    *keys: str, strength: int = 10, custom: tuple[CustomConstruction, ...] = ()
) -> CharacterProposal:
    return CharacterProposal(
        draft=CharacterDraft(
            name="Mira",
            purchases=tuple(
                Purchase(definition_id=f"attribute:{key}", amount=strength if key == "st" else 10)
                for key in ("st", "dx", "iq", "ht")
            )
            + tuple(Purchase(definition_id=key) for key in keys),
        ),
        custom=custom,
    )


def test_legal_build_can_be_policy_blocked_and_not_automatically_activated() -> None:
    engine = reviewer(
        PowerPolicy(
            id="test",
            version=1,
            forbidden=(ForbiddenCombination(id="combo", definitions=("gift", "other")),),
        )
    )
    value = proposal("gift", "other")
    result = engine.review(value)
    assert result.compilation.legal and result.status == "blocked"
    assert result.findings[0].code == "power.forbidden_combination"
    with pytest.raises(ValidationError, match="approval required"):
        engine.approve(value, campaign_id="c", actor_id="a", revision=0)
    with pytest.raises(ValidationError, match="no recorded"):
        engine.activate(value, None, campaign_id="c", actor_id="a")
    with pytest.raises(ValidationError, match="does not permit"):
        engine.approve(
            value, campaign_id="c", actor_id="a", revision=0, approver_id="gm", reason="override"
        )


def test_concentration_and_capability_findings_are_not_legality_errors() -> None:
    engine = reviewer(
        PowerPolicy(
            id="test",
            version=1,
            concentration=(
                ConcentrationLimit(
                    id="physical", definitions=("attribute:st", "gift"), maximum_points=10
                ),
            ),
            benchmarks=(CapabilityBenchmark(id="strength", target="attribute:st", maximum=11),),
        )
    )
    result = engine.review(proposal("gift", strength=11))
    assert result.compilation.legal and result.status == "review"
    assert {f.code for f in result.findings} == {"power.concentration", "power.capability"}


def test_gm_override_records_identity_reason_and_all_revisions() -> None:
    engine = reviewer(
        PowerPolicy(
            id="test",
            version=1,
            allow_gm_overrides=True,
            forbidden=(ForbiddenCombination(id="combo", definitions=("gift", "other")),),
        )
    )
    value = proposal("gift", "other")
    record = engine.approve(
        value,
        campaign_id="c",
        actor_id="a",
        revision=7,
        approver_id="gm",
        reason="Approved for this campaign",
    )
    assert record.decision == "override" and record.approver_id == "gm"
    assert record.reason == "Approved for this campaign" and record.recorded_revision == 7
    assert record.rules_revision and record.build_revision and record.policy_digest
    restored = Approval.model_validate_json(record.model_dump_json())
    build, runtime = engine.activate(value, restored, campaign_id="c", actor_id="a")
    assert build.revision == record.build_revision and runtime.hp == 12
    for campaign, actor in (("other", "a"), ("c", "other")):
        with pytest.raises(ValidationError, match="stale"):
            engine.activate(value, record, campaign_id=campaign, actor_id=actor)
    with pytest.raises(ValidationError):
        engine.activate(proposal("gift"), record, campaign_id="c", actor_id="a")
    changed = reviewer(engine.policy.model_copy(update={"version": 2}))
    with pytest.raises(ValidationError):
        changed.activate(value, record, campaign_id="c", actor_id="a")


@pytest.mark.parametrize("approver,reason", [("player", "please"), ("gm", " ")])
def test_only_named_gm_with_reason_can_approve(approver: str, reason: str) -> None:
    with pytest.raises(ValidationError, match="authorized"):
        reviewer().approve(
            proposal(),
            campaign_id="c",
            actor_id="a",
            revision=1,
            approver_id=approver,
            reason=reason,
        )


def test_custom_components_need_catalog_mechanics_and_explicit_approval() -> None:
    engine = reviewer()
    value = proposal("gift", custom=(CustomConstruction(id="ability", components=("gift",)),))
    assert engine.review(value).status == "review"
    with pytest.raises(ValidationError):
        engine.approve(value, campaign_id="c", actor_id="a", revision=0)
    record = engine.approve(
        value,
        campaign_id="c",
        actor_id="a",
        revision=1,
        approver_id="gm",
        reason="Reviewed component effect",
    )
    assert engine.activate(value, record, campaign_id="c", actor_id="a")[0].spent == 5
    assert engine.review(proposal("custom")).status == "review"
    for bad in (
        proposal("gift", custom=(CustomConstruction(id="x", components=("unknown",)),)),
        proposal("unbound", custom=(CustomConstruction(id="x", components=("unbound",)),)),
        proposal("gift", custom=(CustomConstruction(id="x", components=("gift", "gift")),)),
    ):
        assert engine.review(bad).status == "blocked"
        with pytest.raises(ValidationError):
            engine.approve(
                bad,
                campaign_id="c",
                actor_id="a",
                revision=0,
                approver_id="gm",
                reason="Cannot override mechanics",
            )
    with pytest.raises(SchemaError):
        CharacterProposal.model_validate({**value.model_dump(), "approval": record.model_dump()})


def test_illegal_characters_and_forged_approvals_cannot_activate() -> None:
    engine = reviewer()
    assert engine.review(proposal("unknown")).status == "illegal"
    with pytest.raises(ValidationError, match="Point-illegal"):
        engine.approve(
            proposal("unknown"),
            campaign_id="c",
            actor_id="a",
            revision=0,
            approver_id="gm",
            reason="No",
        )
    record = engine.approve(proposal(), campaign_id="c", actor_id="a", revision=0)
    with pytest.raises(ValidationError):
        engine.activate(
            proposal(),
            record.model_copy(update={"build_revision": "forged"}),
            campaign_id="c",
            actor_id="a",
        )
    payload = proposal().draft.model_copy(
        update={"purchases": (Purchase.model_construct(definition_id="attribute:st", amount=True),)}
    )
    assert not engine.compiler.compile(payload).legal


def test_policy_validation_and_explicit_approval_mode() -> None:
    with pytest.raises(ValidationError, match="references"):
        reviewer(
            PowerPolicy(
                id="test",
                version=1,
                concentration=(
                    ConcentrationLimit(id="x", definitions=("missing",), maximum_points=1),
                ),
            )
        )
    with pytest.raises(ValidationError, match="benchmark"):
        reviewer(
            PowerPolicy(
                id="test",
                version=1,
                benchmarks=(CapabilityBenchmark(id="x", target="missing", maximum=1),),
            )
        )
    engine = reviewer(PowerPolicy(id="test", version=1, automatic_approval=False))
    assert engine.review(proposal()).status == "review"
