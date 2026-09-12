"""Independent expected results: Characters 4e third printing B21 and B26-28."""

import pytest
from test_mundane_trait_runtime import approved
from test_social_dispatch import world

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.social_traits import bind_standing
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.mundane_traits import PROFILE, inventory
from wayfarer.engine.rules.mundane_traits.runtime import Audience
from wayfarer.engine.rules.social_hooks import (
    Reputation,
    Standing,
    reputation_cost,
    standing_modifiers,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.engine.simulation.social import SocialCommand, SocialContext, apply_social


@pytest.mark.parametrize(
    ("identifier", "expected"),
    [
        ("horrific", -6),
        ("monstrous", -5),
        ("transcendent", 8),
        ("handsome-androgynous", 3),
        ("very-handsome-impressive", 4),
        ("transcendent-androgynous", 5),
    ],
)
def test_exceptional_and_flat_appearance_constructions(identifier: str, expected: int) -> None:
    build, compiler = approved(Purchase(definition_id="trait:appearance-" + identifier))
    standing = bind_standing(build, compiler.definitions, None)
    assert standing is not None
    audience = Audience(attracted=identifier == "transcendent")
    assert standing_modifiers(PROFILE, standing, audience, rng=RecordedDice([])).total == expected


def test_very_handsome_resentment_nuisance_universal_and_off_the_shelf() -> None:
    build, compiler = approved(Purchase(definition_id="trait:appearance-very-handsome"))
    standing = bind_standing(build, compiler.definitions, None)
    assert standing is not None
    trace = standing_modifiers(
        PROFILE,
        standing,
        Audience(appearance_resentment=True, nuisance_interest=True),
        rng=RecordedDice([]),
    )
    assert trace.total == -2 and trace.consequences == ("attention-from-nuisances",)

    build, compiler = approved(Purchase(definition_id="trait:appearance-handsome-universal"))
    universal = bind_standing(build, compiler.definitions, None)
    assert universal is not None
    assert (
        standing_modifiers(
            PROFILE, universal, Audience(appearance_applicable=False), rng=RecordedDice([])
        ).total
        == 3
    )

    build, compiler = approved(Purchase(definition_id="trait:appearance-handsome-off-the-shelf"))
    shelf = bind_standing(build, compiler.definitions, None)
    assert shelf is not None
    assert (
        standing_modifiers(
            PROFILE, shelf, Audience(attracted=True, same_culture=True), rng=RecordedDice([])
        ).total
        == 2
    )


def test_modifier_constructions_have_explicit_rounded_prices() -> None:
    points = {entry.id: entry.points for entry in inventory()}
    assert points["trait:appearance-attractive-universal"] == 5
    assert points["trait:appearance-handsome-off-the-shelf"] == 6
    assert reputation_cost(2, "large-class", "sometimes") == 2
    assert reputation_cost(-2, "small-class", "occasionally") == -1


def test_purchased_class_scoped_uncertain_reputation_uses_catalog_details() -> None:
    build, compiler = approved(Purchase(definition_id="trait:reputation-bravery-guild-sometimes"))
    standing = bind_standing(build, compiler.definitions, None)
    assert standing is not None
    missed = standing_modifiers(
        PROFILE, standing, Audience(classes=("outsider",)), rng=RecordedDice([])
    )
    assert missed.modifiers == () and missed.recognition == ()
    recognized = standing_modifiers(
        PROFILE, standing, Audience(classes=("guild",)), rng=RecordedDice([3, 3, 3])
    )
    assert recognized.total == 2 and recognized.recognition[0].recognized


def test_recognition_is_durable_for_the_same_person_across_triggers() -> None:
    context = SocialContext(
        PROFILE,
        10,
        standing=Standing(reputations=(Reputation("known-to-some", 2, recognition="sometimes"),)),
    )
    first = SocialCommand(
        id="first",
        actor_id="a",
        subject_id="npc",
        kind="reaction",
        trigger_id="meeting-one",
        expected_revision=0,
    )
    state, _ = apply_social(
        ResourceState(), world(), first, context, rng=RecordedDice([3, 3, 3, 3, 3, 3]), system=True
    )
    second = first.model_copy(
        update={"id": "second", "trigger_id": "meeting-two", "expected_revision": 1}
    )
    rng = RecordedDice([3, 3, 3])
    state, _ = apply_social(state, world(), second, context, rng=rng, system=True)
    assert rng.exhausted()
    assert len(state.events) == 2
