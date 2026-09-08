"""Independent hand-entered status, reputation and appearance expectations (#111).

Values come from `tests/fixtures/gurps/social_hooks.json`, entered from the named
source pages rather than from the implementation under test. The frozen-artifact
audit remains pending under the provisional implementation policy.
"""

import json
from pathlib import Path
from typing import Any

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.gurps_social import fright_roll, influence_roll, reaction_roll
from wayfarer.rules.social_hooks import (
    APPEARANCE_REACTIONS,
    Audience,
    Reputation,
    Standing,
    standing_modifiers,
    supported_appearance,
    validate_standing,
)
from wayfarer.rules.traits import TraitOptions, TraitRules
from wayfarer.simulation.npcs import NPCSocialStanding, NPCSocialTrigger
from wayfarer.simulation.resources import ResourceState
from wayfarer.simulation.social import SocialCommand, SocialContext, apply_social
from wayfarer.world import Entity, EntityKind, Fact, World

PROFILE = "gurps-basic-set-4e-2004"
FIXTURE: Any = json.loads((Path(__file__).parent / "fixtures/gurps/social_hooks.json").read_text())


def standing(values: dict[str, Any]) -> Standing:
    reputations = tuple(
        Reputation(
            entry["id"],
            entry["level"],
            entry.get("scope", "everyone"),
            entry.get("recognition", "always"),
            tuple(entry.get("classes", ())),
            entry.get("hidden", False),
        )
        for entry in values.get("reputations", ())
    )
    return Standing(
        values.get("appearance", "average"),
        values.get("status", 0),
        values.get("charisma", 0),
        values.get("voice", False),
        reputations,
    )


def audience(values: dict[str, Any]) -> Audience:
    return Audience(
        values.get("recognizes_status", True),
        values.get("attracted", False),
        tuple(values.get("classes", ())),
        values.get("sees_appearance", True),
        values.get("hears_voice", True),
    )


@pytest.mark.parametrize("case", FIXTURE["appearance"], ids=lambda case: case["level"])
def test_appearance_reaction_columns(case: dict[str, Any]) -> None:
    assert APPEARANCE_REACTIONS[case["level"]] == (case["indifferent"], case["attracted"])
    for attracted, expected in ((False, case["indifferent"]), (True, case["attracted"])):
        trace = standing_modifiers(
            PROFILE,
            Standing(appearance=case["level"]),
            Audience(attracted=attracted),
            rng=RecordedDice([]),
        )
        assert trace.total == expected
        assert trace.modifiers == () or trace.modifiers[0].kind == "appearance"


@pytest.mark.parametrize("case", FIXTURE["reaction"], ids=lambda case: case["name"])
def test_golden_reaction_cases(case: dict[str, Any]) -> None:
    rng = RecordedDice(case["dice"])
    trace = standing_modifiers(
        PROFILE, standing(case["standing"]), audience(case["audience"]), rng=rng
    )
    assert trace.total == case["modifier_total"]
    assert [
        {
            "reputation_id": roll.reputation_id,
            "target": roll.target,
            "total": roll.total,
            "recognized": roll.recognized,
        }
        for roll in trace.recognition
    ] == case["recognition"]
    reaction = reaction_roll(PROFILE, trace.modifiers, rng=rng)
    assert reaction.outcome == case["outcome"]
    assert rng.exhausted()


@pytest.mark.parametrize("case", FIXTURE["influence"], ids=lambda case: case["name"])
def test_golden_influence_cases(case: dict[str, Any]) -> None:
    rng = RecordedDice(case["dice"])
    trace = standing_modifiers(
        PROFILE, standing(case["standing"]), audience(case["audience"]), rng=rng
    )
    influence = influence_roll(
        PROFILE,
        case["skill"],
        "pc",
        "npc",
        case["target"],
        case["npc_will"],
        trace.modifiers,
        rng=rng,
    )
    assert influence.contest.first.effective_target == case["effective_target"]
    assert influence.contest.winner == ("pc" if case["winner"] == "actor" else "npc")
    assert influence.outcome == case["outcome"]
    assert rng.exhausted()


@pytest.mark.parametrize("case", FIXTURE["fright"], ids=lambda case: case["name"])
def test_golden_fright_cases(case: dict[str, Any]) -> None:
    rng = RecordedDice(case["dice"])
    result = fright_roll(PROFILE, case["will"], case["modifier"], rng=rng)
    assert result.check.effective_target == case["effective_target"]
    assert result.check.outcome.succeeded is case["succeeded"]
    assert result.check.margin == case["margin"]
    assert result.table_total == case["table_total"]
    assert rng.exhausted()


@pytest.mark.parametrize("case", FIXTURE["rejected_standing"], ids=lambda case: case["name"])
def test_invented_standing_is_rejected(case: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        validate_standing(standing(case["standing"]))
    with pytest.raises(ValidationError):
        standing_modifiers(PROFILE, standing(case["standing"]), Audience(), rng=RecordedDice([]))


def test_hooks_are_bound_to_a_declared_profile_and_capability() -> None:
    assert supported_appearance("gurps-lite-4e-2004") == tuple(
        entry["level"] for entry in FIXTURE["appearance"]
    )
    with pytest.raises(ValidationError):
        supported_appearance("prototype")
    with pytest.raises(ValidationError):
        standing_modifiers("prototype", Standing(), Audience(), rng=RecordedDice([]))


def test_authored_standing_cannot_widen_the_declared_ranges() -> None:
    approved = NPCSocialStanding.model_validate(
        {"appearance": "handsome", "status": 3, "reputations": ({"id": "hero", "level": 2},)}
    )
    assert approved.appearance == "handsome" and approved.audience_recognizes_status
    with pytest.raises(SchemaError):
        NPCSocialTrigger.model_validate(
            {"kind": "fright", "subject_id": "npc", "standing": {"status": 2}}
        )
    for invalid in (
        {"appearance": "radiant"},
        {"status": 9},
        {"charisma": -1},
        {"reputations": ({"id": "hero", "level": 5},)},
        {"reputations": ({"id": "hero", "level": 2, "recognition": "rarely"},)},
        {"modifier": 3},
    ):
        with pytest.raises(SchemaError):
            NPCSocialStanding.model_validate(invalid)


def world() -> World:
    return World(
        entities=(
            Entity("pc", EntityKind.ACTOR, "Player"),
            Entity("npc", EntityKind.ACTOR, "Steward"),
        ),
        facts=(Fact("secret", "npc", "loyalty", "enemy"),),
        knowledge=(("npc", "secret"),),
    )


def command(kind: str, identifier: str = "social-1") -> SocialCommand:
    return SocialCommand(
        id=identifier,
        actor_id="pc",
        expected_revision=0,
        kind=kind,  # type: ignore[arg-type]
        subject_id="npc",
        trigger_id="audience-1",
    )


def test_derived_standing_stays_out_of_the_player_projection() -> None:
    context = SocialContext(
        PROFILE,
        10,
        standing=Standing(
            appearance="handsome",
            status=2,
            reputations=(Reputation("informer", -4, "small-class", "sometimes", ("watch",), True),),
        ),
        audience=Audience(classes=("watch",)),
        required_fact_ids=("secret",),
    )
    # Recognition dice precede the reaction roll: 3+3+3 = 9 recognizes the secret
    # reputation, so 4+4+4 = 12 plus 2 (appearance) + 2 (Status) - 4 lands on 12.
    state, public = apply_social(
        ResourceState(),
        world(),
        command("reaction"),
        context,
        rng=RecordedDice([3, 3, 3, 4, 4, 4]),
        system=True,
    )
    assert public.outcome == "neutral"
    payload = public.model_dump_json()
    for secret in ("informer", "reputation", "appearance", "recognized", "-4"):
        assert secret not in payload
    private = json.loads(state.events[-1].kind)["private"]
    assert private["recognition"][0]["reputation_id"] == "informer"
    assert [modifier["hidden"] for modifier in private["modifiers"]] == [False, False, True]
    replayed = apply_social(
        ResourceState.model_validate_json(state.model_dump_json()),
        world(),
        command("reaction"),
        context,
        rng=RecordedDice([]),
        system=True,
    )
    assert replayed == (state, public)


def test_self_control_timing_is_deterministic_and_retry_safe() -> None:
    context = SocialContext(
        PROFILE,
        0,
        trait_options=TraitOptions.model_validate({"self_control": 12}),
        trait_rules=TraitRules(PROFILE, self_control=True),
        trait_base=-10,
    )
    state, public = apply_social(
        ResourceState(),
        world(),
        command("self-control"),
        context,
        rng=RecordedDice([4, 4, 4]),
        system=True,
    )
    assert public.outcome == "resisted" and "12" not in public.model_dump_json()
    # The same command replays from the receipt without drawing a die.
    assert apply_social(
        state, world(), command("self-control"), context, rng=RecordedDice([]), system=True
    ) == (state, public)
    # A different command cannot re-roll the same disadvantage for the same trigger.
    with pytest.raises(ConflictError):
        apply_social(
            state,
            world(),
            command("self-control", "social-2").model_copy(update={"expected_revision": 1}),
            context,
            rng=RecordedDice([3, 3, 3]),
            system=True,
        )
