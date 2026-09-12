"""Independent B367-377/B384-392 expectations and real tactical HTTP boundaries.

Test-only rules bindings do not enable or certify the production Basic profile.
"""

import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path

import aiohttp
import pytest
from aiohttp import web
from test_gurps_melee import setup as melee_setup
from test_unarmed import setup as unarmed_setup

from scripts.tactical_contracts import contract
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.conformance import BASELINE_ID
from wayfarer.engine.rules.types.object import ObjectProfile
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.encounter import CombatResult, RangedSituation
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield, Pose
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.world import Fact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, CommandReceipt
from wayfarer.orchestration.access import CampaignAccess
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    HexPlacement,
    MigrateEncounterHex,
    TakeCombatTurn,
)
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.tactical_view import TacticalSnapshot, snapshot
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.transport.campaign_api import create_campaign_app


def with_board(play: PlayService, board: HexBattlefield) -> RulesContext:
    rules = play.engine.rules.combat
    assert rules is not None
    combat = rules.model_copy(
        update={"battlefields": tuple(board if b.id == board.id else b for b in rules.battlefields)}
    )
    return replace(
        play.rules_context, rules=play.engine.rules.model_copy(update={"combat": combat})
    )


async def setup(
    tmp_path: Path,
    *,
    unarmed: bool = False,
    migrate: bool = True,
    durability: ObjectProfile | None = None,
) -> tuple[str, PlayService]:
    if unarmed:
        cid, play = await unarmed_setup(tmp_path, third_actor=True)
    else:
        cid, play = await melee_setup(
            tmp_path,
            "gurps-basic-set-4e-2004",
            human=True,
            third_actor=True,
            ranged_fixture=True,
            durability=durability,
            ranged_scene=(RangedSituation(attacker_id="a", defender_id="b", distance_yards=9),),
        )

    def members(campaign: Campaign) -> CommandReceipt:
        state = play._load(campaign)
        facts = state.world.facts + (
            Fact("seen-a", "a", "visible", "yes"),
            Fact("seen-b", "b", "visible", "yes"),
        )
        world = replace(
            state.world,
            facts=facts,
            knowledge=state.world.knowledge + (("a", "seen-b"), ("b", "seen-a")),
        )
        state = state.model_copy(
            update={
                "world": world,
                "members": (
                    CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
                    CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
                    CampaignMember(principal_id="charlie", role="player", actor_ids=("c",)),
                    CampaignMember(principal_id="gm", role="gm"),
                    CampaignMember(principal_id="spectator", role="spectator"),
                ),
            }
        )
        campaign["play_json"] = state.model_dump_json()
        return CommandReceipt(action="combat", outcome="members")

    await play.store.commit_turn(cid, "members", 1, "members", members)
    if migrate:
        await CombatService(play).execute(cid, migration(), authenticated_actor_id="gm")
    return cid, play.for_campaign(await play.store.read(cid))


def migration() -> MigrateEncounterHex:
    return MigrateEncounterHex(
        id="hex-migration",
        actor_id="gm",
        expected_revision=1,
        encounter_id="fight",
        battlefield=HexBattlefield(
            id="dock",
            coordinate_system="hex-axial-v1",
            profile_id="gurps-basic-set-4e-2004",
            baseline_id=BASELINE_ID,
            cells=tuple(Cell(position=Hex(q=q, r=r)) for q in range(-2, 6) for r in range(-2, 3)),
        ),
        placements=(
            HexPlacement(actor_id="a", pose=Pose(position=Hex(q=0, r=0), facing=0)),
            HexPlacement(actor_id="b", pose=Pose(position=Hex(q=1, r=0), facing=3)),
            HexPlacement(actor_id="c", pose=Pose(position=Hex(q=4, r=1), facing=3)),
        ),
    )


@pytest.fixture
async def http(tmp_path: Path) -> AsyncIterator[tuple[str, str, PlayService]]:
    cid, play = await setup(tmp_path)
    app = create_campaign_app(
        CampaignAccess(play),
        {f"{p}-token": p for p in ("alice", "bob", "charlie", "gm", "spectator")},
        legacy_routes=True,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}", cid, play
    finally:
        await runner.cleanup()


@pytest.fixture
async def unarmed_http(tmp_path: Path) -> AsyncIterator[tuple[str, str, PlayService]]:
    cid, play = await setup(tmp_path, unarmed=True)
    app = create_campaign_app(
        CampaignAccess(play),
        {f"{p}-token": p for p in ("alice", "bob", "charlie", "gm", "spectator")},
        legacy_routes=True,
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        yield f"http://127.0.0.1:{runner.addresses[0][1]}", cid, play
    finally:
        await runner.cleanup()


async def view(
    client: aiohttp.ClientSession, url: str, actor: str = "a", principal: str = "alice"
) -> TacticalSnapshot:
    async with client.get(
        f"{url}?actor_id={actor}", headers={"Authorization": f"Bearer {principal}-token"}
    ) as response:
        assert response.status == 200, await response.text()
        from wayfarer.orchestration.equipment_view import TacticalSnapshotV2

        model = TacticalSnapshotV2 if "/v2/" in url else TacticalSnapshot
        return model.model_validate_json(await response.text())


async def wait(cid: str, play: PlayService, actor: str) -> None:
    state = play._load(await play.store.read(cid))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id=f"wait-{state.revision}",
            actor_id=actor,
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id=actor,
    )


async def test_explicit_migration_and_replay_leave_legacy_poses_unchanged(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, migrate=False)
    old = play._load(await play.store.read(cid))
    assert old.encounters[0].participants[0].position == GridPoint(x=0, y=0)
    service = CombatService(play)
    with pytest.raises(ValidationError, match="GM"):
        await service.execute(
            cid, migration().model_copy(update={"actor_id": "a"}), authenticated_actor_id="a"
        )
    command = migration()
    first = await service.execute(cid, command, authenticated_actor_id="gm")
    assert first == await service.execute(cid, command, authenticated_actor_id="gm")
    play = play.for_campaign(await play.store.read(cid))
    state = play._load(await play.store.read(cid))
    assert state.revision == 2 and state.encounters[0].participants[0].position == Hex(q=0, r=0)
    assert old.encounters[0].participants[0].position == GridPoint(x=0, y=0)
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            command.model_copy(update={"placements": command.placements[:-1]}),
            authenticated_actor_id="gm",
        )


async def test_axial_movement_range_updates_and_no_duplicate_turn(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    command = TakeCombatTurn(
        id="hex-move",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="move",
        hex_path=(Hex(q=1, r=-1), Hex(q=2, r=-1)),
    )
    service = CombatService(play)
    result = await service.execute(cid, command, authenticated_actor_id="a")
    assert result == await service.execute(cid, command, authenticated_actor_id="a")
    state = play._load(await play.store.read(cid))
    assert state.revision == 3
    assert state.encounters[0].participants[0].position == Hex(q=2, r=-1)
    assert state.encounters[0].current_actor_id == "b"
    assert (
        play.for_campaign(await play.store.read(cid))
        ._load(await play.store.read(cid))
        .encounters[0]
        .spatial_kind
        == "hex"
    )


async def test_http_visibility_choices_authority_and_stale_revision(
    http: tuple[str, str, PlayService],
) -> None:
    base, cid, play = http
    url = f"{base}/api/tactical/v1/campaigns/{cid}"
    play.rng = RecordedDice(())  # Merely reading all choices must not roll.
    async with aiohttp.ClientSession() as client:
        data = await view(client, url)
        assert {a.id for a in data.encounters[0].actors} == {"a", "b"}
        raw = data.model_dump_json()
        assert '"c"' not in raw and '"world"' not in raw
        choices = data.encounters[0].choices
        assert any(
            c.command.kind == "take_combat_turn" and c.command.maneuver == "attack" for c in choices
        )
        selected = next(
            c.command
            for c in choices
            if isinstance(c.command, TakeCombatTurn) and c.command.hex_path == (Hex(q=1, r=-1),)
        )
        for _ in range(2):
            async with client.post(
                url + "/commands",
                headers={"Authorization": "Bearer alice-token"},
                json={"command": selected.model_dump(mode="json")},
            ) as response:
                assert response.status == 200, await response.text()
                assert TacticalSnapshot.model_validate_json(await response.text()).revision == 3
        async with client.post(
            url + "/commands",
            headers={"Authorization": "Bearer bob-token"},
            json={"command": selected.model_dump(mode="json")},
        ) as response:
            assert response.status == 403
        stale = selected.model_copy(update={"id": "stale"})
        async with client.post(
            url + "/commands",
            headers={"Authorization": "Bearer alice-token"},
            json={"command": stale.model_dump(mode="json")},
        ) as response:
            assert response.status == 409
        async with client.get(
            url + "?actor_id=a", headers={"Authorization": "Bearer spectator-token"}
        ) as response:
            assert response.status == 403
        async with client.get(
            f"{base}/campaigns/{cid}", headers={"Authorization": "Bearer alice-token"}
        ) as response:
            value: object = await response.json()
            assert isinstance(value, dict)
            assert '"c"' not in json.dumps(value["encounters"])


async def test_melee_retreat_numeric_trace_survives_restart(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path)
    service = CombatService(play)
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3))
    await service.execute(
        cid,
        TakeCombatTurn(
            id="attack",
            actor_id="a",
            expected_revision=2,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="swing",
            target_id="b",
        ),
        authenticated_actor_id="a",
    )
    defense = ChooseDefense(
        id="retreat",
        actor_id="b",
        expected_revision=3,
        encounter_id="fight",
        defense="dodge",
        retreat=Hex(q=2, r=0),
    )
    result = await service.execute(cid, defense, authenticated_actor_id="b")
    assert result.injury and result.injury.defense and result.injury.defense.effective_target == 12
    state = play._load(await play.store.read(cid))
    target = state.encounters[0].participants[1]
    assert (
        target.position == Hex(q=2, r=0)
        and not target.retreat_used  # The defender's own turn has just begun.
        and target.tactical_defense_bonus == 0
    )
    restarted = PlayService(
        AsyncSQLiteStore(tmp_path / "melee.sqlite", 10), play.engine, rng=RecordedDice(())
    )
    assert (
        await CombatService(restarted).execute(cid, defense, authenticated_actor_id="b") == result
    )
    projected = await snapshot(CampaignAccess(restarted), cid, "bob", "b")
    assert projected.encounters[0].traces[-1].totals == (9, 9)


async def test_hex_grapple_control_and_escape(tmp_path: Path) -> None:
    from test_unarmed import action, defend

    cid, play = await setup(tmp_path, unarmed=True)
    play.rng = RecordedDice((3, 3, 3))
    await action(cid, play, "a", "grapple", hands=("left-hand",), enter=True)
    await defend(cid, play)
    state = play._load(await play.store.read(cid))
    assert state.encounters[0].participants[0].position == Hex(q=1, r=0)
    assert len(state.encounters[0].grips) == 1
    view_state = await snapshot(CampaignAccess(play), cid, "bob", "b")
    assert view_state.encounters[0].grips[0].holder_id == "a"
    play.rng = RecordedDice((2, 3, 3, 4, 4, 4))
    grip = state.encounters[0].grips[0]
    await action(cid, play, "b", "break_free", grip=grip.id)
    assert not play._load(await play.store.read(cid)).encounters[0].grips


async def test_hidden_target_rejected_without_dice(http: tuple[str, str, PlayService]) -> None:
    base, cid, play = http
    play.rng = RecordedDice(())
    command = TakeCombatTurn(
        id="hidden",
        actor_id="a",
        expected_revision=2,
        encounter_id="fight",
        maneuver="attack",
        item_id="sword-a",
        target_id="c",
    )
    async with aiohttp.ClientSession() as client:
        async with client.post(
            f"{base}/api/tactical/v1/campaigns/{cid}/commands",
            headers={"Authorization": "Bearer alice-token"},
            json={"command": command.model_dump(mode="json")},
        ) as response:
            assert response.status == 400
            assert "Target is unavailable" in await response.text()
    assert play._load(await play.store.read(cid)).revision == 2


def test_tactical_contract_is_current_and_valid() -> None:
    from openapi_spec_validator import validate

    expected = contract()
    assert Path("contracts/tactical/v1/openapi.json").read_text() == expected
    validate(json.loads(expected))


@pytest.mark.parametrize("change", ["occupied", "profile", "posture", "missing"])
async def test_invalid_migration_does_not_write(tmp_path: Path, change: str) -> None:
    cid, play = await setup(tmp_path, migrate=False)
    command = migration()
    if change == "occupied":
        command = command.model_copy(
            update={
                "placements": (
                    command.placements[0],
                    command.placements[1].model_copy(update={"pose": command.placements[0].pose}),
                    command.placements[2],
                )
            }
        )
    elif change == "profile":
        command = command.model_copy(
            update={"battlefield": command.battlefield.model_copy(update={"id": "another-map"})}
        )
    elif change == "posture":
        command = command.model_copy(
            update={
                "placements": (
                    command.placements[0].model_copy(
                        update={
                            "pose": command.placements[0].pose.model_copy(
                                update={"posture": "kneeling"}
                            )
                        }
                    ),
                    *command.placements[1:],
                )
            }
        )
    else:
        command = command.model_copy(update={"placements": command.placements[:-1]})
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    assert play._load(await play.store.read(cid)).revision == 1


async def test_facing_blocks_rear_attack_before_dice(tmp_path: Path) -> None:
    cid, play = await setup(tmp_path, migrate=False)
    command = migration()
    command = command.model_copy(
        update={
            "placements": (
                command.placements[0].model_copy(
                    update={"pose": Pose(position=Hex(q=0, r=0), facing=3)}
                ),
                *command.placements[1:],
            )
        }
    )
    await CombatService(play).execute(cid, command, authenticated_actor_id="gm")
    play = play.for_campaign(await play.store.read(cid))
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="unavailable"):
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="backward",
                actor_id="a",
                expected_revision=2,
                encounter_id="fight",
                maneuver="attack",
                item_id="sword-a",
                mode_id="swing",
                target_id="b",
            ),
            authenticated_actor_id="a",
        )
    assert play._load(await play.store.read(cid)).revision == 2


async def test_hex_ranged_distance_is_current_not_the_declared_nine_yards(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.combat.ranged import situation

    cid, play = await setup(tmp_path)
    encounter = play._load(await play.store.read(cid)).encounters[0]
    assert encounter.ranged_situations[0].distance_yards == 9
    assert situation(play.rules_context, encounter, "a", "b").distance_yards == 1
    play.rng = RecordedDice((3, 3, 3, 3))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="throw",
            actor_id="a",
            expected_revision=2,
            encounter_id="fight",
            maneuver="attack",
            item_id="sword-a",
            mode_id="throw-fixture",
            target_id="b",
        ),
        authenticated_actor_id="a",
    )
    result = await CombatService(play).execute(
        cid,
        ChooseDefense(
            id="take-throw", actor_id="b", expected_revision=3, encounter_id="fight", defense="none"
        ),
        authenticated_actor_id="b",
    )
    assert result.injury is not None
    assert (
        result.injury.attack.effective_target == 13
    )  # DX 10 + 12-point Average skill (+3), range 1 => 0.
    state = play._load(await play.store.read(cid))
    assert not any(i.id == "sword-a" and i.owner_id == "a" for i in state.resources.items)


async def test_nonstanding_melee_and_unarmed_defense_use_level_difference(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.combat.tactical import height_effect
    from wayfarer.engine.simulation.combat.unarmed import unarmed_defense

    cid, play = await setup(tmp_path, unarmed=True)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    board = play.rules_context.hex_map(encounter)
    assert board is not None
    raised = board.model_copy(
        update={
            "cells": tuple(
                cell.model_copy(update={"elevation": 1}) if cell.position == Hex(q=1, r=0) else cell
                for cell in board.cells
            )
        }
    )
    participants = tuple(
        participant.model_copy(
            update={"posture": "kneeling" if participant.actor_id == "a" else "prone"}
        )
        if participant.actor_id in ("a", "b")
        else participant
        for participant in encounter.participants
    )
    encounter = encounter.model_copy(update={"participants": participants})
    raised_encounter = encounter
    raised_runtime = with_board(play, raised)
    actor = next(p for p in raised_encounter.participants if p.actor_id == "a")
    target = next(p for p in raised_encounter.participants if p.actor_id == "b")

    effect = height_effect(raised_encounter, actor, target, reach=1, location="torso", board=raised)
    assert effect.attack_modifier == 0 and effect.defender_modifier == 1
    flat_defense, _ = unarmed_defense(
        play.rules_context, state, encounter, "b", "dodge", None, attacker_id="a"
    )
    raised_defense, _ = unarmed_defense(
        raised_runtime, state, raised_encounter, "b", "dodge", None, attacker_id="a"
    )
    assert flat_defense is not None and raised_defense == flat_defense + 1


async def test_hex_ranged_distance_accounts_for_elevation(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.combat.ranged import situation

    cid, play = await setup(tmp_path)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    board = play.rules_context.hex_map(encounter)
    assert board is not None
    raised = board.model_copy(
        update={
            "cells": tuple(
                cell.model_copy(update={"elevation": 2}) if cell.position == Hex(q=1, r=0) else cell
                for cell in board.cells
            )
        }
    )
    assert situation(with_board(play, raised), encounter, "a", "b").distance_yards == 3


async def test_unarmed_v2_options_do_not_change_v1_request_contract(
    http: tuple[str, str, PlayService],
) -> None:
    base, cid, play = http
    state = play._load(await play.store.read(cid))
    body = {
        "command": {
            "kind": "take_unarmed_turn",
            "id": "v2-strong",
            "actor_id": "a",
            "expected_revision": state.revision,
            "encounter_id": "fight",
            "action": "kick",
            "target_id": "b",
            "maneuver": "all_out_attack",
            "attack_option": "strong",
        }
    }
    headers = {"Authorization": "Bearer alice-token"}
    play.rng = RecordedDice(())
    async with aiohttp.ClientSession() as client:
        async with client.post(
            f"{base}/api/tactical/v1/campaigns/{cid}/commands", json=body, headers=headers
        ) as response:
            assert response.status == 400
        assert play._load(await play.store.read(cid)) == state
        async with client.post(
            f"{base}/api/tactical/v2/campaigns/{cid}/commands", json=body, headers=headers
        ) as response:
            assert response.status == 200, await response.text()
        after = play._load(await play.store.read(cid))
        actor = next(p for p in after.encounters[0].participants if p.actor_id == "a")
        assert actor.maneuver_state.strong and actor.maneuver_state.defense_forbidden
        async with client.post(
            f"{base}/api/tactical/v2/campaigns/{cid}/commands", json=body, headers=headers
        ) as response:
            assert response.status == 200, await response.text()
        assert play._load(await play.store.read(cid)) == after
    assert play.rng.exhausted()


def test_tactical_v2_contract_matches_checked_in_schema() -> None:
    assert Path("contracts/tactical/v2/openapi.json").read_text() == contract(2)


def v1_snapshot_errors(payload: object) -> list[str]:
    """The production v1 client validates every snapshot against the frozen document."""
    import jsonschema

    document = json.loads(Path("contracts/tactical/v1/openapi.json").read_text())
    validator = jsonschema.Draft202012Validator(
        {
            "$ref": "#/components/schemas/TacticalSnapshot",
            "components": document["components"],
        }
    )
    return [error.message for error in validator.iter_errors(payload)]


async def test_every_offered_choice_stays_inside_the_frozen_v1_snapshot(
    http: tuple[str, str, PlayService],
) -> None:
    base, cid, play = http
    play.rng = RecordedDice(())
    async with aiohttp.ClientSession() as client:
        for actor, principal in (("a", "alice"), ("b", "bob"), ("c", "charlie")):
            async with client.get(
                f"{base}/api/tactical/v1/campaigns/{cid}?actor_id={actor}",
                headers={"Authorization": f"Bearer {principal}-token"},
            ) as response:
                assert response.status == 200, await response.text()
                payload = await response.json()
            offered = payload["encounters"][0]["choices"]
            assert v1_snapshot_errors(payload) == []
            if actor == "a":
                # Armed Wait declarations keep their exact pre-existing canonical payload.
                waits = [c["command"] for c in offered if c["command"].get("maneuver") == "wait"]
                assert waits and all(
                    set(c["wait_trigger"])
                    == {
                        "actor_id",
                        "action",
                        "target_id",
                        "reaction",
                        "item_id",
                        "reaction_target_id",
                        "mode_id",
                        "attack_option",
                        "zone",
                        "stop_thrust",
                    }
                    for c in waits
                )
    assert play.rng.exhausted()


async def test_unarmed_wait_declaration_is_a_v2_only_request_option(
    unarmed_http: tuple[str, str, PlayService],
) -> None:
    from test_unarmed import action, defend

    base, cid, play = unarmed_http
    play.rng = RecordedDice(())
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="pass-a",
            actor_id="a",
            expected_revision=play._load(await play.store.read(cid)).revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        authenticated_actor_id="a",
    )
    state = play._load(await play.store.read(cid))
    body = {
        "command": {
            "kind": "take_combat_turn",
            "id": "wait-unarmed",
            "actor_id": "b",
            "expected_revision": state.revision,
            "encounter_id": "fight",
            "maneuver": "wait",
            "wait_trigger": {
                "actor_id": "a",
                "action": "attack",
                "reaction": "attack",
                "reaction_target_id": "a",
                "unarmed": {"action": "punch", "hands": ["right-hand"]},
            },
        }
    }
    headers = {"Authorization": "Bearer bob-token"}
    async with aiohttp.ClientSession() as client:
        async with client.post(
            f"{base}/api/tactical/v1/campaigns/{cid}/commands", json=body, headers=headers
        ) as response:
            assert response.status == 400
        assert play._load(await play.store.read(cid)) == state
        async with client.post(
            f"{base}/api/tactical/v2/campaigns/{cid}/commands", json=body, headers=headers
        ) as response:
            assert response.status == 200, await response.text()
        declared = play._load(await play.store.read(cid)).encounters[0]
        trigger = next(p for p in declared.participants if p.actor_id == "b").maneuver_state.wait
        assert trigger is not None and trigger.item_id is None
        assert trigger.unarmed is not None and trigger.unarmed.action == "punch"

        # The waiter sees only the declared reaction and the decline once the trigger fires.
        play.rng = RecordedDice(())
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="pass-c",
                actor_id="c",
                expected_revision=play._load(await play.store.read(cid)).revision,
                encounter_id="fight",
                maneuver="do_nothing",
            ),
            authenticated_actor_id="c",
        )
        paused = await action(cid, play, "a", "punch", hands=("right-hand",), enter=True)
        assert isinstance(paused, CombatResult) and paused.code == "combat.wait_triggered"
        assert play.rng.exhausted()
        snapshot_b = await view(
            client, f"{base}/api/tactical/v2/campaigns/{cid}", actor="b", principal="bob"
        )
        offered = {choice.label: choice.command for choice in snapshot_b.encounters[0].choices}
        assert set(offered) == {"Take declared Wait reaction", "Decline Wait reaction"}
        reaction = offered["Take declared Wait reaction"]
        assert reaction.kind == "take_unarmed_turn"
        # The shared projection is read by frozen v1 clients, so its choices stay v1-shaped.
        async with client.get(
            f"{base}/api/tactical/v1/campaigns/{cid}?actor_id=b",
            headers={"Authorization": "Bearer bob-token"},
        ) as response:
            assert v1_snapshot_errors(await response.json()) == []
        async with client.post(
            f"{base}/api/tactical/v2/campaigns/{cid}/commands",
            json={"command": reaction.model_dump(mode="json")},
            headers=headers,
        ) as response:
            assert response.status == 200, await response.text()
    # ST 10 thrust is 1d-2 and a punch is thrust-1, so a 5 lands two crushing points.
    play.rng = RecordedDice((3, 3, 3, 5))
    await defend(cid, play)
    assert play.rng.exhausted()
    struck = play._load(await play.store.read(cid))
    assert [t.actor_id for t in struck.encounters[0].unarmed_history] == ["b"]
    assert next(p for p in struck.resources.pools if p.id == "hp:a").current == 8
    ready = struck.encounters[0].wait_interrupt
    assert ready is not None and ready.ready


async def test_v2_equipment_view_and_object_attack_use_authenticated_authority(
    tmp_path: Path,
) -> None:
    cid, play = await setup(
        tmp_path, durability=ObjectProfile(construction="homogenous", hp=12, dr=2, ht=12)
    )
    app = create_campaign_app(
        CampaignAccess(play), {"alice-token": "alice", "bob-token": "bob"}, legacy_routes=True
    )
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    root = f"http://127.0.0.1:{runner.addresses[0][1]}"
    url = f"{root}/api/tactical/v2/campaigns/{cid}"
    try:
        async with aiohttp.ClientSession() as client:
            async with client.get(
                url, params={"actor_id": "a"}, headers={"Authorization": "Bearer alice-token"}
            ) as response:
                assert response.status == 200
                data = await response.json()
            assert all(item["id"].endswith("-a") for item in data["equipment"])
            command = next(
                c["command"]
                for c in data["encounters"][0]["choices"]
                if c["command"].get("target_item_id") == "sword-b"
            )
            async with client.post(
                url + "/commands",
                json={"command": command},
                headers={"Authorization": "Bearer bob-token"},
            ) as response:
                assert response.status == 403
            async with client.post(
                url + "/commands",
                json={"command": command},
                headers={"Authorization": "Bearer alice-token"},
            ) as response:
                assert response.status == 200, await response.text()
            async with client.get(
                url, params={"actor_id": "b"}, headers={"Authorization": "Bearer bob-token"}
            ) as response:
                defense = next(
                    c["command"]
                    for c in (await response.json())["encounters"][0]["choices"]
                    if c["command"].get("defense") == "none"
                )
            play.rng = RecordedDice([3, 3, 3, 2])
            async with client.post(
                url + "/commands",
                json={"command": defense},
                headers={"Authorization": "Bearer bob-token"},
            ) as response:
                assert response.status == 200, await response.text()
                after = await response.json()
            sword = next(i for i in after["equipment"] if i["id"] == "sword-b")
            assert sword["condition"]["hp"] == 11
            state = play._load(await play.store.read(cid))
            assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
            play.rng = RecordedDice([])
            async with client.post(
                url + "/commands",
                json={"command": defense},
                headers={"Authorization": "Bearer bob-token"},
            ) as response:
                assert response.status == 200
                assert await response.json() == after
            async with client.get(
                url.replace("/v2/", "/v1/"),
                params={"actor_id": "b"},
                headers={"Authorization": "Bearer bob-token"},
            ) as response:
                assert "equipment" not in await response.json()
    finally:
        await runner.cleanup()
