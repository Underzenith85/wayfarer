"""B27 recognition belongs to one character/observer and its authored frequency."""

import json
from dataclasses import replace
from typing import Literal

import pytest
from test_prepared_reactions import (
    DISCLOSURE,
    EMPTY,
    PROFILE,
    CountedDice,
    command,
    finish,
    prepare,
    world,
)

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.gurps_social import ReactionModifier, evaluate_reaction
from wayfarer.engine.rules.social.social_hooks import RecognitionRoll, Reputation, Standing
from wayfarer.engine.simulation.social.reactions import prepare_reaction, recognize_reaction
from wayfarer.engine.simulation.social.social import (
    SocialContext,
    SocialDisclosure,
    apply_interaction,
)
from wayfarer.errors import ValidationError


def context(
    frequency: Literal["always", "sometimes", "occasionally"] = "occasionally",
) -> SocialContext:
    return SocialContext(
        PROFILE, 0, standing=Standing(reputations=(Reputation("hero", 2, recognition=frequency),))
    )


def test_forged_fresh_frequency_cannot_turn_neutral_into_good_and_disclose() -> None:
    source = context()
    prepared = prepare(context=source)
    legitimate = recognize_reaction(prepared, rng=RecordedDice([3, 3, 3]))
    assert legitimate.recognition == (RecognitionRoll("hero", (3, 3, 3), 9, 7, False),)
    trace = evaluate_reaction(PROFILE, (), (4, 4, 4))
    _, unchanged, outcome = finish(prepared, legitimate, trace, context=source)
    assert outcome.outcome == "neutral" and unchanged == world()
    forged = legitimate.model_copy(
        update={
            "recognition": (replace(legitimate.recognition[0], target=10, recognized=True),),
            "modifiers": (ReactionModifier("reputation", 2, "reputation:hero"),),
        }
    )
    with pytest.raises(ValidationError, match="prepared reputation"):
        finish(
            prepared,
            forged,
            evaluate_reaction(PROFILE, forged.modifiers, (4, 4, 4)),
            context=source,
        )
    assert ("actor", "answer") not in world().knowledge


@pytest.mark.parametrize(
    "forgery",
    [
        "flag",
        "target",
        "total",
        "missing",
        "duplicate",
        "foreign",
        "provenance",
        "modifier-value",
        "modifier-hidden",
    ],
)
def test_fresh_recognition_rows_and_derived_modifiers_fail_closed(forgery: str) -> None:
    source = context()
    prepared = prepare(context=source)
    recognized = recognize_reaction(prepared, rng=RecordedDice([1, 1, 1]))
    row = recognized.recognition[0]
    changes: dict[str, object]
    if forgery == "flag":
        changes = {"recognition": (replace(row, recognized=False),)}
    elif forgery == "target":
        changes = {"recognition": (replace(row, target=10),)}
    elif forgery == "total":
        changes = {"recognition": (replace(row, total=4),)}
    elif forgery == "missing":
        changes = {"recognition": ()}
    elif forgery == "duplicate":
        changes = {"recognition": (row, row)}
    elif forgery == "foreign":
        changes = {"recognition": (replace(row, reputation_id="someone-else"),)}
    elif forgery == "provenance":
        changes = {"preparation_provenance": "different-source"}
    elif forgery == "modifier-value":
        changes = {"modifiers": (replace(recognized.modifiers[0], value=3),)}
    else:
        changes = {"modifiers": (replace(recognized.modifiers[0], hidden=0),)}  # type: ignore[arg-type]
    forged = recognized.model_copy(update=changes)
    with pytest.raises(ValidationError):
        finish(
            prepared,
            forged,
            evaluate_reaction(PROFILE, recognized.modifiers, (4, 4, 4)),
            context=source,
        )


def test_always_recognized_source_has_no_roll_and_rejects_an_invented_row() -> None:
    source = context("always")
    prepared = prepare(context=source)
    recognized = recognize_reaction(prepared, rng=RecordedDice([]))
    assert recognized.recognition == () and recognized.modifiers[0].value == 2
    selected = evaluate_reaction(PROFILE, recognized.modifiers, (4, 4, 4))
    _, learned, outcome = finish(prepared, recognized, selected, context=source)
    assert outcome.outcome == "good" and ("actor", "answer") in learned.knowledge
    forged = recognized.model_copy(
        update={"recognition": (RecognitionRoll("hero", (1, 1, 1), 3, 10, True),)}
    )
    with pytest.raises(ValidationError, match="prepared reputation"):
        finish(prepared, forged, selected, context=source)


def test_same_reputation_id_on_another_character_does_not_inherit_recognition() -> None:
    source = context()
    first = prepare(context=source)
    recognized = recognize_reaction(first, rng=RecordedDice([1, 1, 1]))
    state, current_world, _ = finish(
        first,
        recognized,
        evaluate_reaction(PROFILE, recognized.modifiers, (3, 3, 3)),
        context=source,
    )
    assert json.loads(state.events[-1].kind)["private"]["recognition_actor_id"] == "actor"
    second = prepare(
        state, current_world, command(1).model_copy(update={"actor_id": "other"}), source
    )
    assert second.known_recognition == ()
    rng = CountedDice([6, 6, 6])
    unfamiliar = recognize_reaction(second, rng=rng)
    assert rng.draws == 3 and unfamiliar.recognition[0].recognized is False
    _, after, _ = finish(
        second,
        unfamiliar,
        evaluate_reaction(PROFILE, (), (4, 4, 4)),
        state=state,
        source_world=current_world,
        context=source,
    )
    assert ("other", "answer") not in after.knowledge


def test_recognition_is_also_scoped_to_the_reacting_npc() -> None:
    source = context()
    first = prepare(context=source)
    recognized = recognize_reaction(first, rng=RecordedDice([1, 1, 1]))
    state, current_world, _ = finish(
        first,
        recognized,
        evaluate_reaction(PROFILE, recognized.modifiers, (3, 3, 3)),
        context=source,
    )
    second = prepare(
        state,
        current_world,
        command(1).model_copy(update={"subject_id": "other"}),
        source,
        SocialDisclosure(),
    )
    assert second.known_recognition == ()
    rng = CountedDice([6, 6, 6])
    assert recognize_reaction(second, rng=rng).recognition[0].recognized is False and rng.draws == 3


def test_legitimate_memory_keeps_exact_original_roll_even_after_frequency_changes() -> None:
    source = context("sometimes")
    first = prepare(context=source)
    recognized = recognize_reaction(first, rng=RecordedDice([3, 3, 3]))
    state, current_world, _ = finish(
        first,
        recognized,
        evaluate_reaction(PROFILE, recognized.modifiers, (3, 3, 3)),
        context=source,
    )
    source = context("occasionally")
    second = prepare(state, current_world, command(1), source)
    remembered = recognize_reaction(second, rng=RecordedDice([]))
    assert remembered.recognition == recognized.recognition
    _, learned, outcome = finish(
        second,
        remembered,
        evaluate_reaction(PROFILE, remembered.modifiers, (4, 4, 4)),
        state=state,
        source_world=current_world,
        context=source,
    )
    assert outcome.outcome == "good" and ("actor", "answer") in learned.knowledge
    forged = remembered.model_copy(
        update={
            "recognition": (replace(remembered.recognition[0], dice=(1, 1, 1), total=3, target=7),)
        }
    )
    with pytest.raises(ValidationError, match="Remembered reaction recognition changed"):
        finish(
            second,
            forged,
            evaluate_reaction(PROFILE, remembered.modifiers, (4, 4, 4)),
            state=state,
            source_world=current_world,
            context=source,
        )


@pytest.mark.parametrize("kind,remembered", [("influence", True), ("skill", True)])
def test_only_attributable_legacy_recognition_can_be_reused(kind: str, remembered: bool) -> None:
    source = context()
    faces = [1, 1, 1, 4, 4, 4]
    if kind != "reaction":
        faces += [4, 4, 4, 4, 4, 4]
    if kind == "skill":
        source.procedure_id = "skill:diplomacy"
        source.skill_level = 10
        source.conditions = frozenset({"audience-audible", "shared-language"})
    state, current_world, _ = apply_interaction(
        EMPTY,
        world(),
        command().model_copy(update={"kind": kind}),
        source,
        DISCLOSURE,
        rng=RecordedDice(faces),
        system=True,
    )
    original_bytes = state.model_dump_json()
    prepared = prepare(state, current_world, command(1), context())
    assert bool(prepared.known_recognition) is remembered
    rng = CountedDice([] if remembered else [3, 3, 3])
    recognized = recognize_reaction(prepared, rng=rng)
    assert rng.draws == (0 if remembered else 3)
    assert recognized.recognition[0].recognized is remembered
    assert state.model_dump_json() == original_bytes


def test_plain_legacy_recognition_requires_verified_command_attribution() -> None:
    source = context()
    original = command()
    state, current_world, _ = apply_interaction(
        EMPTY,
        world(),
        original,
        source,
        DISCLOSURE,
        rng=RecordedDice([1, 1, 1, 4, 4, 4]),
        system=True,
    )
    original_bytes = state.model_dump_json()
    with pytest.raises(ValidationError, match="recorded source command"):
        prepare(state, current_world, command(1), source)
    prepared = prepare_reaction(
        state,
        current_world,
        command(1),
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
        recognition_sources=(original,),
    )
    recognized = recognize_reaction(prepared, rng=RecordedDice([]))
    assert recognized.recognition == (RecognitionRoll("hero", (1, 1, 1), 3, 7, True),)
    _, learned, _ = finish(
        prepared,
        recognized,
        evaluate_reaction(PROFILE, recognized.modifiers, (4, 4, 4)),
        state=state,
        source_world=current_world,
        context=source,
    )
    assert ("actor", "answer") in learned.knowledge
    assert state.model_dump_json() == original_bytes
    # The same verified original belongs to actor, not to the other character.
    second = prepare_reaction(
        state,
        current_world,
        command(1).model_copy(update={"actor_id": "other"}),
        source,
        DISCLOSURE,
        profile_id=PROFILE,
        player_actor_ids=("actor",),
        system=True,
        recognition_sources=(original,),
    )
    assert second.known_recognition == ()
    rng = CountedDice([6, 6, 6])
    assert not recognize_reaction(second, rng=rng).recognition[0].recognized
    assert rng.draws == 3


@pytest.mark.parametrize("change", ["actor_id", "expected_revision", "id", "trigger_id"])
def test_legacy_attribution_cannot_forge_the_committed_source_command(change: str) -> None:
    source = context()
    state, current_world, _ = apply_interaction(
        EMPTY,
        world(),
        command(),
        source,
        DISCLOSURE,
        rng=RecordedDice([1, 1, 1, 4, 4, 4]),
        system=True,
    )
    original = command().model_copy(
        update={change: 99 if change == "expected_revision" else "forged"}
    )
    with pytest.raises(ValidationError, match="(recorded source command|committed receipt)"):
        prepare_reaction(
            state,
            current_world,
            command(1),
            source,
            DISCLOSURE,
            profile_id=PROFILE,
            player_actor_ids=("actor",),
            system=True,
            recognition_sources=(original,),
        )
