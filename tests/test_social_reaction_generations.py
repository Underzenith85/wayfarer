"""New immediate reactions share corrected semantics; historical calls stay exact."""

import json

import pytest
from test_prepared_diplomacy import context as diplomacy_context
from test_prepared_reactions import DISCLOSURE, EMPTY, PROFILE, CountedDice, command, world
from test_reaction_recognition_provenance import context as reputation_context

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.social_hooks import Standing
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.engine.simulation.social.reactions import (
    LegacyRecognitionAttribution,
    attribute_legacy_recognition,
)
from wayfarer.engine.simulation.social.social import (
    SocialContext,
    SocialDisclosure,
    apply_interaction,
)
from wayfarer.errors import ValidationError


def test_new_immediate_generation_keeps_both_standing_and_authored_retribution() -> None:
    state = ResourceState(
        events=(
            ResourceEvent(
                id="interrogation-retribution:prior:npc",
                target_id="npc",
                at=0,
                kind=json.dumps(
                    {
                        "interrogator_id": "actor",
                        "modifier": {
                            "kind": "situation",
                            "value": -2,
                            "source_id": "prior-coercion",
                            "hidden": False,
                        },
                    }
                ),
            ),
        )
    )
    source = SocialContext(PROFILE, 0, standing=Standing(appearance="attractive"))
    legacy, _, legacy_outcome = apply_interaction(
        state, world(), command(), source, DISCLOSURE, rng=RecordedDice([5, 5, 5]), system=True
    )
    corrected, _, corrected_outcome = apply_interaction(
        state,
        world(),
        command(),
        source,
        DISCLOSURE,
        rng=RecordedDice([5, 5, 5]),
        system=True,
        correct_reactions=True,
    )
    old, new = (
        json.loads(legacy.events[-1].kind)["private"],
        json.loads(corrected.events[-1].kind)["private"],
    )
    assert old["total"] == 16 and legacy_outcome.outcome == "very-good"
    assert new["total"] == 14 and corrected_outcome.outcome == "good"
    assert [modifier["value"] for modifier in new["modifiers"]] == [1, -2]
    assert "recognition_actor_id" not in old and new["recognition_actor_id"] == "actor"
    assert corrected.events[0] == legacy.events[0] == state.events[0]


@pytest.mark.parametrize("route", ["influence", "skill"])
def test_new_immediate_diplomacy_final_response_changes_actual_disclosure(route: str) -> None:
    source, value = diplomacy_context(route), command().model_copy(update={"kind": route})
    faces = [5, 5, 5, 3, 3, 3, 5, 5, 5]
    rng = CountedDice(faces)
    after, learned, outcome = apply_interaction(
        EMPTY, world(), value, source, DISCLOSURE, rng=rng, system=True, correct_reactions=True
    )
    assert outcome.outcome == "good" and ("actor", "answer") in learned.knowledge and rng.draws == 9
    private = json.loads(after.events[-1].kind)["private"]
    influence = private if route == "influence" else private["influence"]
    assert influence["contest"]["winner"] == "npc" and influence["contest"]["first"]["dice"] == [
        5,
        5,
        5,
    ]
    assert influence["fallback"]["dice"] == [5, 5, 5] and influence["outcome"] == "good"
    if route == "skill":
        assert private["verdict"] == "failure" and private["effect"]["id"] == "diplomacy-rebuffed"
        _, old_world, old_outcome = apply_interaction(
            EMPTY, world(), value, source, DISCLOSURE, rng=RecordedDice(faces), system=True
        )
        assert (
            old_outcome.outcome == "diplomacy-rebuffed"
            and ("actor", "answer") not in old_world.knowledge
        )


@pytest.mark.parametrize(
    "policy,faces,expected",
    [(("neutral",), [3, 3, 4], "neutral"), (("diplomacy-rebuffed",), [3, 3, 3], "poor")],
)
def test_new_immediate_skill_retains_explicit_policy_meanings(
    policy: tuple[str, ...], faces: list[int], expected: str
) -> None:
    disclosure = SocialDisclosure(("answer",), policy)
    _, learned, outcome = apply_interaction(
        EMPTY,
        world(),
        command().model_copy(update={"kind": "skill"}),
        diplomacy_context("skill"),
        disclosure,
        rng=RecordedDice([5, 5, 5, 3, 3, 3] + faces),
        system=True,
        correct_reactions=True,
    )
    assert outcome.outcome == expected and ("actor", "answer") in learned.knowledge


def test_corrected_flag_does_not_rewrite_a_historical_receipt_or_add_its_missing_effect() -> None:
    value = command().model_copy(update={"kind": "skill"})
    state, old_world, old_outcome = apply_interaction(
        EMPTY,
        world(),
        value,
        diplomacy_context("skill"),
        DISCLOSURE,
        rng=RecordedDice([5, 5, 5, 3, 3, 3, 5, 5, 5]),
        system=True,
    )
    old_bytes = state.model_dump_json()
    replayed, unchanged, outcome = apply_interaction(
        state,
        old_world,
        value,
        SocialContext("removed-profile", 0),
        DISCLOSURE,
        rng=RecordedDice([]),
        system=True,
        correct_reactions=True,
    )
    assert (
        replayed.model_dump_json() == old_bytes
        and unchanged == old_world
        and outcome == old_outcome
    )
    assert ("actor", "answer") not in unchanged.knowledge


def test_corrected_immediate_cache_does_not_transfer_recognition_between_characters() -> None:
    source = reputation_context()
    first, current_world, _ = apply_interaction(
        EMPTY,
        world(),
        command(),
        source,
        DISCLOSURE,
        rng=RecordedDice([1, 1, 1, 3, 3, 3]),
        system=True,
        correct_reactions=True,
    )
    rng = CountedDice([6, 6, 6, 4, 4, 4])
    after, learned, outcome = apply_interaction(
        first,
        current_world,
        command(1).model_copy(update={"actor_id": "other"}),
        source,
        DISCLOSURE,
        rng=rng,
        system=True,
        correct_reactions=True,
    )
    assert (
        rng.draws == 6
        and outcome.outcome == "neutral"
        and ("other", "answer") not in learned.knowledge
    )
    assert not json.loads(after.events[-1].kind)["private"]["recognition"][0]["recognized"]


@pytest.mark.parametrize("recovery", ["command", "gm"])
def test_new_immediate_calls_reuse_safely_recovered_legacy_recognition(recovery: str) -> None:
    source = reputation_context()
    state, current_world, _ = apply_interaction(
        EMPTY,
        world(),
        command(),
        source,
        DISCLOSURE,
        rng=RecordedDice([1, 1, 1, 3, 3, 3]),
        system=True,
    )
    rng = CountedDice([])
    with pytest.raises(ValidationError, match="recorded source command"):
        apply_interaction(
            state,
            current_world,
            command(1),
            source,
            DISCLOSURE,
            rng=rng,
            system=True,
            correct_reactions=True,
        )
    assert rng.draws == 0
    if recovery == "gm":
        state = attribute_legacy_recognition(
            state,
            current_world,
            "recover",
            LegacyRecognitionAttribution(
                event_id=state.events[0].id,
                actor_id="actor",
                reason="GM confirms the original encounter",
            ),
            player_actor_ids=("actor",),
            system=True,
        )
    rng = CountedDice([4, 4, 4])
    _, learned, outcome = apply_interaction(
        state,
        current_world,
        command(1).model_copy(update={"expected_revision": state.revision}),
        source,
        DISCLOSURE,
        rng=rng,
        system=True,
        correct_reactions=True,
        recognition_sources=(command(),) if recovery == "command" else (),
    )
    assert rng.draws == 3 and outcome.outcome == "good" and ("actor", "answer") in learned.knowledge


def test_unopposed_skill_is_unchanged_and_never_consumes_reputation_dice() -> None:
    source = reputation_context()
    source.procedure_id = "skill:carousing"
    source.skill_level = 12
    source.conditions = frozenset({"social-gathering", "audience-perceptible"})
    value = command().model_copy(update={"kind": "skill"})
    legacy = apply_interaction(
        EMPTY, world(), value, source, SocialDisclosure(), rng=RecordedDice([3, 3, 3]), system=True
    )
    rng = CountedDice([3, 3, 3])
    corrected = apply_interaction(
        EMPTY,
        world(),
        value,
        source,
        SocialDisclosure(),
        rng=rng,
        system=True,
        correct_reactions=True,
    )
    assert corrected == legacy and rng.draws == 3
