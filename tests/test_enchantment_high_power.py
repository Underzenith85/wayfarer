"""Issue #1010: B481-482 item Power has no printed upper bound of 100.

The reviewed enchanting rules own the arithmetic. These cases exercise real
approved builds, manufacture, and item casting, without promoting certification.
"""

import asyncio
import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import build_play, seed_campaign
from test_actions import campaign
from test_enchanting_projects import create, setup
from test_enchanting_settlement import assert_reexec, begin
from test_enchanting_settlement import settle as settlement
from test_enchanting_source import QUICK_ENERGY, recipe_runtime, settle, start
from test_statistics import profile_package

from wayfarer.engine.character.compiler import CharacterCompiler
from wayfarer.engine.character.power import PowerReviewer
from wayfarer.engine.rules.catalog import RulesCatalog
from wayfarer.engine.rules.checks import Outcome, RecordedDice
from wayfarer.engine.rules.magic.enchantment import package as enchantment_package
from wayfarer.engine.rules.magic.protocols import MagicItemBinding, ManaLevel
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    CALENDAR_DAY,
    MAGE_DAY,
    AdvanceEnchanting,
    BeginEnchanting,
    CreateEnchantment,
    apply_enchantment,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.simulation.magic.spell_transitions import approved_context
from wayfarer.engine.simulation.magic.spells import SpellCommand, casting_seconds
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.enchantments import EnchantmentService
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

PROFILE = "gurps-basic-set-4e-2004"


def test_unbounded_skill_keeps_the_casting_floor_and_missile_exception() -> None:
    assert casting_seconds(8, 10**100) == 1
    assert casting_seconds(8, 10**100, missile=True) == 8


def high_power_setup(
    path: Path, points: int = 344, mana: ManaLevel = "normal"
) -> tuple[ActionEngine, RulesContext, PlayState]:
    original, runtime, foundation = setup(path, method="quick-and-dirty", hold_target=True)
    old = original.reviewer.compiler
    base = profile_package(PROFILE)
    package = replace(
        base,
        definitions=tuple(old.definitions.values()),
        sources=base.sources
        + tuple(
            s for s in enchantment_package().sources if s.id not in {r.id for r in base.sources}
        ),
    )
    assert package.digest == old.rules.packages[0].digest
    catalog = RulesCatalog((package,))
    compiler = CharacterCompiler(
        catalog,
        old.rules,
        replace(old.policy, point_budget=1000, skill_ceiling=120),
        statistics_profile=PROFILE,
    )
    reviewer = PowerReviewer(compiler, original.reviewer.policy, original.reviewer.gm_ids)
    resources = ResourceEngine(
        foundation.world,
        catalog,
        compiler.rules,
        compiler.policy,
        tuple(original.resources.specs.values()),
    )
    runtime = recipe_runtime(replace(runtime, reviewer=reviewer, resources=resources), mana=mana)
    engine = ActionEngine(reviewer, resources, runtime.rules)
    actors = []
    for actor in foundation.actors:
        draft = actor.proposal.draft.model_copy(
            update={
                "purchases": tuple(
                    p.model_copy(
                        update={
                            "amount": points if p.definition_id == "spell:enchant" else points - 4
                        }
                    )
                    if p.definition_id in ("spell:enchant", "spell:light")
                    else p
                    for p in actor.proposal.draft.purchases
                )
            }
        )
        actors.append(
            ActorSetup(
                actor_id=actor.actor_id, proposal=actor.proposal.model_copy(update={"draft": draft})
            )
        )
    play = PlayService(AsyncSQLiteStore(path / "unused-high-power.sqlite", 10), engine)
    state = play.initial_state(
        campaign(engine), foundation.world, foundation.resources, tuple(actors)
    )
    engine.validate(state)
    return engine, runtime, state


@pytest.mark.parametrize("power", [100, 101, 112, 1000])
def test_positive_item_power_round_trips_without_an_invented_cap(power: int) -> None:
    binding = MagicItemBinding(id="item-light", item_id="blade", spell_id="light", power=power)
    assert MagicItemBinding.model_validate_json(binding.model_dump_json()) == binding


@pytest.mark.parametrize("power", [0, -1, True, 1.5, "101"])
def test_item_power_still_requires_a_strict_positive_integer(power: object) -> None:
    with pytest.raises(SchemaError):
        MagicItemBinding.model_validate(
            dict(id="x", item_id="blade", spell_id="light", power=power)
        )


@pytest.mark.parametrize("mana", ["normal", "low"])
@pytest.mark.parametrize(
    "points,dice,rating,target",
    [
        (340, (3, 3, 3), 100, 100),
        (344, (3, 3, 3), 101, 101),
        (336, (2, 2, 2, 3, 4), 106, 99),
        (344, (2, 2, 2, 3, 4), 108, 101),
    ],
)
def test_actual_manufacture_preserves_high_power_and_critical_bonus(
    tmp_path: Path, mana: ManaLevel, points: int, dice: tuple[int, ...], rating: int, target: int
) -> None:
    engine, runtime, state = high_power_setup(tmp_path, points, mana)
    recorded = RecordedDice(dice)
    runtime = replace(runtime, rng=recorded)
    state, result, command = settle(runtime, start(runtime, state, QUICK_ENERGY))
    assert result.status == "completed" and result.check is not None
    assert result.check.effective_target == target - (5 if mana == "low" else 0)
    assert result.check.outcome is (Outcome.CRITICAL_SUCCESS if len(dice) == 5 else Outcome.SUCCESS)
    item = next(i for i in state.resources.items if i.id == "blade")
    assert item.enchantments[0].power == rating
    for actor in ("a", "b"):
        assert next(p.current for p in state.resources.pools if p.id == "fp:" + actor) == 8
    restored = PlayState.model_validate_json(state.model_dump_json())
    engine.validate(restored)
    assert apply_enchantment(runtime, restored, command, system=True) == (restored, result)
    assert recorded.exhausted()


@pytest.mark.parametrize("extra,rating,days", [(320, 100, 162), (324, 101, 164), (368, 112, 186)])
def test_extra_energy_raises_ordinary_enchanters_past_100(
    tmp_path: Path, extra: int, rating: int, days: int
) -> None:
    engine, runtime, state = setup(tmp_path)
    runtime = replace(runtime, rng=RecordedDice((3, 3, 3)))
    state, _ = create(runtime, state)
    state, _ = apply_enchantment(
        runtime,
        state,
        BeginEnchanting(
            id="begin",
            actor_id="a",
            expected_revision=state.revision,
            project_id="project",
            extra_energy=extra,
        ),
        system=True,
    )
    work = state.resources.enchantment_projects[0].active_work
    assert work is not None and work.due == (days - 1) * CALENDAR_DAY + MAGE_DAY
    state, result, command = settle(runtime, state)
    assert result.status == "completed" and result.check is not None
    assert result.check.base_target == 17 and result.check.effective_target == rating
    assert next(i for i in state.resources.items if i.id == "blade").enchantments[0].power == rating
    assert [(p.id, p.current) for p in state.resources.pools] == [
        (p.id, p.maximum) for p in state.resources.pools
    ]
    restored = PlayState.model_validate_json(state.model_dump_json())
    engine.validate(restored)
    assert apply_enchantment(runtime, restored, command, system=True) == (restored, result)


@pytest.mark.integration
@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("seed,rating", [(1, 101), (270, 112)])
@pytest.mark.parametrize("quick", [False, True])
async def test_high_power_manufacture_cast_restart_and_original_seed_replay(
    tmp_path: Path, backend: str, seed: int, rating: int, quick: bool
) -> None:
    engine, _, foundation = (
        high_power_setup(tmp_path) if quick else setup(tmp_path, hold_target=True)
    )
    play = build_play(
        tmp_path / "host", engine, backend=backend, rng=secrets, seeds=lambda: f"{seed:064x}"
    )
    initial = campaign(engine)
    genesis = play.initial_state(
        initial,
        foundation.world,
        foundation.resources,
        tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in foundation.actors),
    )
    initial["play_json"] = genesis.model_dump_json()
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    service = EnchantmentService(play)
    if quick:
        await begin(play, cid, quick=True)
    else:
        await service.execute(
            cid,
            CreateEnchantment(
                id="create",
                actor_id="a",
                expected_revision=0,
                project_id="project",
                recipe_id="light-blade",
                target_item_id="blade",
                enchanter_ids=("a", "b"),
            ),
            principal_id="gm",
        )
        await service.execute(
            cid,
            BeginEnchanting(
                id="begin",
                actor_id="a",
                expected_revision=1,
                project_id="project",
                extra_energy=324,
            ),
            principal_id="gm",
        )
    due = 3600 if quick else 163 * CALENDAR_DAY + MAGE_DAY
    await service.execute(
        cid,
        AdvanceEnchanting(
            id="clock",
            actor_id="a",
            expected_revision=2,
            project_id="project",
            work_id="begin",
            to=due,
        ),
        principal_id="gm",
    )
    before = await play.store.read(cid)
    with pytest.raises(ValidationError, match="director"):
        await service.execute(cid, settlement(), principal_id="a")
    with pytest.raises(ConflictError):
        await service.execute(cid, settlement(2), principal_id="gm")
    assert await play.store.read(cid) == before
    command = settlement()
    first, duplicate = await asyncio.gather(
        service.execute(cid, command, principal_id="gm"),
        service.execute(cid, command, principal_id="gm"),
    )
    assert first == duplicate and first.status == "completed" and first.check is not None
    assert first.check.effective_target == 101
    state = play._load(await play.store.read(cid))
    assert next(i for i in state.resources.items if i.id == "blade").enchantments[0].power == rating
    restarted = build_play(tmp_path / "restart", engine, store=play.store, rng=RecordedDice(()))
    assert await EnchantmentService(restarted).execute(cid, command, principal_id="gm") == first
    # Item Power supplies the roll target, with ordinary item costs and casting time.
    play.seeds = lambda: f"{1:064x}"
    cast = SpellCommand(
        id="item-start",
        actor_id="a",
        expected_revision=state.revision,
        kind="start",
        spell_id="light",
        cast_id="high-light",
        channel_id="item-light",
    )
    assert approved_context(play.rules_context, state, cast).skill == rating
    started = await SpellService(play).execute(cid, cast, principal_id="gm")
    assert started.outcome == "casting"
    state = play._load(await play.store.read(cid))
    effect = latest(state.resources)["high-light"]
    assert effect.skill == rating and effect.ready_at == due + 1 and effect.cost == 1
    assert next(i for i in state.resources.items if i.id == "blade").enchantments[0].charges == 1
    await play.execute(
        cid,
        Wait(id="item-time", actor_id="a", expected_revision=state.revision, ticks=1),
        principal_id="a",
    )
    state = play._load(await play.store.read(cid))
    complete = cast.model_copy(
        update={"id": "item-complete", "kind": "complete", "expected_revision": state.revision}
    )
    result = await SpellService(play).execute(cid, complete, principal_id="gm")
    assert len(result.checks) == 1 and result.checks[0].effective_target == rating
    assert result.energy_spent == 1
    state = play._load(await play.store.read(cid))
    assert latest(state.resources)["high-light"].phase == "active"
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (7 if quick else 9)
    assert await SpellService(play).execute(cid, complete, principal_id="gm") == result
    await assert_reexec(play, initial, state, tmp_path / "reexecute")
