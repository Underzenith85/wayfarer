"""Actual opposing spell participants receive findings, not private ratings."""

import json
from pathlib import Path

import pytest
from support.paralyze_limb import cast, fixture, revision
from support.runtime import build_runtime

from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.limb_spell_state import contact_results, read_contact
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("case", ["paralyzed", "contact-failed", "held", "no-effect"])
async def test_actual_paralyze_contact_caster_victim_other_and_gm_privacy(
    tmp_path: Path, backend: str, case: str
) -> None:
    cid, play, _ = await fixture(tmp_path, backend)
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid)
    await play.execute(
        cid,
        Wait(id="later", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    service = CombatService(play)
    await service.execute(
        cid,
        StartEncounter(
            id="fight-start",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            battlefield_id="dock-field",
            placements=(
                Placement(actor_id="a", position=GridPoint(x=0, y=0), facing="east"),
                Placement(actor_id="b", position=GridPoint(x=1, y=0), facing="west"),
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="real-staff",
            mode_id="staff-thrust",
            target_id="b",
            hit_location="torso" if case == "no-effect" else "right-arm",
        ),
        principal_id="a",
    )
    before = play._load(await play.store.read(cid))
    pending = before.encounters[0].pending_defense
    assert pending is not None and read_contact(before.resources, pending.id) is not None
    play.rng = RecordedDice(
        (3, 3, 3, 1, 3, 3, 3, 4, 4, 4)
        if case == "paralyzed"
        else (3, 3, 3, 1, 6, 6, 5)
        if case == "contact-failed"
        else (3, 3, 3, 1)
        if case == "no-effect"
        else (3, 3, 3, 2, 2, 2)
    )
    await service.execute(
        cid,
        ChooseDefense(
            id="privacy-response",
            actor_id="b",
            expected_revision=before.revision,
            encounter_id="fight",
            defense="dodge" if case == "held" else "none",
        ),
        principal_id="b",
    )
    final = await play.store.read(cid)
    canonical = contact_results(play._load(final).resources)[0]
    assert canonical.outcome == case
    if case == "paralyzed":
        assert canonical.contact_check is not None and canonical.resistance_check is not None
        assert canonical.resistance_check.base_target == 10
        assert canonical.hp_before == canonical.hp_after == 9
    runtime = build_runtime(play)
    expected_keys = {
        "pending_id",
        "cast_id",
        "attacker_id",
        "defender_id",
        "status",
        "outcome",
        "triggered",
        "location",
        "recovery_at",
        "dropped_item_ids",
    }
    for route in ("campaign", "stream"):
        for principal in ("alice",):
            projected = await runtime.project(route, cid, principal_id=principal)
            rows = validation.sequence(
                validation.decode(json.dumps(projected["limb_spell_findings"]))
            )
            finding = next(
                validation.mapping(row) for row in rows if "pending_id" in validation.mapping(row)
            )
            assert set(finding) == expected_keys
            assert finding["outcome"] == case
            assert finding["location"] == (
                None if case == "held" else "torso" if case == "no-effect" else "right-arm"
            )
            assert finding["defender_id"] == "b"
            assert not {
                "contact_check",
                "resistance_check",
                "grip_checks",
                "hp_before",
                "hp_after",
                "carrier_digest",
                "build_revision",
            } & set(finding)
            assert "limb-spell:" not in json.dumps(projected)
        for principal in ("bob", "watcher"):
            other = await runtime.project(route, cid, principal_id=principal)
            assert "limb_spell_findings" not in other and "limb-spell:" not in json.dumps(other)
        gm = await runtime.project(route, cid, principal_id="gm")
        assert gm["role"] == "gm"
    assert await play.store.read(cid) == final
    assert contact_results(play._load(final).resources)[0] == canonical
    assert await play.store.replay(cid) == final
