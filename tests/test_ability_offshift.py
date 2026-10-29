"""B69/B345/B481: authenticated one-second off-shift concentration."""

from pathlib import Path

import pytest
from test_enchanting_concentration_admission import fixture, replay, work

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.ability_offshift import pending
from wayfarer.engine.simulation.ability_types import AbilityCommand
from wayfarer.engine.simulation.actions import Wait
from wayfarer.engine.simulation.magic.enchanting_transitions import AdvanceEnchanting
from wayfarer.errors import ConflictError
from wayfarer.orchestration.abilities import AbilityService
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.play import PlayService


async def night(path: Path, backend: str) -> tuple[Campaign, PlayService]:
    initial, play = await fixture(path, backend, one_enchanter=True)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(cid, work(state.revision), principal_id="gm")
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        AdvanceEnchanting(
            id="night",
            actor_id="a",
            expected_revision=state.revision,
            project_id="staff-project",
            work_id="work",
            to=28800,
        ),
        principal_id="gm",
    )
    return initial, play


def command(revision: int, kind: str = "activate", identifier: str = "read") -> AbilityCommand:
    return AbilityCommand.model_validate(
        dict(
            id=identifier,
            actor_id="a",
            expected_revision=revision,
            kind=kind,
            ability_id="trait:night-reading",
            channel_id="read-a",
        )
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("clock", ["wait", "work"])
async def test_actual_night_reading_native_second_retry_and_genesis(
    tmp_path: Path, backend: str, clock: str
) -> None:
    initial, play = await night(tmp_path, backend)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    activate = command(state.revision)
    result = await AbilityService(play).execute(cid, activate, principal_id="a")
    assert result.outcome == "concentrating"
    assert await AbilityService(play).execute(cid, activate, principal_id="a") == result
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 28800
    assert pending(state.resources)[0].effect.ready_at == 28801
    assert next(a for a in state.actors if a.actor_id == "a").available_at == 28801
    if clock == "wait":
        await play.execute(
            cid,
            Wait(id="second", actor_id="b", expected_revision=state.revision, ticks=1),
            principal_id="b",
        )
    else:
        await EnchantmentService(play).execute(
            cid,
            AdvanceEnchanting(
                id="second",
                actor_id="a",
                expected_revision=state.revision,
                project_id="staff-project",
                work_id="work",
                to=28801,
            ),
            principal_id="gm",
        )
    state = play._load(await play.store.read(cid))
    resolve = command(state.revision, "resolve", "resolve")
    result = await AbilityService(play).execute(cid, resolve, principal_id="a")
    assert result.outcome in ("active", "resisted")
    assert not pending(play._load(await play.store.read(cid)).resources)
    assert await AbilityService(play).execute(cid, resolve, principal_id="a") == result
    await replay(initial, play, tmp_path / "replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pending_clock_overshoot_is_preentropy_and_cancel_is_native(
    tmp_path: Path, backend: str
) -> None:
    initial, play = await night(tmp_path, backend)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await AbilityService(play).execute(cid, command(state.revision), principal_id="a")
    before = await play.store.read(cid)
    state = play._load(before)
    dice = RecordedDice(())
    play.rng = dice
    with pytest.raises(ConflictError, match="beyond readiness"):
        await play.execute(
            cid,
            Wait(id="overshoot", actor_id="b", expected_revision=state.revision, ticks=2),
            principal_id="b",
        )
    assert await play.store.read(cid) == before
    assert dice.exhausted()
    result = await AbilityService(play).execute(
        cid,
        command(state.revision, "cancel", "cancel"),
        principal_id="a",
    )
    assert result.outcome == "cancelled"
    assert not pending(play._load(await play.store.read(cid)).resources)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("distance,target", [(500, 2), (300, 3)])
async def test_named_b345_checkpoint_current_native_target_before_all_resolve_dice(
    tmp_path: Path,
    backend: str,
    monkeypatch: pytest.MonkeyPatch,
    distance: int,
    target: int,
) -> None:
    from functools import partial

    import test_enchanting_concentration_admission as source

    from wayfarer.engine.simulation.abilities import internal_id
    from wayfarer.engine.simulation.ability_types import AbilityChannel, AbilityEvent
    from wayfarer.errors import ValidationError
    from wayfarer.persistence.ability_resistance_inputs import KEY as RESISTANCE_KEY
    from wayfarer.persistence.command_inputs import replay_payload

    # Author the real channel before campaign genesis; IQ16 with B550 range -14/-13.
    monkeypatch.setattr(source, "AbilityChannel", partial(AbilityChannel, distance_yards=distance))
    initial, play = await night(tmp_path, backend)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    value = command(state.revision)
    if target == 2:
        from wayfarer.orchestration.membership import member_for
        from wayfarer.orchestration.pipeline import submit

        # Retain the independently captured off-shift1 producer that predates
        # B348 activation admission. Fresh B348 target2 now refuses at start;
        # this old origin must still reach its original resolve-time guard.
        await submit(
            play,
            cid,
            AbilityService(play).plan(
                play,
                state,
                member_for(state, "a"),
                value,
                principal_id="a",
                rule16_scope=True,
                offshift=True,
            ),
            principal_id="a",
        )
    else:
        await AbilityService(play).execute(cid, value, principal_id="a")
    saved = await play.store.command_input(cid, value.id)
    assert saved and saved.text
    original_policy = replay_payload(saved.text)
    assert isinstance(original_policy, dict)
    assert (RESISTANCE_KEY in original_policy) == (target == 3)
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="second", actor_id="a", expected_revision=state.revision, ticks=1),
        principal_id="a",
    )
    before = await play.store.read(cid)
    state = play._load(before)
    dice = RecordedDice(()) if target == 2 else RecordedDice((1, 1, 1, 6, 6, 6))
    play.rng = dice
    resolve = command(state.revision, "resolve", "resolve")
    if target == 2:
        with pytest.raises(ValidationError, match="below 3"):
            await AbilityService(play).execute(cid, resolve, principal_id="a")
        assert await play.store.read(cid) == before
        assert pending(state.resources)
        await AbilityService(play).execute(
            cid, command(state.revision, "cancel", "cancel"), principal_id="a"
        )
    else:
        result = await AbilityService(play).execute(cid, resolve, principal_id="a")
        assert result.outcome == "active"
        after = play._load(await play.store.read(cid))
        event = AbilityEvent.model_validate_json(
            next(e.kind for e in after.resources.events if e.id == internal_id(resolve.id))
        )
        assert event.checks[0].effective_target == 3
    assert dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("family", ["physical", "scene", "noncombat", "spell", "ability"])
async def test_unqualified_real_other_actor_hosts_refuse_before_reduction(
    tmp_path: Path,
    backend: str,
    family: str,
) -> None:
    from wayfarer.engine.simulation.magic.spells import SpellCommand
    from wayfarer.orchestration.noncombat import NoncombatCommand, NoncombatService
    from wayfarer.orchestration.physical import PhysicalCommand, PhysicalRoute, PhysicalService
    from wayfarer.orchestration.scenes import SceneService, TravelScene
    from wayfarer.orchestration.spells import SpellService

    initial, play = await night(tmp_path, backend)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await AbilityService(play).execute(cid, command(state.revision), principal_id="a")
    before = await play.store.read(cid)
    state = play._load(before)
    dice = RecordedDice(())
    play.rng = dice
    revision = state.revision
    with pytest.raises(ConflictError, match="pending off-shift|does not continue"):
        if family == "physical":
            await PhysicalService(
                play, lambda *_: PhysicalRoute(id="other", scene_id="forge")
            ).execute(
                cid,
                PhysicalCommand(
                    id="other",
                    actor_id="b",
                    expected_revision=revision,
                    kind="lift",
                    route_id="other",
                ),
                principal_id="b",
            )
        elif family == "scene":
            await SceneService(play).execute(
                cid,
                TravelScene(id="other", actor_id="b", expected_revision=revision, exit_id="out"),
                principal_id="b",
            )
        elif family == "noncombat":
            await NoncombatService(play).execute(
                cid,
                NoncombatCommand(
                    id="other",
                    actor_id="b",
                    expected_revision=revision,
                    kind="start_noncombat",
                    encounter_id="parley",
                ),
                principal_id="b",
            )
        elif family == "spell":
            await SpellService(play).execute(
                cid,
                SpellCommand(
                    id="other",
                    actor_id="b",
                    expected_revision=revision,
                    kind="start",
                    spell_id="light",
                    channel_id="light-b",
                    cast_id="other",
                ),
                principal_id="b",
            )
        else:
            await AbilityService(play).execute(
                cid,
                AbilityCommand(
                    id="other",
                    actor_id="b",
                    expected_revision=revision,
                    ability_id="trait:night-reading",
                    channel_id="read-b",
                ),
                principal_id="b",
            )
    assert await play.store.read(cid) == before
    assert dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("ending", ["resolve", "cancel"])
async def test_exact_next_shift_completion_preserves_native_work_boundary(
    tmp_path: Path, backend: str, ending: str
) -> None:
    initial, play = await night(tmp_path, backend)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(
        cid,
        AdvanceEnchanting(
            id="late-night",
            actor_id="a",
            expected_revision=state.revision,
            project_id="staff-project",
            work_id="work",
            to=86399,
        ),
        principal_id="gm",
    )
    state = play._load(await play.store.read(cid))
    await AbilityService(play).execute(cid, command(state.revision), principal_id="a")
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(id="boundary", actor_id="b", expected_revision=state.revision, ticks=1),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    assert state.resources.game_time == 86400
    result = await AbilityService(play).execute(
        cid, command(state.revision, ending, "finish"), principal_id="a"
    )
    assert result.outcome in ("active", "resisted", "cancelled")
    assert not pending(play._load(await play.store.read(cid)).resources)
    await replay(initial, play, tmp_path / "replay")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_active_work_refuses_before_entropy_and_no_new_maintenance_admission(
    tmp_path: Path, backend: str
) -> None:
    initial, play = await fixture(tmp_path, backend, one_enchanter=True)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await EnchantmentService(play).execute(cid, work(state.revision), principal_id="gm")
    before = await play.store.read(cid)
    state = play._load(before)
    dice = RecordedDice(())
    play.rng = dice
    for kind in ("activate", "analyze", "maintain"):
        with pytest.raises(ConflictError, match="enchanting work"):
            await AbilityService(play).execute(
                cid, command(state.revision, kind, kind), principal_id="a"
            )
    assert await play.store.read(cid) == before and dice.exhausted()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_pending_retry_auth_stale_and_swapped_channel_are_preentropy(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.errors import AuthorizationError, ValidationError

    initial, play = await night(tmp_path, backend)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    activate = command(state.revision)
    original = await AbilityService(play).execute(cid, activate, principal_id="a")
    before = await play.store.read(cid)
    state = play._load(before)
    dice = RecordedDice(())
    play.rng = dice
    assert await AbilityService(play).execute(cid, activate, principal_id="a") == original
    with pytest.raises((AuthorizationError, ValidationError)):
        await AbilityService(play).execute(
            cid, command(state.revision, "cancel", "unauthorized"), principal_id="b"
        )
    with pytest.raises(ConflictError):
        await AbilityService(play).execute(
            cid,
            command(state.revision, "cancel", "swapped").model_copy(
                update={"channel_id": "read-b"}
            ),
            principal_id="a",
        )
    with pytest.raises(ConflictError):
        await AbilityService(play).execute(
            cid, command(state.revision - 1, "cancel", "stale"), principal_id="a"
        )
    assert await play.store.read(cid) == before and dice.exhausted()


def test_private_generation_is_strict_and_digest_bound() -> None:
    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.ability_offshift_generations import KEY, version
    from wayfarer.persistence.command_inputs import canonical
    from wayfarer.persistence.events import CommandInput, payload_digest

    base = {
        "operation": "ability",
        "principal_id": "player:alice",
        "command": command(3).model_dump(mode="json"),
    }
    for value in (True, False, 1.0, 0, 2, "1", None):
        text = canonical({**base, KEY: value})
        with pytest.raises(ValidationError):
            version(CommandInput(payload_digest({"input": text}), text))
    text = canonical({**base, KEY: 1})
    assert version(CommandInput(payload_digest({"input": text}), text))
    with pytest.raises(ValidationError):
        version(CommandInput("wrong", text))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("principal", ["alice", "player:alice"])
@pytest.mark.parametrize("legacy", [False, True])
async def test_delegated_principal_new_and_unmarked_raw_genesis(
    tmp_path: Path,
    backend: str,
    principal: str,
    legacy: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import test_enchanting_concentration_admission as source
    from support.runtime import seed_campaign as original_seed

    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.engine.simulation.campaign.access import CampaignMember
    from wayfarer.orchestration.ability_offshift_generations import KEY
    from wayfarer.orchestration.membership import member_for
    from wayfarer.orchestration.pipeline import submit
    from wayfarer.orchestration.sessions import Store
    from wayfarer.persistence.command_inputs import replay_payload

    async def seed(store: Store, initial: Campaign) -> None:
        state = PlayState.model_validate_json(initial["play_json"])
        state = state.model_copy(
            update={
                "members": state.members
                + (CampaignMember(principal_id=principal, role="player", actor_ids=("a",)),)
            }
        )
        initial["play_json"] = state.model_dump_json()
        await original_seed(store, initial)

    monkeypatch.setattr(source, "seed_campaign", seed)
    initial, play = (
        await fixture(tmp_path, backend, one_enchanter=True)
        if legacy
        else await night(tmp_path, backend)
    )
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    activate = command(state.revision)
    service = AbilityService(play)
    if legacy:
        result = await submit(
            play,
            cid,
            service.plan(
                play,
                state,
                member_for(state, principal),
                activate,
                principal_id=principal,
                rule16_scope=False,
                offshift=False,
            ),
            principal_id=principal,
        )
    else:
        result = await service.execute(cid, activate, principal_id=principal)
    assert result.outcome == "concentrating"
    state = play._load(await play.store.read(cid))
    assert bool(pending(state.resources)) is not legacy
    saved = await play.store.command_input(cid, activate.id)
    assert saved and saved.text
    captured = replay_payload(saved.text)
    assert (
        isinstance(captured, str) if legacy else isinstance(captured, dict) and captured[KEY] == 1
    )
    await play.execute(
        cid,
        Wait(id="second", actor_id="b", expected_revision=state.revision, ticks=1),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    await service.execute(cid, command(state.revision, "resolve", "finish"), principal_id=principal)
    await replay(initial, play, tmp_path / "replay")


async def test_strict_producer_marker_and_no_legacy_inheritance_at_same_instant(
    tmp_path: Path,
) -> None:
    import json

    from pydantic import ValidationError as SchemaError

    from wayfarer.engine.simulation.abilities import internal_id
    from wayfarer.engine.simulation.abilities import record as native_record
    from wayfarer.engine.simulation.ability_offshift import PREFIX
    from wayfarer.engine.simulation.ability_types import AbilityEvent
    from wayfarer.errors import ValidationError

    initial, play = await night(tmp_path, "sqlite")
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await AbilityService(play).execute(cid, command(state.revision), principal_id="a")
    state = play._load(await play.store.read(cid))
    resources = state.resources
    marker = next(e for e in resources.events if e.id.startswith(PREFIX))
    data = json.loads(marker.kind)
    altered = [
        {**data, "generation": 2},
        {**data, "generation": True},
        {**data, "command_id": "swapped"},
        {**data, "extra": 1},
        {**data, "effect": {**data["effect"], "ready_at": data["effect"]["ready_at"] + 1}},
    ]
    for changed in altered:
        forged = resources.model_copy(
            update={
                "events": tuple(
                    e.model_copy(update={"kind": json.dumps(changed)}) if e.id == marker.id else e
                    for e in resources.events
                )
            }
        )
        with pytest.raises((ValidationError, SchemaError)):
            pending(forged)
    native = AbilityEvent.model_validate_json(
        next(e.kind for e in resources.events if e.id == internal_id("read"))
    )
    await AbilityService(play).execute(
        cid, command(state.revision, "cancel", "cancel"), principal_id="a"
    )
    resources = play._load(await play.store.read(cid)).resources
    # An unmarked native producer can have identical actor/target/start/ready bytes.
    # Its own command identity, separated by cancellation, must not inherit the old policy.
    unmarked = native_record(resources, command(resources.revision, "activate", "unmarked"), native)
    assert not pending(unmarked)


async def test_genesis_reserves_private_policy_and_players_never_receive_marker(
    tmp_path: Path,
) -> None:
    import json

    from wayfarer.engine.simulation.ability_offshift import PREFIX
    from wayfarer.engine.simulation.actions import PlayState
    from wayfarer.errors import ValidationError
    from wayfarer.orchestration.membership import member_for
    from wayfarer.orchestration.views import campaign_view

    initial, play = await night(tmp_path, "sqlite")
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await AbilityService(play).execute(cid, command(state.revision), principal_id="a")
    state = play._load(await play.store.read(cid))
    marker = next(e for e in state.resources.events if e.id.startswith(PREFIX))
    genesis = PlayState.model_validate_json(initial["play_json"])
    forged = genesis.resources.model_copy(update={"events": genesis.resources.events + (marker,)})
    with pytest.raises(ValidationError, match="supernatural execution receipts"):
        play.initial_state(initial, genesis.world, forged, ())
    for actor in ("a", "b"):
        encoded = json.dumps(
            campaign_view(state, member_for(state, actor), play.engine.rules.combat)
        )
        assert PREFIX not in encoded and "ability_offshift_generation" not in encoded
        assert marker.kind not in encoded
    # Marker envelope fields and duplicate identities are part of strict producer binding.
    for events in (
        state.resources.events + (marker,),
        tuple(
            e.model_copy(update={"target_id": "b"}) if e.id == marker.id else e
            for e in state.resources.events
        ),
        tuple(
            e.model_copy(update={"at": marker.at + 1}) if e.id == marker.id else e
            for e in state.resources.events
        ),
        state.resources.events
        + (next(e for e in state.resources.events if e.id.startswith("ability:")),),
    ):
        with pytest.raises(ValidationError, match="producer"):
            pending(state.resources.model_copy(update={"events": events}))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "family", ["physical", "social", "medical", "toxin", "survival", "drinking"]
)
async def test_all_six_external_callbacks_are_fenced_before_invocation(
    tmp_path: Path,
    backend: str,
    family: str,
) -> None:
    from typing import Never

    from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
    from wayfarer.engine.simulation.health.survival import BeginSurvival
    from wayfarer.engine.simulation.health.toxins import DrinkCommand, ToxinCommand
    from wayfarer.engine.simulation.social.social import SocialCommand
    from wayfarer.orchestration.drinking import DrinkingService
    from wayfarer.orchestration.drowsy_toxins import DrowsyToxinService
    from wayfarer.orchestration.medical import MedicalService
    from wayfarer.orchestration.physical import PhysicalCommand, PhysicalService
    from wayfarer.orchestration.social import SocialService
    from wayfarer.orchestration.survival import SurvivalService

    def no_source(*args: object) -> Never:
        raise AssertionError("Unqualified source callback ran before pending admission")

    initial, play = await night(tmp_path, backend)
    cid = initial["id"]
    state = play._load(await play.store.read(cid))
    await AbilityService(play).execute(cid, command(state.revision), principal_id="a")
    before = await play.store.read(cid)
    state = play._load(before)
    dice = RecordedDice(())
    play.rng = dice
    with pytest.raises(ConflictError, match="pending off-shift"):
        if family == "physical":
            await PhysicalService(play, no_source).execute(
                cid,
                PhysicalCommand(
                    id="other",
                    actor_id="b",
                    expected_revision=state.revision,
                    kind="lift",
                    route_id="other",
                ),
                principal_id="b",
            )
        elif family == "social":
            await SocialService(play, no_source).execute(
                cid,
                SocialCommand(
                    id="other",
                    actor_id="b",
                    expected_revision=state.revision,
                    kind="reaction",
                    subject_id="a",
                    trigger_id="other",
                ),
                principal_id="gm",
            )
        elif family == "medical":
            await MedicalService(play, no_source).execute(
                cid,
                BeginRecovery(
                    id="other",
                    actor_id="b",
                    expected_revision=state.revision,
                    kind="rest",
                    target_id="b",
                    seconds=600,
                ),
                principal_id="b",
            )
        elif family == "toxin":
            await DrowsyToxinService(play, no_source).execute(
                cid,
                ToxinCommand(
                    id="other",
                    actor_id="b",
                    expected_revision=state.revision,
                    kind="expose",
                    exposure_id="other",
                ),
                principal_id="gm",
            )
        elif family == "survival":
            await SurvivalService(play, no_source).execute(
                cid,
                BeginSurvival(id="other", actor_id="b", expected_revision=state.revision),
                principal_id="b",
            )
        else:
            await DrinkingService(play, no_source).execute(
                cid,
                DrinkCommand(
                    id="other",
                    actor_id="b",
                    expected_revision=state.revision,
                    kind="drink",
                    drinks=1,
                ),
                principal_id="b",
            )
    assert await play.store.read(cid) == before and dice.exhausted()
