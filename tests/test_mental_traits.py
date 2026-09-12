"""Independent expected results: Characters 4e third printing B36,51,85,96,101,124-159."""

import pytest
from pydantic import ValidationError as SchemaError
from test_mundane_traits import runtime_compiler
from test_statistics import gurps_draft

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.traits.mental import mental_traits
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mental import (
    MentalTraits,
    Relationship,
    failed_self_control_obligation,
    frequency_roll,
    validate_relationships,
)
from wayfarer.errors import ValidationError


def projected(*purchases: Purchase) -> MentalTraits:
    engine = runtime_compiler()
    build = engine.compile(gurps_draft(*purchases)).build
    assert build is not None
    return mental_traits(build, engine.definitions)


def test_memory_levels_have_independent_recall_and_learning_results() -> None:
    eidetic = projected(Purchase(definition_id="trait:eidetic-memory"))
    assert eidetic.automatic_recall(detail=False)
    assert not eidetic.automatic_recall(detail=True)
    assert eidetic.modifier("learning") == 5
    photographic = projected(Purchase(definition_id="trait:photographic-memory"))
    assert photographic.automatic_recall(detail=True)
    assert photographic.modifier("learning") == 10


def test_focus_creativity_shyness_and_penetrating_voice_are_contextual() -> None:
    traits = projected(
        Purchase(definition_id="trait:single-minded"),
        Purchase(definition_id="trait:versatile"),
        Purchase(definition_id="trait:shyness-severe"),
        Purchase(definition_id="trait:perk-penetrating-voice"),
    )
    assert traits.modifier("lengthy-mental", focused=True) == 3
    assert traits.modifier("lengthy-mental", focused=True, divided=True) == 0
    assert traits.modifier("notice-interruption", focused=True) == -5
    assert traits.modifier("creative") == 1
    assert traits.modifier("social") == -2
    assert traits.modifier("hearing-penetrating-voice") == 3
    assert traits.modifier("intimidation-surprise") == 1


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("trait:bad-temper", "act-against-source-of-stress"),
        ("trait:curious", "investigate-object-of-curiosity"),
        ("trait:overconfidence", "act-as-if-more-capable"),
        ("trait:honesty", "obey-law-and-act-honorably"),
        ("trait:truthfulness", "tell-truth-or-decline-to-answer"),
    ],
)
def test_failed_self_control_returns_an_obligation_not_a_player_action(
    identifier: str, expected: str
) -> None:
    assert failed_self_control_obligation(identifier) == expected
    engine = runtime_compiler()
    assert engine.compile(
        gurps_draft(Purchase(definition_id=identifier, trait=TraitOptions(self_control=12)))
    ).legal


def test_relationship_frequency_uses_one_recorded_3d_roll() -> None:
    ally = Relationship(
        id="ally:a", person_id="a", kind="ally", frequency=9, character_points_percent=100
    )
    assert frequency_roll(ally, RecordedDice([3, 3, 3])).appears
    missed = frequency_roll(ally, RecordedDice([4, 4, 4]))
    assert not missed.appears and missed.dice == (4, 4, 4)
    constant = ally.model_copy(update={"frequency": 18})
    assert frequency_roll(constant, RecordedDice([])).dice is None


def test_relationship_identity_count_and_ally_dependent_netting_fail_closed() -> None:
    ally = Relationship(
        id="ally:a", person_id="a", kind="ally", frequency=9, character_points_percent=100
    )
    dependent = Relationship(
        id="dependent:a",
        person_id="a",
        kind="dependent",
        frequency=9,
        character_points_percent=50,
    )
    assert validate_relationships((ally, dependent)) == (ally, dependent)
    with pytest.raises(ValidationError, match="share one frequency"):
        validate_relationships((ally, dependent.model_copy(update={"frequency": 12})))
    with pytest.raises(ValidationError, match="Duplicate"):
        validate_relationships((ally, ally))
    with pytest.raises(SchemaError, match="authoritative skill"):
        Relationship(id="contact:a", person_id="a", kind="contact")
