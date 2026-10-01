"""Core form build boundaries, independently checked against Characters3p B83-85."""

from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import seed_play
from test_actions import Dice, campaign, world
from test_statistics import gurps_draft, profile_package
from test_transformations import attachment_routes
from trait_support import approved_build, options, trait_compiler

from wayfarer.engine.character.compiler import Purchase, ValidatedBuild
from wayfarer.engine.rules.traits.movement_forms import PROFILE, RUNTIME_HOOKS, package
from wayfarer.engine.simulation.campaign.transformations import TraitRoute, TransformationRule
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.transformations import _validate_shapeshifting


def form(
    kind: Literal["alternate-form", "morph"] = "alternate-form",
) -> tuple[TransformationRule, tuple[ValidatedBuild, ValidatedBuild]]:
    compiler = trait_compiler("shape", PROFILE, package(), hooks=RUNTIME_HOOKS)
    purchase = Purchase(
        definition_id="advantage:" + kind,
        amount=1,
        trait=options(**{"native-template-cost": 0, "target-template-cost": 50}),
    )
    build, _ = approved_build(compiler, purchase)
    target = compiler.compile(
        gurps_draft(purchase, Purchase(definition_id="advantage:flight"), st_level=11)
    )
    assert target.build is not None
    rule = TransformationRule(
        id="approved-form",
        actor_id="a",
        kind=kind,
        source_ref="B84",
        target=gurps_draft(purchase, Purchase(definition_id="advantage:flight"), st_level=11),
        target_body_id="a:form",
        form_model_id="b" if kind == "morph" else None,
        forced_reversion_influence="dispel magic",
        native_template_cost=0,
        target_template_cost=50,
        trait_routes=tuple(
            TraitRoute(definition_id=p.definition_id, follows="body")
            for p in gurps_draft(purchase, Purchase(definition_id="advantage:flight")).purchases
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
        _validate_shapeshifting(rule.model_copy(update={"target_template_cost": 60}), before, after)
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


@pytest.mark.parametrize("kind", ["alternate-form", "morph"])
async def test_form_changes_the_durable_approved_build_after_ten_seconds(
    tmp_path: Path, kind: Literal["alternate-form", "morph"]
) -> None:
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

    rule, _ = form(kind)
    compiler = trait_compiler("shape", PROFILE, package(), hooks=RUNTIME_HOOKS)
    native = rule.target.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": 10}) if p.definition_id == "attribute:st" else p
                for p in rule.target.purchases
                if p.definition_id != "advantage:flight"
            )
        }
    )
    reviewer = PowerReviewer(compiler, PowerPolicy(id="shape", version=1), frozenset({"gm"}))
    reducer = ActionEngine(
        reviewer,
        ResourceEngine(
            world().learn("a", "promise"),
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
    play = PlayService(AsyncSQLiteStore(tmp_path / "shape.sqlite", 10), reducer, rng=Dice(5))
    initial = campaign(reducer)
    await seed_play(
        play,
        initial,
        world().learn("a", "promise"),
        ResourceState(
            owners=(Owner(actor_id="a", capacity=100),),
            pools=(Pool(id="hp:a", current=5, maximum=10), Pool(id="fp:a", current=10, maximum=10)),
        ),
        (ActorSetup(actor_id="a", proposal=CharacterProposal(draft=native), aware_of=("b",)),),
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
    from wayfarer.orchestration.transformations import shapeshifting_checkpoint

    damaged_pending = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": p.current - 1}) if p.id == "hp:a" else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )
    interrupted = shapeshifting_checkpoint(play, damaged_pending, before=state)
    assert interrupted.transformations.records[-1].status == "interrupted"
    assert interrupted.actors[0].proposal.draft == native
    assert interrupted.transformations.records[-1].concentration_checks[-1].total == 18
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
    from wayfarer.engine.character.traits.movement_forms import movement_forms

    transformed = compiler.compile(state.actors[0].proposal.draft).build
    assert transformed is not None and transformed.statistics is not None
    forms = movement_forms(transformed, compiler.definitions)
    assert forms.has("advantage:flight")
    assert forms.top_move(transformed.statistics.basic_move, "air") == 10
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

    from wayfarer.engine.simulation.resources import Item

    worn = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "items": (
                        Item(
                            id="worn-gear",
                            definition_id="ordinary-gear",
                            owner_id="a",
                            equipped=True,
                            ready=True,
                        ),
                    )
                }
            )
        }
    )
    shifted_gear = _apply_build(play, worn, active, rule, reverse=False, command_id="gear-shift")
    assert shifted_gear.resources.items[0].owner_id == "a"
    assert (
        not shifted_gear.resources.items[0].equipped and not shifted_gear.resources.items[0].ready
    )
    compatible = _apply_build(
        play,
        worn,
        active,
        rule.model_copy(update={"compatible_equipment_ids": ("worn-gear",)}),
        reverse=False,
        command_id="compatible-shift",
    )
    assert compatible.resources.items[0].equipped and compatible.resources.items[0].ready
    if kind == "morph":
        from wayfarer.engine.simulation.campaign.transformations import MorphMemory
        from wayfarer.orchestration.transformations import (
            MemorizeMorph,
            _memorize_morph,
            _validate_morph_access,
        )

        unseen = state.model_copy(update={"world": world()})
        with pytest.raises(ValidationError, match="visible present"):
            _validate_morph_access(unseen, rule)
        full_memories = tuple(
            MorphMemory(
                actor_id="a",
                rule_id=f"slot-{n}",
                build_revision=active.target_build_revision,
                started_at=0,
                ready_at=60,
                status="memorized",
            )
            for n in range(10)
        )
        full = state.model_copy(
            update={
                "transformations": state.transformations.model_copy(
                    update={"morph_memories": full_memories}
                )
            }
        )
        command = MemorizeMorph(
            operation="memorize-start",
            id="full-memory",
            actor_id="a",
            expected_revision=4,
            rule_id=rule.id,
        )
        with pytest.raises(ValidationError, match="Full Morph memory"):
            _memorize_morph(play, full, command)
        replaced = _memorize_morph(
            play, full, command.model_copy(update={"forget_rule_id": "slot-0"})
        )
        assert len(replaced.transformations.morph_memories) == 10
        assert all(m.rule_id != "slot-0" for m in replaced.transformations.morph_memories)
    revision = 4
    if kind == "morph":
        memory_start = {
            "operation": "memorize-start",
            "id": "memorize-start",
            "actor_id": "a",
            "expected_revision": revision,
            "rule_id": rule.id,
        }
        memory = await service.memorize(initial["id"], memory_start, principal_id="a")
        assert memory.status == "concentrating" and memory.ready_at - memory.started_at == 60
        assert await service.memorize(initial["id"], memory_start, principal_id="a") == memory
        with pytest.raises(ValidationError, match="one minute"):
            await service.memorize(
                initial["id"],
                {
                    "operation": "memorize-complete",
                    "id": "memory-early",
                    "actor_id": "a",
                    "expected_revision": 5,
                    "rule_id": rule.id,
                },
                principal_id="a",
            )
        await play.execute(
            initial["id"],
            Wait(id="memorize-wait", actor_id="a", expected_revision=5, ticks=60),
            principal_id="a",
        )
        memory = await service.memorize(
            initial["id"],
            {
                "operation": "memorize-complete",
                "id": "memory-complete",
                "actor_id": "a",
                "expected_revision": 6,
                "rule_id": rule.id,
            },
            principal_id="a",
        )
        assert memory.status == "memorized"
        revision = 7
    force = {
        "operation": "resolve",
        "id": "force",
        "actor_id": "a",
        "expected_revision": revision,
        "proposal_id": proposed.proposal_id,
        "resolution": "force",
        "influence": "dispel magic",
    }
    with pytest.raises(ValidationError, match="director authority"):
        await service.execute(initial["id"], force, principal_id="a")
    with pytest.raises(ValidationError, match="authored external influence"):
        await service.execute(initial["id"], force | {"influence": "invented"}, principal_id="gm")
    reverse = {
        "operation": "resolve",
        "id": "reverse",
        "actor_id": "a",
        "expected_revision": revision,
        "proposal_id": proposed.proposal_id,
        "resolution": "reverse",
    }
    reverting = await service.execute(initial["id"], reverse, principal_id="a")
    assert reverting.status == "reverting"
    assert await service.execute(initial["id"], reverse, principal_id="a") == reverting
    assert play._load(await play.store.read(initial["id"])).actors[0].proposal.draft == rule.target
    await play.execute(
        initial["id"],
        Wait(id="reverse-wait", actor_id="a", expected_revision=revision + 1, ticks=10),
        principal_id="a",
    )
    restored = await service.execute(
        initial["id"],
        {
            "operation": "resolve",
            "id": "reverse-complete",
            "actor_id": "a",
            "expected_revision": revision + 2,
            "proposal_id": proposed.proposal_id,
            "resolution": "complete",
        },
        principal_id="a",
    )
    assert restored.status == "reverted"
    if kind == "morph":
        from wayfarer.orchestration.transformations import _validate_morph_access

        remembered = play._load(await play.store.read(initial["id"]))
        _validate_morph_access(remembered.model_copy(update={"world": world()}), rule)
    final = play._load(await play.store.read(initial["id"]))
    assert final.actors[0].proposal.draft == native
    checkpoint = await play.store.read(initial["id"])
    with pytest.raises(ConflictError):
        await service.execute(
            initial["id"],
            {
                "operation": "propose",
                "id": "stale-propose",
                "actor_id": "a",
                "expected_revision": 0,
                "expected_build_revision": build.revision,
                "rule_id": rule.id,
            },
            principal_id="a",
        )
    assert await play.store.read(initial["id"]) == checkpoint
    assert await play.store.read(initial["id"]) == await play.store.replay(initial["id"])


def test_proportional_fatigue_rebases_components_and_extreme_deficit() -> None:
    from wayfarer.engine.rules.types.recovery import FatigueStatus
    from wayfarer.engine.simulation.resources import Pool
    from wayfarer.orchestration.transformations import _scale_form_pool

    original = Pool(
        id="fp:a",
        current=-10,
        maximum=20,
        fatigue=FatigueStatus(profile_id=PROFILE, starvation=2, dehydration=3, sleep=5),
    )
    reduced = _scale_form_pool(original, 10)
    assert reduced.maximum == 10 and reduced.current == -5
    assert reduced.fatigue is not None
    assert (reduced.fatigue.starvation, reduced.fatigue.dehydration, reduced.fatigue.sleep) == (
        1,
        2,
        2,
    )
    Pool.model_validate(reduced.model_dump())
