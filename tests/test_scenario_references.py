"""Scenario identity survives setup, restart and adventure continuation."""

import json
from pathlib import Path

import pytest
from test_wave12 import ready, service
from test_wave13 import finish, successor

from wayfarer.engine.simulation.scenario_references import boundary, verify
from wayfarer.engine.simulation.setup import SetupCommand
from wayfarer.errors import ValidationError


async def test_setup_receipts_pin_genesis_and_reject_changed_cache(tmp_path: Path) -> None:
    setup = service(tmp_path)
    cid = await ready(setup)
    await setup.execute(
        cid,
        SetupCommand(id="activate", expected_revision=3, operation="activate"),
        principal_id="alice",
    )
    campaign = await setup.play.store.read(cid)
    pin = boundary(campaign)
    assert pin is not None and pin.reference.catalog_id is None
    assert pin.revision == 0 and pin.campaign_revision == 4 and pin.segment == 0
    records = await setup.play.store.history(cid)
    assert [r.command_id for r in records] == [
        "setup:edit",
        "setup:assign",
        "setup:ready",
        "setup:activate",
    ]
    assert records[-1].scenario_boundary == pin
    states = await setup.play.store.stream_states(cid)
    assert states[-1][0] == campaign
    assert all("play_json" not in state for state, _ in states[:-1])
    changed = campaign.copy()
    graph = json.loads(changed["scenario_graph_json"])
    graph["title"] = "Changed after activation"
    changed["scenario_graph_json"] = json.dumps(graph)
    with pytest.raises(ValidationError, match=f"Campaign {cid} scenario revision 1"):
        setup.play.for_campaign(changed)
    assert setup.play.for_campaign(campaign)._load(campaign).revision == 4


async def test_continuation_stream_verifies_both_scenario_segments(tmp_path: Path) -> None:
    setup = service(tmp_path)
    cid = await finish(setup)
    first = boundary(await setup.play.store.read(cid))
    assert first is not None
    await setup.execute(
        cid,
        SetupCommand(id="preview", expected_revision=6, operation="preview", graph=successor()),
        principal_id="alice",
    )
    await setup.execute(
        cid,
        SetupCommand(id="continue", expected_revision=7, operation="continue"),
        principal_id="alice",
    )
    latest = await setup.play.store.read(cid)
    second = boundary(latest)
    assert second is not None and second.segment == 1 and second.revision == 0
    assert second.campaign_revision == 8 and second.reference != first.reference
    assert (await setup.play.store.history(cid))[-1].scenario_boundary == second
    for state, _ in await setup.play.store.stream_states(cid):
        verify(state)
    for revision in (4, 8):
        state = await setup.play.store.replay(cid, revision)
        setup.play.for_campaign(state)._load(state)
