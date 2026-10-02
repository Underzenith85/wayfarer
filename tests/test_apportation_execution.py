"""B251 actual canonical object/subject motion through the private host."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, seed_play
from test_actions import campaign, world
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    DefinitionKind,
    ImplementationStatus,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.movement import package
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, Wait
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.magic.apportation_state import (
    ApportationChannel,
    ApportationRoute,
    CastApportation,
    DeclareApportationChannel,
    DeclareApportationRoute,
    MoveApportation,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import PROFILE
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import EquipmentSpec, Item, Owner, Pool, ResourceState
from wayfarer.orchestration.apportation import ApportationService
from wayfarer.orchestration.play import PlayService


async def prepare(
    path: Path,
    backend: str = "sqlite",
    *,
    weight: int = 1000,
    living: bool = False,
    seeded: bool = False,
) -> tuple[str, PlayService]:
    spells = package()
    crate = RuleDefinition(
        "equipment:crate",
        DefinitionKind.EQUIPMENT,
        "Test crate",
        "sjg:basic-set-characters-4e-2004",
        0,
        ImplementationStatus.IMPLEMENTED,
    )
    base = profile_package(PROFILE, *spells.definitions, crate)
    base = replace(
        base, sources=tuple({s.id: s for s in (*base.sources, *spells.sources)}.values())
    )
    compiled = profile_compiler(PROFILE, package=base)
    compiled = CharacterCompiler(
        RulesCatalog((base,)),
        compiled.rules,
        replace(
            compiled.policy,
            point_budget=1000,
            allow_supernatural=True,
            allowed_equipment=frozenset({"equipment:crate"}),
        ),
        statistics_profile=PROFILE,
    )
    authored = world()
    resources = ResourceEngine(
        authored,
        RulesCatalog((base,)),
        compiled.rules,
        compiled.policy,
        (EquipmentSpec(definition_id="equipment:crate", unit_weight=weight, slot="hand"),),
    )
    engine = ActionEngine(
        PowerReviewer(compiled, PowerPolicy(id="apportation", version=1), frozenset({"gm"})),
        resources,
        ActionRules(id="apportation", version=1, maximum_wait=10000),
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=1),
        Purchase(definition_id="spell:apportation", amount=4),
    )
    await seed_play(
        play,
        initial,
        authored,
        ResourceState(
            owners=tuple(Owner(actor_id=a, capacity=1000000) for a in ("a", "b")),
            pools=tuple(
                Pool(id="hp:" + a, current=10, maximum=10, injury=InjuryStatus(profile_id=PROFILE))
                for a in ("a", "b")
            ),
            items=(
                Item(
                    id="chest",
                    definition_id="equipment:crate",
                    owner_id="a",
                    world_ground_location_id="dock",
                ),
            ),
        ),
        (
            ActorSetup(actor_id="a", proposal=CharacterProposal(draft=draft)),
            ActorSetup(actor_id="b", proposal=CharacterProposal(draft=gurps_draft())),
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a",)),
            CampaignMember(principal_id="bob", role="player", actor_ids=("b",)),
            CampaignMember(principal_id="gm", role="gm"),
        ),
    )
    cid = initial["id"]
    if seeded:
        play.rng = secrets
    service = ApportationService(play)
    await service.execute(
        cid,
        DeclareApportationChannel(
            id="channel",
            actor_id="gm",
            expected_revision=0,
            channel=ApportationChannel(
                id="apportation",
                actor_id="a",
                target_id="b" if living else "chest",
                location_id="dock",
                body_weight_millipounds=weight if living else None,
            ),
        ),
        principal_id="gm",
    )
    await service.execute(
        cid,
        DeclareApportationRoute(
            id="route",
            actor_id="gm",
            expected_revision=1,
            route=ApportationRoute(
                id="out", source_id="dock", destination_id="alley", distance_yards=1
            ),
        ),
        principal_id="gm",
    )
    return cid, play


async def cast(cid: str, play: PlayService, *, dice: tuple[int, ...] = (3, 3, 3)) -> None:
    service = ApportationService(play)
    await service.execute(
        cid,
        CastApportation(
            id="start",
            actor_id="a",
            expected_revision=2,
            operation="start",
            channel_id="apportation",
            cast_id="cast",
        ),
        principal_id="alice",
    )
    await play.execute(
        cid, Wait(id="cast-time", actor_id="a", expected_revision=3, ticks=1), principal_id="a"
    )
    play.rng = RecordedDice(dice)
    await service.execute(
        cid,
        CastApportation(
            id="complete",
            actor_id="a",
            expected_revision=4,
            operation="complete",
            channel_id="apportation",
            cast_id="cast",
        ),
        principal_id="alice",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_actual_object_moves_at_move_one_cost_and_exact_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, weight=10001)
    await cast(cid, play)
    before = play._load(await play.store.read(cid))
    assert latest(before.resources)["cast"].cost == 3
    assert latest(before.resources)["cast"].maintenance == 3
    assert next(p.current for p in before.resources.pools if p.id == "fp:a") == 7
    assert next(e.location_id for e in before.world.entities if e.id == "chest") == "dock"
    service = ApportationService(play)
    move = MoveApportation(
        id="move",
        actor_id="a",
        expected_revision=5,
        channel_id="apportation",
        cast_id="cast",
        route_id="out",
    )
    result = await service.execute(cid, move, principal_id="alice")
    after = await play.store.read(cid)
    state = play._load(after)
    assert state.resources.game_time == 2
    assert next(e.location_id for e in state.world.entities if e.id == "chest") == "alley"
    assert state.resources.items[0].world_ground_location_id == "alley"
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    restarted = build_play(
        tmp_path, play.engine, backend=backend, store=play.store, rng=RecordedDice(())
    )
    assert await ApportationService(restarted).execute(cid, move, principal_id="alice") == result
    assert await play.store.read(cid) == after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("dice", "phase", "location"),
    [
        ((3, 3, 3, 6, 6, 6), "active", "alley"),
        ((3, 3, 3, 1, 1, 1), "ended", "dock"),
        ((5, 5, 5), "ended", "dock"),
    ],
)
async def test_living_will_resistance_failure_and_actual_world_motion(
    tmp_path: Path, backend: str, dice: tuple[int, ...], phase: str, location: str
) -> None:
    from wayfarer.errors import ConflictError

    cid, play = await prepare(tmp_path, backend, weight=200000, living=True)
    await cast(cid, play, dice=dice)
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["cast"].phase == phase
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (
        9 if dice == (5, 5, 5) else 6
    )
    move = MoveApportation(
        id="move",
        actor_id="a",
        expected_revision=5,
        channel_id="apportation",
        cast_id="cast",
        route_id="out",
    )
    if phase == "active":
        await ApportationService(play).execute(cid, move, principal_id="alice")
    else:
        before = await play.store.read(cid)
        with pytest.raises(ConflictError):
            await ApportationService(play).execute(cid, move, principal_id="alice")
        assert await play.store.read(cid) == before
    assert (
        next(
            e.location_id
            for e in play._load(await play.store.read(cid)).world.entities
            if e.id == "b"
        )
        == location
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_authority_stale_revision_expiry_and_cancel_are_atomic(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import AuthorizationError, ConflictError, ValidationError

    cid, play = await prepare(tmp_path, backend)
    service = ApportationService(play)
    start = CastApportation(
        id="start",
        actor_id="a",
        expected_revision=2,
        operation="start",
        channel_id="apportation",
        cast_id="cast",
    )
    before = await play.store.read(cid)
    with pytest.raises((AuthorizationError, ValidationError)):
        await service.execute(cid, start, principal_id="bob")
    assert await play.store.read(cid) == before
    with pytest.raises(ConflictError):
        await service.execute(
            cid, start.model_copy(update={"expected_revision": 1}), principal_id="alice"
        )
    assert await play.store.read(cid) == before
    await cast(cid, play)
    cancel = CastApportation(
        id="cancel",
        actor_id="a",
        expected_revision=5,
        operation="cancel",
        channel_id="apportation",
        cast_id="cast",
    )
    result = await service.execute(cid, cancel, principal_id="alice")
    assert result.energy_spent == 1
    after = await play.store.read(cid)
    assert await service.execute(cid, cancel, principal_id="alice") == result
    assert await play.store.read(cid) == after
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            MoveApportation(
                id="move",
                actor_id="a",
                expected_revision=6,
                channel_id="apportation",
                cast_id="cast",
                route_id="out",
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == after


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_expiry_stops_motion_and_maintenance_pays_original_tier(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import ConflictError

    cid, play = await prepare(tmp_path, backend, weight=10001)
    await cast(cid, play)
    await play.execute(
        cid, Wait(id="duration", actor_id="a", expected_revision=5, ticks=60), principal_id="a"
    )
    before = await play.store.read(cid)
    service = ApportationService(play)
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            MoveApportation(
                id="move",
                actor_id="a",
                expected_revision=6,
                channel_id="apportation",
                cast_id="cast",
                route_id="out",
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before
    result = await service.execute(
        cid,
        CastApportation(
            id="maintain",
            actor_id="a",
            expected_revision=6,
            operation="maintain",
            channel_id="apportation",
            cast_id="cast",
        ),
        principal_id="alice",
    )
    assert result.energy_spent == 3
    state = play._load(await play.store.read(cid))
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 4
    assert latest(state.resources)["cast"].expires_at == 121


@pytest.mark.parametrize("case", ["held", "contained", "weight", "sleep", "unapproved"])
async def test_current_state_rechecked_before_motion(tmp_path: Path, case: str) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.errors import ConflictError, ValidationError

    cid, play = await prepare(tmp_path)
    await cast(cid, play)

    def mutate(state: PlayState) -> PlayState:
        if case == "unapproved":
            return state.model_copy(
                update={
                    "actors": tuple(
                        a.model_copy(update={"approval": None}) if a.actor_id == "a" else a
                        for a in state.actors
                    )
                }
            )
        if case == "sleep":
            return state.model_copy(
                update={
                    "actors": tuple(
                        a.model_copy(update={"conditions": ("unconscious",)})
                        if a.actor_id == "a"
                        else a
                        for a in state.actors
                    )
                }
            )
        item = state.resources.items[0]
        updates: dict[str, object] = (
            {"quantity": 2}
            if case == "weight"
            else {"equipped": True, "ready": True, "world_ground_location_id": None}
            if case == "held"
            else {"container_id": "other"}
        )
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"items": (item.model_copy(update=updates),)}
                )
            }
        )

    # The contained case uses a pure checkpoint so an invalid container is never persisted.
    if case == "contained":
        from wayfarer.engine.simulation.magic.apportation_host import apply_host

        state = mutate(play._load(await play.store.read(cid)))
        with pytest.raises(ValidationError):
            apply_host(
                play.rules_context,
                state,
                MoveApportation(
                    id="move",
                    actor_id="a",
                    expected_revision=5,
                    channel_id="apportation",
                    cast_id="cast",
                    route_id="out",
                ),
            )
        return
    await change(cid, play, "change", mutate)
    before = await play.store.read(cid)
    with pytest.raises((ConflictError, ValidationError)):
        await ApportationService(play).execute(
            cid,
            MoveApportation(
                id="move",
                actor_id="a",
                expected_revision=6,
                channel_id="apportation",
                cast_id="cast",
                route_id="out",
            ),
            principal_id="alice",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_player_cannot_author_mass_route_or_replace_channel(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import AuthorizationError, ConflictError, ValidationError

    cid, play = await prepare(tmp_path, backend)
    service = ApportationService(play)
    before = await play.store.read(cid)
    route = DeclareApportationRoute(
        id="new-route",
        actor_id="gm",
        expected_revision=2,
        route=ApportationRoute(
            id="back", source_id="alley", destination_id="dock", distance_yards=1
        ),
    )
    with pytest.raises((AuthorizationError, ValidationError)):
        await service.execute(cid, route, principal_id="alice")
    assert await play.store.read(cid) == before
    with pytest.raises(ConflictError):
        await service.execute(
            cid,
            DeclareApportationChannel(
                id="replace",
                actor_id="gm",
                expected_revision=2,
                channel=ApportationChannel(
                    id="apportation", actor_id="a", target_id="chest", location_id="dock"
                ),
            ),
            principal_id="gm",
        )
    assert await play.store.read(cid) == before


async def test_pending_cancel_survives_target_custody_and_source_approval_loss(
    tmp_path: Path,
) -> None:
    from test_power_maintenance_lifecycle import change

    from wayfarer.engine.simulation.actions import PlayState

    cid, play = await prepare(tmp_path)
    service = ApportationService(play)
    await service.execute(
        cid,
        CastApportation(
            id="start",
            actor_id="a",
            expected_revision=2,
            operation="start",
            channel_id="apportation",
            cast_id="cast",
        ),
        principal_id="alice",
    )

    def changed(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "actors": tuple(
                    a.model_copy(update={"approval": None}) if a.actor_id == "a" else a
                    for a in state.actors
                ),
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(
                                update={
                                    "equipped": True,
                                    "ready": True,
                                    "world_ground_location_id": None,
                                }
                            )
                            for i in state.resources.items
                        )
                    }
                ),
            }
        )

    await change(cid, play, "change", changed)
    result = await service.execute(
        cid,
        CastApportation(
            id="cancel",
            actor_id="a",
            expected_revision=4,
            operation="cancel",
            channel_id="apportation",
            cast_id="cast",
        ),
        principal_id="alice",
    )
    assert result.outcome == "cancelled" and result.energy_spent == 0
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["cast"].phase == "ended"
    assert state.resources.game_time == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_seeded_reexecution_preserves_world_consequences(
    tmp_path: Path, backend: str
) -> None:
    from support.runtime import played

    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play = await prepare(tmp_path, backend, seeded=True)
    initial = (await play.store.history(cid))[0].state_after
    service = ApportationService(play)
    await service.execute(
        cid,
        CastApportation(
            id="start",
            actor_id="a",
            expected_revision=2,
            operation="start",
            channel_id="apportation",
            cast_id="cast",
        ),
        principal_id="alice",
    )
    await play.execute(
        cid, Wait(id="cast-time", actor_id="a", expected_revision=3, ticks=1), principal_id="a"
    )
    result = await service.execute(
        cid,
        CastApportation(
            id="complete",
            actor_id="a",
            expected_revision=4,
            operation="complete",
            channel_id="apportation",
            cast_id="cast",
        ),
        principal_id="alice",
    )
    if result.outcome == "active":
        await service.execute(
            cid,
            MoveApportation(
                id="move",
                actor_id="a",
                expected_revision=5,
                channel_id="apportation",
                cast_id="cast",
                route_id="out",
            ),
            principal_id="alice",
        )
    final, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)


@pytest.mark.parametrize(
    "case",
    [
        "object-mass",
        "living-no-mass",
        "location-target",
        "unmodeled-object",
        "unapproved-caster",
        "unknown-location",
        "unknown-route",
    ],
)
async def test_invalid_trusted_construction_has_no_state_change(tmp_path: Path, case: str) -> None:
    from wayfarer.errors import ValidationError

    cid, play = await prepare(tmp_path)
    before = await play.store.read(cid)
    value = ApportationChannel(id="invalid", actor_id="a", target_id="chest", location_id="dock")
    if case == "object-mass":
        value = value.model_copy(update={"body_weight_millipounds": 1})
    elif case == "living-no-mass":
        value = value.model_copy(update={"target_id": "b"})
    elif case == "location-target":
        value = value.model_copy(update={"target_id": "dock"})
    elif case == "unmodeled-object":
        value = value.model_copy(update={"target_id": "hidden"})
    elif case == "unapproved-caster":
        value = value.model_copy(update={"actor_id": "nobody"})
    elif case == "unknown-location":
        value = value.model_copy(update={"location_id": "unknown"})
    command = DeclareApportationChannel(
        id="invalid", actor_id="gm", expected_revision=2, channel=value
    )
    with pytest.raises(ValidationError):
        if case == "unknown-route":
            await ApportationService(play).execute(
                cid,
                DeclareApportationRoute(
                    id="invalid",
                    actor_id="gm",
                    expected_revision=2,
                    route=ApportationRoute(
                        id="invalid", source_id="dock", destination_id="far", distance_yards=1
                    ),
                ),
                principal_id="gm",
            )
        else:
            await ApportationService(play).execute(cid, command, principal_id="gm")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("kind", ["alternate-form", "morph"])
async def test_transformed_living_body_without_size_delta_rejected(
    tmp_path: Path, backend: str, kind: str
) -> None:
    from wayfarer.engine.simulation.campaign.transformations import (
        TransformationRecord,
        TransformationState,
    )
    from wayfarer.engine.simulation.magic.apportation_host import apply_host
    from wayfarer.errors import ValidationError

    cid, play = await prepare(tmp_path, backend, living=True, weight=200000)
    await cast(cid, play, dice=(3, 3, 3, 6, 6, 6))
    before = await play.store.read(cid)
    state = play._load(before)
    subject = next(a for a in state.actors if a.actor_id == "b")
    assert subject.proposal is not None
    record = TransformationRecord.model_validate(
        {
            "id": "changed-body",
            "proposal_id": "0" * 64,
            "rule_id": "form",
            "actor_id": "b",
            "kind": kind,
            "status": "active",
            "source_ref": "B294",
            "source_proposal": subject.proposal,
            "target_proposal": subject.proposal,
            "source_approval": subject.approval,
            "target_approval": subject.approval,
            "source_build_revision": "native",
            "target_build_revision": "changed",
            "source_body_id": "b",
            "target_body_id": "b:changed",
            "trait_routes": (),
            "attachment_routes": (),
            "proposed_by": "gm",
            "approved_by": "gm",
            "proposed_at": 0,
            "approved_at": 0,
            "point_value_delta": 0,
        }
    )
    changed = state.model_copy(update={"transformations": TransformationState(records=(record,))})
    with pytest.raises(ValidationError, match="Transformed living mass"):
        apply_host(
            play.rules_context,
            changed,
            MoveApportation(
                id="move",
                actor_id="a",
                expected_revision=5,
                channel_id="apportation",
                cast_id="cast",
                route_id="out",
            ),
        )
    cancelled, receipt = apply_host(
        play.rules_context,
        changed,
        CastApportation(
            id="cancel",
            actor_id="a",
            expected_revision=5,
            operation="cancel",
            channel_id="apportation",
            cast_id="cast",
        ),
    )
    assert latest(cancelled.resources)["cast"].phase == "ended"
    assert receipt.energy_spent == 1
    reverted = changed.model_copy(
        update={
            "transformations": TransformationState(
                records=(record.model_copy(update={"status": "reverted"}),)
            )
        }
    )
    moved, receipt = apply_host(
        play.rules_context,
        reverted,
        MoveApportation(
            id="move",
            actor_id="a",
            expected_revision=5,
            channel_id="apportation",
            cast_id="cast",
            route_id="out",
        ),
    )
    assert receipt.outcome == "moved"
    assert next(e.location_id for e in moved.world.entities if e.id == "b") == "alley"
    assert await play.store.read(cid) == before
