"""B35-36/B109/B236-237/B421: Symptoms change personal rolls, not commitments."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_lock_spell_persistence import declare
from test_lock_spell_persistence import prepare as lock_prepare
from test_magic_item_execution import item_campaign
from test_spell_bindings import command, draft, setup

from scripts.replay_fixtures import FixtureExecutor
from wayfarer import validation
from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.character.power import CharacterProposal
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import CeremonialContribution, CeremonialPlan
from wayfarer.engine.rules.types.symptoms import SymptomSpec
from wayfarer.engine.rules.types.toxin import Intoxication
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, Wait
from wayfarer.engine.simulation.combat.battlefield import GridPoint
from wayfarer.engine.simulation.combat.spatial import Placement
from wayfarer.engine.simulation.events import document
from wayfarer.engine.simulation.health.condition_checks import check_modifiers
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.health.symptom_state import penalties
from wayfarer.engine.simulation.health.symptoms import reconcile_recovery, register
from wayfarer.engine.simulation.magic.lock_bindings import LockChannel
from wayfarer.engine.simulation.magic.lock_host import DeclareLockChannel
from wayfarer.engine.simulation.magic.ritual_state import DeclareRitualCapability
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand, SpellCommand, latest
from wayfarer.engine.simulation.resources import ResourceState
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, StartEncounter
from wayfarer.orchestration.locks import LockService, LockSpellService
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spell_rituals import SpellRitualService
from wayfarer.orchestration.spells import SpellService, approved_context
from wayfarer.persistence.replay import command_text, verify_commands


def symptoms(state: ResourceState, *, target: bool = False) -> ResourceState:
    """A settled six-HP causal injury has already crossed the B109 boundary."""
    actor = "b" if target else "a"
    state = state.model_copy(
        update={
            "pools": tuple(
                p.model_copy(update={"current": 4}) if p.id == "hp:" + actor else p
                for p in state.pools
            )
        }
    )
    return register(
        state,
        actor_id=actor,
        source_id="symptom-source:" + actor,
        injury_id="symptom-injury:" + actor,
        amount=6,
        pool_id="hp:" + actor,
        spec=SymptomSpec(kind="attribute-penalty", attribute="ht" if target else "iq", level=2),
    )


def recover(state: ResourceState) -> ResourceState:
    hp = next(p for p in state.pools if p.id == "hp:a")
    restored, healed = restore_hp(state, hp, 2, kind="spell")
    assert healed == 2
    return reconcile_recovery(
        state,
        state.model_copy(
            update={"pools": tuple(restored if p.id == hp.id else p for p in state.pools)}
        ),
    )


async def change_symptoms(
    play: PlayService, cid: str, change: Callable[[ResourceState], ResourceState], identifier: str
) -> None:
    """Commit an independent, settled health boundary without changing cast state."""
    before = play._load(await play.store.read(cid))

    def commit(value: Campaign) -> CommandReceipt:
        resources = change(before.resources).model_copy(update={"revision": before.revision + 1})
        updated = before.model_copy(
            update={"revision": before.revision + 1, "resources": resources}
        )
        play.commit(value, updated)
        return CommandReceipt(action="resource", outcome="fixture.symptoms")

    await play.store.commit_turn(
        cid, identifier, before.revision, identifier, commit, actor_id="gm"
    )


async def prepare(
    path: Path,
    backend: str,
    *,
    points: int = 4,
    active: bool = True,
    combat: bool = False,
    ceremonial: bool = False,
    tipsy: bool = False,
) -> tuple[str, PlayService]:
    cid, foundation = await setup(path / "foundation", execution_version=2, combat=combat)
    old = foundation._load(await foundation.store.read(cid))
    rules = foundation.engine.rules
    if ceremonial:
        assert rules.spells
        plan = CeremonialPlan(
            leader_id="a",
            contributions=(
                CeremonialContribution(actor_id="a", fp=0, role="leader"),
                CeremonialContribution(actor_id="b", fp=1, role="spectator"),
            ),
        )
        rules = rules.model_copy(
            update={
                "spells": rules.spells.model_copy(
                    update={
                        "channels": tuple(
                            c.model_copy(update={"ceremonial": plan}) if c.id == "light" else c
                            for c in rules.spells.channels
                        )
                    }
                )
            }
        )
    engine = ActionEngine(foundation.engine.reviewer, foundation.engine.resources, rules)
    play = build_play(path, engine, backend=backend, rng=RecordedDice(()))
    initial = campaign(engine)
    caster = draft()
    caster = caster.model_copy(
        update={
            "purchases": tuple(
                p.model_copy(update={"amount": points})
                if p.definition_id in ("spell:light", "spell:daze")
                else p
                for p in caster.purchases
            )
        }
    )
    state = play.initial_state(
        initial,
        old.world,
        old.resources,
        tuple(
            ActorSetup(
                actor_id=a.actor_id,
                proposal=CharacterProposal(draft=caster) if a.actor_id == "a" else a.proposal,
            )
            for a in old.actors
        ),
    )
    resources = symptoms(state.resources) if active else state.resources
    resources = symptoms(resources, target=True)
    if tipsy:
        resources = resources.model_copy(
            update={"intoxications": (Intoxication(actor_id="a", window_started=0, level="tipsy"),)}
        )
    initial["play_json"] = state.model_copy(update={"resources": resources}).model_dump_json()
    await seed_campaign(play.store, initial)
    return initial["id"], play


async def finish(play: PlayService, cid: str, start: SpellCommand) -> tuple[SpellCommand, int]:
    effect = latest(play._load(await play.store.read(cid)).resources)[start.cast_id]
    for second in range(effect.started_at + 1, effect.ready_at + 1):
        revision = (await play.store.read(cid))["revision"]
        await play.execute(
            cid,
            Wait(id="wait:" + str(second), actor_id="a", expected_revision=revision, ticks=1),
            principal_id="a",
        )
        if second < effect.ready_at:
            await SpellService(play).execute(
                cid,
                start.model_copy(
                    update={
                        "id": "concentrate:" + str(second),
                        "kind": "concentrate",
                        "expected_revision": (await play.store.read(cid))["revision"],
                    }
                ),
                principal_id="a",
            )
    return (
        start.model_copy(
            update={
                "id": "complete",
                "kind": "complete",
                "expected_revision": (await play.store.read(cid))["revision"],
            }
        ),
        effect.ready_at,
    )


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "points,spell,base,seconds,cost",
    [(4, "light", 14, 1, 1), (8, "light", 15, 1, 0), (28, "daze", 20, 1, 1)],
)
async def test_iq_loss_changes_only_roll_not_ritual_cost_or_time(
    tmp_path: Path, backend: str, points: int, spell: str, base: int, seconds: int, cost: int
) -> None:
    cid, play = await prepare(tmp_path, backend, points=points)
    if base >= 15:
        await SpellRitualService(play).execute(
            cid,
            DeclareRitualCapability(
                id="limited-ritual",
                actor_id="a",
                expected_revision=0,
                gesture_available=False,
                speech_available=base < 20,
                reason="The settled injury limits gesture and, at skill20, speech",
            ),
            principal_id="gm",
        )
    start = command((await play.store.read(cid))["revision"]).model_copy(
        update={"spell_id": spell, "channel_id": spell}
    )
    before = play._load(await play.store.read(cid))
    context = approved_context(play.rules_context, before, start)
    assert (context.skill, context.iq, context.target_ht) == (base, 12, 10)
    await SpellService(play).execute(cid, start, principal_id="a")
    started = latest(play._load(await play.store.read(cid)).resources)["cast"]
    assert (started.skill, started.cost, started.ready_at) == (base, cost, seconds)
    complete, _ = await finish(play, cid, start)
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    check = result.checks[0]
    assert (check.base_target, check.effective_target, check.margin) == (base, base - 2, base - 11)
    assert [m.value for m in check.modifiers if m.reason == "Symptoms IQ loss"] == [-2]
    assert result.energy_spent == cost
    if spell == "daze":
        assert result.checks[1].effective_target == 10  # B421: temporary HT loss is exempt.
    final = latest(play._load(await play.store.read(cid)).resources)["cast"]
    assert (final.cost, final.ready_at, final.required_turns) == (cost, seconds, None)
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("onset", [False, True])
async def test_current_symptoms_at_completion_and_finished_trace_are_immutable(
    tmp_path: Path, backend: str, onset: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, active=not onset, tipsy=True)
    start = command()
    await SpellService(play).execute(cid, start, principal_id="a")
    await change_symptoms(play, cid, symptoms if onset else recover, "health-boundary")
    complete, _ = await finish(play, cid, start)
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    with pytest.raises(AuthorizationError):
        await SpellService(play).execute(cid, complete, principal_id="b")
    with pytest.raises(ConflictError):
        await SpellService(play).execute(
            cid, complete.model_copy(update={"expected_revision": 0}), principal_id="a"
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    play.rng = RecordedDice((3, 3, 3, 3, 3, 3) if onset else (3, 3, 3))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    if onset:
        assert result.checks[0].effective_target == 9  # Existing injury distraction Will-3.
    assert result.checks[-1].effective_target == (11 if onset else 13)  # Tipsy applies once.
    assert result.energy_spent == 1
    await change_symptoms(play, cid, recover if onset else symptoms, "later-health-boundary")
    final = await play.store.read(cid)
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    play.rng = RecordedDice(())
    assert await SpellService(play).execute(cid, complete, principal_id="a") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_ceremonial_leader_keeps_eligibility_but_rolls_at_current_iq(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await prepare(tmp_path, backend, points=8, ceremonial=True)
    await SpellService(play).execute(cid, command(), principal_id="a")
    complete, ready_at = await finish(play, cid, command())
    assert ready_at == 10
    play.rng = RecordedDice((4, 5, 5))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    assert result.checks[0].rule_id == "gurps.magic.ceremonial"
    assert (result.checks[0].effective_target, result.checks[0].margin) == (13, -1)
    assert result.outcome == "failed" and result.energy_spent == 1


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("combat", [False, True])
@pytest.mark.parametrize("current", [False, True])
async def test_recorded_check_generation_preserves_old_and_new_reexecution_and_retries(
    tmp_path: Path, backend: str, combat: bool, current: bool
) -> None:
    cid, play = await prepare(tmp_path, backend, combat=combat)
    play.rng = secrets
    initial = await play.store.read(cid)
    if combat:
        await CombatService(play).execute(
            cid,
            StartEncounter(
                id="encounter",
                actor_id="gm",
                expected_revision=0,
                encounter_id="fight",
                battlefield_id="room",
                placements=(
                    Placement(actor_id="a", position=GridPoint(x=1, y=1)),
                    Placement(actor_id="b", position=GridPoint(x=1, y=2)),
                ),
            ),
            principal_id="gm",
        )
    start = command((await play.store.read(cid))["revision"])

    async def execute(value: SpellCommand) -> None:
        before = await play.store.read(cid)
        active_play = play.for_campaign(before)
        state = active_play._load(before)
        await submit(
            active_play,
            cid,
            SpellService(active_play).plan(
                active_play,
                member_for(state, "a"),
                value,
                principal_id="a",
                check_symptoms=current,
            ),
            principal_id="a",
        )

    await execute(start)
    complete = start if combat else (await finish(play, cid, start))[0]
    if not combat:
        await execute(complete)
    final = await play.store.read(cid)
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    assert result.checks[0].effective_target == (11 if combat else 12) + (0 if current else 2)
    assert result.energy_spent in (0, 1)
    effect = latest(play._load(final).resources)["cast"]
    assert (effect.cost, effect.required_turns) == (1, 1 if combat else None)
    records = await played(play.store, cid)
    check_record = next(r for r in records if r.command_id == complete.id)
    payload = validation.mapping(validation.decode(command_text(check_record)))
    assert payload["targeting_generation"] == 1  # Its existing meaning is unchanged.
    assert payload.get("check_generation") == (1 if current else None)
    stream = await play.store.stream(cid)
    play.rng = RecordedDice(())
    assert await SpellService(play).execute(cid, complete, principal_id="a") == result
    assert await play.store.read(cid) == final and await played(play.store, cid) == records
    assert await play.store.stream(cid) == stream
    replayed, checks = await verify_commands(
        initial,
        records,
        stream,
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert document(replayed) == document(final) and all(c.folded and c.reexecuted for c in checks)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_item_power_and_generic_defensive_modifiers_ignore_personal_iq_loss(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await item_campaign(tmp_path, backend, power=20, mana="normal", reduction=0)
    await change_symptoms(play, cid, symptoms, "caster-symptoms")
    await change_symptoms(play, cid, lambda state: symptoms(state, target=True), "target-symptoms")
    start = command((await play.store.read(cid))["revision"]).model_copy(
        update={"spell_id": "daze", "channel_id": "item-daze"}
    )
    await SpellService(play).execute(cid, start, principal_id="a")
    complete, ready_at = await finish(play, cid, start)
    assert ready_at == 2
    play.rng = RecordedDice((3, 3, 3, 6, 6, 6))
    result = await SpellService(play).execute(cid, complete, principal_id="a")
    assert [check.effective_target for check in result.checks] == [20, 10]
    assert result.energy_spent == 3
    state = play._load(await play.store.read(cid)).resources
    assert penalties(state, "a")["iq"] == 2 and penalties(state, "b")["ht"] == 2
    assert check_modifiers(state, "a", "iq") == ()  # Projected skill consumers own their loss.
    assert check_modifiers(state, "a", "will", defensive=True) == ()  # B421 Fright exemption.
    assert check_modifiers(state, "b", "ht", defensive=True) == ()
    assert check_modifiers(state, "a", "dx", defensive=True) == ()


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("current", [False, True])
async def test_lock_host_preserves_its_own_check_generation(
    tmp_path: Path, backend: str, current: bool
) -> None:
    cid, play = await lock_prepare(tmp_path, backend)
    await declare(play, cid)
    await change_symptoms(play, cid, symptoms, "caster-symptoms")
    initial = await play.store.read(cid)
    play.rng = secrets
    start = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=initial["revision"],
        kind="start",
        spell_id="magelock",
        channel_id="magelock",
        cast_id="ward",
    )

    async def execute(value: RuntimeSpellCommand) -> None:
        before = await play.store.read(cid)
        active_play = play.for_campaign(before)
        await submit(
            active_play,
            cid,
            LockSpellService(active_play).plan(
                active_play,
                active_play._load(before),
                value,
                principal_id="gm",
                check_symptoms=current,
            ),
            principal_id="gm",
        )

    await execute(start)
    for second in range(1, 5):
        await play.execute(
            cid,
            Wait(
                id=f"wait:{second}",
                actor_id="a",
                expected_revision=(await play.store.read(cid))["revision"],
                ticks=1,
            ),
            principal_id="a",
        )
        complete = start.model_copy(
            update={
                "id": f"concentrate:{second}",
                "kind": "complete" if second == 4 else "concentrate",
                "expected_revision": (await play.store.read(cid))["revision"],
            }
        )
        await execute(complete)
    result = await LockSpellService(play).execute(cid, complete, principal_id="gm")
    assert result.checks[0].effective_target == (12 if current else 14)
    final = await play.store.read(cid)
    effect = latest(play._load(final).resources)["ward"]
    assert (effect.cost, effect.ready_at) == (3, 4)
    records = [
        r for r in await played(play.store, cid) if r.expected_revision >= initial["revision"]
    ]
    payload = validation.mapping(validation.decode(command_text(records[-1])))
    assert payload.get("check_generation") == (1 if current else None)
    assert "targeting_generation" not in payload
    stream = await play.store.stream(cid, after=initial["revision"])
    replayed, checks = await verify_commands(
        initial,
        records,
        stream,
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecution"),
    )
    assert document(replayed) == document(final) and all(c.folded and c.reexecuted for c in checks)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_iq_loss_cannot_bypass_existing_lock_minimum_skill(
    tmp_path: Path, backend: str
) -> None:
    cid, play = await lock_prepare(tmp_path, backend)
    await declare(play, cid)
    await LockService(play).execute(
        cid,
        DeclareLockChannel(
            id="distant-channel",
            actor_id="gm",
            expected_revision=(await play.store.read(cid))["revision"],
            channel=LockChannel(
                id="distant",
                actor_id="a",
                target_id="chest",
                location_id="dock",
                spell_id="magelock",
                distance_yards=10,
            ),
        ),
        principal_id="gm",
    )
    start = RuntimeSpellCommand(
        id="start",
        actor_id="a",
        expected_revision=(await play.store.read(cid))["revision"],
        kind="start",
        spell_id="magelock",
        channel_id="distant",
        cast_id="ward",
    )
    await LockSpellService(play).execute(cid, start, principal_id="gm")
    started = latest(play._load(await play.store.read(cid)).resources)["ward"]
    assert (started.skill, started.cost, started.ready_at) == (4, 3, 4)
    await change_symptoms(play, cid, symptoms, "symptoms-after-admission")
    for second in range(1, 5):
        await play.execute(
            cid,
            Wait(
                id=f"wait:{second}",
                actor_id="a",
                expected_revision=(await play.store.read(cid))["revision"],
                ticks=1,
            ),
            principal_id="a",
        )
        complete = start.model_copy(
            update={
                "id": f"concentrate:{second}",
                "kind": "complete" if second == 4 else "concentrate",
                "expected_revision": (await play.store.read(cid))["revision"],
            }
        )
        if second < 4:
            await LockSpellService(play).execute(cid, complete, principal_id="gm")
    before = await play.store.read(cid)
    history = await play.store.history(cid)
    stream = await play.store.stream(cid)
    play.rng = RecordedDice(())
    # Approved14 - distance10 - Symptoms2 =2. No check or energy payment is legal.
    with pytest.raises(ValidationError, match="at least 3"):
        await LockSpellService(play).execute(cid, complete, principal_id="gm")
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history and await play.store.stream(cid) == stream
    resources = play._load(before).resources
    fp = next(p.current for p in resources.pools if p.id == "fp:a")
    assert latest(resources)["ward"].phase == "casting"
    cancel = complete.model_copy(update={"id": "cancel-unavailable-cast", "kind": "cancel"})
    result = await LockSpellService(play).execute(cid, cancel, principal_id="gm")
    assert result.outcome == "cancelled" and result.energy_spent == 0 and result.checks == ()
    after = await play.store.read(cid)
    resources = play._load(after).resources
    assert latest(resources)["ward"].phase == "ended"
    assert next(p.current for p in resources.pools if p.id == "fp:a") == fp
    assert await LockSpellService(play).execute(cid, cancel, principal_id="gm") == result
    assert await play.store.read(cid) == after == await play.store.replay(cid)
