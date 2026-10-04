"""Real paid Haste and Rooted effects; pure score and legacy-policy diagnostics."""

from pathlib import Path
from typing import Literal

import pytest
from support.rooted_feet import fixture as source_fixture
from support.rooted_haste import cast_haste, cast_rooted, fixture, prepare_composition

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.simulation.combat.encounter import Combatant
from wayfarer.engine.simulation.combat.generations import combat_generation
from wayfarer.engine.simulation.combat.melee.values import standard_defense_value
from wayfarer.engine.simulation.magic.spell_state import active_spells
from wayfarer.errors import ValidationError

FEATURE = frozenset({"rooted-dodge-haste-composition"})


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("order", ["before", "after"])
@pytest.mark.parametrize("energy", [1, 2, 3])
async def test_paid_cross_actor_haste_precedes_rooted_score_floor(
    tmp_path: Path, backend: str, order: Literal["before", "after"], energy: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend, subject_dx=14, subject_ht=10)
    await prepare_composition(play, cid, energy=energy, order=order)
    state = play._load(await play.store.read(cid))
    haste = next(e for e in active_spells(state.resources) if e.spell_id == "haste")
    assert haste.actor_id == "a" and haste.target_id == "b" and haste.energy == energy
    assert haste.cost == 2 * energy - 1 and haste.execute_effects
    participant = Combatant(actor_id="b", reach=1, initiative=6, movement_allowance=6 + energy)
    with combat_generation(FEATURE):
        value, _ = standard_defense_value(play.rules_context, state, participant, "dodge")
    assert value is not None and value.value == (9 + energy) // 2
    for old_features in (frozenset(), frozenset({"rooted-dodge-health-trait-composition"})):
        with (
            combat_generation(old_features),
            pytest.raises(ValidationError, match="Dodge composition"),
        ):
            standard_defense_value(play.rules_context, state, participant, "dodge")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_even_base_odd_haste_is_added_before_halving(tmp_path: Path, backend: str) -> None:
    cid, play, _ = await fixture(tmp_path, backend, subject_dx=16, subject_ht=12)
    await prepare_composition(play, cid, energy=1, order="after")
    state = play._load(await play.store.read(cid))
    with combat_generation(FEATURE):
        value, _ = standard_defense_value(
            play.rules_context,
            state,
            Combatant(actor_id="b", reach=1, initiative=7, movement_allowance=8),
            "dodge",
        )
    assert value is not None and value.value == 5


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("combination", ["low-hp", "low-fp", "reflexes"])
async def test_real_effects_do_not_widen_unverified_health_reflexes_haste_combinations(
    tmp_path: Path, backend: str, combination: str
) -> None:
    cid, play, _ = await source_fixture(
        tmp_path,
        backend,
        subject_dx=14,
        subject_ht=10,
        subject_hp=3 if combination == "low-hp" else 10,
        subject_fp=3 if combination == "low-fp" else 10,
        subject_purchases=(Purchase(definition_id="trait:combat-reflexes"),)
        if combination == "reflexes"
        else (),
        bidirectional_visibility=True,
    )
    await cast_rooted(play, cid)
    await cast_haste(play, cid, energy=1)
    state = play._load(await play.store.read(cid))
    assert any(e.spell_id == "haste" and e.execute_effects for e in active_spells(state.resources))
    with (
        combat_generation(FEATURE | {"rooted-dodge-health-trait-composition"}),
        pytest.raises(ValidationError, match="Dodge composition"),
    ):
        standard_defense_value(
            play.rules_context,
            state,
            Combatant(actor_id="b", reach=1, initiative=6, movement_allowance=7),
            "dodge",
        )
