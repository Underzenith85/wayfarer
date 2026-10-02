"""B66/B374/B382: one source-bound attack, then canonical defense and injury."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_composed_attack_host import declare, fixture
from test_composed_attacks import pick

from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.simulation.combat.commands import ChooseDefense
from wayfarer.engine.simulation.traits.composed_phases import ComposedAttackChoice, ComposedDelivery
from wayfarer.engine.simulation.traits.composed_resolution import (
    finish_delivery,
    owner_damage_arguments,
    prepare_delivery,
    resolve,
)
from wayfarer.engine.simulation.traits.opponent_attack import (
    OpponentAttackPreparation,
    captured_choice,
    prepare_opponent_attack,
    rescore_opponent_attack,
    validate_opponent_attack,
)
from wayfarer.errors import ConflictError, ValidationError


@pytest.mark.parametrize("selected", ["none", "dodge"])
async def test_delivery_round_trip_preserves_actual_legacy_consequences(
    tmp_path: Path, selected: str
) -> None:
    cid, play, source = await fixture(tmp_path, skill_points=4)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    command = ChooseDefense.model_validate(
        dict(
            id="defend",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            defense=selected,
        )
    )
    faces = (3, 3, 3) + ((4, 4, 4) if selected == "dodge" else ()) + (2, 2)
    old_rng = RecordedDice(faces)
    immediate = resolve(replace(play.rules_context, rng=old_rng), state, encounter, command)
    phased_rng = RecordedDice(faces)
    runtime = replace(play.rules_context, rng=phased_rng)
    before_damage, prepared_encounter, delivery = prepare_delivery(
        runtime, state, encounter, command
    )
    assert next(p.current for p in before_damage.resources.pools if p.id == "hp:b") == 10
    restored = ComposedDelivery.model_validate_json(delivery.model_dump_json())
    assert restored == delivery
    arguments = owner_damage_arguments(runtime, before_damage, prepared_encounter, restored)
    assert encounter.pending_defense is not None
    assert arguments.command.id == encounter.pending_defense.id
    phased = finish_delivery(runtime, before_damage, prepared_encounter, restored)
    assert phased == immediate
    assert phased.result.injury and phased.result.injury.hp_after == 6
    assert old_rng.exhausted() and phased_rng.exhausted()


@pytest.mark.parametrize(
    "total,selected,following,injury",
    [
        (15, "none", (), 0),
        (9, "dodge", (2, 2, 2), 0),
        (9, "none", (2, 2), 4),
    ],
)
async def test_selected_worst_attack_controls_hit_defense_and_actual_hp(
    tmp_path: Path, total: int, selected: str, following: tuple[int, ...], injury: int
) -> None:
    cid, play, source = await fixture(tmp_path, skill_points=4)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    assert encounter.pending_defense
    original_rng = RecordedDice((1, 1, 1))
    prepared = prepare_opponent_attack(
        replace(play.rules_context, rng=original_rng),
        state,
        encounter,
        owner_id="b",
        attack_id=encounter.pending_defense.id,
    )
    assert original_rng.exhausted()
    assert prepared.original is not None
    assert prepared.original.effective_target == 12
    assert prepared.original.outcome is Outcome.CRITICAL_SUCCESS
    restored = OpponentAttackPreparation.model_validate_json(prepared.model_dump_json())
    validate_opponent_attack(play.rules_context, state, encounter, restored)
    chosen = rescore_opponent_attack(restored, (total // 3,) * 3)
    remaining = RecordedDice(following)
    choice = captured_choice(restored, chosen)
    assert isinstance(choice, ComposedAttackChoice)
    result = resolve(
        replace(play.rules_context, rng=remaining),
        state,
        encounter,
        ChooseDefense.model_validate(
            dict(
                id="selected",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                defense=selected,
            )
        ),
        selected_attack=choice,
    )
    assert result.result.injury
    assert result.result.injury.attack == chosen
    assert result.result.injury.injury == injury
    assert next(p.current for p in result.state.resources.pools if p.id == "hp:b") == 10 - injury
    assert remaining.exhausted()
    assert state.encounters[0].pending_defense is not None
    assert state.encounters[0].pending_defense.attack_roll is None
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10


async def test_closed_attack_or_forged_target_cannot_replace_committed_trace(
    tmp_path: Path,
) -> None:
    cid, play, source = await fixture(tmp_path, skill_points=4)
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    assert encounter.pending_defense
    prepared = prepare_opponent_attack(
        replace(play.rules_context, rng=RecordedDice((3, 3, 3))),
        state,
        encounter,
        owner_id="b",
        attack_id=encounter.pending_defense.id,
    )
    assert prepared.original is not None
    committed = encounter.model_copy(
        update={
            "pending_defense": encounter.pending_defense.model_copy(
                update={"attack_roll": prepared.original}
            )
        }
    )
    with pytest.raises(ConflictError, match="committed"):
        prepare_opponent_attack(
            play.rules_context, state, committed, owner_id="b", attack_id=prepared.attack_id
        )
    forged = captured_choice(prepared, prepared.original)
    assert isinstance(forged, ComposedAttackChoice)
    with pytest.raises(ValidationError, match="captured original"):
        resolve(
            replace(play.rules_context, rng=RecordedDice(())),
            state,
            encounter,
            ChooseDefense(
                id="forged",
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                defense="none",
            ),
            selected_attack=forged.model_copy(
                update={"selected": replace(prepared.original, base_target=30, effective_target=30)}
            ),
        )
    with pytest.raises(ValidationError, match="owner"):
        prepare_opponent_attack(
            play.rules_context, state, encounter, owner_id="a", attack_id=prepared.attack_id
        )


async def test_malediction_is_not_an_ordinary_opponent_attack(tmp_path: Path) -> None:
    cid, play, source = await fixture(
        tmp_path, modifiers=(pick("enhancement:malediction", option="1"),)
    )
    await declare(play, cid, source)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    assert encounter.pending_defense
    with pytest.raises(ValidationError, match="resistance choice"):
        prepare_opponent_attack(
            play.rules_context,
            state,
            encounter,
            owner_id="b",
            attack_id=encounter.pending_defense.id,
        )
