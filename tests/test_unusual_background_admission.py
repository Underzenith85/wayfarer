"""B96 GM decisions change real point construction, pins and approval eligibility."""

import asyncio
import json
from dataclasses import asdict, replace
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import open_store, played, seed_play
from test_actions import campaign, world
from test_mundane_traits import combined_package, runtime_compiler
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import CharacterCompiler, CharacterDraft, Purchase
from wayfarer.engine.character.power import Approval, CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.character.traits import mundane_trait_effects
from wayfarer.engine.rules.catalog import (
    CampaignRules,
    ImplementationStatus,
    PackagePin,
    RulesCatalog,
    RulesPackage,
)
from wayfarer.engine.rules.skills import gurps_skills
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane.complete import SPEC_BY_ID
from wayfarer.engine.rules.traits.mundane.runtime import SUPPORTED_HOOKS
from wayfarer.engine.rules.traits.unusual_background import bind_unusual_background
from wayfarer.engine.rules.types.background_admission import (
    UNUSUAL_BACKGROUND_ID,
    UnusualBackgroundDecision,
)
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import ApproveCharacter, PlayService

BENEFIT = "trait:advantage:trained-by-a-master"
PROFILE = "gurps-basic-set-4e-2004"


def decision(cost: int = 10, *, allowed: bool = True) -> UnusualBackgroundDecision:
    return UnusualBackgroundDecision(
        id="rare-cinematic-training",
        gm_id="gm",
        description="The GM permits rare cinematic training through a unique monastery",
        point_cost=cost,
        benefits=(BENEFIT,),
        allowed=allowed,
    )


def configured(selected: UnusualBackgroundDecision) -> tuple[RulesPackage, PowerReviewer]:
    package = bind_unusual_background(
        combined_package(), selected, package_id="package:b96", version="1"
    )
    policy = replace(runtime_compiler().policy, point_budget=1000, allow_supernatural=True)
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        CampaignRules(
            package.edition,
            (PackagePin(package.id, package.version, package.digest),),
            policy.id,
            policy.version,
        ),
        policy,
        statistics_profile=PROFILE,
        trait_runtime_hooks=SUPPORTED_HOOKS,
    )
    return package, PowerReviewer(compiler, PowerPolicy(id="b96", version=1), frozenset({"gm"}))


def proposal(*, background: bool = False, options: TraitOptions | None = None) -> CharacterProposal:
    return CharacterProposal(
        draft=gurps_draft(
            Purchase(definition_id=BENEFIT),
            *(
                (Purchase(definition_id=UNUSUAL_BACKGROUND_ID, trait=options),)
                if background
                else ()
            ),
        )
    )


@pytest.mark.parametrize("price", [10, 50, 150])
def test_gm_cost_is_additional_to_the_actual_benefit_and_requires_gm_approval(price: int) -> None:
    _, reviewer = configured(decision(price))
    missing = reviewer.review(proposal())
    assert missing.status == "illegal" and missing.compilation.build is None
    review = reviewer.review(proposal(background=True))
    assert review.status == "review" and review.compilation.spent == 30 + price
    assert review.compilation.build is not None
    assert (
        next(
            row.cost
            for row in review.compilation.build.purchases
            if row.definition_id == UNUSUAL_BACKGROUND_ID
        )
        == price
    )
    with pytest.raises(ValidationError, match="approval required"):
        reviewer.approve(proposal(background=True), campaign_id="c", actor_id="a", revision=0)
    with pytest.raises(ValidationError, match="authorized approver"):
        reviewer.approve(
            proposal(background=True),
            campaign_id="c",
            actor_id="a",
            revision=0,
            approver_id="a",
            reason="I choose my own price",
        )
    approved = reviewer.approve(
        proposal(background=True),
        campaign_id="c",
        actor_id="a",
        revision=0,
        approver_id="gm",
        reason="Approved the defined B96 benefit and surcharge",
    )
    built, _ = reviewer.activate(proposal(background=True), approved, campaign_id="c", actor_id="a")
    assert built.spent == 30 + price and approved.decision == "gm"
    assert all(
        effect.definition_id != UNUSUAL_BACKGROUND_ID
        for effect in mundane_trait_effects(built, reviewer.compiler.definitions)
    )


def test_same_benefit_is_common_charged_or_forbidden_by_the_gm_decision() -> None:
    _, common = configured(decision(0))
    _, rare = configured(decision(10))
    _, forbidden = configured(decision(50, allowed=False))
    value = proposal()
    assert common.review(value).status == "automatic"
    assert common.review(value).compilation.spent == 30
    assert rare.review(value).status == "illegal"
    assert forbidden.review(value).status == "illegal"
    assert forbidden.review(proposal(background=True)).status == "illegal"
    assert common.review(proposal(background=True)).status == "illegal"


@pytest.mark.parametrize("price", [0, 1, 9, 10, 50, 100])
def test_player_cannot_choose_even_a_matching_price(price: int) -> None:
    _, reviewer = configured(decision(10))
    review = reviewer.review(
        proposal(background=True, options=TraitOptions(parameters=(("point-cost", price),)))
    )
    assert review.status == "illegal" and review.compilation.build is None


def test_background_color_without_a_current_tangible_benefit_cannot_gain_approval() -> None:
    _, reviewer = configured(decision())
    value = CharacterProposal(draft=gurps_draft(Purchase(definition_id=UNUSUAL_BACKGROUND_ID)))
    assert reviewer.review(value).status == "blocked"
    assert any(f.code == "background.no_benefit" for f in reviewer.review(value).findings)
    with pytest.raises(ValidationError, match="does not permit"):
        reviewer.approve(
            value,
            campaign_id="c",
            actor_id="a",
            revision=0,
            approver_id="gm",
            reason="No benefit exists",
        )


def test_json_restart_preserves_the_gm_decision_cost_pin_and_approval() -> None:
    original = decision(50)
    package, reviewer = configured(original)
    payload = json.loads(package.canonical_json())
    restored = UnusualBackgroundDecision.model_validate_json(
        json.dumps(payload["unusual_background"])
    )
    rebuilt_package, restarted = configured(restored)
    assert restored == original and rebuilt_package.digest == package.digest
    approval = reviewer.approve(
        proposal(background=True),
        campaign_id="c",
        actor_id="a",
        revision=0,
        approver_id="gm",
        reason="Pinned B96 source decision",
    )
    saved = Approval.model_validate_json(approval.model_dump_json())
    build, _ = restarted.activate(proposal(background=True), saved, campaign_id="c", actor_id="a")
    assert build.spent == 80 and saved == approval
    _, changed = configured(decision(10))
    with pytest.raises(ValidationError, match="stale"):
        changed.activate(proposal(background=True), saved, campaign_id="c", actor_id="a")
    with pytest.raises(ValidationError, match="does not resolve"):
        RulesCatalog((rebuilt_package,)).package(PackagePin(package.id, package.version, "0" * 64))


def test_cost_benefit_author_and_narrative_changes_are_pinned() -> None:
    package, _ = configured(decision())
    for changed in (
        decision(11),
        decision(10, allowed=False),
        decision().model_copy(update={"description": "A different GM-approved benefit"}),
    ):
        other, _ = configured(changed)
        assert other.digest != package.digest
    with pytest.raises(ValidationError, match="trusted campaign GM"):
        configured(decision().model_copy(update={"gm_id": "player"}))
    with pytest.raises(SchemaError):
        CharacterDraft.model_validate({"name": "a", "unusual_background": decision().model_dump()})


def test_invalid_or_unbound_constructions_fail_closed() -> None:
    unbound = runtime_compiler().compile(
        gurps_draft(
            Purchase(
                definition_id=UNUSUAL_BACKGROUND_ID,
                trait=TraitOptions(parameters=(("point-cost", 1),)),
            )
        )
    )
    assert unbound.build is None
    assert SPEC_BY_ID[UNUSUAL_BACKGROUND_ID].family == "resources"
    assert (
        runtime_compiler().definitions[UNUSUAL_BACKGROUND_ID].status
        is ImplementationStatus.UNSUPPORTED
    )
    with pytest.raises(SchemaError):
        UnusualBackgroundDecision.model_validate({**decision().model_dump(), "point_cost": True})
    with pytest.raises(SchemaError):
        UnusualBackgroundDecision.model_validate(
            {**decision().model_dump(), "benefits": (BENEFIT, BENEFIT)}
        )
    base = combined_package()
    with pytest.raises(ValidationError, match="explicit new package"):
        bind_unusual_background(base, decision(), package_id=base.id, version=base.version)
    with pytest.raises(ValidationError, match="existing implemented"):
        bind_unusual_background(
            base,
            decision().model_copy(update={"benefits": ("unknown",)}),
            package_id="new",
            version="1",
        )
    package, _ = configured(decision())
    with pytest.raises(ValidationError, match="pinned GM decision"):
        RulesCatalog((replace(package, unusual_background=None),))
    damaged = replace(
        package,
        definitions=tuple(
            replace(d, point_cost=1) if d.id == UNUSUAL_BACKGROUND_ID else d
            for d in package.definitions
        ),
    )
    with pytest.raises(ValidationError, match="price and construction"):
        RulesCatalog((damaged,))


def test_disabled_extension_preserves_old_package_serialization() -> None:
    package = combined_package()
    serialized = json.loads(package.canonical_json())
    assert "unusual_background" not in serialized
    assert "unusual_background" in asdict(package)
    original = next(d for d in package.definitions if d.id == UNUSUAL_BACKGROUND_ID)
    configured_package, _ = configured(decision())
    bound = next(d for d in configured_package.definitions if d.id == UNUSUAL_BACKGROUND_ID)
    assert (bound.id, bound.source_id, bound.name) == (
        original.id,
        original.source_id,
        original.name,
    )
    assert configured_package.sources == package.sources


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_durable_approval_retries_restart_and_campaign_change(
    tmp_path: Path, backend: str
) -> None:
    def reducer(selected: UnusualBackgroundDecision) -> ActionEngine:
        package, reviewer = configured(selected)
        compiler = reviewer.compiler
        resources = ResourceEngine(
            world(), RulesCatalog((package,)), compiler.rules, compiler.policy, ()
        )
        return ActionEngine(reviewer, resources, ActionRules(id="b96-actions", version=1))

    # The trusted host persists its exact campaign decision rather than relying
    # on an in-memory fixture or accepting an authoring field in a player command.
    artifact = tmp_path / "campaign-background.json"
    artifact.write_text(decision(50).model_dump_json())
    original = reducer(UnusualBackgroundDecision.model_validate_json(artifact.read_text()))
    store = open_store(tmp_path, backend=backend)
    service = PlayService(store, original)
    initial = campaign(original)
    state = await seed_play(
        service,
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=proposal(background=True)),),
    )
    assert state.actors[0].approval is None
    pending = await service.execute(
        initial["id"],
        Wait(id="pending", actor_id="a", expected_revision=0, ticks=1),
        principal_id="a",
    )
    assert pending.code == "character.approval_required"
    command = ApproveCharacter(
        id="approve-background",
        actor_id="gm",
        target_actor_id="a",
        expected_revision=0,
        reason="GM approved the B96 background and its additional 50 points",
    )
    with pytest.raises(ValidationError, match="authority"):
        await service.approve(initial["id"], command, principal_id="a")
    other = PlayService(store, original)
    records = await asyncio.gather(
        service.approve(initial["id"], command, principal_id="gm"),
        other.approve(initial["id"], command, principal_id="gm"),
    )
    assert records[0] == records[1] and records[0].recorded_revision == 1
    rebuilt = reducer(UnusualBackgroundDecision.model_validate_json(artifact.read_text()))
    restarted = PlayService(store, rebuilt)
    assert await restarted.approve(initial["id"], command, principal_id="gm") == records[0]
    active = await restarted.execute(
        initial["id"],
        Wait(id="active", actor_id="a", expected_revision=1, ticks=1),
        principal_id="a",
    )
    assert active.status == "committed"
    assert len(await played(store, initial["id"])) == 2
    assert await store.replay(initial["id"]) == await store.read(initial["id"])
    with pytest.raises(ConflictError):
        await restarted.approve(
            initial["id"],
            command.model_copy(update={"reason": "Changed decision"}),
            principal_id="gm",
        )
    changed = PlayService(store, reducer(decision(10)))
    with pytest.raises(ValidationError, match="rules do not match"):
        await changed.preview(
            initial["id"],
            Wait(id="changed", actor_id="a", expected_revision=2, ticks=1),
            principal_id="a",
        )
    assert len(await played(store, initial["id"])) == 2


def test_admission_block_cannot_be_overridden_as_campaign_suitability() -> None:
    _, reviewer = configured(decision())
    override = PowerReviewer(
        reviewer.compiler,
        reviewer.policy.model_copy(update={"allow_gm_overrides": True}),
        reviewer.gm_ids,
    )
    no_benefit = CharacterProposal(draft=gurps_draft(Purchase(definition_id=UNUSUAL_BACKGROUND_ID)))
    with pytest.raises(ValidationError, match="does not permit"):
        override.approve(
            no_benefit,
            campaign_id="c",
            actor_id="a",
            revision=0,
            approver_id="gm",
            reason="Attempt suitability override",
        )
    _, reviewer = configured(decision(10, allowed=False))
    with pytest.raises(ValidationError, match="Point-illegal"):
        reviewer.approve(
            proposal(background=True),
            campaign_id="c",
            actor_id="a",
            revision=0,
            approver_id="gm",
            reason="Attempt admission override",
        )


@pytest.mark.parametrize("allowed", [False, True])
def test_skill_admission_is_explicitly_unsupported_including_defaults(allowed: bool) -> None:
    base = combined_package()
    package = replace(base, definitions=base.definitions + gurps_skills.definitions(PROFILE))
    selected = UnusualBackgroundDecision(
        id="rare-stealth",
        gm_id="gm",
        description="A skill admission needs default gating",
        point_cost=10,
        benefits=("skill:stealth",),
        allowed=allowed,
    )
    with pytest.raises(ValidationError, match="existing implemented trait rows"):
        bind_unusual_background(package, selected, package_id="skill-background", version="1")
