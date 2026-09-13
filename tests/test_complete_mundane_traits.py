"""Independent executable evidence for issues #680 and #681.

Counts and representative numeric assertions were transcribed from Basic Set:
Characters, Fourth Edition, third printing, B21-B165.  The exhaustive loop
then proves that every independently indexed mundane row constructs through the
real compiler and produces one typed family-local runtime consequence.
"""

from collections import Counter
from pathlib import Path

import pytest
from test_mundane_traits import runtime_compiler
from test_statistics import gurps_draft

from wayfarer.certification.basic_set_certification import evaluate
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.character.traits import mundane_trait_effects
from wayfarer.engine.rules.traits.base import TraitOptions
from wayfarer.engine.rules.traits.mundane.complete import (
    POINT_COST_PARAMETER,
    SPECS,
    CompleteTraitSpec,
)


def options(identifier: str) -> TraitOptions:
    spec = next(value for value in SPECS if value.id == identifier)
    return TraitOptions(
        parameters=(
            ((POINT_COST_PARAMETER, spec.point_cost_choices[0]),) if spec.point_cost_choices else ()
        ),
        self_control=12 if spec.self_control else None,
    )


def test_selected_printing_mundane_denominator_is_exact() -> None:
    assert Counter(spec.kind for spec in SPECS) == {"advantage": 89, "disadvantage": 178}
    assert len({spec.id for spec in SPECS}) == 267
    assert {spec.owner_issue for spec in SPECS if spec.kind == "advantage"} == {680}
    assert {spec.owner_issue for spec in SPECS if spec.kind == "disadvantage"} == {681}


def test_no_completed_mundane_row_remains_a_certification_blocker() -> None:
    root = Path(__file__).resolve().parents[1]
    target_ids = {spec.id for spec in SPECS}
    blockers = evaluate(root).blockers
    assert not target_ids.intersection(blocker.identifier for blocker in blockers)


@pytest.mark.parametrize(
    ("identifier", "levels", "expected"),
    [
        ("trait:advantage:absolute-timing", 1, 2),  # B35
        ("trait:advantage:acute-vision", 3, 6),  # B35
        ("trait:advantage:extra-attack", 2, 50),  # B53
        ("trait:advantage:hard-to-kill", 3, 6),  # B58
        ("trait:advantage:less-sleep", 4, 8),  # B65
        ("trait:disadvantage:absent-mindedness", 1, -15),  # B122
        ("trait:disadvantage:bad-grip", 2, -10),  # B123
        ("trait:disadvantage:easy-to-kill", 3, -6),  # B134
        ("trait:disadvantage:extra-sleep", 2, -4),  # B136
        ("trait:disadvantage:noisy", 3, -6),  # B146
    ],
)
def test_independent_fixed_and_leveled_costs(identifier: str, levels: int, expected: int) -> None:
    result = runtime_compiler().compile(
        gurps_draft(Purchase(definition_id=identifier, amount=levels, trait=options(identifier)))
    )
    assert result.build is not None, result.diagnostics
    assert result.spent == expected


@pytest.mark.parametrize("spec", SPECS, ids=lambda value: value.id)
def test_every_mundane_row_compiles_and_projects_one_owned_effect(
    spec: CompleteTraitSpec,
) -> None:
    identifier = spec.id
    compiler = runtime_compiler()
    result = compiler.compile(
        gurps_draft(Purchase(definition_id=identifier, trait=options(identifier)))
    )
    assert result.build is not None, result.diagnostics
    projected = mundane_trait_effects(result.build, compiler.definitions)
    assert len(projected) == 1
    assert projected[0].definition_id == identifier
    assert projected[0].family == spec.family


@pytest.mark.parametrize(
    "identifier",
    [spec.id for spec in SPECS if spec.point_cost_choices],
)
def test_variable_constructions_fail_closed_without_an_explicit_selection(
    identifier: str,
) -> None:
    spec = next(value for value in SPECS if value.id == identifier)
    result = runtime_compiler().compile(
        gurps_draft(
            Purchase(
                definition_id=identifier,
                trait=TraitOptions(self_control=12 if spec.self_control else None),
            )
        )
    )
    assert result.build is None
    assert "trait.invalid" in {diagnostic.code for diagnostic in result.diagnostics}


def test_self_control_is_recorded_and_never_automatically_selects_behavior() -> None:
    identifier = "trait:disadvantage:berserk"
    compiler = runtime_compiler()
    result = compiler.compile(
        gurps_draft(Purchase(definition_id=identifier, trait=options(identifier)))
    )
    assert result.build is not None
    effect = mundane_trait_effects(result.build, compiler.definitions)[0]
    assert effect.family == "behavior"
    assert effect.requires_player_choice is True
