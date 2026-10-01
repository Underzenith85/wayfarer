"""B237 ritual bands and authoritative capability observations, not narrative guesses."""

import asyncio
import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, build_runtime, played
from test_actions import campaign
from test_lock_spell_persistence import cast, declare, prepare, revision

from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.engine.rules.traits.mundane import candidate_package
from wayfarer.engine.rules.types.location import HumanBody, HumanLocation, LastingInjury
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.magic.lock_bindings import LockChannel
from wayfarer.engine.simulation.magic.lock_host import DeclareLockChannel
from wayfarer.engine.simulation.magic.ritual_state import (
    PREFIX,
    DeclareRitualCapability,
)
from wayfarer.engine.simulation.magic.ritual_state import (
    latest as capability,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.engine.simulation.resources import ResourceEvent
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter
from wayfarer.orchestration.locks import LockService, LockSpellService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spell_rituals import SpellRitualService


class NoRolls:
    calls = 0

    def randbelow(self, upper: int) -> int:
        self.calls += 1
        raise AssertionError("A rejected ritual must not draw randomness")


async def configured(
    path: Path,
    *,
    points: int = 4,
    mana: ManaLevel = "normal",
    backend: str = "sqlite",
    mapped: bool = False,
) -> tuple[str, PlayService]:
    cid, play = await prepare(path, backend, spell_points=points, combat=mapped)
    await declare(play, cid)
    if mapped:
        await CombatService(play).execute(
            cid,
            StartEncounter(
                id="fight",
                actor_id="gm",
                expected_revision=await revision(play, cid),
                encounter_id="fight",
                battlefield_id="dock",
                placements=(
                    Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                    Placement(actor_id="b", position=GridPoint(x=3, y=1)),
                ),
            ),
            principal_id="gm",
        )
    await LockService(play).execute(
        cid,
        DeclareLockChannel(
            id="ritual-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=LockChannel(
                id="ritual",
                actor_id="a",
                target_id="chest",
                location_id="dock",
                spell_id="magelock",
                mana=mana,
                encounter_id="fight" if mapped else None,
                position=(2, 1) if mapped else None,
            ),
        ),
        principal_id="gm",
    )
    return cid, play


async def observation(
    play: PlayService,
    cid: str,
    *,
    speech: bool | None = None,
    gesture: bool | None = None,
    body: bool | None = None,
    identifier: str = "capability",
) -> DeclareRitualCapability:
    command = DeclareRitualCapability(
        id=identifier,
        actor_id="a",
        expected_revision=await revision(play, cid),
        speech_available=speech,
        gesture_available=gesture,
        full_body_free=body,
        reason="The director observes the caster's current physical ritual capability",
    )
    await SpellRitualService(play).execute(cid, command, principal_id="gm")
    return command


async def start_command(play: PlayService, cid: str) -> RuntimeSpellCommand:
    return RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=await revision(play, cid),
        kind="start",
        spell_id="magelock",
        cast_id="ward",
        channel_id="ritual",
    )


async def rejected_without_effects(
    play: PlayService, cid: str, command: RuntimeSpellCommand
) -> None:
    before = await play.store.read(cid)
    dice = NoRolls()
    play.rng = dice
    with pytest.raises(ValidationError, match="B237"):
        await LockSpellService(play).execute(cid, command, principal_id="gm")
    assert dice.calls == 0
    assert await play.store.read(cid) == before == await play.store.replay(cid)


async def physical_fixture(
    play: PlayService,
    cid: str,
    *,
    locations: tuple[HumanLocation, ...] = (),
    restrained: bool = False,
) -> None:
    """A canonical existing injury/restraint, committed independently of spell logic."""
    before = play._load(await play.store.read(cid))
    hp = next(p for p in before.resources.pools if p.id == "hp:a")
    assert hp.injury is not None
    injury = hp.injury.model_copy(
        update={
            "anatomy": "human",
            "lasting_injuries": tuple(
                LastingInjury(
                    id=location,
                    location=location,
                    kind="severed",
                    duration="permanent",
                    inflicted_at=0,
                    injury=6,
                )
                for location in locations
            ),
        }
    )

    def commit(value: Campaign) -> CommandReceipt:
        updated = before.model_copy(
            update={
                "revision": before.revision + 1,
                "actors": tuple(
                    actor.model_copy(
                        update={
                            "body": HumanBody(anatomy="human"),
                            "conditions": ("restrained",) if restrained else (),
                        }
                    )
                    if actor.actor_id == "a"
                    else actor
                    for actor in before.actors
                ),
                "resources": before.resources.model_copy(
                    update={
                        "revision": before.revision + 1,
                        "pools": tuple(
                            p.model_copy(update={"injury": injury}) if p.id == hp.id else p
                            for p in before.resources.pools
                        ),
                    }
                ),
            }
        )
        play.commit(value, updated)
        return CommandReceipt(action="resource", outcome="fixture.physical-capability")

    await play.store.commit_turn(
        cid, "physical", before.revision, "physical", commit, actor_id="gm"
    )


@pytest.mark.parametrize(
    "points,mana,skill,seconds,energy",
    [
        (4, "low", 9, 8, 3),
        (8, "low", 10, 4, 3),
        (4, "normal", 14, 4, 3),
        (8, "normal", 15, 4, 2),
        (24, "normal", 19, 4, 2),
        (28, "normal", 20, 2, 1),
    ],
)
async def test_b237_all_ritual_bands_reach_real_ward(
    tmp_path: Path, points: int, mana: ManaLevel, skill: int, seconds: int, energy: int
) -> None:
    cid, play = await configured(tmp_path, points=points, mana=mana)
    await observation(play, cid, speech=True, gesture=True, body=True)
    result, _ = await cast(play, cid, "magelock", "ward", channel="ritual", dice=(3, 3, 2))
    after = play._load(await play.store.read(cid))
    effect = latest(after.resources)["ward"]
    assert result.outcome == "active" and result.energy_spent == energy
    assert result.checks[0].effective_target == skill
    assert effect.phase == "active" and effect.expires_at == seconds + 21600
    assert after.resources.game_time == seconds
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 10 - energy


@pytest.mark.parametrize("points,mana", [(4, "low"), (8, "low"), (4, "normal")])
@pytest.mark.parametrize("speech,gesture", [(False, True), (True, False), (False, False)])
async def test_b237_below_fifteen_requires_both_speech_and_gesture(
    tmp_path: Path, points: int, mana: ManaLevel, speech: bool, gesture: bool
) -> None:
    cid, play = await configured(tmp_path, points=points, mana=mana)
    await observation(play, cid, speech=speech, gesture=gesture)
    await rejected_without_effects(play, cid, await start_command(play, cid))


@pytest.mark.parametrize("points", [8, 24])
@pytest.mark.parametrize("speech,gesture", [(False, True), (True, False), (False, False)])
async def test_b237_fifteen_through_nineteen_is_speech_or_gesture(
    tmp_path: Path, points: int, speech: bool, gesture: bool
) -> None:
    cid, play = await configured(tmp_path, points=points)
    await physical_fixture(play, cid, restrained=True)
    await observation(play, cid, speech=speech, gesture=gesture, body=False)
    if not speech and not gesture:
        await rejected_without_effects(play, cid, await start_command(play, cid))
    else:
        result, _ = await cast(play, cid, "magelock", "ward", channel="ritual")
        assert result.outcome == "active"
        assert latest(play._load(await play.store.read(cid)).resources)["ward"].phase == "active"


async def test_b237_twenty_needs_neither_speech_gesture_nor_free_limbs(tmp_path: Path) -> None:
    cid, play = await configured(tmp_path, points=28)
    await physical_fixture(
        play, cid, locations=("left-arm", "right-arm", "left-foot", "right-foot"), restrained=True
    )
    await observation(play, cid, speech=False, gesture=False, body=False)
    result, _ = await cast(play, cid, "magelock", "ward", channel="ritual")
    assert result.outcome == "active" and result.energy_spent == 1


@pytest.mark.parametrize("points,distance,speech", [(8, 7, True), (28, 12, False)])
async def test_b237_range_penalty_does_not_change_the_ritual_band(
    tmp_path: Path, points: int, distance: int, speech: bool
) -> None:
    cid, play = await configured(tmp_path, points=points)
    await observation(play, cid, speech=speech, gesture=False)
    await LockService(play).execute(
        cid,
        DeclareLockChannel(
            id="distant-channel",
            actor_id="gm",
            expected_revision=await revision(play, cid),
            channel=LockChannel(
                id="distant",
                actor_id="a",
                target_id="chest",
                location_id="dock",
                spell_id="magelock",
                distance_yards=distance,
            ),
        ),
        principal_id="gm",
    )
    result, _ = await cast(play, cid, "magelock", "ward", channel="distant", dice=(3, 3, 2))
    assert result.outcome == "active" and result.checks[0].effective_target == 8


@pytest.mark.parametrize("trait", ["cannot-speak", "mute"])
@pytest.mark.parametrize(
    "points,gesture,permitted",
    [(4, True, False), (8, True, True), (8, False, False), (28, False, True)],
)
async def test_b237_approved_speech_disadvantages_cannot_be_overridden_by_observation(
    tmp_path: Path, trait: str, points: int, gesture: bool, permitted: bool
) -> None:
    identifier = "trait:disadvantage:" + trait
    package = candidate_package()
    definition = next(d for d in package.definitions if d.id == identifier)
    cid, play = await prepare(
        tmp_path,
        spell_points=points,
        extra_definitions=(definition,),
        extra_purchases=(Purchase(definition_id=identifier),),
        extra_sources=package.sources,
        trait_runtime_hooks=frozenset({"mundane-trait:behavior", "mundane-trait:physiology"}),
    )
    await declare(play, cid)
    compiled = play.rules_context.approved_build(play._load(await play.store.read(cid)), "a")
    assert any(p.definition_id == identifier for p in compiled.purchases)
    await observation(play, cid, speech=True, gesture=gesture)
    command = (await start_command(play, cid)).model_copy(update={"channel_id": "magelock"})
    if not permitted:
        await rejected_without_effects(play, cid, command)
    else:
        result, _ = await cast(play, cid, "magelock", "ward")
        assert result.outcome == "active"


@pytest.mark.parametrize("mapped", [False, True])
@pytest.mark.parametrize("location", ["left-arm", "right-arm", "left-foot", "right-foot"])
async def test_b237_low_mana_full_body_fact_cannot_restore_severed_limbs(
    tmp_path: Path, location: HumanLocation, mapped: bool
) -> None:
    cid, play = await configured(tmp_path, mana="low", mapped=mapped)
    await physical_fixture(play, cid, locations=(location,))
    await observation(play, cid, speech=True, gesture=True, body=True)
    await rejected_without_effects(play, cid, await start_command(play, cid))


@pytest.mark.parametrize("mapped", [False, True])
@pytest.mark.parametrize("skill", [9, 10, 14, 15, 19, 20])
async def test_b237_occupied_hands_are_not_unavailable_gestures(
    tmp_path: Path, mapped: bool, skill: int
) -> None:
    from test_gurps_melee import setup

    from wayfarer.engine.simulation.magic.rituals import require_ordinary_ritual

    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True, start_encounter=mapped)
    state = play._load(await play.store.read(cid))
    actor = next(a for a in state.actors if a.actor_id == "b")
    assert {h for _, h in actor.held_item_hands} == {"left-hand", "right-hand"}
    compiled = play.rules_context.approved_build(state, "b")
    if skill == 9:
        with pytest.raises(ValidationError, match="both hands and both feet"):
            require_ordinary_ritual(play.rules_context, state, "b", compiled, skill)
    else:
        require_ordinary_ritual(play.rules_context, state, "b", compiled, skill)


async def test_b237_capability_loss_mid_cast_preserves_pending_cast_and_energy(
    tmp_path: Path,
) -> None:
    cid, play = await configured(tmp_path)
    start = await start_command(play, cid)
    await LockSpellService(play).execute(cid, start, principal_id="gm")
    await play.execute(
        cid,
        Wait(id="time", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    await observation(play, cid, speech=False, gesture=False)
    await rejected_without_effects(
        play,
        cid,
        start.model_copy(
            update={
                "id": "continue",
                "kind": "concentrate",
                "expected_revision": await revision(play, cid),
            }
        ),
    )
    after = play._load(await play.store.read(cid))
    assert latest(after.resources)["ward"].phase == "casting"
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 10
    result = await LockSpellService(play).execute(
        cid,
        start.model_copy(
            update={
                "id": "cancel",
                "kind": "cancel",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="gm",
    )
    assert result.outcome == "cancelled" and result.energy_spent == 0


async def test_b237_canonical_limb_loss_mid_cast_overrides_old_positive_fact(
    tmp_path: Path,
) -> None:
    cid, play = await configured(tmp_path, mana="low")
    await observation(play, cid, speech=True, gesture=True, body=True)
    start = await start_command(play, cid)
    await LockSpellService(play).execute(cid, start, principal_id="gm")
    await play.execute(
        cid,
        Wait(id="time", actor_id="a", expected_revision=await revision(play, cid), ticks=1),
        principal_id="a",
    )
    await physical_fixture(play, cid, locations=("left-leg",))
    await rejected_without_effects(
        play,
        cid,
        start.model_copy(
            update={
                "id": "continue",
                "kind": "concentrate",
                "expected_revision": await revision(play, cid),
            }
        ),
    )
    resources = play._load(await play.store.read(cid)).resources
    assert next(p.current for p in resources.pools if p.id == "fp:a") == 10
    assert latest(resources)["ward"].concentration_seconds == 1


async def test_b237_final_roll_rechecks_live_capability_without_spending_or_rolling(
    tmp_path: Path,
) -> None:
    cid, play = await configured(tmp_path)
    start = await start_command(play, cid)
    await LockSpellService(play).execute(cid, start, principal_id="gm")
    for second in range(1, 5):
        await play.execute(
            cid,
            Wait(
                id=f"time:{second}",
                actor_id="a",
                expected_revision=await revision(play, cid),
                ticks=1,
            ),
            principal_id="a",
        )
        if second < 4:
            await LockSpellService(play).execute(
                cid,
                start.model_copy(
                    update={
                        "id": f"concentrate:{second}",
                        "kind": "concentrate",
                        "expected_revision": await revision(play, cid),
                    }
                ),
                principal_id="gm",
            )
    await observation(play, cid, speech=False, gesture=False)
    await rejected_without_effects(
        play,
        cid,
        start.model_copy(
            update={
                "id": "complete",
                "kind": "complete",
                "expected_revision": await revision(play, cid),
            }
        ),
    )
    resources = play._load(await play.store.read(cid)).resources
    assert latest(resources)["ward"].phase == "casting"
    assert next(p.current for p in resources.pools if p.id == "fp:a") == 10


async def test_b237_maintenance_and_cancellation_do_not_require_a_new_ritual(
    tmp_path: Path,
) -> None:
    cid, play = await configured(tmp_path)
    _, complete = await cast(play, cid, "magelock", "ward", channel="ritual")
    await observation(play, cid, speech=False, gesture=False, body=False)
    await physical_fixture(play, cid, locations=("left-arm", "right-arm"), restrained=True)
    play.rng = NoRolls()
    for n, ticks in enumerate((10000, 10000, 1600)):
        await play.execute(
            cid,
            Wait(
                id=f"expiry:{n}",
                actor_id="a",
                expected_revision=await revision(play, cid),
                ticks=ticks,
            ),
            principal_id="a",
        )
    maintained = await LockSpellService(play).execute(
        cid,
        complete.model_copy(
            update={
                "id": "maintain",
                "kind": "maintain",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="gm",
    )
    assert maintained.outcome == "active" and maintained.energy_spent == 2
    cancelled = await LockSpellService(play).execute(
        cid,
        complete.model_copy(
            update={
                "id": "cancel",
                "kind": "cancel",
                "expected_revision": await revision(play, cid),
            }
        ),
        principal_id="gm",
    )
    assert cancelled.outcome == "cancelled" and cancelled.energy_spent == 1
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ritual_observations_authority_retry_stale_replay_and_private_projection(
    tmp_path: Path, backend: str
) -> None:
    from scripts.replay_fixtures import FixtureExecutor
    from wayfarer.persistence.replay import verify_commands

    cid, play = await prepare(tmp_path, backend)
    initial = await play.store.read(cid)
    play.rng = secrets
    command = DeclareRitualCapability(
        id="observed",
        actor_id="a",
        expected_revision=0,
        speech_available=False,
        gesture_available=True,
        reason="Private gesture-only capability observation",
    )
    service = SpellRitualService(play)
    with pytest.raises(ValidationError, match="director authority"):
        await service.execute(cid, command, principal_id="alice")
    assert await play.store.read(cid) == initial
    results = await asyncio.gather(
        *(service.execute(cid, command, principal_id="gm") for _ in range(3))
    )
    assert results == [results[0]] * 3
    after = await play.store.read(cid)
    assert after["revision"] == 1 and after == await play.store.replay(cid)
    with pytest.raises(ConflictError):
        await service.execute(cid, command.model_copy(update={"id": "stale"}), principal_id="gm")
    with pytest.raises(ConflictError):
        await service.execute(
            cid, command.model_copy(update={"speech_available": True}), principal_id="gm"
        )
    assert await play.store.read(cid) == after
    visible = await build_runtime(play).events(cid, principal_id="alice")
    assert PREFIX not in str(visible) and command.reason not in str(visible)
    restarted = build_play(tmp_path, play.engine, backend=backend, rng=NoRolls())
    assert (
        await SpellRitualService(restarted).execute(cid, command, principal_id="gm") == results[0]
    )
    replayed, checks = await verify_commands(
        initial,
        await played(play.store, cid),
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert all(c.folded and c.reexecuted for c in checks) and replayed == after


@pytest.mark.parametrize("retry", [False, True])
async def test_ritual_observation_rechecks_current_gm_at_commit_and_duplicate(
    tmp_path: Path, retry: bool
) -> None:
    from test_gadgeteer_gizmos_persistence import RevokingStore, revoke_gm

    cid, original = await prepare(tmp_path)
    command = DeclareRitualCapability(
        id="observed",
        actor_id="a",
        expected_revision=0,
        gesture_available=True,
        reason="Current director observation",
    )
    if retry:
        await SpellRitualService(original).execute(cid, command, principal_id="gm")
    prior = await revision(original, cid)
    store = RevokingStore(tmp_path / "runtime.sqlite", 10)
    play = build_play(tmp_path, original.engine, store=store, rng=NoRolls())
    store.revoke = lambda: revoke_gm(original, cid, prior)
    if not retry:
        command = command.model_copy(update={"expected_revision": prior + 1})
    with pytest.raises(ValidationError, match="director authority"):
        await SpellRitualService(play).execute(cid, command, principal_id="gm")
    saved = original._load(await original.store.read(cid))
    assert saved.revision == prior + 1
    assert (capability(saved.resources, "a") is not None) == retry


async def test_authored_genesis_cannot_seed_ritual_capability(tmp_path: Path) -> None:
    cid, play = await prepare(tmp_path)
    state = play._load(await play.store.read(cid))
    resources = state.resources.model_copy(
        update={"events": (ResourceEvent(id=PREFIX + "forged", at=0, target_id="a", kind="{}"),)}
    )
    actors = tuple(
        ActorSetup.model_validate(a.model_dump(exclude={"approval"})) for a in state.actors
    )
    with pytest.raises(ValidationError, match="cannot seed supernatural"):
        play.initial_state(campaign(play.engine), state.world, resources, actors)
