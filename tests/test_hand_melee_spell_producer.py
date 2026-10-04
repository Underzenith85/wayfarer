"""Local producer/reducer diagnostics; registered host proof is separate."""

from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError
from support.hand_deathtouch import fixture
from support.melee_spell import fixture as staff_fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.hand_melee_spell_state import (
    ADAPTER,
    CastHandDeathtouch,
    casts,
    projection,
)
from wayfarer.engine.simulation.magic.hand_melee_spell_transitions import apply, checkpoint
from wayfarer.engine.simulation.magic.melee_spell_state import HandCarrier
from wayfarer.engine.simulation.resources import Unequip
from wayfarer.errors import ConflictError


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("hand", ["left-hand", "right-hand"])
@pytest.mark.parametrize("energy", [1, 2, 3])
async def test_local_hand_producer_credits_real_second_and_selected_energy(
    tmp_path: Path, backend: str, hand: str, energy: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    initial_time = state.resources.game_time
    initial_fp = next(p.current for p in state.resources.pools if p.id == "fp:a")
    dice = RecordedDice((3, 3, 3))
    runtime = replace(play.rules_context, rng=dice)
    for operation in ("start", "concentrate", "complete"):
        command = CastHandDeathtouch(
            id="hand-" + operation,
            actor_id="a",
            expected_revision=state.revision,
            operation=operation,
            cast_id="hand",
            energy=energy,
            carrier=HandCarrier.model_validate({"hand": hand}),
        )
        assert ADAPTER.validate_json(command.model_dump_json()) == command
        state, receipt = apply(runtime, state, command)
    charge = casts(state.resources)["hand"]
    assert charge.status == "held" and charge.skill == 16 and charge.generation == 1
    assert charge.credited_seconds == 1
    assert charge.completed_at == state.resources.game_time == initial_time + 1
    assert charge.energy == energy and charge.paid_fp == max(0, energy - 1)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == initial_fp - max(
        0, energy - 1
    )
    assert receipt.energy_spent == charge.paid_fp and dice.exhausted()
    assert projection(state.resources, ("b",)) == ()
    assert projection(state.resources, ("a",))[0]["energy"] == energy
    for invalid in (True, 1.0, "1", 2):
        with pytest.raises(ValidationError):
            type(charge).model_validate({**charge.model_dump(), "generation": invalid})


async def test_local_hand_occupation_refuses_start_without_rng_and_cancel_needs_no_hand(
    tmp_path: Path,
) -> None:
    cid, play, _ = await staff_fixture(tmp_path, "sqlite")
    state = play._load(await play.store.read(cid))
    dice = RecordedDice(())
    runtime = replace(play.rules_context, rng=dice)
    command = CastHandDeathtouch(
        id="hand-start",
        actor_id="a",
        expected_revision=state.revision,
        operation="start",
        cast_id="hand",
        energy=3,
        carrier=HandCarrier(hand="right-hand"),
    )
    with pytest.raises(ConflictError, match="empty"):
        apply(runtime, state, command)
    assert dice.exhausted() and not casts(state.resources)
    state = state.model_copy(
        update={
            "resources": play.engine.resources.apply(
                state.resources,
                Unequip(
                    id="free-hands",
                    actor_id="a",
                    expected_revision=state.resources.revision,
                    item_id="real-staff",
                ),
            )
        }
    )
    command = command.model_copy(update={"carrier": HandCarrier(hand="left-hand")})
    state, _ = apply(runtime, state, command)
    # Canonical-state diagnostic for interruption, not a registered external-clock claim.
    advanced = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"game_time": state.resources.game_time + 1}
            )
        }
    )
    canceled = checkpoint(runtime, advanced, before=state)
    assert casts(canceled.resources)["hand"].status == "cancelled"
    state, receipt = apply(
        runtime,
        state,
        command.model_copy(
            update={
                "id": "hand-cancel",
                "operation": "cancel",
                "carrier": HandCarrier(hand="right-hand"),
            }
        ),
    )
    assert receipt.outcome == "cancelled" and dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "roll,status,paid",
    [((1, 1, 1), "held", 0), ((6, 6, 5), "failed", 1), ((6, 6, 6, 3, 3, 4), "failed", 2)],
)
async def test_local_hand_cast_critical_and_failure_preserve_selected_energy(
    tmp_path: Path, backend: str, roll: tuple[int, ...], status: str, paid: int
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    fp_before = next(p.current for p in state.resources.pools if p.id == "fp:a")
    dice = RecordedDice(roll)
    runtime = replace(play.rules_context, rng=dice)
    for operation in ("start", "concentrate", "complete"):
        state, receipt = apply(
            runtime,
            state,
            CastHandDeathtouch(
                id="hand-" + operation,
                actor_id="a",
                expected_revision=state.revision,
                operation=operation,
                cast_id="hand",
                energy=3,
                carrier=HandCarrier(hand="right-hand"),
            ),
        )
    charge = casts(state.resources)["hand"]
    assert charge.status == status and charge.energy == 3 and charge.paid_fp == paid
    assert receipt.energy_spent == paid
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == fp_before - paid
    assert dice.exhausted()
