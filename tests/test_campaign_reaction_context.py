"""Approved B21/B27/B41 standing at actual campaign consequences, not copied bonuses."""

import json
from dataclasses import replace
from typing import Literal

import pytest
from test_campaign_reaction_continuations import (
    administration_case,
    initial_case,
    law_case,
    prepare,
    rescue_case,
)
from test_mundane_traits import combined_package, runtime_compiler
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.gurps_social import ReactionModifier, evaluate_reaction
from wayfarer.engine.rules.social.social_hooks import Reputation, Standing
from wayfarer.engine.rules.traits.mundane.runtime import DEFAULT_AUDIENCE, Audience
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.campaign.reactions import (
    CampaignReactionInteraction,
    CampaignReactionSource,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Owner
from wayfarer.engine.simulation.social.social import SocialCommand, SocialContext, apply_social
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.errors import ConflictError, ValidationError

REP = "trait:reputation-bravery-guild-sometimes"


def with_traits(
    engine: ActionEngine,
    state: PlayState,
    *purchases: Purchase,
) -> tuple[ActionEngine, PlayState]:
    reviewer = PowerReviewer(
        runtime_compiler(), PowerPolicy(id="approved-campaign-social", version=1), frozenset({"gm"})
    )
    proposal = CharacterProposal(draft=gurps_draft(*purchases))
    approval = reviewer.approve(proposal, campaign_id=state.campaign_id, actor_id="a", revision=0)
    resources = ResourceEngine(
        state.world,
        RulesCatalog((combined_package(),)),
        reviewer.compiler.rules,
        reviewer.compiler.policy,
        (),
    )
    engine = ActionEngine(
        reviewer, resources, engine.rules.model_copy(update={"checks": (), "consumables": ()})
    )
    state = state.model_copy(
        update={
            "configuration_digest": engine.digest,
            "resources": state.resources.model_copy(
                update={
                    "items": (),
                    "owners": tuple(
                        owner.model_copy(
                            update={
                                "definitions": tuple(
                                    item.definition_id for item in proposal.draft.purchases
                                )
                            }
                        )
                        if owner.actor_id == "a"
                        else owner
                        for owner in state.resources.owners
                    ),
                }
            ),
            "actors": (
                state.actors[0].model_copy(update={"proposal": proposal, "approval": approval}),
            ),
            "approvals": (approval,),
        }
    )
    engine.validate(state)
    return engine, state


def interaction(
    source: CampaignReactionSource,
    *,
    mode: Literal["active", "passive", "absent", "proxy"] = "active",
    audience: Audience = DEFAULT_AUDIENCE,
    sapient: bool = True,
    proxy: str | None = None,
) -> CampaignReactionSource:
    return source.model_copy(
        update={
            "interaction": CampaignReactionInteraction(
                actor_id=source.command.actor_id,
                mode=mode,
                audience=audience,
                sapient=sapient,
                proxy_actor_id=proxy,
            )
        }
    )


def finish(
    engine: ActionEngine,
    state: PlayState,
    source: CampaignReactionSource,
    *,
    search: tuple[int, ...] = (),
    recognition: tuple[int, ...] = (),
    faces: tuple[int, int, int] = (4, 4, 4),
) -> PlayState:
    engine.validate(state)
    pending = prepare(engine, state, source, search)
    rng = RecordedDice(recognition)
    recognized = engine.campaign.recognize_reaction(pending, rng=rng)
    assert rng.exhausted()
    trace = evaluate_reaction(pending.profile_id, recognized.modifiers, faces)
    updated, _ = engine.campaign.resolve_reaction(
        state, pending, trace, rng=RecordedDice(()), player_actor_ids=("a",), recognized=recognized
    )
    assert updated.revision == updated.resources.revision == state.revision + 1
    engine.validate(updated)
    return updated


@pytest.mark.parametrize(
    "mode,sapient,audience,expected",
    [
        ("active", True, Audience(), 13),  # B41: raw12 + approved Charisma1.
        ("passive", True, Audience(), 12),
        ("absent", True, Audience(), 12),
        ("proxy", True, Audience(), 12),
        ("active", False, Audience(), 12),
        ("active", True, Audience(perceptible=False), 12),
    ],
)
def test_initial_loyalty_derives_charisma_only_for_applicable_actual_interaction(
    mode: Literal["active", "passive", "absent", "proxy"],
    sapient: bool,
    audience: Audience,
    expected: int,
) -> None:
    engine, state, source = initial_case()
    engine, state = with_traits(engine, state, Purchase(definition_id="trait:charisma"))
    if mode == "proxy":
        state = state.model_copy(
            update={
                "world": replace(
                    state.world,
                    entities=state.world.entities
                    + (
                        Entity(
                            "representative",
                            EntityKind.ACTOR,
                            "The representative",
                            location_id="dock",
                        ),
                    ),
                )
            }
        )
    source = interaction(
        source,
        mode=mode,
        audience=audience,
        sapient=sapient,
        proxy="representative" if mode == "proxy" else None,
    )
    assert (
        engine.campaign.economics is not None
        and engine.campaign.economics.hirelings[0].loyalty_modifiers == ()
    )
    after = finish(engine, state, source, search=(2, 2, 2))
    assert state.economics.hirelings == ()
    assert after.economics.hirelings[0].loyalty == expected


@pytest.mark.parametrize(
    "mode,audience,expected",
    [
        ("active", Audience(), 16),  # Charisma1 + Attractive1 + audible Voice2.
        ("passive", Audience(), 15),  # Appearance/Voice perceived, but no active Charisma.
        ("active", Audience(visible=False), 15),
        ("active", Audience(audible=False), 14),
        ("absent", Audience(), 12),
    ],
)
def test_appearance_and_voice_keep_their_own_perception_conditions(
    mode: Literal["active", "passive", "absent", "proxy"],
    audience: Audience,
    expected: int,
) -> None:
    engine, state, source = initial_case()
    engine, state = with_traits(
        engine,
        state,
        Purchase(definition_id="trait:charisma"),
        Purchase(definition_id="trait:appearance-attractive"),
        Purchase(definition_id="trait:voice"),
    )
    after = finish(
        engine, state, interaction(source, mode=mode, audience=audience), search=(2, 2, 2)
    )
    assert after.economics.hirelings[0].loyalty == expected


@pytest.mark.parametrize("kind", ["law", "administration", "rescue"])
def test_approved_charisma_reaches_real_case_knowledge_and_rescue_state(kind: str) -> None:
    engine, state, source = {
        "law": law_case,
        "administration": administration_case,
        "rescue": rescue_case,
    }[kind]()
    engine, state = with_traits(engine, state, Purchase(definition_id="trait:charisma"))
    after = finish(engine, state, source)
    if kind == "law":
        assert after.law.cases[0].status == "acquitted"  # Raw12 -> Good13, not Neutral12.
    elif kind == "administration":
        assert {fact.id for fact in after.world.perspective("a").facts} == {"clue"}
    else:
        assert after.economics.hirelings[0].loyalty == 16  # Raw12 + rescue3 + Charisma1.


def test_absent_defendant_keeps_reputation_by_name_without_borrowing_personal_charisma() -> None:
    engine, state, source = law_case()
    engine, state = with_traits(
        engine,
        state,
        Purchase(definition_id="trait:charisma", amount=3),
        Purchase(definition_id=REP),
    )
    source = interaction(source, mode="absent", audience=Audience(classes=("guild",)))
    after = finish(engine, state, source, recognition=(3, 3, 3), faces=(3, 4, 4))
    assert after.law.cases[0].status == "acquitted"  # Raw11 + recognized2 = Good13.
    details = [
        json.loads(event.kind)["private"]
        for event in after.resources.events
        if event.id.startswith(("social:", "social-key:"))
    ]
    assert details[0]["total"] == 13
    assert all(row["source_id"] != "trait:charisma" for row in details[0]["modifiers"])


@pytest.mark.parametrize(
    "audience,recognition,expected",
    [
        (Audience(classes=("guild",)), (3, 3, 3), 14),
        (Audience(classes=("guild",)), (6, 6, 6), 12),
        (Audience(classes=("outsider",)), (), 12),
    ],
)
def test_approved_reputation_draws_once_for_its_actual_audience_before_target(
    audience: Audience,
    recognition: tuple[int, ...],
    expected: int,
) -> None:
    engine, state, source = initial_case()
    engine, state = with_traits(engine, state, Purchase(definition_id=REP))
    source = interaction(source, audience=audience)
    pending = prepare(engine, state, source, (2, 2, 2))
    assert pending.reaction.source.standing is not None
    assert pending.reaction.source.standing.reputations[0].id == REP
    rng = RecordedDice(recognition)
    recognized = engine.campaign.recognize_reaction(pending, rng=rng)
    assert rng.exhausted()
    after, _ = engine.campaign.resolve_reaction(
        state,
        pending,
        evaluate_reaction(pending.profile_id, recognized.modifiers, (4, 4, 4)),
        rng=RecordedDice(()),
        player_actor_ids=("a",),
        recognized=recognized,
    )
    assert after.economics.hirelings[0].loyalty == expected
    assert len(after.resources.receipts) == 2


def test_campaign_roles_share_actor_scoped_recognition_without_new_dice() -> None:
    engine, state, admin = administration_case()
    rescue_engine, rescue_state, rescue = rescue_case()
    assert rescue_engine.rules.economics is not None
    engine = ActionEngine(
        engine.reviewer,
        engine.resources,
        engine.rules.model_copy(
            update={"economics": rescue_engine.rules.economics.model_copy(update={"jobs": ()})}
        ),
    )
    state = state.model_copy(update={"economics": rescue_state.economics})
    engine, state = with_traits(engine, state, Purchase(definition_id=REP))
    admin = interaction(admin, audience=Audience(classes=("guild",)))
    learned = finish(engine, state, admin, recognition=(3, 3, 3))
    rescue = interaction(
        rescue.model_copy(
            update={
                "command": rescue.command.model_copy(update={"expected_revision": learned.revision})
            }
        ),
        audience=Audience(classes=("guild",)),
    )
    pending = prepare(engine, learned, rescue)
    assert len(pending.reaction.known_recognition) == 1
    assert pending.reaction.known_recognition[0].dice == (3, 3, 3)
    recognized = engine.campaign.recognize_reaction(pending, rng=RecordedDice(()))
    after, _ = engine.campaign.resolve_reaction(
        learned,
        pending,
        evaluate_reaction(pending.profile_id, recognized.modifiers, (4, 4, 4)),
        rng=RecordedDice(()),
        player_actor_ids=("a",),
        recognized=recognized,
    )
    assert after.economics.hirelings[0].loyalty == 17
    assert after.world.perspective("a").facts == learned.world.perspective("a").facts


def test_campaign_recognition_reuses_verified_legacy_sources_without_rewriting() -> None:
    engine, state, source = initial_case()
    engine, state = with_traits(engine, state, Purchase(definition_id=REP))
    source = interaction(source, audience=Audience(classes=("guild",)))
    legacy = SocialCommand(
        id="legacy",
        actor_id="a",
        expected_revision=0,
        kind="reaction",
        subject_id="b",
        trigger_id="old",
    )
    standing = Standing(reputations=(Reputation(REP, 2, "large-class", "sometimes", ("guild",)),))
    resources, _ = apply_social(
        state.resources,
        state.world,
        legacy,
        SocialContext(
            "gurps-basic-set-4e-2004", 0, standing=standing, audience=Audience(classes=("guild",))
        ),
        rng=RecordedDice((3, 3, 3, 4, 4, 4)),
        system=True,
    )
    state = state.model_copy(update={"revision": resources.revision, "resources": resources})
    source = source.model_copy(
        update={"command": source.command.model_copy(update={"expected_revision": state.revision})}
    )
    with pytest.raises(ValidationError, match="recorded source"):
        engine.campaign.prepare_reaction(
            state, source, rng=RecordedDice(()), player_actor_ids=("a",)
        )
    rng = RecordedDice((2, 2, 2))
    _, pending, _ = engine.campaign.prepare_reaction(
        state, source, rng=rng, player_actor_ids=("a",), recognition_sources=(legacy,)
    )
    assert rng.exhausted() and pending is not None
    assert engine.campaign.recognize_reaction(pending, rng=RecordedDice(())).recognition[
        0
    ].dice == (3, 3, 3)
    assert state.resources.events == resources.events


def test_forged_trait_modifier_and_current_approval_loss_reject_before_search_or_target() -> None:
    engine, state, source = initial_case()
    engine, state = with_traits(engine, state, Purchase(definition_id="trait:charisma"))
    pending = prepare(engine, state, source, (2, 2, 2))
    no_approval = state.model_copy(
        update={"actors": (state.actors[0].model_copy(update={"approval": None}),)}
    )
    with pytest.raises((ValidationError, ConflictError)):
        engine.campaign.validate_reaction(no_approval, pending, player_actor_ids=("a",))
    assert engine.campaign.economics is not None
    engine.campaign.economics = engine.campaign.economics.model_copy(
        update={
            "hirelings": (
                engine.campaign.economics.hirelings[0].model_copy(
                    update={"loyalty_modifiers": (ReactionModifier("trait", 5, "trait:charisma"),)}
                ),
            )
        }
    )
    with pytest.raises(ValidationError, match="derived from approved"):
        engine.campaign.prepare_reaction(
            state, source, rng=RecordedDice(()), player_actor_ids=("a",)
        )


def test_changed_interaction_recognized_context_and_approval_are_bound() -> None:
    engine, state, source = initial_case()
    engine, state = with_traits(engine, state, Purchase(definition_id="trait:charisma"))
    pending = prepare(engine, state, source, (2, 2, 2))
    changed = pending.model_copy(update={"source": interaction(source, mode="absent")})
    with pytest.raises(ConflictError, match="context changed"):
        engine.campaign.validate_reaction(state, changed, player_actor_ids=("a",))
    recognized = engine.campaign.recognize_reaction(pending, rng=RecordedDice(()))
    forged = recognized.model_copy(update={"modifiers": ()})
    with pytest.raises(ValidationError, match="modifiers changed"):
        engine.campaign.resolve_reaction(
            state,
            pending,
            evaluate_reaction(pending.profile_id, (), (4, 4, 4)),
            rng=RecordedDice(()),
            player_actor_ids=("a",),
            recognized=forged,
        )


def test_shared_campaign_recognition_cannot_cross_to_another_actor_with_the_same_purchase() -> None:
    engine, state, source = administration_case()
    engine, state = with_traits(engine, state, Purchase(definition_id=REP))
    source = interaction(source, audience=Audience(classes=("guild",)))
    learned = finish(engine, state, source, recognition=(3, 3, 3))
    actor = learned.actors[0]
    approval = engine.reviewer.approve(
        actor.proposal, campaign_id=learned.campaign_id, actor_id="c", revision=learned.revision
    )
    other = actor.model_copy(update={"actor_id": "c", "approval": approval})
    learned = learned.model_copy(
        update={
            "actors": learned.actors + (other,),
            "world": replace(
                learned.world,
                entities=learned.world.entities
                + (Entity("c", EntityKind.ACTOR, "Another PC", location_id="dock"),),
            ),
        }
    )
    learned = learned.model_copy(
        update={
            "approvals": learned.approvals + (approval,),
            "resources": learned.resources.model_copy(
                update={
                    "owners": learned.resources.owners
                    + (
                        Owner(
                            actor_id="c",
                            capacity=100,
                            definitions=tuple(
                                item.definition_id for item in actor.proposal.draft.purchases
                            ),
                        ),
                    ),
                    "pools": learned.resources.pools
                    + tuple(
                        pool.model_copy(update={"id": pool.id.replace(":a", ":c")})
                        for pool in learned.resources.pools
                        if pool.id.endswith(":a")
                    ),
                }
            ),
        }
    )
    engine = ActionEngine(engine.reviewer, engine.resources.for_world(learned.world), engine.rules)
    learned = learned.model_copy(update={"configuration_digest": engine.digest})
    engine.validate(learned)
    second = source.model_copy(
        update={
            "command": source.command.model_copy(
                update={
                    "id": "second-person",
                    "actor_id": "c",
                    "expected_revision": learned.revision,
                }
            ),
            "interaction": source.interaction.model_copy(update={"actor_id": "c"}),
        }
    )
    _, pending, _ = engine.campaign.prepare_reaction(
        learned, second, rng=RecordedDice(()), player_actor_ids=("a", "c"), actor_id="c"
    )
    assert pending is not None and pending.reaction.known_recognition == ()
    rng = RecordedDice((6, 6, 6))
    recognized = engine.campaign.recognize_reaction(pending, rng=rng)
    assert rng.exhausted() and recognized.recognition[0].recognized is False
    after, _ = engine.campaign.resolve_reaction(
        learned,
        pending,
        evaluate_reaction(pending.profile_id, recognized.modifiers, (4, 4, 4)),
        rng=RecordedDice(()),
        player_actor_ids=("a", "c"),
        recognized=recognized,
    )
    engine.validate(after)
    assert after.world.perspective("c").facts == ()
    assert {fact.id for fact in after.world.perspective("a").facts} == {"clue"}


def test_campaign_cannot_skip_unknown_recognition_or_duplicate_purchased_standing() -> None:
    engine, state, source = initial_case()
    engine, state = with_traits(engine, state, Purchase(definition_id=REP))
    source = interaction(source, audience=Audience(classes=("guild",)))
    pending = prepare(engine, state, source, (2, 2, 2))
    with pytest.raises(ValidationError):
        engine.campaign.resolve_reaction(
            state,
            pending,
            evaluate_reaction(pending.profile_id, (), (4, 4, 4)),
            rng=RecordedDice(()),
            player_actor_ids=("a",),
        )
    copied = source.model_copy(
        update={
            "interaction": source.interaction.model_copy(
                update={
                    "standing": Standing(
                        reputations=(Reputation(REP, 2, "large-class", "sometimes", ("guild",)),)
                    ),
                }
            )
        }
    )
    with pytest.raises(ValidationError, match="Purchased reputation"):
        engine.campaign.prepare_reaction(
            state, copied, rng=RecordedDice(()), player_actor_ids=("a",)
        )
