"""Independent hand-entered status, reputation and appearance expectations (#111).

Values come from `tests/fixtures/gurps/social_hooks.json`, entered from the named
source pages rather than from the implementation under test. The frozen-artifact
audit remains pending under the provisional implementation policy.
"""

import json
from pathlib import Path
from typing import cast

import pytest
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.rules.checks import RecordedDice
from wayfarer.rules.gurps_social import (
    InfluenceSkill,
    fright_roll,
    influence_roll,
    reaction_roll,
)
from wayfarer.rules.social_hooks import (
    APPEARANCE_REACTIONS,
    Appearance,
    Audience,
    Recognition,
    Reputation,
    ReputationScope,
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
Case = dict[str, object]


def fixture() -> Case:
    value = json.loads((Path(__file__).parent / "fixtures/gurps/social_hooks.json").read_text())
    assert isinstance(value, dict)
    return cast(Case, value)


FIXTURE = fixture()


def cases(section: str) -> list[Case]:
    """The hand-entered rows of one fixture section, kept in file order."""
    value = FIXTURE[section]
    assert isinstance(value, list)
    return cast(list[Case], value)


def rows(case: Case, key: str) -> list[Case]:
    value = case.get(key, [])
    assert isinstance(value, list)
    return cast(list[Case], value)


def mapping(case: Case, key: str) -> Case:
    value = case.get(key, {})
    assert isinstance(value, dict)
    return cast(Case, value)


def number(case: Case, key: str) -> int:
    value = case[key]
    assert isinstance(value, int)
    return value


def text(case: Case, key: str) -> str:
    value = case[key]
    assert isinstance(value, str)
    return value


def flag(case: Case, key: str) -> bool:
    value = case[key]
    assert isinstance(value, bool)
    return value


def dice(case: Case) -> RecordedDice:
    value = case["dice"]
    assert isinstance(value, list)
    return RecordedDice(cast(list[int], value))


def standing(values: Case) -> Standing:
    reputations = tuple(
        Reputation(
            text(entry, "id"),
            number(entry, "level"),
            cast(ReputationScope, entry.get("scope", "everyone")),
            cast(Recognition, entry.get("recognition", "always")),
            tuple(cast(list[str], entry.get("classes", []))),
            bool(entry.get("hidden", False)),
        )
        for entry in rows(values, "reputations")
    )
    return Standing(
        cast(Appearance, values.get("appearance", "average")),
        cast(int, values.get("status", 0)),
        cast(int, values.get("charisma", 0)),
        bool(values.get("voice", False)),
        reputations,
    )


def audience(values: Case) -> Audience:
    return Audience(
        bool(values.get("recognizes_status", True)),
        bool(values.get("attracted", False)),
        tuple(cast(list[str], values.get("classes", []))),
        bool(values.get("sees_appearance", True)),
        bool(values.get("hears_voice", True)),
    )


@pytest.mark.parametrize("case", cases("appearance"), ids=lambda case: str(case["level"]))
def test_appearance_reaction_columns(case: Case) -> None:
    level = cast(Appearance, text(case, "level"))
    indifferent, attracted = number(case, "indifferent"), number(case, "attracted")
    assert APPEARANCE_REACTIONS[level] == (indifferent, attracted)
    for drawn, expected in ((False, indifferent), (True, attracted)):
        trace = standing_modifiers(
            PROFILE,
            Standing(appearance=level),
            Audience(attracted=drawn),
            rng=RecordedDice([]),
        )
        assert trace.total == expected
        assert trace.modifiers == () or trace.modifiers[0].kind == "appearance"


@pytest.mark.parametrize("case", cases("reaction"), ids=lambda case: str(case["name"]))
def test_golden_reaction_cases(case: Case) -> None:
    rng = dice(case)
    trace = standing_modifiers(
        PROFILE, standing(mapping(case, "standing")), audience(mapping(case, "audience")), rng=rng
    )
    assert trace.total == number(case, "modifier_total")
    assert [
        {
            "reputation_id": roll.reputation_id,
            "target": roll.target,
            "total": roll.total,
            "recognized": roll.recognized,
        }
        for roll in trace.recognition
    ] == rows(case, "recognition")
    reaction = reaction_roll(PROFILE, trace.modifiers, rng=rng)
    assert reaction.outcome == text(case, "outcome")
    assert rng.exhausted()


@pytest.mark.parametrize("case", cases("influence"), ids=lambda case: str(case["name"]))
def test_golden_influence_cases(case: Case) -> None:
    rng = dice(case)
    trace = standing_modifiers(
        PROFILE, standing(mapping(case, "standing")), audience(mapping(case, "audience")), rng=rng
    )
    influence = influence_roll(
        PROFILE,
        cast(InfluenceSkill, text(case, "skill")),
        "pc",
        "npc",
        number(case, "target"),
        number(case, "npc_will"),
        trace.modifiers,
        rng=rng,
    )
    assert influence.contest.first.effective_target == number(case, "effective_target")
    assert influence.contest.winner == ("pc" if text(case, "winner") == "actor" else "npc")
    assert influence.outcome == text(case, "outcome")
    assert rng.exhausted()


@pytest.mark.parametrize("case", cases("fright"), ids=lambda case: str(case["name"]))
def test_golden_fright_cases(case: Case) -> None:
    rng = dice(case)
    result = fright_roll(PROFILE, number(case, "will"), number(case, "modifier"), rng=rng)
    assert result.check.effective_target == number(case, "effective_target")
    assert result.check.outcome.succeeded is flag(case, "succeeded")
    assert result.check.margin == number(case, "margin")
    assert result.table_total == case["table_total"]
    assert rng.exhausted()


@pytest.mark.parametrize("case", cases("rejected_standing"), ids=lambda case: str(case["name"]))
def test_invented_standing_is_rejected(case: Case) -> None:
    declared = standing(mapping(case, "standing"))
    with pytest.raises(ValidationError):
        validate_standing(declared)
    with pytest.raises(ValidationError):
        standing_modifiers(PROFILE, declared, Audience(), rng=RecordedDice([]))


def test_hooks_are_bound_to_a_declared_profile_and_capability() -> None:
    assert supported_appearance("gurps-lite-4e-2004") == tuple(
        text(entry, "level") for entry in cases("appearance")
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
