"""B239/B482: item Power retains the contained spell's current sight penalty."""

import secrets
from dataclasses import replace
from pathlib import Path
from typing import Literal

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_combat_sensory_authority import change
from test_magic_item_execution import item_campaign
from test_staff_casting import prepare as staff_campaign
from test_staff_casting_vision import injured_eyes, symptoms
from test_symptom_casting_checks import finish

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import MagicItemBinding
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.events import document
from wayfarer.engine.simulation.magic.effects import dazed
from wayfarer.engine.simulation.magic.spells import SpellCommand, SpellId, SpellResult, latest
from wayfarer.engine.simulation.magic.staff_casting import apply_targeting
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter, TakeCombatTurn
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService, approved_context
from wayfarer.persistence.replay import command_text, verify_commands


async def prepare(
    path: Path,
    backend: str,
    *,
    spell: SpellId = "daze",
    distance: int = 0,
    combat: bool = False,
    self_target: bool = False,
    execution_version: Literal[1, 2] = 2,
) -> tuple[str, PlayService, SpellCommand]:
    if combat:
        source, foundation, _ = await staff_campaign(
            path / "source", backend, distance=min(distance, 8), combat=True, enchanted=False
        )
    else:
        source, foundation = await item_campaign(
            path / "source", backend, power=15, mana="normal", reduction=0
        )
    old = foundation._load(await foundation.store.read(source))
    rules = foundation.engine.rules
    if combat and distance > 8:
        assert rules.combat is not None
        field = rules.combat.battlefields[0]
        assert isinstance(field, Battlefield)
        rules = rules.model_copy(
            update={
                "combat": rules.combat.model_copy(
                    update={
                        "battlefields": (field.model_copy(update={"width": distance + 2}),),
                    }
                )
            }
        )
    assert rules.spells
    channel = rules.spells.channels[0].model_copy(
        update={
            "spell_id": spell,
            "distance_yards": distance,
            "target_id": "a" if self_target else "b",
            "magic_item_id": "blade",
        }
    )
    rules = rules.model_copy(
        update={
            "spells": rules.spells.model_copy(
                update={
                    "execution_version": execution_version,
                    "channels": (channel,),
                    "magic_items": (
                        MagicItemBinding(
                            id="item-spell", item_id="blade", spell_id=spell, power=15
                        ),
                    ),
                }
            ),
        }
    )
    engine = ActionEngine(foundation.engine.reviewer, foundation.engine.resources, rules)
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        old.world,
        old.resources.model_copy(update={"revision": 0, "receipts": (), "events": ()}),
        tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in old.actors),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    if combat:
        await CombatService(play).execute(
            cid,
            StartEncounter(
                id="encounter",
                actor_id="gm",
                expected_revision=0,
                encounter_id="fight",
                battlefield_id="forge",
                placements=(
                    Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                    Placement(actor_id="b", position=GridPoint(x=1 + distance, y=1)),
                ),
            ),
            principal_id="gm",
        )
    return (
        cid,
        play,
        SpellCommand(
            id="start",
            actor_id="a",
            expected_revision=(await play.store.read(cid))["revision"],
            kind="start",
            spell_id=spell,
            channel_id=channel.id,
            cast_id="item-cast",
        ),
    )


def commitment(state: PlayState) -> tuple[int, int, int, int | None]:
    effect = latest(state.resources)["item-cast"]
    return effect.cost, effect.hp_energy, effect.ready_at, effect.required_turns


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_blind_item_daze_fails_against_modified_power_and_spends_failure_energy(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await item_campaign(tmp_path, backend, power=15, mana="normal", reduction=0)
    await change(play, cid, symptoms)
    start = SpellCommand(
        id="start",
        actor_id="a",
        expected_revision=(await play.store.read(cid))["revision"],
        kind="start",
        spell_id="daze",
        channel_id="item-daze",
        cast_id="item-cast",
    )
    await SpellService(play).execute(cid, start, principal_id="a")
    started = play._load(await play.store.read(cid))
    assert latest(started.resources)["item-cast"].skill == 10
    assert commitment(started) == (3, 0, 2, None)
    complete, ready_at = await finish(play, cid, start)
    dice = RecordedDice((4, 4, 3))
    play.rng = dice
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert (result.checks[0].effective_target, sum(result.checks[0].dice)) == (10, 11)
    assert result.outcome == "failed" and result.energy_spent == 1 and ready_at == 2
    assert dice.exhausted() and len(result.checks) == 1 and not dazed(after.resources, "b")
    assert commitment(after) == commitment(started)
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("cause", ["symptoms", "two-eyes", "one-eye", "recovered"])
@pytest.mark.parametrize("spell", ["light", "daze"])
async def test_item_regular_and_resisted_sight_uses_current_physical_state(
    tmp_path: Path, backend: str, cause: str, spell: SpellId
) -> None:
    cid, play, start = await prepare(tmp_path, backend, spell=spell, distance=2)
    await change(
        play,
        cid,
        (lambda s: symptoms(s, active=cause == "symptoms"))
        if cause in ("symptoms", "recovered")
        else lambda s: injured_eyes(s, both=cause == "two-eyes"),
    )
    start = start.model_copy(update={"expected_revision": (await play.store.read(cid))["revision"]})
    await SpellService(play).execute(cid, start, principal_id="a")
    complete, _ = await finish(play, cid, start)
    play.rng = RecordedDice((2, 2, 2, 6, 6, 6))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    assert result.checks[0].effective_target == (8 if cause in ("symptoms", "two-eyes") else 13)
    assert result.outcome == "active" and result.energy_spent == (1 if spell == "light" else 3)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("onset", [False, True])
@pytest.mark.parametrize("execution_version", [1, 2])
async def test_item_sight_changes_only_unresolved_roll_and_keeps_retry_authority(
    tmp_path: Path, backend: str, onset: bool, execution_version: Literal[1, 2]
) -> None:
    cid, play, start = await prepare(tmp_path, backend, execution_version=execution_version)
    await change(play, cid, lambda s: symptoms(s, active=not onset))
    start = start.model_copy(update={"expected_revision": (await play.store.read(cid))["revision"]})
    await SpellService(play).execute(cid, start, principal_id="a")
    started = play._load(await play.store.read(cid))
    assert latest(started.resources)[start.cast_id].skill == (15 if onset else 10)
    await change(play, cid, lambda s: symptoms(s, active=onset))
    complete, _ = await finish(play, cid, start)
    before, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    with pytest.raises(AuthorizationError):
        await SpellService(play).execute(cid, complete, principal_id="b")
    with pytest.raises(ConflictError):
        await SpellService(play).execute(
            cid, complete.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (before, history, stream)
    play.rng = RecordedDice((4, 4, 3) if onset else (4, 4, 3, 6, 6, 6))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert result.checks[0].effective_target == (10 if onset else 15)
    assert commitment(after) == commitment(started)
    await change(play, cid, lambda s: symptoms(s, active=not onset))
    final, history, stream = (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    )
    play.rng = RecordedDice(())
    assert await SpellService(play).execute(cid, complete, principal_id="a") == result
    assert (
        await play.store.read(cid),
        await play.store.history(cid),
        await play.store.stream(cid),
    ) == (final, history, stream)
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "members": tuple(
                    m.model_copy(update={"actor_ids": ()}) if m.principal_id == "a" else m
                    for m in s.members
                )
            }
        ),
    )
    final = await play.store.read(cid)
    with pytest.raises(AuthorizationError):
        await SpellService(play).execute(cid, complete, principal_id="a")
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("onset", [False, True])
async def test_combat_item_final_concentration_rechecks_sight(
    tmp_path: Path, backend: str, onset: bool
) -> None:
    cid, play, start = await prepare(tmp_path, backend, distance=1, combat=True)
    await change(play, cid, lambda s: symptoms(s, active=not onset))
    start = start.model_copy(update={"expected_revision": (await play.store.read(cid))["revision"]})
    await SpellService(play).execute(cid, start, principal_id="a")
    started = play._load(await play.store.read(cid))
    await change(play, cid, lambda s: symptoms(s, active=onset))
    await CombatService(play).execute(
        cid,
        TakeCombatTurn(
            id="b-waits",
            actor_id="b",
            expected_revision=(await play.store.read(cid))["revision"],
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )
    complete = start.model_copy(
        update={
            "id": "finish",
            "kind": "concentrate",
            "expected_revision": (await play.store.read(cid))["revision"],
        }
    )
    play.rng = RecordedDice((4, 4, 3) if onset else (4, 4, 3, 6, 6, 6))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert result.checks[0].effective_target == (9 if onset else 14)
    assert result.outcome == ("failed" if onset else "active")
    assert dazed(after.resources, "b") is not onset
    assert commitment(after) == commitment(started)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_blindness_does_not_authorize_unknown_item_subject_or_penalize_self(
    tmp_path: Path, backend: str
) -> None:
    cid, play, start = await prepare(tmp_path, backend)
    await change(
        play,
        cid,
        lambda s: symptoms(s).model_copy(
            update={"world": replace(s.world, facts=(), knowledge=())}
        ),
    )
    start = start.model_copy(update={"expected_revision": (await play.store.read(cid))["revision"]})
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="not perceived"):
        await SpellService(play).execute(cid, start, principal_id="a")
    assert await play.store.read(cid) == before
    cid, play, start = await prepare(tmp_path / "self", backend, spell="light", self_target=True)
    await change(play, cid, symptoms)
    start = start.model_copy(update={"expected_revision": (await play.store.read(cid))["revision"]})
    await SpellService(play).execute(cid, start, principal_id="a")
    complete, _ = await finish(play, cid, start)
    play.rng = RecordedDice((4, 4, 3))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    assert result.checks[0].effective_target == 15 and result.outcome == "active"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("execution_version,combat", [(1, False), (2, False), (2, True)])
@pytest.mark.parametrize("current", [False, True])
async def test_item_sight_generation_preserves_historical_and_current_reexecution(
    tmp_path: Path, backend: str, combat: bool, current: bool, execution_version: Literal[1, 2]
) -> None:
    cid, play, start = await prepare(
        tmp_path,
        backend,
        spell="light",
        distance=1,
        combat=combat,
        execution_version=execution_version,
    )
    await change(play, cid, symptoms)
    initial = await play.store.read(cid)
    start = start.model_copy(update={"expected_revision": initial["revision"]})
    play.rng = secrets

    async def execute(command: SpellCommand) -> SpellResult:
        campaign = await play.store.read(cid)
        active = play.for_campaign(campaign)
        return await submit(
            active,
            cid,
            SpellService(active).plan(
                active,
                member_for(active._load(campaign), "a"),
                command,
                principal_id="a",
                item_sight=current,
            ),
            principal_id="a",
        )

    result = await execute(start)
    complete = start if combat else (await finish(play, cid, start))[0]
    if not combat:
        result = await execute(complete)
    assert result.checks[0].effective_target == (9 if current else 14)
    final = await play.store.read(cid)
    records = [
        r for r in await played(play.store, cid) if r.expected_revision >= initial["revision"]
    ]
    stream = await play.store.stream(cid, after=initial["revision"])
    payload = validation.mapping(validation.decode(command_text(records[-1])))
    assert payload["targeting_generation"] == payload["check_generation"] == 1
    assert payload.get("item_sight_generation") == (1 if current else None)
    play.rng = RecordedDice(())
    assert await SpellService(play).execute(cid, complete, principal_id="a") == result
    assert await play.store.read(cid) == final
    replayed, checks = await verify_commands(
        initial,
        records,
        stream,
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert document(replayed) == document(final) and all(c.folded and c.reexecuted for c in checks)


@pytest.mark.parametrize("spell", ["create-fire", "fireball"])
async def test_item_sight_preserves_historical_area_and_missile_targeting(
    tmp_path: Path, spell: SpellId
) -> None:
    # Compatibility only: Area still needs nearest-edge distance and current
    # area sight/touch authority; Missile release has separate attack rules.
    cid, play, start = await prepare(
        tmp_path, "sqlite", spell="create-fire", distance=2, combat=True
    )
    await change(play, cid, symptoms)
    state = play._load(await play.store.read(cid))
    context = approved_context(play.rules_context, state, start)
    context = apply_targeting(
        play.rules_context, state, start.model_copy(update={"spell_id": spell}), context
    )
    assert context.item_cast and context.skill == 15 and not context.unseen
    assert context.position == (3, 1) and context.target_id == "b"


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("execution_version", [1, 2])
async def test_item_blindness_onset_keeps_existing_cancel_path(
    tmp_path: Path, backend: str, execution_version: Literal[1, 2]
) -> None:
    cid, play, start = await prepare(tmp_path, backend, execution_version=execution_version)
    await SpellService(play).execute(cid, start, principal_id="a")
    started = play._load(await play.store.read(cid))
    await change(play, cid, symptoms)
    cancel = start.model_copy(
        update={
            "id": "cancel",
            "kind": "cancel",
            "expected_revision": (await play.store.read(cid))["revision"],
        }
    )
    result = await SpellService(play).execute(cid, cancel, principal_id="a")
    after = play._load(await play.store.read(cid))
    assert result.outcome == "cancelled" and result.checks == () and result.energy_spent == 0
    assert latest(after.resources)[start.cast_id].phase == "ended"
    assert commitment(after) == commitment(started)
    assert next(p.current for p in after.resources.pools if p.id == "fp:a") == 10
    assert await SpellService(play).execute(cid, cancel, principal_id="a") == result
    assert await play.store.read(cid) == await play.store.replay(cid)
