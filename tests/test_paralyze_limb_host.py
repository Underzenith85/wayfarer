"""Actual manufactured Staff contact, second skill/HT contest and timed arm effect."""

from pathlib import Path

import pytest
from support.paralyze_limb import cast, fixture, revision

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.health.hit_locations import disabled
from wayfarer.engine.simulation.magic.limb_spell_state import casts, contact_results, read_contact
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "case",
    [
        "paralyzed",
        "resisted",
        "tie",
        "critical-success",
        "critical-failure",
        "contact-failed",
        "no-effect",
    ],
)
async def test_real_staff_hit_resolves_independent_paralyze_contact(
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
    spell = (
        ()
        if case == "no-effect"
        else (6, 6, 5)
        if case == "contact-failed"
        else (6, 6, 6)
        if case == "critical-failure"
        else (1, 1, 1)
        if case == "critical-success"
        else (4, 5, 5)
        if case == "tie"
        else (3, 3, 3)
    )
    resistance = (
        ()
        if case in ("contact-failed", "no-effect", "critical-success", "critical-failure")
        else (2, 3, 3)
        if case == "tie"
        else (1, 1, 1)
        if case == "resisted"
        else (4, 4, 4)
    )
    rng = RecordedDice(
        (3, 3, 3, 1) + spell + resistance + ((3, 3, 4) if case == "critical-failure" else ())
    )
    play.rng = rng
    response = await service.execute(
        cid,
        ChooseDefense(
            id="response",
            actor_id="b",
            expected_revision=before.revision,
            encounter_id="fight",
            defense="none",
        ),
        principal_id="b",
    )
    assert rng.exhausted()
    assert response.injury is not None and response.injury.injury == 1
    after = play._load(await play.store.read(cid))
    result = contact_results(after.resources)[0]
    assert result.injury == 0 and result.hp_before == result.hp_after == 9
    assert casts(after.resources)["paralyze"].status == "spent"
    assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 9
    success = case in ("paralyzed", "critical-success")
    expected_outcome = (
        "paralyzed"
        if success
        else "resisted"
        if case == "tie"
        else "contact-failed"
        if case == "critical-failure"
        else case
    )
    assert result.outcome == expected_outcome
    assert ("right-arm" in disabled(after.resources, "b")) == success
    assert result.triggered == (case != "no-effect")
    assert (result.lasting_id is not None) == success
    if success:
        hp = next(p for p in after.resources.pools if p.id == "hp:b")
        assert hp.injury is not None
        injury = next(w for w in hp.injury.lasting_injuries if w.id == result.lasting_id)
        assert injury.kind == "crippled" and injury.duration == "timed" and injury.injury == 0
        assert injury.recovery_at == injury.inflicted_at + 60 == result.recovery_at
    assert (result.contact_check is None) == (case == "no-effect")
    assert (result.resistance_check is None) == (
        case in ("contact-failed", "no-effect", "critical-success", "critical-failure")
    )
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == next(
        p.current for p in before.resources.pools if p.id == "fp:a"
    )
    if case == "critical-failure":
        from wayfarer.engine.simulation.magic.backfires import backfires

        assert any(b.spell_id == "paralyze-limb" for b in backfires(after.resources))
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_seeded_contact_restarts_retries_and_reexecutes_all_producers(
    tmp_path: Path, backend: str
) -> None:
    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play, original = await fixture(tmp_path, backend)
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
            hit_location="right-arm",
        ),
        principal_id="a",
    )
    before = play._load(await play.store.read(cid))
    pending = before.encounters[0].pending_defense
    assert pending is not None and read_contact(before.resources, pending.id) is not None
    command = ChooseDefense(
        id="seeded-response",
        actor_id="b",
        expected_revision=before.revision,
        encounter_id="fight",
        defense="none",
    )
    response = await service.execute(cid, command, principal_id="b")
    final = await play.store.read(cid)
    after = play._load(final)
    result = contact_results(after.resources)[0]
    assert result.triggered and result.contact_check is not None
    assert result.outcome in ("paralyzed", "resisted", "contact-failed")
    history, stream = await play.store.history(cid), await play.store.stream(cid)
    assert (
        await CombatService(play.for_campaign(final)).execute(cid, command, principal_id="b")
        == response
    )
    assert await play.store.read(cid) == final
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    records = history[1:]
    assert history[0].command_id == "setup:seed"
    ids = {row.command_id for row in records}
    replayed, checks = await verify_commands(
        original,
        records,
        [event for event in stream if event.command_id in ids],
        configuration_digest=after.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    import json

    assert {k: v for k, v in replayed.items() if k != "play_json"} == {
        k: v for k, v in final.items() if k != "play_json"
    }
    assert json.loads(replayed["play_json"]) == json.loads(final["play_json"])
    assert len(checks) == len(records)
    assert all(check.folded and check.reexecuted for check in checks)
