"""B103-104/B421/B442-443 private persisted observation and current-clock contracts.

The restored fixture is an already-resolved canonical attack checkpoint. Delivery
admission itself belongs to the composed host suite; these cases execute the real
GM service, ordinary Wait, both command stores, retry locks and seed replay.
"""

import asyncio
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played, seed_campaign
from test_actions import campaign
from test_attack_defense_traits import channel, command, world
from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm
from test_statistics import gurps_draft, profile_package
from trait_support import options

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import (
    PROTOTYPE_SOURCE,
    CampaignPolicy,
    CampaignRules,
    DefinitionKind,
    ImplementationStatus,
    PackagePin,
    RuleDefinition,
    RulesCatalog,
)
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.attack_defense import PROFILE, RUNTIME_HOOKS, package
from wayfarer.engine.rules.traits.modifiers import (
    EnhancementParameters,
    LimitationParameters,
    ModifierSelection,
)
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.traits.physical import PHYSICAL_HOOKS
from wayfarer.engine.rules.types.hazard import blocked_hp
from wayfarer.engine.rules.types.injury import InjuryStatus
from wayfarer.engine.rules.types.location import HumanBody, HumanLocation
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.access import CampaignMember
from wayfarer.engine.simulation.health.cyclic_context import resolver
from wayfarer.engine.simulation.health.cyclic_host_state import (
    BeginCyclicProcedure,
    CompleteCyclicProcedure,
    CyclicPolicy,
    CyclicPrecaution,
    CyclicProcedure,
    ObserveCyclicExposure,
    ObserveCyclicStop,
    bind_occurrence,
    history,
)
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.resources import Advance, EquipmentSpec, Item, Owner, ResourceState
from wayfarer.engine.simulation.traits.attack_defense import apply_trait_attack
from wayfarer.engine.world import Entity, EntityKind
from wayfarer.errors import ConflictError, NotFoundError, ValidationError
from wayfarer.orchestration.cyclic import CyclicService
from wayfarer.orchestration.play import PlayService


def source_purchase(
    *, contagious: bool = False, interval: int = 10, resistible: bool = False
) -> Purchase:
    return Purchase(
        definition_id="advantage:innate-attack",
        trait=options(**{"damage-type": "tox" if contagious else "burn"}).model_copy(
            update={
                "attack_modifiers": (
                    ModifierSelection(
                        definition_id="modifier:enhancement:cyclic",
                        option="host-cyclic",
                        parameters=EnhancementParameters(
                            interval_seconds=interval,
                            cycles=3,
                            stop_condition="wash",
                            contagious="mild" if contagious else "none",
                            damage_kind="toxic" if contagious else "burning",
                        ),
                    ),
                )
                + (
                    (
                        ModifierSelection(
                            definition_id="modifier:limitation:resistible",
                            option="ht+0",
                            limitation=LimitationParameters(resistance_modifier=0),
                        ),
                    )
                    if resistible
                    else ()
                )
            }
        ),
    )


async def prepare(
    path: Path,
    backend: str = "sqlite",
    *,
    policy: CyclicPolicy | None = None,
    contagious: bool = False,
    interval: int = 10,
    incubation: int = 86400,
    resistible: bool = False,
    location: HumanLocation | None = None,
    target_status: InjuryStatus | None = None,
    restored_at: int = 0,
) -> tuple[str, PlayService, str]:
    base = profile_package(PROFILE)
    physical = candidate_package()
    rules_package = replace(
        base,
        id="package:cyclic-host",
        sources=base.sources + physical.sources,
        definitions=base.definitions
        + tuple(d for d in physical.definitions if d.id in {"trait:fit", "trait:very-fit"})
        + package().definitions
        + (
            RuleDefinition(
                "medicine",
                DefinitionKind.EQUIPMENT,
                "Medicine",
                PROTOTYPE_SOURCE.id,
                0,
                ImplementationStatus.IMPLEMENTED,
            ),
        ),
    )
    policy_rules = CampaignPolicy(
        "policy:cyclic-host",
        1,
        10000,
        10000,
        20,
        20,
        frozenset(s.id for s in rules_package.sources),
        allow_supernatural=True,
        allowed_equipment=frozenset({"medicine"}),
    )
    rules = CampaignRules(
        rules_package.edition,
        (
            PackagePin(
                rules_package.id,
                rules_package.version,
                rules_package.digest,
            ),
        ),
        policy_rules.id,
        policy_rules.version,
    )
    catalog = RulesCatalog((rules_package,))
    compiler = CharacterCompiler(
        catalog,
        rules,
        policy_rules,
        statistics_profile=PROFILE,
        trait_runtime_hooks=RUNTIME_HOOKS | PHYSICAL_HOOKS,
    )
    scene = replace(
        world(),
        entities=world().entities
        + (
            Entity("c", EntityKind.ACTOR, "Secondary", "room"),
            Entity("elsewhere", EntityKind.LOCATION, "Elsewhere"),
        ),
    )
    resource_engine = ResourceEngine(
        scene,
        catalog,
        rules,
        policy_rules,
        (EquipmentSpec(definition_id="medicine", unit_weight=1, slot="hand"),),
    )
    engine = ActionEngine(
        PowerReviewer(
            compiler,
            PowerPolicy(id="cyclic-host", version=1, automatic_approval=True),
            frozenset({"gm", "unseated"}),
        ),
        resource_engine,
        ActionRules(id="cyclic-host", version=1, maximum_wait=10000),
    )
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        scene,
        ResourceState(
            owners=tuple(Owner(actor_id=a, capacity=100) for a in ("a", "b", "c")),
            items=(Item(id="dose", definition_id="medicine", owner_id="b", quantity=2),),
        ),
        tuple(
            ActorSetup(
                actor_id=actor,
                proposal=CharacterProposal(
                    draft=gurps_draft(
                        *(
                            (
                                source_purchase(
                                    contagious=contagious, interval=interval, resistible=resistible
                                ),
                            )
                            if actor == "a"
                            else ()
                        )
                    )
                ),
                body=HumanBody(anatomy="human"),
            )
            for actor in ("a", "b", "c")
        ),
        members=(
            CampaignMember(principal_id="alice", role="player", actor_ids=("a", "b", "c")),
            CampaignMember(principal_id="gm", role="gm"),
            CampaignMember(principal_id="untrusted", role="gm"),
        ),
    )
    resources, _ = apply_trait_attack(
        state.resources,
        scene,
        command(),
        build(play.rules_context, state, "a"),
        build(play.rules_context, state, "b"),
        compiler.definitions,
        (
            channel(
                basic_damage=2,
                damage_type="tox" if contagious else "burn",
                contagion_vector="respiratory" if contagious else None,
                incubation_seconds=incubation,
            ),
        ),
        target_ht=10,
        rng=RecordedDice((6, 6, 6) if resistible else ()),
        authorized_actor_id="a",
        system=True,
    )
    occurrence = resources.cyclic_attacks[0]
    resources = bind_occurrence(
        resources,
        occurrence.id,
        source_id=occurrence.attack_id,
        source_revision=build(play.rules_context, state, "a").revision,
        policy=policy or CyclicPolicy(condition="wash"),
        location=location,
    )
    resources = resources.model_copy(
        update={
            "game_time": restored_at,
            "pools": tuple(
                pool.model_copy(update={"injury": target_status})
                if pool.id == "hp:b" and target_status is not None
                else pool
                for pool in resources.pools
            ),
        }
    )
    state = state.model_copy(update={"resources": resources, "revision": resources.revision})
    play.commit(initial, state)
    await seed_campaign(play.store, initial)
    return initial["id"], play, occurrence.id


async def current(play: PlayService, cid: str) -> PlayState:
    return play._load(await play.store.read(cid))


def hp(state: PlayState, actor: str = "b") -> int:
    return next(p.current for p in state.resources.pools if p.id == "hp:" + actor)


def stop_command(occurrence: str, revision: int = 1) -> ObserveCyclicStop:
    return ObserveCyclicStop(
        id="observed-stop",
        actor_id="b",
        expected_revision=revision,
        occurrence_id=occurrence,
        location_id="room",
        reason="The source-approved wash is visibly complete",
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_persisted_stop_atomic_debt_release_race_restart_retry(
    tmp_path: Path, backend: str
) -> None:
    cid, play, occurrence = await prepare(tmp_path, backend)
    waiting = await play.execute(
        cid,
        Wait(id="nine-seconds", actor_id="b", expected_revision=1, ticks=9),
        principal_id="b",
    )
    assert waiting.status == "committed"
    before = await current(play, cid)
    assert hp(before) == 8 and blocked_hp(before.resources.illnesses, "b", "natural") == 2
    cmd = stop_command(occurrence, 2)
    other = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    results = await asyncio.gather(
        CyclicService(play).execute(cid, cmd, principal_id="gm"),
        CyclicService(other).execute(cid, cmd, principal_id="gm"),
    )
    assert results[0] == results[1] and results[0].at == 9
    state = await current(play, cid)
    assert state.revision == state.resources.revision == 3 and hp(state) == 8
    assert not state.resources.cyclic_attacks[0].active
    assert blocked_hp(state.resources.illnesses, "b", "natural") == 0
    assert len(await played(play.store, cid)) == 2
    assert len(history(state.resources)) == 1
    await play.execute(
        cid, Wait(id="later", actor_id="b", expected_revision=3, ticks=100), principal_id="b"
    )
    restart = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await restart.store.read(cid) == await restart.store.replay(cid)
    assert await CyclicService(restart).execute(cid, cmd, principal_id="gm") == results[0]
    assert hp(await current(restart, cid)) == 8
    with pytest.raises(ConflictError):
        await CyclicService(restart).execute(
            cid, cmd.model_copy(update={"actor_id": "c"}), principal_id="gm"
        )
    with pytest.raises(ValidationError, match="active"):
        await CyclicService(restart).execute(
            cid, cmd.model_copy(update={"id": "again", "expected_revision": 4}), principal_id="gm"
        )
    rendered = str(await build_runtime(play).read(cid, principal_id="alice"))
    assert "source_revision" not in rendered and "observed-stop" not in rendered


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_stop_authority_staleness_and_closed_payload(tmp_path: Path, backend: str) -> None:
    cid, play, occurrence = await prepare(tmp_path, backend)
    original = await play.store.read(cid)
    cmd = stop_command(occurrence)
    for principal in ("alice", "outsider", "untrusted", "unseated"):
        with pytest.raises((ValidationError, NotFoundError)):
            await CyclicService(play).execute(cid, cmd, principal_id=principal)
    for change in (
        {"expected_revision": 0},
        {"occurrence_id": "other"},
        {"actor_id": "c"},
        {"location_id": "elsewhere"},
    ):
        with pytest.raises((ValidationError, ConflictError)):
            await CyclicService(play).execute(cid, cmd.model_copy(update=change), principal_id="gm")
    for field in ("condition", "ht", "damage", "success"):
        with pytest.raises(ValidationError, match="Invalid Cyclic"):
            await CyclicService(play).execute(
                cid, {**cmd.model_dump(), field: 10}, principal_id="gm"
            )
    assert await play.store.read(cid) == original


@pytest.mark.parametrize("retry", [False, True])
async def test_revoked_seat_is_rechecked_before_commit_and_exact_retry(
    tmp_path: Path, retry: bool
) -> None:
    cid, original, occurrence = await prepare(tmp_path)
    cmd = stop_command(occurrence)
    if retry:
        await CyclicService(original).execute(cid, cmd, principal_id="gm")
    revision = 2 if retry else 1
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    play = build_play(tmp_path, original.engine, store=store, rng=RecordedDice(()))
    store.revoke = lambda: revoke_gm(original, cid, revision)
    if not retry:
        cmd = cmd.model_copy(update={"expected_revision": revision + 1})
    with pytest.raises(ValidationError, match="director authority"):
        await CyclicService(play).execute(cid, cmd, principal_id="gm")
    assert len(history((await current(original, cid)).resources)) == int(retry)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("dice,stopped", [((2, 3, 4), True), ((6, 6, 6), False)])
async def test_procedure_uses_actual_elapsed_clock_current_check_and_consumable(
    tmp_path: Path,
    backend: str,
    dice: tuple[int, ...],
    stopped: bool,
) -> None:
    cid, play, occurrence = await prepare(
        tmp_path,
        backend,
        policy=CyclicPolicy(
            condition="wash",
            procedure=CyclicProcedure(seconds=2, check="dx", consume_definition_id="medicine"),
        ),
    )
    service = CyclicService(play)
    with pytest.raises(ValidationError, match="procedure"):
        await service.execute(cid, stop_command(occurrence), principal_id="gm")
    begin = BeginCyclicProcedure(
        id="start-wash",
        actor_id="b",
        expected_revision=1,
        occurrence_id=occurrence,
        performer_id="b",
        location_id="room",
        item_id="dose",
        reason="Started the approved wash procedure",
    )
    await service.execute(cid, begin, principal_id="gm")
    complete = CompleteCyclicProcedure(
        id="finish-wash",
        actor_id="b",
        expected_revision=2,
        occurrence_id=occurrence,
        procedure_id=begin.id,
        location_id="room",
        uninterrupted=True,
        reason="Observed this performer continuously washing for the required interval",
    )
    with pytest.raises(ConflictError, match="elapsed"):
        await service.execute(cid, complete, principal_id="gm")
    await play.execute(
        cid,
        Wait(id="procedure-time", actor_id="b", expected_revision=2, ticks=2),
        principal_id="b",
    )
    play.rng = RecordedDice(dice)
    complete = complete.model_copy(update={"expected_revision": 3})
    result = await service.execute(cid, complete, principal_id="gm")
    assert result.kind == ("stopped" if stopped else "procedure-failed")
    assert result.check is not None and result.check.base_target == 10 and result.check.dice == dice
    state = await current(play, cid)
    assert state.resources.items[0].quantity == 1 and state.resources.game_time == 2
    assert state.resources.cyclic_attacks[0].active != stopped
    assert blocked_hp(state.resources.illnesses, "b", "natural") == (0 if stopped else 2)
    restart = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await CyclicService(restart).execute(cid, complete, principal_id="gm") == result
    if not stopped:
        with pytest.raises(ConflictError, match="consumed"):
            await CyclicService(restart).execute(
                cid,
                complete.model_copy(update={"id": "again", "expected_revision": 4}),
                principal_id="gm",
            )


def expose_command(occurrence: str, revision: int = 1) -> ObserveCyclicExposure:
    return ObserveCyclicExposure(
        id="contact",
        actor_id="c",
        expected_revision=revision,
        source_occurrence_id=occurrence,
        location_id="room",
        contact="close-conversation",
        route="shared-air",
        reason="Observed close conversation in the carrier's shared air",
    )


def advance(play: PlayService, state: PlayState, to: int, dice: tuple[int, ...]) -> PlayState:
    resources = play.engine.resources.for_world(state.world).apply(
        state.resources,
        Advance(
            id="clock:" + str(to), actor_id="a", expected_revision=state.resources.revision, to=to
        ),
        system=True,
        rng=RecordedDice(dice),
        cyclic_context=resolver(play.rules_context, state),
    )
    return state.model_copy(update={"revision": resources.revision, "resources": resources})


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_contact_observation_current_ht_exposure_based_incubation_and_original_cure(
    tmp_path: Path,
    backend: str,
) -> None:
    cid, play, occurrence = await prepare(tmp_path, backend, contagious=True, interval=86400)
    cmd = expose_command(occurrence)
    service = CyclicService(play)
    result = await service.execute(cid, cmd, principal_id="gm")
    await service.execute(cid, stop_command(occurrence, 2), principal_id="gm")
    state = await current(play, cid)
    assert len(state.resources.cyclic_exposures) == 1
    original = await play.store.read(cid)
    restart = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await CyclicService(restart).execute(cid, cmd, principal_id="gm") == result
    assert await restart.store.read(cid) == original
    state = advance(play, state, 86400, (6, 6, 6, 2))
    exposure = state.resources.cyclic_exposures[0]
    assert exposure.check is not None and exposure.check.base_target == 12
    infection = next(a for a in state.resources.cyclic_attacks if a.actor_id == "c")
    assert infection.cycle == 1 and infection.remaining == 2 and infection.due == 172800
    assert hp(state, "c") == 8 and hp(state) == 8
    assert not next(a for a in state.resources.cyclic_attacks if a.actor_id == "b").active


async def test_exposure_requires_contact_route_source_precaution_and_explicit_short_delay_choice(
    tmp_path: Path,
) -> None:
    policy = CyclicPolicy(
        condition="wash",
        precautions=(
            CyclicPrecaution(
                id="mask",
                description="GM-approved correctly fitted and understood mask",
                bonus=2,
            ),
        ),
    )
    cid, play, occurrence = await prepare(
        tmp_path, contagious=True, interval=86400, incubation=3600, policy=policy
    )
    service = CyclicService(play)
    cmd = expose_command(occurrence)
    original = await play.store.read(cid)
    with pytest.raises(ValidationError, match="Incubation precedes"):
        await service.execute(cid, cmd, principal_id="gm")
    explicit = cmd.model_copy(update={"early_incubation_resolution": "defer-to-daily-check"})
    for changes in (
        {"route": "skin-contact"},
        {"precaution_id": "invented"},
        {"precaution_id": "mask"},
        {"actor_id": "b"},
        {"contact": "raw-flesh"},
        {"location_id": "elsewhere"},
    ):
        with pytest.raises(ValidationError):
            await service.execute(cid, explicit.model_copy(update=changes), principal_id="gm")
    assert await play.store.read(cid) == original
    result = await service.execute(
        cid,
        explicit.model_copy(update={"precaution_id": "mask", "precaution_understood": True}),
        principal_id="gm",
    )
    assert result.early_incubation_resolution == "defer-to-daily-check"
    state = advance(play, await current(play, cid), 86399, ())
    assert hp(state, "c") == 10
    state = advance(play, state, 86400, (6, 6, 6, 2, 2))
    assert hp(state, "c") == 8
    check = state.resources.cyclic_exposures[0].check
    assert check is not None and check.base_target == 14


async def test_bound_clock_rejects_naked_advance_before_any_roll(tmp_path: Path) -> None:
    cid, play, _ = await prepare(tmp_path)
    state = await current(play, cid)
    cmd = Advance(id="naked", actor_id="a", expected_revision=1, to=10)
    for kwargs in (
        {},
        {"rng": RecordedDice(())},
        {"cyclic_context": resolver(play.rules_context, state)},
    ):
        with pytest.raises(ConflictError, match="current approved target context"):
            play.engine.resources.apply(state.resources, cmd, system=True, **kwargs)
    due = advance(play, state, 20, (3, 3))
    assert hp(due) == 2 and due.resources.cyclic_attacks[0].remaining == 0


@pytest.mark.parametrize("resistible", [False, True])
async def test_secondary_first_onset_has_no_second_resistance_then_next_cycle_can_end_it(
    tmp_path: Path,
    resistible: bool,
) -> None:
    cid, play, occurrence = await prepare(
        tmp_path, contagious=True, interval=86400, resistible=resistible
    )
    service = CyclicService(play)
    await service.execute(cid, expose_command(occurrence), principal_id="gm")
    await service.execute(cid, stop_command(occurrence, 2), principal_id="gm")
    state = await current(play, cid)
    assert (state.resources.cyclic_attacks[0].resistance_modifier is not None) == resistible
    # Exposure fails18. The next die1 is first onset damage, not a new HT roll.
    state = advance(play, state, 86400, (6, 6, 6, 1))
    infection = next(a for a in state.resources.cyclic_attacks if a.actor_id == "c")
    assert infection.resistance_modifier == 0
    assert infection.cycle == 1 and infection.remaining == 2 and hp(state, "c") == 9
    state = advance(play, state, 172800, (1, 1, 1))
    infection = next(a for a in state.resources.cyclic_attacks if a.actor_id == "c")
    assert not infection.active and hp(state, "c") == 9
