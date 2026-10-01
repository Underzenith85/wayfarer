"""Independent Campaigns B460 animal damage expectations through live reducers."""

import pytest
from test_creature_combat import creature_state, natural
from test_creatures import catalog

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.creature_combat import (
    NaturalAttackOutcome,
    apply_creature_combat,
)
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import ConflictError


@pytest.mark.parametrize(
    ("species", "attack", "rolls", "damage", "injury"),
    [
        ("house-cat", "bite", [4], 1, 1),
        ("large-guard-dog", "bite", [4], 2, 3),
        ("timber-wolf", "bite", [4], 2, 3),
        ("cavalry-horse", "kick", [4, 4], 8, 8),
        ("draft-horse", "kick", [4, 4], 10, 10),
        ("gryphon", "claw", [4], 6, 9),
        ("gryphon", "beak", [4], 6, 9),
    ],
)
def test_each_registered_physical_attack_reaches_source_injury_consumer(
    species: str, attack: str, rolls: list[int], damage: int, injury: int
) -> None:
    # B16 thrust: ST4=1d-5,9/10=1d-2,17=1d+2,22=2d,25=2d+2.
    # B460 bite/claw -1; Brawling DX+2 +1/die. Hooves and Horizontal cancel.
    creature = catalog().compile("wolf", species, "creature:" + species).creature
    seed = creature_state().model_copy(
        update={"creatures": (creature, creature_state().creatures[1])}
    )
    command = natural(attack_id=attack, motivation="defensive", target_dr=0)
    dice = RecordedDice([2, 2, 2, *rolls, *([2, 2, 2] if injury > 4 else [])])
    updated, result = apply_creature_combat(seed, command, rng=dice, system=True)
    assert isinstance(result, NaturalAttackOutcome) and result.hit
    assert result.damage_dice == tuple(rolls)
    assert result.basic_damage == damage
    assert result.injury is not None and result.injury.injury == injury
    assert next(p.current for p in updated.pools if p.id == "hp:dog") == 9 - injury
    assert dice.exhausted()
    restored = ResourceState.model_validate_json(updated.model_dump_json())
    assert apply_creature_combat(restored, command, rng=RecordedDice([]), system=True) == (
        restored,
        result,
    )
    with pytest.raises(ConflictError):
        apply_creature_combat(
            restored, command.model_copy(update={"id": "stale"}), rng=RecordedDice([]), system=True
        )


def test_meta_trait_and_racial_attribute_expansion_prices_actual_components() -> None:
    from test_mundane_traits import runtime_compiler
    from test_statistics import gurps_draft

    from wayfarer.engine.character.compiler import Purchase
    from wayfarer.engine.character.templates import Template, TemplateCatalog, TemplateComponent

    # B258/B262/B454: template total is sum of individual traits, no discount.
    templates = TemplateCatalog(
        (
            Template(
                id="meta:healthy",
                kind="meta-trait",
                purchases=(Purchase(definition_id="trait:fit"),),
            ),
            Template(
                id="race:strong",
                kind="racial",
                includes=("meta:healthy",),
                components=(
                    TemplateComponent(
                        purchase=Purchase(definition_id="attribute:st", amount=2), mode="additive"
                    ),
                ),
            ),
        ),
        runtime_compiler(),
    )
    result = templates.preview(gurps_draft(), ("race:strong",))
    assert result.compilation.legal and result.compilation.build is not None
    assert result.compilation.spent == 25  # ST +2 [20], Fit [5].
    assert next(p.amount for p in result.draft.purchases if p.definition_id == "attribute:st") == 12
    assert {p.definition_id for p in result.compilation.build.trait_purchases} == {"trait:fit"}
    assert any(
        p.origin == "meta-trait" and p.definition_id == "trait:fit" for p in result.provenance
    )
