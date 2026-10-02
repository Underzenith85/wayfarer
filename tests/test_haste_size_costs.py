"""B239 Regular size multiplier is SM+1; Haste bonuses keep their magnitude."""

from pathlib import Path

import pytest
from test_haste_effects import command, prepare
from test_power_wearer_haste import prepare as item_prepare
from test_power_wearer_haste import scores
from test_spells import context, state

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import apply_spell
from wayfarer.orchestration.haste import HasteService


@pytest.mark.parametrize(
    ("sm", "skill", "cost", "upkeep"),
    [
        (-2, 14, 2, 1),
        (0, 14, 2, 1),
        (1, 14, 4, 2),
        (2, 14, 6, 3),
        (3, 14, 8, 4),
        (2, 15, 5, 2),
    ],
)
def test_haste_cost_and_maintenance_scale_before_skill_reduction(
    sm: int,
    skill: int,
    cost: int,
    upkeep: int,
) -> None:
    bound = context().model_copy(
        update={
            "learned": ("haste",),
            "energy": 1,
            "skill": skill,
            "haste_size_scale": 1 + max(0, sm),
            "execution_version": 2,
        }
    )
    resources, _ = apply_spell(
        state(), command("start", 0, "start", levels=1), bound, system=True, rng=RecordedDice(())
    )
    effect = latest(resources)["haste"]
    assert (effect.cost, effect.maintenance, effect.energy) == (cost, upkeep, 1)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_approved_positive_size_cast_pays_scaled_energy_and_retries(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play = await prepare(tmp_path, backend, size_modifier=2)
    service = HasteService(play)
    start = command("start", 1, "start", levels=1)
    await service.execute(cid, start, principal_id="alice")
    after = await play.store.read(cid)
    effect = latest(play._load(after).resources)["haste"]
    assert (effect.cost, effect.maintenance, effect.energy) == (6, 3, 1)
    await service.execute(cid, start, principal_id="alice")
    assert await play.store.read(cid) == after


@pytest.mark.parametrize(("sm", "power", "bonus"), [(1, 2, 0), (1, 4, 1), (2, 4, 0), (2, 6, 1)])
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_wearer_power_threshold_uses_approved_size(
    tmp_path: Path,
    backend: str,
    sm: int,
    power: int,
    bonus: int,
) -> None:
    cid, play = await item_prepare(tmp_path, backend, size_modifier=sm, reduction=power)
    current = play._load(await play.store.read(cid))
    assert scores(play, current) == (5 + bonus, 8 + bonus)


def test_accepted_cost_does_not_rebase_when_later_context_size_changes() -> None:
    bound = context().model_copy(
        update={
            "learned": ("haste",),
            "energy": 1,
            "skill": 14,
            "haste_size_scale": 3,
            "execution_version": 2,
        }
    )
    resources, _ = apply_spell(
        state(),
        command("start", 0, "start", levels=1),
        bound,
        system=True,
        rng=RecordedDice(()),
    )
    resources = resources.model_copy(update={"game_time": 2})
    completed, result = apply_spell(
        resources,
        command("complete", 1, "complete", levels=1),
        bound.model_copy(update={"haste_size_scale": 5}),
        system=True,
        rng=RecordedDice((3, 3, 3)),
    )
    assert result.energy_spent == 6
    assert (latest(completed)["haste"].cost, latest(completed)["haste"].maintenance) == (6, 3)
    assert "haste_size_scale" not in context().model_dump()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_paid_item_records_original_scaled_zero_upkeep_base(
    tmp_path: Path,
    backend: str,
) -> None:
    from wayfarer.engine.simulation.magic.haste_state import DeclareHasteChannel, HasteChannel
    from wayfarer.engine.simulation.magic.power_lifecycle import origins

    cid, play = await item_prepare(tmp_path, backend, size_modifier=2, reduction=3)
    service = HasteService(play)
    await service.execute(
        cid,
        DeclareHasteChannel(
            id="channel",
            actor_id="gm",
            expected_revision=2,
            channel=HasteChannel(
                id="haste", actor_id="a", target_id="a", location_id="dock", magic_item_id="cloak"
            ),
        ),
        principal_id="gm",
    )
    await service.execute(cid, command("start", 3, "start", levels=1), principal_id="alice")
    resources = play._load(await play.store.read(cid)).resources
    assert (latest(resources)["haste"].cost, latest(resources)["haste"].maintenance) == (3, 0)
    assert origins(resources)[0].maintenance == 3
