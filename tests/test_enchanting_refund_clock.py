"""B235/B426: returned FP must precede later damage, without replay migration."""

import secrets
from pathlib import Path

import pytest
from support.runtime import build_play, seed_campaign
from test_actions import campaign
from test_enchanting_projects import advance, setup
from test_enchanting_settlement import assert_reexec
from test_enchanting_source import recipe_runtime, start

from wayfarer.contracts import Campaign
from wayfarer.engine.rules.types.cyclic import CyclicAttack, CyclicOccurrence
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActorSetup, PlayState, Wait
from wayfarer.engine.simulation.health.cyclic import save
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy, bind_occurrence
from wayfarer.engine.simulation.magic.backfires import ManaRefund
from wayfarer.engine.simulation.magic.enchanting import EnergyContribution
from wayfarer.engine.simulation.magic.enchanting_transitions import (
    SettleEnchanting,
    apply_enchantment,
)
from wayfarer.orchestration.play import PlayService


async def prepare_refund(
    path: Path, backend: str, *, cyclic: bool, legacy: bool
) -> tuple[PlayService, Campaign, PlayState]:
    original, runtime, foundation = setup(path, method="quick-and-dirty")
    runtime = recipe_runtime(runtime, mana="very-high")
    engine = ActionEngine(original.reviewer, original.resources, runtime.rules)
    play = build_play(path, engine, backend=backend, rng=secrets, seeds=lambda: format(3, "064x"))
    initial = campaign(engine)
    state = play.initial_state(
        initial,
        foundation.world,
        foundation.resources,
        tuple(ActorSetup(actor_id=a.actor_id, proposal=a.proposal) for a in foundation.actors),
    )
    state = start(
        runtime,
        state,
        contributions=(EnergyContribution(actor_id="a", fp=9), EnergyContribution(actor_id="b")),
    )
    state = advance(engine, state, 3600, "settlement-time")
    state, _ = apply_enchantment(
        runtime,
        state,
        SettleEnchanting(
            id="settle",
            actor_id="a",
            expected_revision=state.revision,
            project_id="project",
            work_id="begin",
        ),
        system=True,
    )
    resources = state.resources
    if legacy:
        resources = resources.model_copy(
            update={
                "events": tuple(
                    e.model_copy(
                        update={
                            "kind": ManaRefund.model_validate_json(e.kind)
                            .model_copy(update={"clock_generation": None})
                            .model_dump_json()
                        }
                    )
                    if e.id.startswith("mana-refund:")
                    else e
                    for e in resources.events
                )
            }
        )
    if cyclic:
        attack = CyclicAttack(
            id="saved-fatigue",
            attack_id="captured-fatigue-source",
            attacker_id="b",
            actor_id="a",
            basic_damage=2,
            damage_dice=1,
            damage_type="fat",
            resistance=0,
            ht=10,
            interval=10,
            remaining=1,
            due=3610,
            stop_condition="wash",
        )
        resources = bind_occurrence(
            save(resources, attack),
            attack.id,
            source_id=attack.attack_id,
            source_revision="captured-approved-source",
            policy=CyclicPolicy(condition="wash"),
        )
    state = state.model_copy(update={"resources": resources})
    engine.validate(state)
    play.commit(initial, state)
    await seed_campaign(play.store, initial)
    return play, initial, state


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("cyclic", [False, True])
@pytest.mark.parametrize("legacy", [False, True])
async def test_refund_precedes_later_fatigue_and_keeps_unmarked_record_timing(
    tmp_path: Path, backend: str, cyclic: bool, legacy: bool
) -> None:
    play, initial, state = await prepare_refund(tmp_path, backend, cyclic=cyclic, legacy=legacy)
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 1
    original = next(e for e in state.resources.events if e.id.startswith("mana-refund:"))
    refund = ManaRefund.model_validate_json(original.kind)
    assert refund.amount == 9 and refund.due_at == 3601
    assert refund.clock_generation == (None if legacy else 1)
    assert ("clock_generation" not in original.kind) is legacy
    command = Wait(
        id="cross-refund-and-damage", actor_id="b", expected_revision=state.revision, ticks=10
    )
    await play.execute(initial["id"], command, principal_id="b")
    saved = await play.store.read(initial["id"])
    final = play._load(saved)
    settled = next(
        e
        for e in final.resources.events
        if e.id.startswith("mana-refund:") and e.id.endswith(":settled")
    )
    assert settled.at == (3610 if legacy else 3601)
    # Seed 3's first die is 2. Refunding 9 before losing 2 leaves 8 FP and no HP
    # injury. The old ordering remains only for unmarked historical records.
    assert next(p.current for p in final.resources.pools if p.id == "fp:a") == (8 if cyclic else 10)
    assert next(p.current for p in final.resources.pools if p.id == "hp:a") == (
        9 if cyclic and legacy else 10
    )
    if cyclic:
        hit = CyclicOccurrence.model_validate_json(
            next(e.kind for e in final.resources.events if e.id.startswith("cyclic:"))
        )
        assert hit.at == 3610 and hit.fp_lost == 2 and hit.hp_lost == int(legacy)
    await play.execute(initial["id"], command, principal_id="b")
    assert await play.store.read(initial["id"]) == saved
    await assert_reexec(play, initial, state, tmp_path / "reexecuted")
