"""Critical-table permission cannot replace full physical armor authentication."""

import json
from typing import Literal

import pytest
from test_corrosion_armor import context, initial, total_loss

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.critical_hit import CorrosionCritical
from wayfarer.engine.simulation.equipment.corrosion_state import CorrosionProtection, erosion
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.corrosion_generations import KEY, version
from wayfarer.persistence.events import CommandInput, payload_digest


def wound(damage: int = 6) -> Wound:
    return Wound(
        id="critical",
        actor_id="b",
        expected_revision=0,
        basic_damage=damage,
        resistance=5,
        damage_type="cor",
    )


def test_half_dr_preserves_current_hit_old_protection_and_zero_penetration() -> None:
    state = initial()
    critical = CorrosionCritical(table=(1, 1, 2))
    state, result = apply_injury(
        state,
        wound(2),
        ht=10,
        system=True,
        rng=RecordedDice(()),
        corrosion=context(state),
        corrosion_critical=critical,
        halve_dr="down",
    )
    assert result.effective_resistance == 2 and result.injury == 0
    assert erosion(state, "plate") is None
    state = initial()
    state, result = apply_injury(
        state,
        wound(6),
        ht=10,
        system=True,
        rng=RecordedDice(()),
        corrosion=context(state),
        corrosion_critical=critical,
        halve_dr="down",
    )
    assert result.effective_resistance == 2 and result.injury == 4
    assert total_loss(state, "plate") == 1


def test_critical_erosion_saturates_and_receipt_binds_exact_native_table() -> None:
    state = initial()
    critical = CorrosionCritical(table=(6, 6, 6))
    updated, result = apply_injury(
        state,
        wound(36),
        ht=10,
        system=True,
        rng=RecordedDice(()),
        corrosion=context(state),
        corrosion_critical=critical,
    )
    assert result.effective_resistance == 5 and result.injury == 31
    assert total_loss(updated, "plate") == 5
    assert apply_injury(
        updated,
        wound(36),
        ht=10,
        system=True,
        rng=RecordedDice(()),
        corrosion=context(state),
        corrosion_critical=critical,
    ) == (updated, result)
    with pytest.raises(ConflictError, match="different payload"):
        apply_injury(
            updated,
            wound(36),
            ht=10,
            system=True,
            rng=RecordedDice(()),
            corrosion=context(state),
            corrosion_critical=CorrosionCritical(table=(1, 1, 1)),
        )


@pytest.mark.parametrize(
    "bad", ["half", "major", "shock", "ignore", "table", "context", "dr", "custody"]
)
def test_forged_mixed_critical_or_physical_provenance_refuses_before_dice(bad: str) -> None:
    state = initial()
    protection: CorrosionProtection | None = context(state)
    command = wound()
    critical = CorrosionCritical(table=(1, 1, 2))
    half: Literal["up", "down"] | None = "down"
    if bad == "half":
        half = None
    elif bad == "table":
        critical = CorrosionCritical(table=(3, 3, 3))
    elif bad == "context":
        protection = None
    elif bad == "dr":
        command = command.model_copy(update={"resistance": 2})
    elif bad == "custody":
        state = state.model_copy(
            update={"items": (state.items[0].model_copy(update={"owner_id": "a"}),)}
        )
    rng = RecordedDice(())
    with pytest.raises((ValidationError, ConflictError)):
        apply_injury(
            state,
            command,
            ht=10,
            system=True,
            rng=rng,
            corrosion=protection,
            corrosion_critical=critical,
            halve_dr=half,
            force_major_wound=bad == "major",
            double_shock=bad == "shock",
            ignore_dr=bad == "ignore",
        )
    assert rng.exhausted()


@pytest.mark.parametrize("row,flag", [(7, "major"), (8, "shock")])
def test_critical_major_and_shock_require_penetration(row: int, flag: str) -> None:
    state = initial()
    table = (1, 1, 5) if row == 7 else (1, 1, 6)
    updated, result = apply_injury(
        state,
        wound(5),
        ht=10,
        system=True,
        rng=RecordedDice(()),
        corrosion=context(state),
        corrosion_critical=CorrosionCritical(table=table),
        force_major_wound=flag == "major",
        double_shock=flag == "shock",
    )
    assert result.injury == 0 and result.checks == () and updated.pools == state.pools
    assert total_loss(updated, "plate") == 1


@pytest.mark.parametrize("bad", [True, 0, 2, "1"])
def test_unknown_or_wrong_typed_source_generation_refuses(bad: object) -> None:
    text = json.dumps({"operation": "composed-attack", "command": {"kind": "bind"}, KEY: bad})
    with pytest.raises(ValidationError, match="generation"):
        version(CommandInput(payload_digest({"input": text}), text))


def test_generation_requires_exact_record_digest_and_bind_operation() -> None:
    text = json.dumps({"operation": "composed-attack", "command": {"kind": "bind"}, KEY: 1})
    assert version(CommandInput(payload_digest({"input": text}), text))
    with pytest.raises(ValidationError, match="digest"):
        version(CommandInput("0" * 64, text))
    text = json.dumps({"operation": "composed-attack", "command": {"kind": "declare"}, KEY: 1})
    with pytest.raises(ValidationError, match="source binding"):
        version(CommandInput(payload_digest({"input": text}), text))
