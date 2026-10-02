"""A trusted GM can restore missing identity without replacing old recognition."""

import json

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
from test_reaction_recognition_provenance import context

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.social.gurps_social import evaluate_reaction
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social.reactions import (
    LegacyRecognitionAttribution,
    attribute_legacy_recognition,
    prepare_reaction,
    recognize_reaction,
)
from wayfarer.engine.simulation.social.social import apply_interaction
from wayfarer.engine.world import World
from wayfarer.errors import ConflictError, ValidationError


def legacy() -> tuple[ResourceState, World]:
    state, source_world, _ = apply_interaction(
        EMPTY,
        world(),
        command(),
        context(),
        DISCLOSURE,
        rng=RecordedDice([3, 3, 3, 3, 3, 3]),
        system=True,
    )
    return state, source_world


def attribution() -> LegacyRecognitionAttribution:
    return LegacyRecognitionAttribution(
        event_id="social:reaction:npc:trigger0",
        actor_id="actor",
        reason="GM confirms this encounter involved this character",
    )


def test_explicit_gm_attribution_preserves_old_decision_and_original_event_bytes() -> None:
    state, source_world = legacy()
    original = state.events[0].model_dump_json()
    with pytest.raises(ValidationError, match="recorded source command"):
        prepare(state, source_world, command(1), context())
    recovered = attribute_legacy_recognition(
        state, source_world, "recover", attribution(), player_actor_ids=("actor",), system=True
    )
    assert recovered.revision == 2 and recovered.events[0].model_dump_json() == original
    assert recovered.events[:-1] == state.events and recovered.receipts[:-1] == state.receipts
    assert source_world == world()
    marker = json.loads(recovered.events[-1].kind)
    assert json.loads(marker["public"])["outcome"] == "recognition-recorded"
    assert attribution().reason not in marker["public"]
    prepared = prepare(recovered, source_world, command(2), context())
    recognized = recognize_reaction(prepared, rng=RecordedDice([]))
    assert recognized.recognition[0].dice == (3, 3, 3)
    assert recognized.recognition[0].target == 7 and not recognized.recognition[0].recognized
    _, unchanged, outcome = finish(
        prepared,
        recognized,
        evaluate_reaction(PROFILE, (), (4, 4, 4)),
        state=recovered,
        source_world=source_world,
        context=context(),
    )
    assert outcome.outcome == "neutral" and unchanged == source_world
    assert (
        attribute_legacy_recognition(
            recovered,
            source_world,
            "recover",
            attribution(),
            player_actor_ids=("actor",),
            system=True,
        )
        == recovered
    )
    with pytest.raises(ConflictError, match="immutable attribution"):
        attribute_legacy_recognition(
            recovered,
            source_world,
            "replace",
            attribution().model_copy(update={"actor_id": "other"}),
            player_actor_ids=("actor",),
            system=True,
        )
    # The GM identifies the original actor, not every character sharing this trait ID.
    other = prepare(
        recovered, source_world, command(2).model_copy(update={"actor_id": "other"}), context()
    )
    rng = CountedDice([1, 1, 1])
    assert recognize_reaction(other, rng=rng).recognition[0].recognized and rng.draws == 3


@pytest.mark.parametrize(
    "invalid",
    [
        "authority",
        "missing-event",
        "missing-actor",
        "same-actor",
        "player-observer",
        "no-recognition",
    ],
)
def test_gm_recovery_validates_its_specific_original_event_and_actor(invalid: str) -> None:
    state, source_world = legacy()
    value, system, players = attribution(), True, ("actor",)
    if invalid == "authority":
        system = False
    elif invalid == "missing-event":
        value = value.model_copy(update={"event_id": "social:reaction:npc:missing"})
    elif invalid == "missing-actor":
        value = value.model_copy(update={"actor_id": "missing"})
    elif invalid == "same-actor":
        value = value.model_copy(update={"actor_id": "npc"})
    elif invalid == "player-observer":
        players = ("npc",)
    else:
        state = state.model_copy(
            update={"events": (state.events[0].model_copy(update={"kind": "{}"}),)}
        )
    with pytest.raises(ValidationError):
        attribute_legacy_recognition(
            state, source_world, "recover", value, player_actor_ids=players, system=system
        )


@pytest.mark.parametrize("tamper", ["old-event", "marker-receipt"])
def test_adjudicated_attribution_remains_bound_to_the_unchanged_original(tamper: str) -> None:
    state, source_world = legacy()
    recovered = attribute_legacy_recognition(
        state, source_world, "recover", attribution(), player_actor_ids=("actor",), system=True
    )
    if tamper == "old-event":
        payload = json.loads(recovered.events[0].kind)
        payload["private"]["recognition"][0].update(dice=[1, 1, 1], total=3, recognized=True)
        recovered = recovered.model_copy(
            update={
                "events": (
                    recovered.events[0].model_copy(update={"kind": json.dumps(payload)}),
                    recovered.events[1],
                )
            }
        )
    else:
        recovered = recovered.model_copy(update={"receipts": recovered.receipts[:-1]})
    with pytest.raises(ValidationError, match="source event"):
        prepare(recovered, source_world, command(2), context())


def test_new_recognition_already_has_identity_and_cannot_be_adjudicated_away() -> None:
    source = context()
    prepared = prepare(context=source)
    recognized = recognize_reaction(prepared, rng=RecordedDice([1, 1, 1]))
    state, current_world, _ = finish(
        prepared,
        recognized,
        evaluate_reaction(PROFILE, recognized.modifiers, (3, 3, 3)),
        context=source,
    )
    with pytest.raises(ValidationError, match="already identifies"):
        attribute_legacy_recognition(
            state, current_world, "recover", attribution(), player_actor_ids=("actor",), system=True
        )


def test_gm_attribution_cannot_contradict_a_later_supplied_immutable_command() -> None:
    state, source_world = legacy()
    # The GM must be accurate: if later recovered command evidence contradicts
    # an attribution, fail explicitly instead of silently picking one identity.
    recovered = attribute_legacy_recognition(
        state,
        source_world,
        "recover",
        attribution().model_copy(update={"actor_id": "other"}),
        player_actor_ids=("actor",),
        system=True,
    )
    with pytest.raises(ValidationError, match="conflicts with its immutable source"):
        prepare_reaction(
            recovered,
            source_world,
            command(2),
            context(),
            DISCLOSURE,
            profile_id=PROFILE,
            player_actor_ids=("actor",),
            system=True,
            recognition_sources=(command(),),
        )
