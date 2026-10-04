"""B249 actual instant Water and held Fireball producer consequences."""

from pathlib import Path

import pytest
from support.identify_spell import fixture, revision
from test_identify_spell_producers import _identify, _observe, _wait

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.bindings import SpellChannel, SpellRules
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellCommand
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_state import WaterBody
from wayfarer.engine.simulation.magic.water_state import latest as water_bodies
from wayfarer.orchestration.combat import (
    CombatService,
    EndEncounter,
    StartEncounter,
    TakeCombatTurn,
)
from wayfarer.orchestration.spells import SpellService
from wayfarer.orchestration.water import WaterService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_instant_create_water_ended_effect_is_recent_by_caster(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        extra_purchases=tuple(
            Purchase(definition_id="spell:" + key)
            for key in ("seek-water", "purify-water", "create-water")
        ),
    )
    service = WaterService(play)
    await service.execute(
        cid,
        DeclareWater(
            id="receiver",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            body=WaterBody(
                object_id="chest",
                location_id="dock",
                gallons=0,
                pure_gallons=0,
                nature="empty receiver",
                capacity_gallons=2,
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        DeclareWaterChannel(
            id="water-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(
                id="water-source",
                actor_id="a",
                location_id="dock",
                plan=WaterPlan(spell_id="create-water", target_id="chest"),
            ),
        ),
        principal_id="gm",
    )

    async def cast(kind: str) -> None:
        await service.execute(
            cid,
            RuntimeSpellCommand.model_validate(
                dict(
                    id="water-" + kind,
                    actor_id="a",
                    expected_revision=await revision(play, cid),
                    kind=kind,
                    spell_id="create-water",
                    channel_id="water-source",
                    cast_id="water-produced",
                )
            ),
            principal_id="alice",
        )

    await cast("start")
    await _wait(play, cid, 1, "water-second")
    await cast("complete")
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["water-produced"].phase == "ended"
    assert water_bodies(state.resources)["chest"].gallons == 1
    await _observe(play, cid, "a")
    result = await _identify(play, cid)
    assert result.descriptions == ("Create Water",)
    assert len(result.spells) == 1
    found = result.spells[0]
    assert (found.cast_id, found.caster_id, found.subject_id, found.status) == (
        "water-produced",
        "a",
        "chest",
        "completed",
    )
    assert result.at - found.at == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("subject,included", [("a", True), ("b", False)])
async def test_actual_held_fireball_is_by_caster_not_on_future_victim(
    tmp_path: Path, backend: str, subject: str, included: bool
) -> None:
    rules = SpellRules(
        id="real-fire",
        version=1,
        channels=(
            SpellChannel(
                id="fire-source",
                actor_id="a",
                target_id="b",
                location_id="dock",
                spell_id="fireball",
            ),
        ),
    )
    cid, play, _ = await fixture(
        tmp_path,
        backend,
        spell_rules=rules,
        extra_purchases=tuple(
            Purchase(definition_id="spell:" + key)
            for key in ("ignite-fire", "create-fire", "shape-fire", "fireball")
        ),
    )
    service = SpellService(play)
    combat = CombatService(play)
    await combat.execute(
        cid,
        StartEncounter(
            id="fight-start",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0)),
                Placement(actor_id="b", position=GridPoint(x=2, y=0)),
            ),
        ),
        principal_id="gm",
    )
    start = SpellCommand(
        id="fire-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="fireball",
        channel_id="fire-source",
        cast_id="fire-produced",
        energy=1,
    )
    await service.execute(cid, start, principal_id="alice")
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="other-turn",
            actor_id="b",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    await service.execute(
        cid,
        start.model_copy(
            update={
                "id": "fire-complete",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    held = latest(state.resources)["fire-produced"]
    assert held.phase == "active" and held.expires_at is None
    assert held.spell_id == "fireball" and held.missile_seconds == 1
    await combat.execute(
        cid,
        EndEncounter(
            id="fight-end",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            reason="No missile released",
        ),
        principal_id="gm",
    )
    assert (
        latest(play._load(await play.store.read(cid)).resources)["fire-produced"].phase == "active"
    )
    await _observe(play, cid, subject)
    result = await _identify(play, cid)
    assert bool(result.spells) is included
    assert result.descriptions == (("Fireball",) if included else ())
    if included:
        assert len(result.spells) == 1
        assert result.spells[0].caster_id == "a"
        assert result.spells[0].status == "completed"
