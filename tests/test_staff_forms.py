"""B240 short Staff forms manufacture, wield and discharge through real melee."""

import json
import secrets
from pathlib import Path
from typing import Literal

import pytest
from support.melee_spell import cast, fixture, revision
from support.runtime import build_play, played

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import StaffForm
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.melee_spell_state import MeleeSpellContactResult, read_contact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    StartEncounter,
    TakeCombatTurn,
    generations,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.replay import verify_commands


async def ready(
    path: Path,
    backend: str,
    definition: Literal["quarterstaff", "baton", "short-staff"],
    *,
    form: StaffForm | None = None,
) -> tuple[str, PlayService, Campaign]:
    cid, play, initial = await fixture(
        path, backend, staff_definition=definition, construction_form=form
    )
    play.seeds = lambda: f"{1:064x}"
    await cast(play, cid)
    await play.execute(
        cid,
        Wait(id="next-second", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    await CombatService(play).execute(
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
    return cid, play, initial


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "definition,skill", [("baton", "shortsword"), ("short-staff", "smallsword")]
)
@pytest.mark.parametrize("seeded", [False, True])
async def test_manufactured_short_staff_delivers_actual_physical_and_magic_packets(
    tmp_path: Path,
    backend: str,
    definition: Literal["baton", "short-staff"],
    skill: str,
    seeded: bool,
) -> None:
    cid, play, initial = await ready(tmp_path, backend, definition)
    service = CombatService(play)
    command = TakeCombatTurn(
        id="short-staff-attack",
        actor_id="a",
        expected_revision=await revision(play, cid),
        encounter_id="fight",
        maneuver="attack",
        item_id="real-staff",
        mode_id=skill + "-thrust",
        target_id="b",
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="authorized"):
        await service.execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            command.model_copy(update={"expected_revision": command.expected_revision - 1}),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
    await service.execute(cid, command, principal_id="a")
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    contact = read_contact(state.resources, pending.id)
    assert contact is not None and contact.staff_form_generation == 1
    assert contact.mode_id == skill + "-thrust" and contact.carrier_item_id == "real-staff"
    response = ChooseDefense(
        id="short-staff-contact",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    dice = RecordedDice((3, 3, 3, 4, 1, 1, 1))
    play.rng = secrets if seeded else dice
    result = await service.execute(cid, response, principal_id="b")
    assert result.injury is not None
    after = play._load(await play.store.read(cid))
    magical = MeleeSpellContactResult.model_validate_json(
        next(e.kind for e in after.resources.events if e.id.startswith("melee-spell:result:"))
    )
    assert magical.triggered and magical.status == "spent"
    if not seeded:
        # ST10 thrust 1d-2: baton/short staff adds 0, separate Deathtouch 3d.
        assert result.injury.basic_damage == result.injury.injury == 2
        assert magical.dice == (1, 1, 1) and magical.injury == 3 and dice.exhausted()
        assert next(p.current for p in after.resources.pools if p.id == "hp:b") == 5
    final = await play.store.read(cid)
    restarted = build_play(
        tmp_path / "restart", play.engine, store=play.store, rng=RecordedDice(())
    )
    assert await CombatService(restarted).execute(cid, response, principal_id="b") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    if seeded:
        records = await played(play.store, cid)
        ids = {r.command_id for r in records}
        replayed, checks = await verify_commands(
            initial,
            records,
            [e for e in await play.store.stream(cid) if e.command_id in ids],
            configuration_digest=after.configuration_digest,
            execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
        )
        assert checks and all(c.folded and c.reexecuted for c in checks)
        assert json.loads(replayed["play_json"]) == json.loads(final["play_json"])


@pytest.mark.parametrize("form", ["short-staff", "wand"])
async def test_mismatched_quarterstaff_mode_refuses_before_dice(
    tmp_path: Path, form: StaffForm
) -> None:
    cid, play, _ = await ready(tmp_path, "sqlite", "quarterstaff", form=form)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="B240 physical form|without a physical blow"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="wrong-form",
                actor_id="a",
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                maneuver="attack",
                item_id="real-staff",
                mode_id="staff-thrust",
                target_id="b",
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history


@pytest.mark.parametrize("enabled", [False, True])
async def test_original_contact_generation_survives_feature_change_and_reexecution(
    tmp_path: Path, enabled: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    active = generations.ACTIVE
    monkeypatch.setattr(
        generations, "ACTIVE", active if enabled else active - {"staff-form-contact"}
    )
    # Earlier captures allowed a quarterstaff mode even for a short construction.
    # Reexecute that recorded mistake faithfully; fresh declarations reject above.
    cid, play, initial = await ready(
        tmp_path,
        "sqlite",
        "baton" if enabled else "quarterstaff",
        form=None if enabled else "short-staff",
    )
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=await revision(play, cid),
            encounter_id="fight",
            maneuver="attack",
            item_id="real-staff",
            mode_id="shortsword-thrust" if enabled else "staff-thrust",
            target_id="b",
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    pending = state.encounters[0].pending_defense
    assert pending is not None
    contact = read_contact(state.resources, pending.id)
    assert contact is not None and contact.staff_form_generation == (1 if enabled else None)
    if not enabled:
        assert "staff_form_generation" not in contact.model_dump_json()
    monkeypatch.setattr(
        generations, "ACTIVE", active - {"staff-form-contact"} if enabled else active
    )
    response = ChooseDefense(
        id="defense",
        actor_id="b",
        expected_revision=state.revision,
        encounter_id="fight",
        defense="none",
    )
    await CombatService(play).execute(cid, response, principal_id="b")
    final = await play.store.read(cid)
    records = await played(play.store, cid)
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert json.loads(replayed["play_json"]) == json.loads(final["play_json"])
