"""Core form build boundaries, independently checked against Characters3p B83-85."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import seed_play
from test_actions import campaign, world
from test_statistics import gurps_draft, profile_package
from test_transformations import attachment_routes
from trait_support import approved_build, options, trait_compiler

from wayfarer.engine.character.compiler import Purchase, ValidatedBuild
from wayfarer.engine.rules.traits.movement_forms import PROFILE, RUNTIME_HOOKS, package
from wayfarer.engine.simulation.campaign.transformations import TraitRoute, TransformationRule
from wayfarer.errors import ValidationError
from wayfarer.orchestration.transformations import _validate_shapeshifting


def form(
    kind: Literal["alternate-form", "morph"] = "alternate-form",
) -> tuple[TransformationRule, tuple[ValidatedBuild, ValidatedBuild]]:
    compiler = trait_compiler("shape", PROFILE, package(), hooks=RUNTIME_HOOKS)
    purchase = Purchase(
        definition_id="advantage:" + kind,
        amount=1,
        trait=options(**{"native-template-cost": 0, "target-template-cost": 10}),
    )
    build, _ = approved_build(compiler, purchase)
    target = compiler.compile(gurps_draft(purchase, st_level=11))
    assert target.build is not None
    rule = TransformationRule(
        id="approved-form",
        actor_id="a",
        kind=kind,
        source_ref="B84",
        target=gurps_draft(purchase, st_level=11),
        target_body_id="a:form",
        native_template_cost=0,
        target_template_cost=10,
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body")
            for p in gurps_draft(purchase).purchases
        ),
        attachment_routes=attachment_routes(),
        reversible=True,
        treatment_seconds=10,
    )
    return rule, (build, target.build)


@pytest.mark.parametrize("kind", ["alternate-form", "morph"])
def test_purchased_form_limits_are_bound_to_the_compiled_target(
    kind: Literal["alternate-form", "morph"],
) -> None:
    rule, builds = form(kind)
    before, after = builds
    _validate_shapeshifting(rule, before, after)
    assert after.statistics is not None and before.statistics is not None
    assert after.statistics.st == 11
    assert before.statistics.st == 10
    with pytest.raises(ValidationError, match="approved template limits"):
        _validate_shapeshifting(rule.model_copy(update={"target_template_cost": 20}), before, after)
    with pytest.raises(ValidationError, match="racial template costs"):
        _validate_shapeshifting(rule, before, replace(after, spent=after.spent + 1))
    with pytest.raises(ValidationError, match="purchased approved"):
        _validate_shapeshifting(rule, replace(before, trait_purchases=()), after)


def test_core_form_authorship_rejects_instant_change_and_ownership_transfer() -> None:
    rule, _ = form()
    from pydantic import ValidationError as SchemaError

    with pytest.raises(SchemaError, match="ten-second"):
        TransformationRule.model_validate(rule.model_dump() | {"treatment_seconds": 0})
    with pytest.raises(SchemaError, match="attachment ownership"):
        TransformationRule.model_validate(
            rule.model_dump()
            | {
                "attachment_routes": tuple(
                    route.model_copy(update={"follows": "body", "destination_id": "other"})
                    if route.kind == "control"
                    else route
                    for route in rule.attachment_routes
                )
            }
        )


async def test_form_changes_the_durable_approved_build_after_ten_seconds(tmp_path: Path) -> None:
    from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
    from wayfarer.engine.rules.catalog import RulesCatalog
    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
    from wayfarer.engine.simulation.campaign.transformations import TransformationRules
    from wayfarer.engine.simulation.resource_engine import ResourceEngine
    from wayfarer.engine.simulation.resources import Owner, Pool, ResourceState
    from wayfarer.orchestration.play import PlayService
    from wayfarer.orchestration.transformations import TransformationService
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

    rule, _ = form()
    compiler = trait_compiler("shape", PROFILE, package(), hooks=RUNTIME_HOOKS)
    native = rule.target.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 10}) if p.definition_id == "attribute:st" else p
                for p in rule.target.purchases
            )
        }
    )
    reviewer = PowerReviewer(compiler, PowerPolicy(id="shape", version=1), frozenset({"gm"}))
    reducer = ActionEngine(
        reviewer,
        ResourceEngine(
            world(),
            RulesCatalog(
                (
                    replace(
                        profile_package(PROFILE),
                        id="package:test-shape",
                        definitions=profile_package(PROFILE).definitions + package().definitions,
                    ),
                )
            ),
            compiler.rules,
            compiler.policy,
            (),
        ),
        ActionRules(
            id="shape",
            version=1,
            transformations=TransformationRules(id="shape", version=1, transformations=(rule,)),
        ),
    )
    play = PlayService(AsyncSQLiteStore(tmp_path / "shape.sqlite", 10), reducer)
    initial = campaign(reducer)
    await seed_play(
        play,
        initial,
        world(),
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100),),
            pools=(Pool(id="hp:a", current=5, maximum=10), Pool(id="fp:a", current=10, maximum=10)),
        ),
        (ActorSetup(actor_id="a", proposal=CharacterProposal(draft=native)),),
    )
    service = TransformationService(play)
    build = compiler.compile(native).build
    assert build is not None
    proposed = await service.execute(
        initial["id"],
        {
            "operation": "propose",
            "id": "propose",
            "actor_id": "a",
            "expected_revision": 0,
            "expected_build_revision": build.revision,
            "rule_id": rule.id,
        },
        principal_id="a",
    )
    await service.execute(
        initial["id"],
        {
            "operation": "approve",
            "id": "approve",
            "actor_id": "a",
            "expected_revision": 1,
            "proposal_id": proposed.proposal_id,
            "reason": "Approved racial template",
        },
        principal_id="gm",
    )
    state = play._load(await play.store.read(initial["id"]))
    assert state.actors[0].proposal.draft == native
    await play.execute(
        initial["id"],
        Wait(id="wait", actor_id="a", expected_revision=2, ticks=10),
        principal_id="a",
    )
    await service.execute(
        initial["id"],
        {
            "operation": "resolve",
            "id": "complete",
            "actor_id": "a",
            "expected_revision": 3,
            "proposal_id": proposed.proposal_id,
            "resolution": "complete",
        },
        principal_id="a",
    )
    state = play._load(await play.store.read(initial["id"]))
    assert state.actors[0].proposal.draft == rule.target
    hp = next(p for p in state.resources.pools if p.id == "hp:a")
    assert hp.maximum == 11 and hp.current == 11
    assert await play.store.read(initial["id"]) == await play.store.replay(initial["id"])

    from wayfarer.orchestration.transformations import _apply_build

    active = state.transformations.records[-1]
    injured = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": 5}) if p.id == "hp:a" else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )
    reverted = _apply_build(play, injured, active, rule, reverse=True, command_id="wounded-revert")
    hp = next(p for p in reverted.resources.pools if p.id == "hp:a")
    assert hp.maximum == 10 and hp.current == 4
    assert reverted.actors[0].proposal.draft == native
    from wayfarer.orchestration.transformations import shapeshifting_checkpoint

    unconscious = injured.model_copy(
        update={
            "resources": injured.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(
                            update={"injury": p.injury.model_copy(update={"unconscious": True})}
                        )
                        if p.id == "hp:a" and p.injury is not None
                        else p
                        for p in injured.resources.pools
                    )
                }
            )
        }
    )
    forced = shapeshifting_checkpoint(play, unconscious)
    hp = next(p for p in forced.resources.pools if p.id == "hp:a")
    assert hp.maximum == 10 and hp.current == 4 and hp.injury is not None and hp.injury.unconscious
    assert forced.actors[0].proposal.draft == native
    assert forced.transformations.records[-1].status == "reverted"
