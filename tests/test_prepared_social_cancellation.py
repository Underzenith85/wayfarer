"""Canceling a pending reaction also closes its existing immediate source route."""

import json
from dataclasses import replace

import pytest
from test_prepared_diplomacy import context as diplomacy_context
from test_prepared_reactions import (
    DISCLOSURE,
    EMPTY,
    PROFILE,
    CountedDice,
    command,
    prepare,
    world,
)

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.social_hooks import Reputation, Standing
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social.diplomacy import (
    PreparedDiplomacy,
    cancel_prepared_diplomacy,
    prepare_diplomacy,
)
from wayfarer.engine.simulation.social.reactions import cancel_prepared_reaction
from wayfarer.engine.simulation.social.social import (
    SocialContext,
    SocialDisclosure,
    apply_interaction,
)
from wayfarer.errors import ConflictError, ValidationError


@pytest.mark.parametrize("trigger", ["trigger0", "trigger:with:colons"])
def test_cancelled_reaction_closes_legacy_trigger_without_dice_or_disclosure(trigger: str) -> None:
    value = command().model_copy(update={"trigger_id": trigger})
    prepared = prepare(value=value)
    before_world = world()
    cancelled = cancel_prepared_reaction(EMPTY, prepared, system=True)
    assert cancelled.revision == 1 and len(cancelled.receipts) == len(cancelled.events) == 1
    private = json.loads(cancelled.events[-1].kind)
    assert json.loads(private["public"])["outcome"] == "cancelled"
    assert private["private"]["preparation"]["provenance"] == prepared.provenance
    assert before_world == world()
    # Exact original replay returns the cancellation, even after source loss.
    replayed, unchanged, outcome = apply_interaction(
        cancelled,
        replace(before_world, knowledge=()),
        value,
        SocialContext("lost-profile", 0),
        DISCLOSURE,
        rng=RecordedDice([]),
        system=True,
    )
    assert replayed == cancelled and unchanged.knowledge == () and outcome.outcome == "cancelled"
    with pytest.raises(ConflictError, match="trigger already resolved"):
        apply_interaction(
            cancelled,
            before_world,
            value.model_copy(update={"id": "new-command", "expected_revision": 1}),
            SocialContext(PROFILE, 0),
            DISCLOSURE,
            rng=RecordedDice([]),
            system=True,
        )
    with pytest.raises(ConflictError, match="already committed"):
        cancel_prepared_reaction(cancelled, prepared, system=True)


@pytest.mark.parametrize("route", ["influence", "skill"])
def test_cancelled_diplomacy_retains_frozen_contest_and_blocks_immediate_reroll(route: str) -> None:
    source, value = diplomacy_context(route), command().model_copy(update={"kind": route})
    rng = CountedDice([5, 5, 5, 3, 3, 3])
    prepared = prepare_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=rng,
        system=True,
    )
    assert rng.draws == 6
    # A private pending/clock commit can advance resources before GM cancellation.
    state = ResourceState(revision=4)
    source.profile_id = "removed-profile"
    source.required_fact_ids = ("removed-fact",)
    cancelled = cancel_prepared_diplomacy(state, prepared, system=True)
    assert rng.draws == 6 and cancelled.revision == 5
    details = json.loads(cancelled.events[-1].kind)
    assert json.loads(details["public"])["outcome"] == "cancelled"
    frozen = PreparedDiplomacy.model_validate_json(json.dumps(details["private"]["preparation"]))
    assert frozen == prepared and frozen.influence.fallback is None
    assert frozen.influence.contest is not None and frozen.influence.contest.winner == "npc"
    assert ("actor", "answer") not in world().knowledge
    replayed, _, outcome = apply_interaction(
        cancelled,
        world(),
        value,
        source,
        DISCLOSURE,
        rng=RecordedDice([]),
        system=True,
    )
    assert replayed == cancelled and outcome.outcome == "cancelled"
    with pytest.raises(ConflictError, match="trigger already resolved"):
        apply_interaction(
            cancelled,
            world(),
            value.model_copy(update={"id": "repeat", "expected_revision": 5}),
            diplomacy_context(route),
            DISCLOSURE,
            rng=RecordedDice([]),
            system=True,
        )


@pytest.mark.parametrize("route", ["influence", "skill"])
def test_cancelled_diplomacy_keeps_already_established_actor_scoped_recognition(route: str) -> None:
    source, value = diplomacy_context(route), command().model_copy(update={"kind": route})
    source.standing = Standing(reputations=(Reputation("hero", 2, recognition="sometimes"),))
    rng = CountedDice([1, 1, 1, 5, 5, 5, 3, 3, 3])
    prepared = prepare_diplomacy(
        EMPTY,
        world(),
        value,
        source,
        SocialDisclosure(),
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=rng,
        system=True,
    )
    cancelled = cancel_prepared_diplomacy(EMPTY, prepared, system=True)
    assert rng.draws == 9
    next_rng = CountedDice([5, 5, 5, 3, 3, 3])
    next_prepared = prepare_diplomacy(
        cancelled,
        world(),
        command(1).model_copy(update={"kind": route}),
        source,
        SocialDisclosure(),
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        rng=next_rng,
        system=True,
    )
    assert next_rng.draws == 6
    assert next_prepared.recognized.recognition == prepared.recognized.recognition


def test_cancellation_requires_host_authority_and_does_not_validate_current_world() -> None:
    prepared = prepare()
    with pytest.raises(ValidationError, match="authoritative"):
        cancel_prepared_reaction(EMPTY, prepared)
    # No World or live source argument is required; source loss cannot trap the
    # campaign behind its pending gate. The host owns current authority checks.
    cancelled = cancel_prepared_reaction(ResourceState(revision=7), prepared, system=True)
    assert cancelled.revision == 8
