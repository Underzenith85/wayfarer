"""B96 campaign admission gates B170 defaults and B230-232 technique investment."""

from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import open_store, seed_play
from test_actions import campaign, world
from test_mundane_traits import combined_package
from test_statistics import gurps_draft
from test_unusual_background_admission import PROFILE, configured, decision

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import Approval, CharacterProposal
from wayfarer.engine.rules.catalog import RulesCatalog, RulesPackage
from wayfarer.engine.rules.skills import gurps_skills
from wayfarer.engine.rules.traits.unusual_background import bind_unusual_background
from wayfarer.engine.rules.types.background_admission import UNUSUAL_BACKGROUND_ID
from wayfarer.engine.rules.types.skill import PrerequisiteGroup, PrerequisiteKind, SkillPrerequisite
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.orchestration.play import ApproveCharacter, PlayService


def skill_package() -> RulesPackage:
    base = combined_package()
    return replace(base, definitions=base.definitions + gurps_skills.definitions(PROFILE))


def skill_proposal(*purchases: Purchase) -> CharacterProposal:
    return CharacterProposal(draft=gurps_draft(*purchases))


@pytest.mark.parametrize("price,allowed", [(0, True), (10, True), (50, False)])
def test_campaign_skill_admission_gates_both_defaults_and_purchases(
    price: int, allowed: bool
) -> None:
    selected = decision(price, allowed=allowed).model_copy(update={"benefits": ("skill:stealth",)})
    _, reviewer = configured(selected, base=skill_package())
    empty = reviewer.review(skill_proposal())
    assert empty.compilation.build is not None
    values = {value.target: value.value for value in empty.compilation.build.sheet.values}
    assert ("skill:stealth" in values) == (allowed and price == 0)
    # B224's unrelated Swimming default remains HT-4 = 6 in every campaign.
    assert values["skill:swimming"] == 6
    trained = reviewer.review(skill_proposal(Purchase(definition_id="skill:stealth", amount=1)))
    assert (trained.status == "automatic") == (allowed and price == 0)
    if trained.compilation.build is not None:
        assert trained.compilation.spent == 1
        assert (
            next(
                value.value
                for value in trained.compilation.build.sheet.values
                if value.target == "skill:stealth"
            )
            == 9
        )  # B170: one point in DX/A at DX 10.
    paid = reviewer.review(
        skill_proposal(
            Purchase(definition_id=UNUSUAL_BACKGROUND_ID),
            Purchase(definition_id="skill:stealth", amount=1),
        )
    )
    assert (paid.status == "review") == (allowed and price > 0)
    if paid.compilation.build is not None:
        assert paid.compilation.spent == price + 1


def test_paid_default_is_a_tangible_benefit_requiring_real_gm_approval(tmp_path: Path) -> None:
    selected = decision(50).model_copy(update={"benefits": ("skill:stealth",)})
    package, reviewer = configured(selected, base=skill_package())
    proposal = skill_proposal(Purchase(definition_id=UNUSUAL_BACKGROUND_ID))
    review = reviewer.review(proposal)
    assert review.status == "review" and review.compilation.spent == 50
    with pytest.raises(ValidationError, match="approval required"):
        reviewer.approve(proposal, campaign_id="c", actor_id="a", revision=0)
    approval = reviewer.approve(
        proposal,
        campaign_id="c",
        actor_id="a",
        revision=0,
        approver_id="gm",
        reason="Exceptional background permits this otherwise unavailable skill default",
    )
    path = tmp_path / "approval.json"
    path.write_text(approval.model_dump_json())
    rebuilt, restarted = configured(selected, base=skill_package())
    assert rebuilt.digest == package.digest
    build, _ = restarted.activate(
        proposal, Approval.model_validate_json(path.read_text()), campaign_id="c", actor_id="a"
    )
    assert build.spent == 50
    assert next(value.value for value in build.sheet.values if value.target == "skill:stealth") == 5
    _, changed = configured(selected.model_copy(update={"point_cost": 10}), base=skill_package())
    with pytest.raises(ValidationError, match="stale"):
        changed.activate(proposal, approval, campaign_id="c", actor_id="a")


def test_background_cannot_buy_a_skill_without_a_purchase_or_default() -> None:
    selected = decision().model_copy(update={"benefits": ("skill:karate",)})
    _, reviewer = configured(selected, base=skill_package())
    value = skill_proposal(Purchase(definition_id=UNUSUAL_BACKGROUND_ID))
    assert reviewer.review(value).status == "blocked"
    trained = skill_proposal(
        Purchase(definition_id=UNUSUAL_BACKGROUND_ID),
        Purchase(definition_id="skill:karate", amount=1),
    )
    assert reviewer.review(trained).status == "review"


@pytest.mark.parametrize("price,allowed", [(0, True), (10, True), (50, False)])
def test_skill_to_skill_default_cannot_bypass_campaign_admission(price: int, allowed: bool) -> None:
    selected = decision(price, allowed=allowed).model_copy(
        update={"benefits": ("skill:shortsword",)}
    )
    _, reviewer = configured(selected, base=skill_package())
    for owned in (False, True):
        proposal = skill_proposal(
            Purchase(definition_id="skill:broadsword", amount=4),
            *((Purchase(definition_id=UNUSUAL_BACKGROUND_ID),) if owned else ()),
        )
        review = reviewer.review(proposal)
        if owned and (not allowed or price == 0):
            assert review.status == "illegal"
            continue
        assert review.compilation.build is not None
        values = {value.target: value.value for value in review.compilation.build.sheet.values}
        assert values["skill:broadsword"] == 11
        assert ("skill:shortsword" in values) == (allowed and (price == 0 or owned))
        if "skill:shortsword" in values:
            assert values["skill:shortsword"] == 9  # B208: Broadsword-2.


@pytest.mark.parametrize("mutation", ["missing", "trained", "conditional", "higher", "alternative"])
def test_package_tampering_cannot_detach_default_admission(mutation: str) -> None:
    selected = decision().model_copy(update={"benefits": ("skill:stealth",)})
    package, _ = configured(selected, base=skill_package())
    row = next(row for row in package.definitions if row.id == "skill:stealth")
    assert row.skill is not None
    requirement = SkillPrerequisite(
        UNUSUAL_BACKGROUND_ID, kind=PrerequisiteKind.PURCHASED_DEFINITION
    )
    if mutation == "trained":
        requirement = replace(requirement, kind=PrerequisiteKind.TRAINED_SKILL)
    elif mutation == "conditional":
        requirement = replace(requirement, minimum_technology_level=8)
    elif mutation == "higher":
        requirement = replace(requirement, minimum=2)
    changed = replace(
        row,
        skill=replace(
            row.skill,
            prerequisites=() if mutation in ("missing", "alternative") else (requirement,),
            prerequisite_groups=(PrerequisiteGroup((requirement,)),)
            if mutation == "alternative"
            else (),
        ),
    )
    package = replace(
        package, definitions=tuple(changed if r.id == row.id else r for r in package.definitions)
    )
    with pytest.raises(ValidationError, match="exact admission prerequisite"):
        RulesCatalog((package,))


@pytest.mark.parametrize("bound", [False, True])
@pytest.mark.parametrize("alternative", [False, True])
def test_background_cannot_inherit_or_add_unselected_skill_links(
    bound: bool, alternative: bool
) -> None:
    selected = decision().model_copy(update={"benefits": ("skill:stealth",)})
    package = configured(selected, base=skill_package())[0] if bound else skill_package()
    row = next(row for row in package.definitions if row.id == "skill:swimming")
    assert row.skill is not None
    requirement = SkillPrerequisite(
        UNUSUAL_BACKGROUND_ID, kind=PrerequisiteKind.PURCHASED_DEFINITION
    )
    changed = replace(
        row,
        skill=replace(
            row.skill,
            prerequisites=() if alternative else (requirement,),
            prerequisite_groups=(PrerequisiteGroup((requirement,)),) if alternative else (),
        ),
    )
    package = replace(
        package, definitions=tuple(changed if r.id == row.id else r for r in package.definitions)
    )
    with pytest.raises(ValidationError, match="exact GM-selected|unbound background"):
        if bound:
            RulesCatalog((package,))
        else:
            bind_unusual_background(package, selected, package_id="unbound", version="1")


def test_skill_admission_requires_executable_skill_metadata() -> None:
    base = skill_package()
    base = replace(
        base,
        definitions=tuple(
            replace(row, skill=None) if row.id == "skill:stealth" else row
            for row in base.definitions
        ),
    )
    selected = decision().model_copy(update={"benefits": ("skill:stealth",)})
    with pytest.raises(ValidationError, match="existing implemented"):
        configured(selected, base=base)


def test_one_background_admits_a_trait_and_skill_for_one_surcharge() -> None:
    selected = decision(50).model_copy(
        update={"benefits": ("trait:combat-reflexes", "skill:stealth")}
    )
    _, reviewer = configured(selected, base=skill_package())
    proposal = skill_proposal(
        Purchase(definition_id=UNUSUAL_BACKGROUND_ID),
        Purchase(definition_id="trait:combat-reflexes"),
        Purchase(definition_id="skill:stealth", amount=1),
    )
    review = reviewer.review(proposal)
    assert review.status == "review" and review.compilation.spent == 50 + 15 + 1
    approval = reviewer.approve(
        proposal,
        campaign_id="c",
        actor_id="a",
        revision=0,
        approver_id="gm",
        reason="The same background admits both benefits with one surcharge",
    )
    build, _ = reviewer.activate(proposal, approval, campaign_id="c", actor_id="a")
    assert build.spent == 66


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_skill_default_admission_approval_survives_durable_restart(
    tmp_path: Path, backend: str
) -> None:
    selected = decision(50).model_copy(update={"benefits": ("skill:stealth",)})

    def reducer() -> ActionEngine:
        package, reviewer = configured(selected, base=skill_package())
        compiler = reviewer.compiler
        resources = ResourceEngine(
            world(), RulesCatalog((package,)), compiler.rules, compiler.policy, ()
        )
        return ActionEngine(reviewer, resources, ActionRules(id="b96-skills", version=1))

    original = reducer()
    store = open_store(tmp_path, backend=backend)
    service = PlayService(store, original)
    initial = campaign(original)
    proposal = skill_proposal(Purchase(definition_id=UNUSUAL_BACKGROUND_ID))
    await seed_play(
        service,
        initial,
        world(),
        ResourceState(owners=(Owner(actor_id="a", capacity=100),)),
        (ActorSetup(actor_id="a", proposal=proposal),),
    )
    waiting = await service.execute(
        initial["id"],
        Wait(id="unapproved", actor_id="a", expected_revision=0, ticks=1),
        principal_id="a",
    )
    assert waiting.code == "character.approval_required"
    command = ApproveCharacter(
        id="approve-skill-background",
        actor_id="gm",
        target_actor_id="a",
        expected_revision=0,
        reason="Approve the exceptional skill default and its 50-point cost",
    )
    approval = await service.approve(initial["id"], command, principal_id="gm")
    restarted = PlayService(store, reducer())
    assert await restarted.approve(initial["id"], command, principal_id="gm") == approval
    active = await restarted.execute(
        initial["id"],
        Wait(id="approved", actor_id="a", expected_revision=1, ticks=1),
        principal_id="a",
    )
    assert active.status == "committed"
    saved = await store.read(initial["id"])
    assert await store.replay(initial["id"]) == saved
    build, _ = restarted.engine.reviewer.activate(
        proposal, approval, campaign_id=initial["id"], actor_id="a"
    )
    assert build.spent == 50
    assert next(value.value for value in build.sheet.values if value.target == "skill:stealth") == 5


@pytest.mark.parametrize("price,allowed", [(0, True), (10, True), (50, False)])
def test_technique_admission_gates_investment_and_trained_parent_default(
    price: int, allowed: bool
) -> None:
    selected = decision(price, allowed=allowed).model_copy(
        update={"benefits": ("skill:kicking-karate",)}
    )
    _, reviewer = configured(selected, base=skill_package())
    for owned in (False, True):
        for points in (0, 2):
            proposal = skill_proposal(
                Purchase(definition_id="skill:karate", amount=1),
                *((Purchase(definition_id=UNUSUAL_BACKGROUND_ID),) if owned else ()),
                *(
                    (Purchase(definition_id="skill:kicking-karate", amount=points),)
                    if points
                    else ()
                ),
            )
            review = reviewer.review(proposal)
            legal = allowed and (price == 0 or owned) and (price > 0 or not owned)
            if (owned or points) and not legal:
                assert review.status == "illegal"
                continue
            assert review.compilation.build is not None
            values = {value.target: value.value for value in review.compilation.build.sheet.values}
            assert values["skill:karate"] == 8  # One point in DX/H, B170.
            assert ("skill:kicking-karate" in values) == legal
            if legal:
                # B232: Karate-2; two points in a Hard technique improve it by one.
                assert values["skill:kicking-karate"] == (7 if points else 6)
                assert review.compilation.spent == 1 + points + (price if owned else 0)
                assert review.status == ("review" if owned else "automatic")


@pytest.mark.parametrize("alternative", [False, True])
@pytest.mark.parametrize("paid", [False, True])
@pytest.mark.parametrize("access", ["none", "trait", "skill"])
def test_techniques_honor_existing_acquisition_prerequisites(
    alternative: bool, paid: bool, access: str
) -> None:
    base = skill_package()
    row = next(row for row in base.definitions if row.id == "skill:kicking-karate")
    assert row.skill is not None
    requirement = SkillPrerequisite(
        "trait:combat-reflexes", kind=PrerequisiteKind.PURCHASED_DEFINITION
    )
    changed = replace(
        row,
        skill=replace(
            row.skill,
            prerequisites=() if alternative else (requirement,),
            prerequisite_groups=(PrerequisiteGroup((requirement, SkillPrerequisite("skill:judo"))),)
            if alternative
            else (),
        ),
    )
    base = replace(
        base, definitions=tuple(changed if r.id == row.id else r for r in base.definitions)
    )
    _, reviewer = configured(decision(), base=base)
    proposal = skill_proposal(
        Purchase(definition_id="skill:karate", amount=1),
        *((Purchase(definition_id="skill:kicking-karate", amount=2),) if paid else ()),
        *((Purchase(definition_id="trait:combat-reflexes"),) if access == "trait" else ()),
        *((Purchase(definition_id="skill:judo", amount=1),) if access == "skill" else ()),
    )
    acquired = access == "trait" or (alternative and access == "skill")
    result = reviewer.review(proposal)
    if paid and not acquired:
        assert result.status == "illegal"
        assert any(d.code == "skill.prerequisite" for d in result.compilation.diagnostics)
    else:
        assert result.compilation.build is not None
        values = {value.target: value.value for value in result.compilation.build.sheet.values}
        assert ("skill:kicking-karate" in values) == acquired
        if acquired:
            assert values["skill:kicking-karate"] == (7 if paid else 6)
