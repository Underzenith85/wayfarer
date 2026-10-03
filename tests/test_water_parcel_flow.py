"""Actual B253 purification of complete physically separate measured containers."""

import json
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_play
from test_actions import campaign
from test_combat_sensory_authority import change
from test_gadgeteer_gizmos_persistence import FailingCommitPlay
from test_lock_spell_persistence import revision
from test_water_persistence import prepare

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellResult
from wayfarer.engine.simulation.magic.water_bindings import WaterChannel
from wayfarer.engine.simulation.magic.water_effects import WaterPlan
from wayfarer.engine.simulation.magic.water_host import DeclareWater, DeclareWaterChannel
from wayfarer.engine.simulation.magic.water_parcels import DeclareWaterParcels, ParcelFlow
from wayfarer.engine.simulation.magic.water_state import WaterBody, latest
from wayfarer.engine.world import Entity, EntityKind, Fact
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.water import WaterService
from wayfarer.persistence.replay import verify_commands


async def fixture(
    path: Path, backend: str, *, purify_points: int = 4, iq: int = 12
) -> tuple[str, PlayService, tuple[str, ...], Campaign]:
    cid, play = await prepare(path, backend)
    source = play._load(await play.store.read(cid))
    entities = tuple(
        replace(e, owner_id="a") if e.id in ("chest", "hidden") else e
        for e in source.world.entities
    ) + (
        Entity("ring", EntityKind.OBJECT, "Actual purification ring", "dock", "a"),
        *(
            Entity(f"parcel-{n}", EntityKind.OBJECT, "Separate gallon container", "dock", "hidden")
            for n in range(3)
        ),
    )
    values = {
        "hidden": {"position_x_yards": "0", "position_y_yards": "0", "height_mm": "1000"},
        "chest": {"position_x_yards": "0", "position_y_yards": "0", "height_mm": "0"},
        "ring": {
            "position_x_yards": "0",
            "position_y_yards": "0",
            "height_mm": "500",
            "opening_mm": "20",
            "condition": "intact",
            "stream_width_mm": "10",
            "flow_seconds_per_gallon": "5",
        },
        **{
            f"parcel-{n}": {
                "position_x_yards": "0",
                "position_y_yards": "0",
                "height_mm": "1000",
                "water_volume_gallons": "1",
                "water_purity": "impure" if n == 0 else "pure",
            }
            for n in range(3)
        },
    }
    measured = tuple(
        Fact(f"measure:{subject}:{attribute}", subject, attribute, value)
        for subject, attrs in values.items()
        for attribute, value in attrs.items()
    )
    world = replace(source.world, entities=entities, facts=source.world.facts + measured)
    initial = campaign(play.engine)
    await seed_play(
        play,
        initial,
        world,
        source.resources,
        tuple(
            ActorSetup(
                actor_id=a.actor_id,
                proposal=a.proposal.model_copy(
                    update={
                        "draft": a.proposal.draft.model_copy(
                            update={
                                "purchases": tuple(
                                    p.model_copy(update={"amount": purify_points})
                                    if p.definition_id == "spell:purify-water"
                                    else p.model_copy(update={"amount": iq})
                                    if p.definition_id == "attribute:iq"
                                    else p
                                    for p in a.proposal.draft.purchases
                                )
                            }
                        )
                    }
                ),
                aware_of=("chest", "hidden", "ring"),
            )
            for a in source.actors
        ),
        members=source.members,
    )
    cid = initial["id"]
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    genesis = await play.store.read(cid)
    for name, gallons, pure in (("hidden", 3, 2), ("chest", 0, 0)):
        await WaterService(play).execute(
            cid,
            DeclareWater(
                id="body:" + name,
                actor_id="gm",
                expected_revision=await revision(play, cid),
                body=WaterBody(
                    object_id=name,
                    location_id="dock",
                    gallons=gallons,
                    pure_gallons=pure,
                    nature="assembly of separate containers" if name == "hidden" else "receiver",
                    capacity_gallons=5,
                ),
            ),
            principal_id="gm",
        )
    return cid, play, tuple(f.id for f in measured), genesis


async def channel(play: PlayService, cid: str, plan: WaterPlan) -> None:
    await WaterService(play).execute(
        cid,
        DeclareWaterChannel(
            id="channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(
                id="flow", actor_id="a", location_id="dock", touching=True, plan=plan
            ),
        ),
        principal_id="gm",
    )


async def cast(play: PlayService, cid: str, seconds: int = 5) -> RuntimeSpellCommand:
    command = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="purify-water",
        cast_id="partial",
        channel_id="flow",
    )
    await WaterService(play).execute(cid, command, principal_id="alice")
    for n in range(seconds):
        await play.execute(
            cid,
            Wait(
                id=f"tick:{n}", actor_id="a", expected_revision=await revision(play, cid), ticks=1
            ),
            principal_id="a",
        )
        if n + 1 < seconds:
            await WaterService(play).execute(
                cid,
                command.model_copy(
                    update={
                        "id": f"concentrate:{n}",
                        "kind": "concentrate",
                        "expected_revision": await revision(play, cid),
                    }
                ),
                principal_id="alice",
            )
    return command.model_copy(
        update={
            "id": "complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_measured_separate_container_partial_purification_changes_actual_material(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await observe(play, cid, facts)
    await channel(
        play,
        cid,
        WaterPlan(
            spell_id="purify-water",
            target_id="chest",
            source_id="hidden",
            gallons=1,
            flowing_through_ring=True,
            parcel_flow_id="measured",
        ),
    )
    completion = await cast(play, cid)
    play.rng = RecordedDice((3, 3, 3))
    result = await WaterService(play).execute(cid, completion, principal_id="alice")
    assert isinstance(result, SpellResult) and result.outcome == "active"
    bodies = latest(play._load(await play.store.read(cid)).resources)
    assert (bodies["hidden"].gallons, bodies["hidden"].pure_gallons) == (2, 2)
    assert (bodies["chest"].gallons, bodies["chest"].pure_gallons) == (1, 1)


async def observe(play: PlayService, cid: str, facts: tuple[str, ...]) -> None:
    await WaterService(play).execute(
        cid,
        DeclareWaterParcels(
            id="observation",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            flow=ParcelFlow(
                id="measured",
                caster_id="a",
                source_id="hidden",
                target_id="chest",
                ring_id="ring",
                container_ids=("parcel-0", "parcel-1", "parcel-2"),
                fact_ids=facts,
            ),
        ),
        principal_id="gm",
    )


def plan(gallons: int = 1) -> WaterPlan:
    return WaterPlan(
        spell_id="purify-water",
        target_id="chest",
        source_id="hidden",
        gallons=gallons,
        flowing_through_ring=True,
        parcel_flow_id="measured",
    )


async def fact_change(
    play: PlayService, cid: str, subject: str, predicate: str, value: str
) -> None:
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "world": replace(
                    s.world,
                    facts=tuple(
                        replace(f, value=value)
                        if (f.subject_id, f.predicate) == (subject, predicate)
                        else f
                        for f in s.world.facts
                    ),
                )
            }
        ),
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("gallons,rolls", [(1, (3, 3, 3)), (2, (3, 3, 3)), (1, (5, 5, 6))])
async def test_material_cost_time_failure_restart_exact_retry(
    tmp_path: Path, backend: str, gallons: int, rolls: tuple[int, ...]
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await observe(play, cid, facts)
    await channel(play, cid, plan(gallons))
    completion = await cast(play, cid, seconds=5 * gallons)
    before = await play.store.read(cid)
    play.rng = RecordedDice(rolls)
    result = await WaterService(play).execute(cid, completion, principal_id="alice")
    final = await play.store.read(cid)
    state = play._load(final)
    bodies = latest(state.resources)
    success = rolls != (5, 5, 6)
    assert isinstance(result, SpellResult) and result.outcome == ("active" if success else "failed")
    assert (bodies["hidden"].gallons, bodies["hidden"].pure_gallons) == (
        (3 - gallons, 2 - max(0, gallons - 1)) if success else (3, 2)
    )
    assert (bodies["chest"].gallons, bodies["chest"].pure_gallons) == (
        (gallons, gallons) if success else (0, 0)
    )
    for n in range(3):
        water = {f.predicate: f.value for f in state.world.facts if f.subject_id == f"parcel-{n}"}
        assert water["water_volume_gallons"] == ("0" if success and n < gallons else "1")
        assert water["water_purity"] == (
            "empty" if success and n < gallons else "impure" if n == 0 else "pure"
        )
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 10 - (
        gallons if success else 1
    )
    assert state.resources.game_time == 5 * gallons
    if not success:
        assert state.world == play._load(before).world
    fresh = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await WaterService(fresh).execute(cid, completion, principal_id="alice") == result
    raw = json.dumps(
        {
            "operation": "water",
            "generation": 1,
            "principal_id": "alice",
            "command": completion.model_dump(mode="json"),
        },
        sort_keys=True,
    )
    assert await play.store.duplicate(cid, completion.id, raw) == final
    with pytest.raises(ConflictError, match="different input"):
        await play.store.duplicate(cid, completion.id, raw + " ")
    assert await play.store.read(cid) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "subject,predicate,value",
    [
        ("ring", "condition", "broken"),
        ("ring", "opening_mm", "5"),
        ("ring", "height_mm", "1000"),
        ("ring", "flow_seconds_per_gallon", "4"),
        ("ring", "position_x_yards", "1"),
        ("parcel-0", "water_volume_gallons", "1/2"),
        ("parcel-0", "water_volume_gallons", "01"),
        ("parcel-0", "water_volume_gallons", "one"),
        ("parcel-0", "water_purity", "partially-pure"),
        ("parcel-1", "water_volume_gallons", "2"),
    ],
)
async def test_invalid_current_physical_measurements_reject_before_rng(
    tmp_path: Path, backend: str, subject: str, predicate: str, value: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await fact_change(play, cid, subject, predicate, value)
    before = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError):
        await observe(play, cid, facts)
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "mutation", ["custody", "missing", "nested", "extra", "ambiguous", "wrong-unit"]
)
async def test_physical_topology_and_measurement_references_fail_closed(
    tmp_path: Path, backend: str, mutation: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    world = state.world
    if mutation == "custody":
        world = replace(
            world,
            entities=tuple(
                replace(e, owner_id="chest") if e.id == "ring" else e for e in world.entities
            ),
        )
    elif mutation == "missing":
        world = replace(
            world,
            entities=tuple(
                replace(e, owner_id="a") if e.id == "parcel-0" else e for e in world.entities
            ),
        )
    elif mutation in ("nested", "extra"):
        world = replace(
            world,
            entities=world.entities
            + (
                Entity(
                    "extra",
                    EntityKind.OBJECT,
                    "Unaccounted child",
                    "dock",
                    "parcel-0" if mutation == "nested" else "hidden",
                ),
            ),
        )
    elif mutation == "ambiguous":
        world = replace(world, facts=world.facts + (Fact("duplicate", "ring", "opening_mm", "20"),))
    else:
        world = replace(
            world,
            facts=tuple(
                replace(f, predicate="opening_cm")
                if f.subject_id == "ring" and f.predicate == "opening_mm"
                else f
                for f in world.facts
            ),
        )
    await change(play, cid, lambda s: s.model_copy(update={"world": world}))
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await observe(play, cid, facts)
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("boundary", ["cut", "stale-fact", "stale-custody", "source-quantity"])
async def test_current_boundary_recheck_is_atomic_before_roll(
    tmp_path: Path, backend: str, boundary: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    if boundary == "cut":
        await fact_change(play, cid, "parcel-0", "water_volume_gallons", "2")
        await fact_change(play, cid, "parcel-1", "water_volume_gallons", "0")
        await fact_change(play, cid, "parcel-1", "water_purity", "empty")
        # Keep aggregate composition exact: two impure gallons, one pure gallon.
        from wayfarer.engine.simulation.magic.water_state import save

        await change(
            play,
            cid,
            lambda s: s.model_copy(
                update={
                    "resources": save(
                        s.resources,
                        latest(s.resources)["hidden"].model_copy(update={"pure_gallons": 1}),
                        "fixture-composition",
                    )
                }
            ),
        )
        await observe(play, cid, facts)
        before = await play.store.read(cid)
        with pytest.raises(ValidationError, match="cut inside"):
            await channel(play, cid, plan())
    else:
        await observe(play, cid, facts)
        await channel(play, cid, plan())
        completion = await cast(play, cid)
        if boundary == "stale-fact":
            await fact_change(play, cid, "ring", "opening_mm", "30")
        elif boundary == "stale-custody":
            await change(
                play,
                cid,
                lambda s: s.model_copy(
                    update={
                        "world": replace(
                            s.world,
                            entities=tuple(
                                replace(e, owner_id="chest") if e.id == "ring" else e
                                for e in s.world.entities
                            ),
                        )
                    }
                ),
            )
        else:
            from wayfarer.engine.simulation.magic.water_state import save

            await change(
                play,
                cid,
                lambda s: s.model_copy(
                    update={
                        "resources": save(
                            s.resources,
                            latest(s.resources)["hidden"].model_copy(
                                update={"gallons": 2, "pure_gallons": 1}
                            ),
                            "fixture-material",
                        )
                    }
                ),
            )
        before = await play.store.read(cid)
        play.rng = RecordedDice(())
        with pytest.raises(ValidationError):
            await WaterService(play).execute(
                cid,
                completion.model_copy(update={"expected_revision": await revision(play, cid)}),
                principal_id="alice",
            )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_success_material_and_cost_rollback_together(tmp_path: Path, backend: str) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await observe(play, cid, facts)
    await channel(play, cid, plan())
    completion = await cast(play, cid)
    before = await play.store.read(cid)
    failing = FailingCommitPlay(engine=play.engine, store=play.store, rng=RecordedDice((3, 3, 3)))
    with pytest.raises(RuntimeError, match="candidate checkpoint"):
        await WaterService(failing).execute(cid, completion, principal_id="alice")
    assert await play.store.read(cid) == before
    play.rng = RecordedDice((3, 3, 3))
    assert (
        await WaterService(play).execute(cid, completion, principal_id="alice")
    ).outcome == "active"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("points", [4, 28])
async def test_private_observation_and_actual_commands_fully_seed_reexecute(
    tmp_path: Path, backend: str, points: int
) -> None:
    cid, play, facts, genesis = await fixture(tmp_path, backend, purify_points=points)
    play.rng = secrets
    play.seeds = lambda: f"{1:064x}"
    await observe(play, cid, facts)
    await channel(play, cid, plan())
    completion = await cast(play, cid)
    assert (
        await WaterService(play).execute(cid, completion, principal_id="alice")
    ).outcome == "active"
    final = await play.store.read(cid)
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(genesis).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "water-parcels-reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert canonical_campaign(replayed) == canonical_campaign(final)
    if points == 4:
        assert replayed == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_legacy_absence_preserves_actual_mixed_source_refusal(
    tmp_path: Path, backend: str
) -> None:
    cid, play, _, _ = await fixture(tmp_path, backend)
    legacy = plan().model_copy(update={"parcel_flow_id": None})
    assert "parcel_flow_id" not in legacy.model_dump()
    await channel(play, cid, legacy)
    completion = await cast(play, cid)
    before = await play.store.read(cid)
    play.rng = RecordedDice((3, 3, 3))
    with pytest.raises(ValidationError, match="authored flow composition"):
        await WaterService(play).execute(cid, completion, principal_id="alice")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "variant", ["alias", "cycle", "duplicate", "double-body", "referenced-missing", "typed-number"]
)
async def test_actual_private_observation_identity_rejects_aliases_and_uncanonical_facts(
    tmp_path: Path, backend: str, variant: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    flow = ParcelFlow(
        id="measured",
        caster_id="a",
        source_id="hidden",
        target_id="chest",
        ring_id="ring",
        container_ids=("parcel-0", "parcel-1", "parcel-2"),
        fact_ids=facts,
    )
    if variant == "alias":
        flow = flow.model_copy(update={"ring_id": "chest"})
    elif variant == "duplicate":
        flow = flow.model_copy(update={"container_ids": ("parcel-0", "parcel-0", "parcel-2")})
    elif variant == "referenced-missing":
        flow = flow.model_copy(update={"fact_ids": facts + ("missing-fact",)})
    elif variant == "cycle":
        await change(
            play,
            cid,
            lambda s: s.model_copy(
                update={
                    "world": replace(
                        s.world,
                        entities=tuple(
                            replace(e, owner_id="hidden") if e.id == "a" else e
                            for e in s.world.entities
                        ),
                    )
                }
            ),
        )
    elif variant == "typed-number":
        await fact_change(play, cid, "ring", "opening_mm", "20.0")
    else:
        await WaterService(play).execute(
            cid,
            DeclareWater(
                id="child-body",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                body=WaterBody(
                    object_id="parcel-0",
                    location_id="dock",
                    gallons=1,
                    pure_gallons=0,
                    nature="same material twice",
                ),
            ),
            principal_id="gm",
        )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await WaterService(play).execute(
            cid,
            DeclareWaterParcels(
                id="observation",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                flow=flow,
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_authority_privacy_and_terminal_pour(tmp_path: Path, backend: str) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    command = DeclareWaterParcels(
        id="observation",
        actor_id="gm",
        expected_revision=await revision(play, cid),
        flow=ParcelFlow(
            id="measured",
            caster_id="a",
            source_id="hidden",
            target_id="chest",
            ring_id="ring",
            container_ids=("parcel-0", "parcel-1", "parcel-2"),
            fact_ids=facts,
        ),
    )
    before = await play.store.read(cid)
    for principal, actor in (("alice", "a"), ("gm", "a")):
        with pytest.raises(ValidationError):
            await WaterService(play).execute(
                cid, command.model_copy(update={"actor_id": actor}), principal_id=principal
            )
        assert await play.store.read(cid) == before
    receipt = await WaterService(play).execute(cid, command, principal_id="gm")
    saved = await play.store.read(cid)
    assert await WaterService(play).execute(cid, command, principal_id="gm") == receipt
    assert await play.store.read(cid) == saved
    runtime = build_runtime(play)
    assert "water-parcels:" not in json.dumps(await runtime.read(cid, principal_id="alice"))
    assert "water-parcels:" not in json.dumps(
        [e.model_dump(mode="json") for e in await runtime.events(cid, principal_id="alice")]
    )
    await channel(play, cid, plan())
    completion = await cast(play, cid)
    play.rng = RecordedDice((3, 3, 3))
    await WaterService(play).execute(cid, completion, principal_id="alice")
    final = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await WaterService(play).execute(
            cid,
            completion.model_copy(
                update={"id": "new-completion", "expected_revision": await revision(play, cid)}
            ),
            principal_id="alice",
        )
    with pytest.raises(ConflictError, match="cannot be replaced"):
        await WaterService(play).execute(
            cid,
            command.model_copy(
                update={"id": "replace-observation", "expected_revision": await revision(play, cid)}
            ),
            principal_id="gm",
        )
    with pytest.raises(
        ValidationError,
        match="cannot duplicate water aggregates",
    ):
        await WaterService(play).execute(
            cid,
            DeclareWater(
                id="duplicate-child",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                body=WaterBody(
                    object_id="parcel-1",
                    location_id="dock",
                    gallons=1,
                    pure_gallons=1,
                    nature="double count",
                ),
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == final


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pure_first_container_preserves_impure_remaining_volume(
    tmp_path: Path, backend: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await fact_change(play, cid, "parcel-0", "water_purity", "pure")
    await fact_change(play, cid, "parcel-1", "water_purity", "impure")
    await fact_change(play, cid, "ring", "flow_seconds_per_gallon", "10")
    await observe(play, cid, facts)
    await channel(play, cid, plan().model_copy(update={"seconds_per_gallon": 10}))
    completion = await cast(play, cid, seconds=10)
    play.rng = RecordedDice((3, 3, 3))
    await WaterService(play).execute(cid, completion, principal_id="alice")
    state = play._load(await play.store.read(cid))
    assert (
        latest(state.resources)["hidden"].gallons,
        latest(state.resources)["hidden"].pure_gallons,
    ) == (2, 1)
    assert state.resources.game_time == 10


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_remaining_separate_containers_require_a_new_finite_observation(
    tmp_path: Path, backend: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await observe(play, cid, facts)
    await channel(play, cid, plan())
    completion = await cast(play, cid)
    play.rng = RecordedDice((3, 3, 3))
    await WaterService(play).execute(cid, completion, principal_id="alice")
    await WaterService(play).execute(
        cid,
        DeclareWaterParcels(
            id="next-observation",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            flow=ParcelFlow(
                id="remaining",
                caster_id="a",
                source_id="hidden",
                target_id="chest",
                ring_id="ring",
                container_ids=("parcel-0", "parcel-1", "parcel-2"),
                fact_ids=facts,
            ),
        ),
        principal_id="gm",
    )
    await WaterService(play).execute(
        cid,
        DeclareWaterChannel(
            id="next-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=WaterChannel(
                id="next-flow",
                actor_id="a",
                location_id="dock",
                touching=True,
                plan=plan(2).model_copy(update={"parcel_flow_id": "remaining"}),
            ),
        ),
        principal_id="gm",
    )
    start = RuntimeSpellCommand(
        id="next-start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="purify-water",
        cast_id="next-pour",
        channel_id="next-flow",
    )
    await WaterService(play).execute(cid, start, principal_id="alice")
    for n in range(10):
        await play.execute(
            cid,
            Wait(
                id=f"next-tick:{n}",
                actor_id="a",
                expected_revision=await revision(play, cid),
                ticks=1,
            ),
            principal_id="a",
        )
        if n < 9:
            await WaterService(play).execute(
                cid,
                start.model_copy(
                    update={
                        "id": f"next-concentrate:{n}",
                        "kind": "concentrate",
                        "expected_revision": await revision(play, cid),
                    }
                ),
                principal_id="alice",
            )
    play.rng = RecordedDice((3, 3, 3))
    await WaterService(play).execute(
        cid,
        start.model_copy(
            update={
                "id": "next-complete",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    bodies = latest(state.resources)
    assert (
        bodies["hidden"].gallons,
        bodies["hidden"].pure_gallons,
        bodies["chest"].gallons,
        bodies["chest"].pure_gallons,
    ) == (0, 0, 3, 3)
    assert all(f.value == "0" for f in state.world.facts if f.predicate == "water_volume_gallons")
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 7


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("operation", ["purify", "create", "destroy"])
@pytest.mark.parametrize("ended", [False, True])
async def test_observed_material_cannot_diverge_through_legacy_whole_body_mutation(
    tmp_path: Path, backend: str, operation: str, ended: bool
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await observe(play, cid, facts)
    if ended:
        await channel(play, cid, plan())
        completion = await cast(play, cid)
        play.rng = RecordedDice((3, 3, 3))
        await WaterService(play).execute(cid, completion, principal_id="alice")
    before = await play.store.read(cid)
    records, events = await played(play.store, cid), await play.store.stream(cid)
    if operation == "purify":
        candidate = plan(2 if ended else 3).model_copy(
            update={"parcel_flow_id": None, "purify_entire_source": True}
        )
    elif operation == "create":
        candidate = WaterPlan(
            spell_id="create-water", target_id="hidden", gallons=1, allow_receiver_mixing=True
        )
    else:
        candidate = WaterPlan(
            spell_id="destroy-water",
            target_id="hidden",
            destroyed_ids=("hidden",),
            area_center=(0, 0),
        )
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="concrete pour adapter"):
        await WaterService(play).execute(
            cid,
            DeclareWaterChannel(
                id="bypass-channel",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                channel=WaterChannel(
                    id="bypass", actor_id="a", location_id="dock", touching=True, plan=candidate
                ),
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await played(play.store, cid) == records
    assert await play.store.stream(cid) == events


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_unrelated_receiver_only_legacy_create_still_changes_actual_material(
    tmp_path: Path, backend: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await observe(play, cid, facts)
    await channel(play, cid, WaterPlan(spell_id="create-water", target_id="chest", gallons=1))
    command = RuntimeSpellCommand(
        id="start-create",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="create-water",
        cast_id="create",
        channel_id="flow",
    )
    await WaterService(play).execute(cid, command, principal_id="alice")
    await play.execute(
        cid,
        Wait(id="create-tick", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    play.rng = RecordedDice((3, 3, 3))
    await WaterService(play).execute(
        cid,
        command.model_copy(
            update={
                "id": "complete-create",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="alice",
    )
    state = play._load(await play.store.read(cid))
    assert (
        latest(state.resources)["chest"].gallons,
        latest(state.resources)["chest"].pure_gallons,
    ) == (1, 1)
    assert latest(state.resources)["hidden"].gallons == 3
    assert all(f.value == "1" for f in state.world.facts if f.predicate == "water_volume_gallons")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_observation_during_legacy_cast_blocks_completion_before_roll_and_checkpoint(
    tmp_path: Path, backend: str
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    await channel(
        play, cid, plan(3).model_copy(update={"parcel_flow_id": None, "purify_entire_source": True})
    )
    completion = await cast(play, cid, 15)
    await observe(play, cid, facts)
    completion = completion.model_copy(update={"expected_revision": await revision(play, cid)})
    before = await play.store.read(cid)
    records, events = await played(play.store, cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="concrete pour adapter"):
        await WaterService(play).execute(cid, completion, principal_id="alice")
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await played(play.store, cid) == records
    assert await play.store.stream(cid) == events


async def receiver_assembly(play: PlayService, cid: str) -> tuple[str, ...]:
    await fact_change(play, cid, "chest", "height_mm", "400")
    extra = (
        Entity("receiver-child", EntityKind.OBJECT, "Separate empty container", "dock", "chest"),
        Entity("other-receiver", EntityKind.OBJECT, "Other actual receiver", "dock", "a"),
        Entity("other-ring", EntityKind.OBJECT, "Other actual ring", "dock", "a"),
    )
    values = {
        "receiver-child": {
            "position_x_yards": "0",
            "position_y_yards": "0",
            "height_mm": "400",
            "water_volume_gallons": "0",
            "water_purity": "empty",
        },
        "other-receiver": {"position_x_yards": "0", "position_y_yards": "0", "height_mm": "0"},
        "other-ring": {
            "position_x_yards": "0",
            "position_y_yards": "0",
            "height_mm": "200",
            "opening_mm": "20",
            "condition": "intact",
            "stream_width_mm": "10",
            "flow_seconds_per_gallon": "5",
        },
    }
    facts = tuple(
        Fact(f"measure:{i}:{key}", i, key, value)
        for i, measured in values.items()
        for key, value in measured.items()
    )
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "world": replace(
                    s.world, entities=s.world.entities + extra, facts=s.world.facts + facts
                )
            }
        ),
    )
    await WaterService(play).execute(
        cid,
        DeclareWater(
            id="other-body",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            body=WaterBody(
                object_id="other-receiver",
                location_id="dock",
                gallons=0,
                pure_gallons=0,
                capacity_gallons=5,
                nature="other receiver",
            ),
        ),
        principal_id="gm",
    )
    return tuple(
        f.id
        for f in play._load(await play.store.read(cid)).world.facts
        if f.subject_id in {"chest", "receiver-child", "other-receiver", "other-ring"}
        and f.id.startswith("measure:")
    )


async def observe_receiver(play: PlayService, cid: str, facts: tuple[str, ...]) -> None:
    await WaterService(play).execute(
        cid,
        DeclareWaterParcels(
            id="receiver-observation",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            flow=ParcelFlow(
                id="receiver-assembly",
                caster_id="a",
                source_id="chest",
                target_id="other-receiver",
                ring_id="other-ring",
                container_ids=("receiver-child",),
                fact_ids=facts,
            ),
        ),
        principal_id="gm",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("during_cast", [False, True])
async def test_actual_valid_parcel_flow_cannot_diverge_other_assembly(
    tmp_path: Path, backend: str, during_cast: bool
) -> None:
    cid, play, facts, _ = await fixture(tmp_path, backend)
    receiver_facts = await receiver_assembly(play, cid)
    await observe(play, cid, facts)
    if during_cast:
        await channel(play, cid, plan())
        completion = await cast(play, cid)
        await observe_receiver(play, cid, receiver_facts)
        completion = completion.model_copy(update={"expected_revision": await revision(play, cid)})
    else:
        await observe_receiver(play, cid, receiver_facts)
    before = await play.store.read(cid)
    records, events = await played(play.store, cid), await play.store.stream(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="concrete pour adapter"):
        if during_cast:
            await WaterService(play).execute(cid, completion, principal_id="alice")
        else:
            await channel(play, cid, plan())
    assert await play.store.read(cid) == before == await play.store.replay(cid)
    assert await played(play.store, cid) == records
    assert await play.store.stream(cid) == events


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("skill,points,iq,seconds,fp", [(20, 28, 12, 5, 10), (8, 1, 8, 10, 9)])
async def test_actual_measured_flow_is_physical_minimum_with_generic_skill_modifiers(
    tmp_path: Path, backend: str, skill: int, points: int, iq: int, seconds: int, fp: int
) -> None:
    from wayfarer.engine.simulation.magic.spell_state import latest as spell_states

    cid, play, facts, _ = await fixture(tmp_path, backend, purify_points=points, iq=iq)
    await observe(play, cid, facts)
    await channel(play, cid, plan())
    command = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="purify-water",
        cast_id="partial",
        channel_id="flow",
    )
    await WaterService(play).execute(cid, command, principal_id="alice")
    current = play._load(await play.store.read(cid))
    effect = spell_states(current.resources)["partial"]
    assert (effect.skill, effect.started_at, effect.ready_at) == (skill, 0, seconds)
    for n in range(seconds):
        if n == seconds - 1:
            before = await play.store.read(cid)
            play.rng = RecordedDice(())
            with pytest.raises(ConflictError, match="deadline"):
                await WaterService(play).execute(
                    cid,
                    command.model_copy(
                        update={
                            "id": "too-early",
                            "kind": "complete",
                            "expected_revision": await revision(play, cid),
                        }
                    ),
                    principal_id="alice",
                )
            assert await play.store.read(cid) == before
        await play.execute(
            cid,
            Wait(
                id=f"tick:{n}", actor_id="a", expected_revision=await revision(play, cid), ticks=1
            ),
            principal_id="a",
        )
        if n + 1 < seconds:
            await WaterService(play).execute(
                cid,
                command.model_copy(
                    update={
                        "id": f"concentrate:{n}",
                        "kind": "concentrate",
                        "expected_revision": await revision(play, cid),
                    }
                ),
                principal_id="alice",
            )
    await fact_change(play, cid, "ring", "opening_mm", "30")
    changed = await play.store.read(cid)
    play.rng = RecordedDice(())
    with pytest.raises(ValidationError, match="stale"):
        await WaterService(play).execute(
            cid,
            command.model_copy(
                update={
                    "id": "changed-physical-setup",
                    "kind": "complete",
                    "expected_revision": await revision(play, cid),
                }
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == changed
    await fact_change(play, cid, "ring", "opening_mm", "20")
    play.rng = RecordedDice((3, 3, 3) if skill == 20 else (1, 2, 3))
    completion = command.model_copy(
        update={
            "id": "complete",
            "kind": "complete",
            "expected_revision": await revision(play, cid),
        }
    )
    result = await WaterService(play).execute(cid, completion, principal_id="alice")
    assert isinstance(result, SpellResult) and result.outcome == "active"
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["chest"].gallons == 1
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == fp


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_legacy_absent_flow_keeps_high_skill_casting_time_and_seeded_reexecution(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.spell_state import latest as spell_states

    cid, play, _, genesis = await fixture(tmp_path, backend, purify_points=28)
    legacy = plan(3).model_copy(update={"parcel_flow_id": None, "purify_entire_source": True})
    assert "parcel_flow_id" not in legacy.model_dump()
    await channel(play, cid, legacy)
    completion = await cast(play, cid, seconds=8)
    before = play._load(await play.store.read(cid))
    assert (
        spell_states(before.resources)["partial"].skill,
        spell_states(before.resources)["partial"].ready_at,
    ) == (20, 8)
    result = await WaterService(play).execute(cid, completion, principal_id="alice")
    assert result.outcome == "active"
    final = await play.store.read(cid)
    replayed, checks = await verify_commands(
        genesis,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(genesis).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "legacy-high-skill-water-reexecuted"),
    )
    assert checks and all(c.folded and c.reexecuted for c in checks)
    assert canonical_campaign(replayed) == canonical_campaign(final)


def canonical_campaign(value: Campaign) -> str:
    # The existing zero-FP projection can reorder optional injury JSON keys.
    # Compare the full document; signed fold/reexecution checks remain required.
    return json.dumps(
        {**value, "play_json": json.loads(value["play_json"])},
        sort_keys=True,
        separators=(",", ":"),
    )
