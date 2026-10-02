"""Original private water commitments and receiving restrictions remain unchanged."""

import hashlib
from pathlib import Path

import pytest
from test_lock_spell_persistence import revision
from test_water_persistence import prepare

from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_cast_state import WaterCastPlan
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_state import WaterBody
from wayfarer.errors import ValidationError
from wayfarer.orchestration.water import WaterService


def test_original_cast_plan_bytes_do_not_gain_a_default_mixture_field() -> None:
    plan = WaterPlan(
        spell_id="purify-water",
        target_id="chest",
        source_id="hidden",
        gallons=2,
        flowing_through_ring=True,
    )
    value = WaterCastPlan(
        cast_id="cast", actor_id="a", channel_id="water", plan=plan
    ).model_dump_json()
    # Independently pinned from the exact pre-mixture53a4dca3 model.
    assert (
        hashlib.sha256(value.encode()).hexdigest()
        == "0775efa601968aaccebafe2e45f953887ece464400cc58529835f3a38afd9312"
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unopted_historical_receiver_restriction_rolls_back(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend)
    service = WaterService(play)
    for name, capacity in (("chest", 5), ("hidden", None)):
        await service.execute(
            cid,
            DeclareWater(
                id="body:" + name,
                actor_id="gm",
                expected_revision=await revision(play, cid),
                body=WaterBody(
                    object_id=name,
                    location_id="dock",
                    gallons=1,
                    pure_gallons=0,
                    capacity_gallons=capacity,
                    nature="dirty water",
                ),
            ),
            principal_id="gm",
        )
    before = await play.store.read(cid)
    plan = WaterPlan(
        spell_id="purify-water", target_id="chest", source_id="hidden", flowing_through_ring=True
    )
    with pytest.raises(ValidationError, match="impure"):
        await service.execute(
            cid,
            DeclareWaterChannel(
                id="channel",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                channel=WaterChannel(id="legacy", actor_id="a", location_id="dock", plan=plan),
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before
