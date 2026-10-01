"""B238/B480-482: real paid casts retain subjects and renew only while awake."""

import secrets
from collections.abc import Callable
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_magic_item_execution import item_campaign
from test_magic_item_lifecycle import binding

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import ManaLevel
from wayfarer.engine.rules.types.object import ObjectCondition, ObjectProfile
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.health.survival import (
    BeginSleep,
    BeginSurvival,
    SurvivalContext,
    begin_sleep,
    begin_survival,
)
from wayfarer.engine.simulation.magic.effects import dazed
from wayfarer.engine.simulation.magic.power_lifecycle import checkpoint, origins
from wayfarer.engine.simulation.magic.spell_state import active_spells, break_daze, latest
from wayfarer.engine.simulation.magic.spells import SpellCommand
from wayfarer.errors import AuthorizationError, ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.replay import verify_commands


async def prepare(
    path: Path,
    backend: str,
    *,
    mana: ManaLevel = "normal",
    reduction: int = 2,
    completed: bool = False,
    seeded: bool = False,
) -> tuple[str, PlayService, SpellCommand]:
    cid, play = await item_campaign(path, backend, power=20, mana=mana, reduction=reduction)
    old = play._load(await play.store.read(cid))
    assert play.engine.rules.spells is not None
    rules = play.engine.rules.model_copy(
        update={
            "maximum_wait": 10000,
            "spells": play.engine.rules.spells.model_copy(
                update={"magic_items": () if completed else play.engine.rules.spells.magic_items}
            ),
        }
    )
    play.engine.resources.specs["equipment:sword"] = play.engine.resources.specs[
        "equipment:sword"
    ].model_copy(
        update={"durability": ObjectProfile(construction="homogenous", hp=10, dr=0, ht=12)}
    )
    engine = ActionEngine(play.engine.reviewer, play.engine.resources, rules)
    play = build_play(path, engine, backend=backend, store=play.store, rng=RecordedDice(()))
    initial = campaign(engine)
    items = tuple(
        i.model_copy(
            update={
                "enchantments": (
                    binding("daze", item_id="blade", spell="daze").model_copy(
                        update={"maximum_charges": 1, "charges": 1}
                    ),
                    binding("power", item_id="blade", spell="power", reduction=reduction),
                )
            }
        )
        if completed and i.id == "blade"
        else i
        for i in old.resources.items
    )
    items = tuple(
        i.model_copy(update={"condition": ObjectCondition(hp=10)}) if i.id == "blade" else i
        for i in items
    )
    state = play.initial_state(
        initial,
        old.world,
        old.resources.model_copy(update={"items": items}),
        tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in old.actors),
    )
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    if seeded:
        play = build_play(path, play.engine, store=play.store, rng=secrets)
    return (
        cid,
        play,
        SpellCommand(
            id="start",
            actor_id="a",
            expected_revision=0,
            kind="start",
            spell_id="daze",
            channel_id="item-daze",
            cast_id="cast",
        ),
    )


async def cast_item(
    cid: str,
    play: PlayService,
    command: SpellCommand,
    *,
    seeded: bool = False,
    dice: tuple[int, ...] = (3, 3, 3, 6, 6, 6),
) -> None:
    spells = SpellService(play)
    await spells.execute(cid, command, principal_id="a")
    await play.execute(
        cid, Wait(id="one", actor_id="a", expected_revision=1, ticks=1), principal_id="a"
    )
    await spells.execute(
        cid,
        command.model_copy(
            update={"id": "concentrate", "kind": "concentrate", "expected_revision": 2}
        ),
        principal_id="a",
    )
    await play.execute(
        cid, Wait(id="two", actor_id="a", expected_revision=3, ticks=1), principal_id="a"
    )
    if not seeded:
        play.rng = RecordedDice(dice)
    await SpellService(play).execute(
        cid,
        command.model_copy(update={"id": "complete", "kind": "complete", "expected_revision": 4}),
        principal_id="a",
    )


async def advance(cid: str, play: PlayService, to: int) -> None:
    state = play._load(await play.store.read(cid))
    await play.execute(
        cid,
        Wait(
            id=f"to-{to}",
            actor_id="a",
            expected_revision=state.revision,
            ticks=to - state.resources.game_time,
        ),
        principal_id="a",
    )


async def change(
    cid: str, play: PlayService, name: str, modify: Callable[[PlayState], PlayState]
) -> PlayState:
    before = play._load(await play.store.read(cid))

    def resolve(campaign: Campaign) -> CommandReceipt:
        changed = modify(before)
        changed = changed.model_copy(
            update={
                "revision": before.revision + 1,
                "resources": changed.resources.model_copy(update={"revision": before.revision + 1}),
            }
        )
        changed = play.checkpoint(changed, before=before)
        play.commit(campaign, changed)
        return CommandReceipt(action="resource", outcome=name)

    await play.store.commit_turn(cid, name, before.revision, name, resolve, actor_id="gm")
    return play._load(await play.store.read(cid))


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("mana", "reduction", "completed"),
    [("normal", 2, False), ("high", 1, False), ("low", 4, True), ("normal", 2, True)],
)
async def test_b480_paid_daze_renews_original_subject_across_restart_and_intervals(
    tmp_path: Path, backend: str, mana: ManaLevel, reduction: int, completed: bool
) -> None:
    cid, play, command = await prepare(
        tmp_path, backend, mana=mana, reduction=reduction, completed=completed
    )
    before = await play.store.read(cid)
    with pytest.raises(AuthorizationError):
        await SpellService(play).execute(cid, command, principal_id="b")
    assert await play.store.read(cid) == before
    await cast_item(cid, play, command)
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["cast"]
    assert (
        effect.cost,
        effect.maintenance,
        effect.target_id,
        effect.ready_at,
        effect.expires_at,
    ) == (1, 0, "b", 2, 62)
    assert dazed(state.resources, "b") and not dazed(state.resources, "a")
    assert len(origins(state.resources)) == 1
    if completed:
        item = next(i for i in state.resources.items if i.id == "blade")
        assert item.enchantments[0].charges == 0
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    play = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    for to, expiry in ((61, 62), (62, 122), (122, 182), (303, 362)):
        await advance(cid, play, to)
        state = play._load(await play.store.read(cid))
        assert latest(state.resources)["cast"].expires_at == expiry
        assert dazed(state.resources, "b") and not dazed(state.resources, "a")
        assert len(active_spells(state.resources)) == 1
        assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
        assert checkpoint(play.rules_context, state, before=state) == state
    retry = command.model_copy(
        update={"id": "complete", "kind": "complete", "expected_revision": 4}
    )
    done = await SpellService(play).execute(cid, retry, principal_id="a")
    assert done.energy_spent == 1 and len(done.checks) == 2
    with pytest.raises(ConflictError):
        await SpellService(play).execute(
            cid, command.model_copy(update={"id": "stale"}), principal_id="a"
        )
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    "reason",
    [
        "sleep",
        "unequip",
        "transfer",
        "breakage",
        "source-loss",
        "binding-replaced",
        "approval",
        "daze-break",
    ],
)
async def test_b238_b480_current_authority_and_breaks_prevent_resurrection(
    tmp_path: Path, backend: str, reason: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend, completed=True)
    await cast_item(cid, play, command)
    await advance(cid, play, 20)

    def lose(state: PlayState) -> PlayState:
        resources = state.resources
        if reason == "sleep":
            context = SurvivalContext(profile_id="gurps-basic-set-4e-2004", ht=10, will=10)
            resources, _ = begin_survival(
                resources,
                BeginSurvival(id="needs", actor_id="a", expected_revision=resources.revision),
                context,
                system=True,
            )
            resources, _ = begin_sleep(
                resources,
                BeginSleep(
                    id="nap", actor_id="a", expected_revision=resources.revision, seconds=3600
                ),
                context,
                system=True,
            )
        elif reason == "daze-break":
            resources = break_daze(resources, "b", "injury")
        elif reason == "approval":
            return state.model_copy(
                update={
                    "actors": tuple(
                        a.model_copy(update={"approval": None}) if a.actor_id == "a" else a
                        for a in state.actors
                    )
                }
            )
        else:
            items = []
            for item in resources.items:
                if item.id == "blade":
                    if reason == "unequip":
                        item = item.model_copy(update={"ready": False, "equipped": False})
                    elif reason == "transfer":
                        item = item.model_copy(
                            update={
                                "owner_id": "b",
                                "enchantments": tuple(
                                    e.model_copy(update={"owner_id": "b"})
                                    for e in item.enchantments
                                ),
                            }
                        )
                    elif reason == "breakage":
                        item = item.model_copy(
                            update={
                                "condition": ObjectCondition(hp=0, disabled=True),
                                "ready": False,
                            }
                        )
                    elif reason == "source-loss":
                        item = item.model_copy(
                            update={
                                "enchantments": tuple(
                                    e for e in item.enchantments if e.spell_id != "power"
                                )
                            }
                        )
                    else:
                        item = item.model_copy(
                            update={
                                "enchantments": tuple(
                                    e.model_copy(update={"id": "replacement"})
                                    if e.spell_id == "daze"
                                    else e
                                    for e in item.enchantments
                                )
                            }
                        )
                items.append(item)
            resources = resources.model_copy(update={"items": tuple(items)})
        return state.model_copy(update={"resources": resources})

    original = play._load(await play.store.read(cid))
    changed = await change(cid, play, "loss", lose)
    if reason == "sleep":
        assert dazed(changed.resources, "b")  # Sleep cannot erase the paid interval.
    else:
        assert not dazed(changed.resources, "b")
    # Use a trusted clock change for sleep/approval cases where the actor cannot act.
    await change(
        cid,
        play,
        "deadline",
        lambda s: s.model_copy(
            update={"resources": s.resources.model_copy(update={"game_time": 62})}
        ),
    )
    expired = play._load(await play.store.read(cid))
    assert not dazed(expired.resources, "b")

    def restore(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "actors": original.actors,
                "resources": state.resources.model_copy(
                    update={
                        "items": original.resources.items,
                        "survival_tasks": (),
                        "game_time": 63,
                    }
                ),
            }
        )

    restored = await change(cid, play, "restore", restore)
    assert not dazed(restored.resources, "b")
    assert latest(restored.resources)["cast"].phase == "ended"
    assert next(p.current for p in restored.resources.pools if p.id == "fp:a") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b480_power_commands_reexecute_from_persisted_entropy(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend, seeded=True)
    initial = (await play.store.history(cid))[0].state_after
    await cast_item(cid, play, command, seeded=True)
    state = play._load(await play.store.read(cid))
    assert dazed(state.resources, "b")
    await advance(cid, play, 182)
    records = await played(play.store, cid)
    final, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks)
    assert final == await play.store.read(cid)
    assert dazed(play._load(final).resources, "b")


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b480_zero_cast_outward_effect_is_explicitly_unsupported(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend, reduction=3)
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="supported wearer effect"):
        await SpellService(play).execute(cid, command, principal_id="a")
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b238_power_spell_still_counts_on_and_cancellation_is_terminal(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend)
    await cast_item(cid, play, command)
    await advance(cid, play, 122)
    state = play._load(await play.store.read(cid))
    await SpellService(play).execute(
        cid,
        command.model_copy(
            update={"id": "second", "cast_id": "second", "expected_revision": state.revision}
        ),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["second"].skill == 19
    cancelled = await SpellService(play).execute(
        cid,
        command.model_copy(
            update={"id": "off", "kind": "cancel", "expected_revision": state.revision}
        ),
        principal_id="a",
    )
    assert cancelled.energy_spent == 1
    await advance(cid, play, 182)
    state = play._load(await play.store.read(cid))
    assert not dazed(state.resources, "b")
    assert latest(state.resources)["cast"].phase == "ended"
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 8


@pytest.mark.parametrize("mana", ["none", "low"])
async def test_b481_mana_loss_stops_support_without_destroying_enchantment(
    tmp_path: Path, mana: ManaLevel
) -> None:
    from dataclasses import replace

    from wayfarer.engine.simulation.magic.item_state import losses, usable_item_enchantment

    cid, play, command = await prepare(tmp_path, "sqlite", completed=True)
    await cast_item(cid, play, command)
    state = play._load(await play.store.read(cid))
    rules = play.engine.rules.spells
    assert rules is not None
    changed = replace(
        play.rules_context,
        rules=play.engine.rules.model_copy(
            update={
                "spells": rules.model_copy(
                    update={
                        "channels": tuple(
                            c.model_copy(update={"mana": mana}) for c in rules.channels
                        )
                    }
                )
            }
        ),
    )
    ended = checkpoint(changed, state, before=state)
    assert not dazed(ended.resources, "b")
    assert not losses(ended.resources)
    item = next(i for i in ended.resources.items if i.id == "blade")
    assert usable_item_enchantment(ended.resources, item.enchantments[0], "normal")
    assert checkpoint(play.rules_context, ended, before=ended) == ended


def test_b247_numeric_zero_is_not_a_maintainable_spell() -> None:
    from test_spells import context, state

    from wayfarer.engine.rules.magic.protocols import MagicItemBinding
    from wayfarer.engine.simulation.magic.bindings import SpellChannel
    from wayfarer.engine.simulation.magic.power_lifecycle import remember
    from wayfarer.engine.simulation.magic.spells import apply_spell
    from wayfarer.engine.simulation.resources import Item

    command = SpellCommand(
        id="missile",
        actor_id="a",
        expected_revision=0,
        kind="start",
        spell_id="fireball",
        cast_id="missile",
        channel_id="missile",
    )
    resources = state().model_copy(
        update={"items": (Item(id="staff", definition_id="staff", owner_id="a", ready=True),)}
    )
    resources, _ = apply_spell(
        resources,
        command,
        context().model_copy(
            update={"item_cast": True, "item_power_reduction": 1, "energy": 2, "magery": 2}
        ),
        rng=RecordedDice(()),
        system=True,
    )
    assert latest(resources)["missile"].cost == 1
    assert latest(resources)["missile"].maintenance == 0
    channel = SpellChannel(
        id="missile",
        actor_id="a",
        target_id="b",
        location_id="room",
        spell_id="fireball",
        magic_item_id="staff",
    )
    assert (
        remember(
            resources,
            command,
            channel,
            (
                MagicItemBinding(
                    id="fire", item_id="staff", spell_id="fireball", power=20, power_reduction=1
                ),
            ),
        )
        == resources
    )
    assert not origins(resources)


def test_b480_manual_item_upkeep_uses_current_power_not_cached_zero() -> None:
    from test_spells import context, state

    from wayfarer.engine.simulation.magic.spells import apply_spell

    ctx = context().model_copy(update={"item_cast": True, "item_power_reduction": 2})
    command = SpellCommand(
        id="start", actor_id="a", expected_revision=0, kind="start", spell_id="daze", cast_id="daze"
    )
    started, _ = apply_spell(state(), command, ctx, rng=RecordedDice(()), system=True)
    active, _ = apply_spell(
        started.model_copy(update={"game_time": 2}),
        command.model_copy(update={"id": "complete", "expected_revision": 1, "kind": "complete"}),
        ctx,
        rng=RecordedDice((3, 3, 3, 6, 6, 6)),
        system=True,
    )
    assert latest(active)["daze"].maintenance == 0
    maintained, result = apply_spell(
        active.model_copy(update={"game_time": 62}),
        command.model_copy(update={"id": "maintain", "expected_revision": 2, "kind": "maintain"}),
        ctx.model_copy(update={"item_power_reduction": 0}),
        rng=RecordedDice(()),
        system=True,
    )
    assert result.energy_spent == 2
    assert latest(maintained)["daze"].maintenance == 2
    assert next(p.current for p in maintained.pools if p.id == "fp:a") == 7


@pytest.mark.parametrize(("wake_at", "continues"), [(61, True), (62, True), (63, False)])
async def test_b238_waking_can_renew_only_before_a_boundary_is_missed(
    tmp_path: Path, wake_at: int, continues: bool
) -> None:
    from wayfarer.engine.rules.types.survival import SurvivalTask
    from wayfarer.engine.simulation.health.sleep_state import wake_sleep

    cid, play, command = await prepare(tmp_path, "sqlite")
    await cast_item(cid, play, command)
    state = play._load(await play.store.read(cid))
    asleep = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "survival_tasks": (
                        SurvivalTask(id="nap", actor_id="a", kind="sleep", start=2, due=3602),
                    )
                }
            )
        }
    )
    sleeping = checkpoint(play.rules_context, asleep, before=state)
    assert dazed(sleeping.resources, "b")
    waking_resources = wake_sleep(sleeping.resources.model_copy(update={"game_time": wake_at}), "a")
    waking = sleeping.model_copy(update={"resources": waking_resources})
    waking = checkpoint(play.rules_context, waking, before=sleeping)
    if wake_at < 62:
        waking = checkpoint(
            play.rules_context,
            waking.model_copy(
                update={"resources": waking.resources.model_copy(update={"game_time": 62})}
            ),
            before=waking,
        )
    assert dazed(waking.resources, "b") == continues
    assert latest(waking.resources)["cast"].expires_at == (122 if continues else 62)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize(
    ("dice", "outcome", "checks"), [((6, 6, 5), "failed", 1), ((5, 5, 5, 1, 1, 1), "resisted", 2)]
)
async def test_b482_failed_and_resisted_casts_pay_once_without_power_activation(
    tmp_path: Path, backend: str, dice: tuple[int, ...], outcome: str, checks: int
) -> None:
    cid, play, command = await prepare(tmp_path, backend)
    await cast_item(cid, play, command, dice=dice)
    result = await SpellService(play).execute(
        cid,
        command.model_copy(update={"id": "complete", "kind": "complete", "expected_revision": 4}),
        principal_id="a",
    )
    assert result.outcome == outcome and result.energy_spent == 1
    assert len(result.checks) == checks
    await advance(cid, play, 182)
    state = play._load(await play.store.read(cid))
    assert not dazed(state.resources, "b")
    assert latest(state.resources)["cast"].phase == "ended"
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 9
    assert await play.store.read(cid) == await play.store.replay(cid)


async def test_b480_genesis_cannot_forge_free_maintenance_provenance(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.resources import ResourceEvent

    cid, play, _ = await prepare(tmp_path, "sqlite")
    initial = play._load(await play.store.read(cid))
    forged = initial.resources.model_copy(
        update={
            "events": (
                ResourceEvent(id="power-cast-origin:forged", at=0, target_id="a", kind="{}"),
            )
        }
    )
    with pytest.raises(ValidationError, match="genesis|initial|authored|runtime|execution|trusted"):
        play.initial_state(
            campaign(play.engine),
            initial.world,
            forged,
            tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in initial.actors),
        )


async def test_b250_injury_at_renewal_boundary_still_breaks_daze(tmp_path: Path) -> None:
    from wayfarer.engine.simulation.health.injury import Wound, apply_injury

    cid, play, command = await prepare(tmp_path, "sqlite")
    await cast_item(cid, play, command)

    def injure_at_boundary(state: PlayState) -> PlayState:
        resources, _ = apply_injury(
            state.resources.model_copy(update={"game_time": 62}),
            Wound(
                id="wound",
                actor_id="b",
                expected_revision=state.resources.revision,
                basic_damage=1,
                damage_type="cr",
                resistance=0,
            ),
            ht=10,
            rng=RecordedDice(()),
            system=True,
        )
        return state.model_copy(update={"resources": resources})

    ended = await change(cid, play, "wound", injure_at_boundary)
    assert next(p.current for p in ended.resources.pools if p.id == "hp:b") == 9
    assert not dazed(ended.resources, "b")
    assert latest(ended.resources)["cast"].phase == "ended"


async def test_b480_cast_cannot_switch_to_a_replacement_item_binding(tmp_path: Path) -> None:
    cid, play, command = await prepare(tmp_path, "sqlite", completed=True)
    await SpellService(play).execute(cid, command, principal_id="a")

    def replace_binding(state: PlayState) -> PlayState:
        items = tuple(
            item.model_copy(
                update={
                    "enchantments": tuple(
                        e.model_copy(update={"id": "new-daze"}) if e.spell_id == "daze" else e
                        for e in item.enchantments
                    )
                }
            )
            if item.id == "blade"
            else item
            for item in state.resources.items
        )
        return state.model_copy(
            update={
                "resources": state.resources.model_copy(update={"items": items, "game_time": 1})
            }
        )

    await change(cid, play, "re-enchant", replace_binding)
    before = await play.store.read(cid)
    with pytest.raises(ConflictError, match="original item and spell binding"):
        await SpellService(play).execute(
            cid,
            command.model_copy(
                update={"id": "concentrate", "kind": "concentrate", "expected_revision": 2}
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b480_reenchantment_spends_only_the_admitted_fresh_charge(
    tmp_path: Path, backend: str
) -> None:
    from wayfarer.engine.simulation.magic.item_state import item_magic_lost
    from wayfarer.engine.simulation.resources import ResourceState

    cid, play, original = await prepare(tmp_path, backend, completed=True)
    await SpellService(play).execute(cid, original, principal_id="a")
    await SpellService(play).execute(
        cid,
        original.model_copy(update={"id": "cancel-old", "kind": "cancel", "expected_revision": 1}),
        principal_id="a",
    )

    def break_item(state: PlayState) -> PlayState:
        items = tuple(
            item.model_copy(
                update={"condition": ObjectCondition(hp=0, disabled=True), "ready": False}
            )
            if item.id == "blade"
            else item
            for item in state.resources.items
        )
        return state.model_copy(
            update={"resources": state.resources.model_copy(update={"items": items})}
        )

    broken = await change(cid, play, "break-old", break_item)
    assert item_magic_lost(broken.resources, "blade", "daze")
    assert item_magic_lost(broken.resources, "blade", "power")

    def reenchant(state: PlayState) -> PlayState:
        fresh = binding("fresh-daze", item_id="blade", spell="daze").model_copy(
            update={"maximum_charges": 1, "charges": 1}
        )
        power = binding("fresh-power", item_id="blade", spell="power", reduction=2)
        items = tuple(
            item.model_copy(
                update={
                    "condition": ObjectCondition(hp=10),
                    "ready": True,
                    "enchantments": item.enchantments + (fresh, power),
                }
            )
            if item.id == "blade"
            else item
            for item in state.resources.items
        )
        return state.model_copy(
            update={"resources": state.resources.model_copy(update={"items": items})}
        )

    repaired = await change(cid, play, "reenchant", reenchant)
    before_item = next(item for item in repaired.resources.items if item.id == "blade")
    historical = next(item for item in before_item.enchantments if item.id == "daze")
    assert historical.charges == 0
    assert next(item for item in before_item.enchantments if item.id == "fresh-daze").charges == 1
    assert not item_magic_lost(repaired.resources, "blade", "fresh-daze")
    fresh_cast = original.model_copy(
        update={"id": "fresh-start", "cast_id": "fresh-cast", "expected_revision": 4}
    )
    result = await SpellService(play).execute(cid, fresh_cast, principal_id="a")
    recorded = await play.store.read(cid)
    after = play._load(recorded)
    item = next(item for item in after.resources.items if item.id == "blade")
    assert next(e for e in item.enchantments if e.id == "daze") == historical
    assert next(e.charges for e in item.enchantments if e.id == "fresh-daze") == 0
    assert (
        next(o for o in origins(after.resources) if o.cast_id == "fresh-cast").binding_id
        == "fresh-daze"
    )
    assert ResourceState.model_validate_json(after.resources.model_dump_json()) == after.resources
    restarted = build_play(tmp_path, play.engine, store=play.store, rng=RecordedDice(()))
    assert await SpellService(restarted).execute(cid, fresh_cast, principal_id="a") == result
    assert await play.store.read(cid) == recorded == await play.store.replay(cid)
