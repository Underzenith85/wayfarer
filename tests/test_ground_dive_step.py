"""B368/B377 mapped ground Dive admission through the real combat host."""

from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, open_store, seed_campaign
from test_actions import world
from test_area_attacks import grenade, launch
from test_gurps_maneuvers import turn
from test_gurps_melee import setup
from test_gurps_ranged import scene

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.types.explosion import BlastResponse, ExplosionSpec
from wayfarer.engine.rules.types.object import GroundPosition
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.explosions import blasts
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.hex_geometry import Cell, Hex, HexBattlefield
from wayfarer.errors import ValidationError
from wayfarer.orchestration.combat import CombatService, ResolveWeaponExplosion, generations
from wayfarer.orchestration.play import PlayService


async def pending(
    path: Path,
    backend: str,
    *,
    blocked: bool,
    hexes: bool = False,
    elevation: int = 0,
    corridor: bool = False,
) -> tuple[str, PlayService, ResolveWeaponExplosion]:
    geometry: Literal["grid", "hex"] = "hex" if hexes else "grid"
    cid, play = await setup(
        path,
        "gurps-basic-set-4e-2004",
        human=True,
        aware_of=("a", "b", "c"),
        runtime_world=world().learn("a", "promise"),
        third_actor=True,
        extra_purchases=(Purchase(definition_id="secondary:basic-move", amount=11),)
        if corridor
        else (),
        ranged_mode=grenade(),
        ranged_scene=scene(),
        warhead=ExplosionSpec(dice=1),
        placements=tuple(
            Placement(actor_id=a, position=Hex(q=x, r=0), hex_facing=0 if a == "a" else 3)
            for a, x in (("a", 0), ("b", 1), ("c", 2))
        )
        if hexes
        else tuple(
            Placement(
                actor_id=a, position=GridPoint(x=x, y=0), facing="east" if a == "a" else "west"
            )
            for a, x in (("a", 0), ("b", 1), ("c", 4))
        )
        if corridor
        else None,
        battlefield=HexBattlefield(
            id="dock",
            location_id="dock",
            coordinate_system="hex-axial-v1",
            profile_id="gurps-basic-set-4e-2004",
            baseline_id="gurps-4e-characters-3p-2008+campaigns-4p-2008",
            cells=tuple(
                Cell(
                    position=Hex(q=x, r=y),
                    blocked=blocked and (x, y) == (1, 1),
                    elevation=elevation if (x, y) == (1, 1) else 0,
                )
                for x in range(4)
                for y in range(4)
            ),
        )
        if hexes
        else Battlefield(
            id="dock",
            location_id="dock",
            width=5 if corridor else 4,
            height=1 if corridor else 4,
            blocked=(GridPoint(x=2, y=0),)
            if corridor and blocked
            else (GridPoint(x=1, y=1),)
            if blocked
            else (),
        ),
    )
    if backend == "postgres":
        store = open_store(path, backend=backend)
        await seed_campaign(store, await play.store.read(cid))
        play = build_play(path, play.engine, store=store)
    await launch(
        cid, play, GroundPosition(encounter_id="fight", geometry=geometry, x=1, y=0), [3, 3, 3]
    )
    await turn(cid, play, "b", "do_nothing")
    await turn(cid, play, "c", "do_nothing")
    state = play._load(await play.store.read(cid))
    command = ResolveWeaponExplosion(
        id="ground-dive",
        actor_id="gm",
        expected_revision=state.revision,
        encounter_id="fight",
        blast_id=blasts(state.resources)[0].id,
        responses=tuple(
            BlastResponse(
                actor_id=actor,
                cover_dr=0,
                size_modifier=0,
                dive_to=GroundPosition(
                    encounter_id="fight",
                    geometry=geometry,
                    x=3 if corridor else 1,
                    y=0 if corridor else 1,
                )
                if actor == "b"
                else None,
                dive_cover_dr=100 if actor == "b" else 0,
                dive_covered_locations=("torso",) if actor == "b" else (),
            )
            for actor in (("a", "b") if corridor else ("a", "b", "c"))
        ),
        object_cover={},
        environment="air",
    )
    return cid, play, command


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_blocked_ground_dive_rejects_before_any_randomness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=True)
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="legal ground diving step"):
        await CombatService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before
    assert play.rng.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("succeeds", [True, False])
async def test_lawful_ground_dive_preserves_damage_order_and_exact_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, succeeds: bool
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=False)
    roll = 2 if succeeds else 5
    play.rng = RecordedDice([3, roll, roll, roll, 3, 3])
    service = CombatService(play)
    result = await service.execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        10 if succeeds else 7
    )
    actor = next(p for p in state.encounters[0].participants if p.actor_id == "b")
    assert actor.position == GridPoint(x=1, y=1) and actor.posture == "prone"
    assert play.rng.exhausted()
    play.rng = RecordedDice([])
    assert await service.execute(cid, command, principal_id="gm") == result
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_occupied_destination_needs_explicit_contact(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=False)
    command = command.model_copy(
        update={
            "responses": tuple(
                r.model_copy(
                    update={
                        "dive_to": GroundPosition(encounter_id="fight", geometry="grid", x=0, y=0)
                    }
                )
                if r.actor_id == "b"
                else r
                for r in command.responses
            )
        }
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="legal ground diving step"):
        await CombatService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_water_skips_ground_geometry_but_not_existing_response_validation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=True)
    command = command.model_copy(update={"environment": "water"})
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Replay requested more dice"):
        await CombatService(play).execute(cid, command, principal_id="gm")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_absent_feature_keeps_legacy_pre_randomness_semantics(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE - {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=True)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Replay requested more dice"):
        await CombatService(play).execute(cid, command, principal_id="gm")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_failed_roll_stream_rolls_back_and_retries_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=False)
    before = await play.store.read(cid)
    play.rng = RecordedDice([3, 5, 5, 5])
    with pytest.raises(ValidationError, match="Replay requested more dice"):
        await CombatService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before
    play.rng = RecordedDice([3, 5, 5, 5, 3, 3])
    service = CombatService(play)
    receipt = await service.execute(cid, command, principal_id="gm")
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, principal_id="gm") == receipt
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("elevation", [0, 2])
async def test_hex_ground_dive_requires_canonical_elevation_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, elevation: int
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(
        tmp_path, backend, blocked=False, hexes=True, elevation=elevation
    )
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    if elevation:
        with pytest.raises(ValidationError, match="legal ground diving step"):
            await CombatService(play).execute(cid, command, principal_id="gm")
    else:
        with pytest.raises(ValidationError, match="Replay requested more dice"):
            await CombatService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("succeeds", [True, False])
async def test_contact_exception_only_for_authoritative_blast_center(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, succeeds: bool
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=False)
    center = GroundPosition(encounter_id="fight", geometry="grid", x=1, y=0)
    command = command.model_copy(
        update={
            "responses": tuple(
                BlastResponse(
                    actor_id=a,
                    cover_dr=0,
                    size_modifier=0,
                    dive_to=center if a == "c" else None,
                    sacrificial_contact=a == "c",
                )
                for a in ("a", "b", "c")
            )
        }
    )
    roll = 2 if succeeds else 5
    play.rng = RecordedDice([roll, roll, roll, 3, 3] if succeeds else [roll, roll, roll, 3, 3, 3])
    await CombatService(play).execute(cid, command, principal_id="gm")
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == (
        10 if succeeds else 7
    )
    assert next(p.current for p in state.resources.pools if p.id == "hp:c") == (
        14 if succeeds else 19
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("feature", [True, False])
async def test_seeded_reexecution_uses_captured_ground_generation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, feature: bool
) -> None:
    import secrets

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    active = (
        generations.ACTIVE | {"ground-dive-step"}
        if feature
        else generations.ACTIVE - {"ground-dive-step"}
    )
    monkeypatch.setattr(generations, "ACTIVE", active)
    cid, play, command = await pending(tmp_path, backend, blocked=not feature, corridor=not feature)
    initial = await play.store.read(cid)
    state = play._load(initial)
    count = len(await play.store.history(cid))
    play.rng, play.seeds = secrets, lambda: "0" * 64
    await CombatService(play).execute(cid, command, principal_id="gm")
    final = await play.store.read(cid)
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE ^ {"ground-dive-step"})
    records = (await play.store.history(cid))[count:]
    ids = {r.command_id for r in records}
    replayed, checks = await verify_commands(
        initial,
        records,
        [e for e in await play.store.stream(cid) if e.command_id in ids],
        configuration_digest=state.configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "seed-only"),
    )
    assert len(checks) == 1 and all(c.folded and c.reexecuted for c in checks)
    assert play._load(replayed) == play._load(final)
    play.rng = RecordedDice([])
    await CombatService(play).execute(cid, command, principal_id="gm")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("blocked", [False, True])
async def test_multiyard_dive_admits_only_reachable_current_move_step(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str, blocked: bool
) -> None:
    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=blocked, corridor=True)
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(
        ValidationError,
        match="legal ground diving step" if blocked else "Replay requested more dice",
    ):
        await CombatService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ground_dive_keeps_host_authority_and_revision_guards(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    from wayfarer.errors import ConflictError

    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=False)
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="not authorized"):
        await CombatService(play).execute(cid, command, principal_id="b")
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid,
            command.model_copy(update={"expected_revision": command.expected_revision - 1}),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("succeeds", [True, False])
async def test_feature_keeps_existing_airborne_dive_resolution(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, succeeds: bool
) -> None:
    from test_area_attacks import (
        test_source_dive_cover_applies_before_success_damage_and_after_failed_damage,
    )

    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    await test_source_dive_cover_applies_before_success_damage_and_after_failed_damage(
        tmp_path, succeeds, "flying"
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ground_dive_cannot_leave_a_retained_grip(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, backend: str
) -> None:
    from wayfarer.contracts import Campaign, CommandReceipt
    from wayfarer.engine.simulation.combat.engine import CombatEngine
    from wayfarer.engine.simulation.combat.unarmed.records import Grip

    monkeypatch.setattr(generations, "ACTIVE", generations.ACTIVE | {"ground-dive-step"})
    cid, play, command = await pending(tmp_path, backend, blocked=False)
    state = play._load(await play.store.read(cid))
    encounter = state.encounters[0]
    b = next(p for p in encounter.participants if p.actor_id == "b")
    c = next(p for p in encounter.participants if p.actor_id == "c")
    encounter = CombatEngine._replace(encounter, b.model_copy(update={"hand_bindings": ()}))
    encounter = CombatEngine._replace(
        encounter, c.model_copy(update={"position": b.position, "grappled": True})
    ).model_copy(
        update={
            "close_pairs": (("b", "c"),),
            "grips": (
                Grip(id="retained-grip", holder_id="b", target_id="c", hands=("left-hand",)),
            ),
        }
    )
    updated = state.model_copy(
        update={
            "encounters": (encounter,),
            "revision": state.revision + 1,
            "resources": state.resources.model_copy(update={"revision": state.revision + 1}),
        }
    )

    def install(campaign: Campaign) -> CommandReceipt:
        play.engine.validate(updated)
        campaign["play_json"], campaign["revision"] = updated.model_dump_json(), updated.revision
        return CommandReceipt(action="combat", outcome="grip-fixture")

    await play.store.commit_turn(cid, "grip-fixture", state.revision, "grip-fixture", install)
    command = command.model_copy(update={"expected_revision": updated.revision})
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    with pytest.raises(ValidationError, match="Release or escape the grapple"):
        await CombatService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before
