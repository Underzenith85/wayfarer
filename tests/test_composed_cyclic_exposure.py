"""B61/B103/B378: delivered Cyclic exposure is independent of initial FP loss."""

from pathlib import Path

import pytest
from support.runtime import build_play
from test_combat_sensory_authority import change
from test_composed_attack_host import declare, defense, fixture
from test_composed_cyclic_boundaries import cyclic

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.traits.modifiers import LimitationParameters, ModifierSelection
from wayfarer.engine.rules.types.hazard import blocked_fp
from wayfarer.engine.simulation.actions import PlayState, Wait
from wayfarer.engine.simulation.combat.commands import EndEncounter
from wayfarer.engine.simulation.health.cyclic_host_state import CyclicPolicy, binding
from wayfarer.orchestration.combat import CombatService


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_b103_absorbed_fatigue_delivery_repeats_against_current_dr_and_retries(
    tmp_path: Path, backend: str
) -> None:
    cid, play, source = await fixture(
        tmp_path,
        backend,
        kind="fat",
        modifiers=(cyclic("fat"),),
        levels=1,
        dr=3,
        armor=True,
        cyclic_policy=CyclicPolicy(condition="wash"),
    )
    await declare(play, cid, source)
    # Natural DR3 + worn DR2 absorbs the actual 1d roll of2. B103 still
    # establishes a nonimmune exposure, with no fabricated FP recovery debt.
    play.rng = RecordedDice((2, 2, 2, 2))
    defend, result = await defense(play, cid)
    delivered = play._load(await play.store.read(cid))
    assert len(delivered.resources.cyclic_attacks) == 1
    occurrence = delivered.resources.cyclic_attacks[0]
    assert (
        occurrence.basic_damage,
        occurrence.resistance,
        occurrence.fp_debt,
        occurrence.hp_debt,
        occurrence.remaining,
        occurrence.due,
    ) == (2, 5, 0, 0, 2, 10)
    bound = binding(delivered.resources, occurrence.id)
    assert bound and bound.source_id == source
    assert next(p.current for p in delivered.resources.pools if p.id == "fp:b") == 10
    assert blocked_fp(delivered.resources.illnesses, "b") == 0
    assert play.rng.exhausted()

    await CombatService(play).execute(
        cid,
        EndEncounter(
            id="end",
            actor_id="gm",
            expected_revision=delivered.revision,
            encounter_id="fight",
            reason="The attack has ended; its delivered exposure continues",
        ),
        principal_id="gm",
    )
    await change(
        play,
        cid,
        lambda state: state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={
                        "items": tuple(
                            i.model_copy(update={"equipped": False}) if i.id == "armor-b" else i
                            for i in state.resources.items
                        )
                    }
                )
            }
        ),
    )
    before_repeat = play._load(await play.store.read(cid))
    wait = Wait(
        id="first-repeat",
        actor_id="b",
        expected_revision=before_repeat.revision,
        ticks=occurrence.due - before_repeat.resources.game_time,
    )
    # B103 uses current DR3 after removing armor, so the next 1d roll of5
    # spends2FP. Reusing the original DR5 would incorrectly spend none.
    play.rng = RecordedDice((5,))
    repeated = await play.execute(cid, wait, principal_id="b")
    assert repeated.status == "committed"
    final = await play.store.read(cid)
    state = play._load(final)
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 8
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    after = state.resources.cyclic_attacks[0]
    assert (after.resistance, after.fp_debt, after.hp_debt, after.remaining) == (3, 2, 0, 1)
    assert blocked_fp(state.resources.illnesses, "b") == 2
    assert play.rng.exhausted()

    restarted = build_play(tmp_path, play.engine, backend=backend, rng=RecordedDice(()))
    assert await CombatService(restarted).execute(cid, defend, principal_id="bob") == result
    assert await restarted.execute(cid, wait, principal_id="b") == repeated
    assert await restarted.store.read(cid) == final == await restarted.store.replay(cid)
    assert isinstance(restarted.rng, RecordedDice) and restarted.rng.exhausted()


@pytest.mark.parametrize("exclusion", ["machine", "miss", "defense", "resistance"])
async def test_b61_b103_undelivered_or_immune_fatigue_never_schedules_cycles(
    tmp_path: Path, exclusion: str
) -> None:
    modifiers: tuple[ModifierSelection, ...] = (cyclic("fat"),)
    if exclusion == "resistance":
        modifiers += (
            ModifierSelection(
                definition_id="modifier:limitation:resistible",
                option="ht+0",
                limitation=LimitationParameters(resistance_modifier=0),
            ),
        )
    cid, play, source = await fixture(
        tmp_path,
        kind="fat",
        modifiers=modifiers,
        levels=1,
        cyclic_policy=CyclicPolicy(condition="wash"),
    )
    if exclusion == "machine":

        def machine(state: PlayState) -> PlayState:
            return state.model_copy(
                update={
                    "resources": state.resources.model_copy(
                        update={
                            "pools": tuple(
                                p.model_copy(
                                    update={"injury": p.injury.model_copy(update={"machine": True})}
                                )
                                if p.id == "hp:b" and p.injury
                                else p
                                for p in state.resources.pools
                            )
                        }
                    )
                }
            )

        await change(play, cid, machine)
    await declare(play, cid, source)
    dice = {
        "machine": (2, 2, 2),
        "miss": (3, 3, 3),
        "defense": (2, 2, 2, 2, 2, 2),
        "resistance": (2, 2, 2, 2, 2, 2),
    }
    play.rng = RecordedDice(dice[exclusion])
    await defense(play, cid, selected="dodge" if exclusion == "defense" else "none")
    state = play._load(await play.store.read(cid))
    assert not state.resources.cyclic_attacks and not state.resources.illnesses
    assert next(p.current for p in state.resources.pools if p.id == "fp:b") == 10
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 10
    assert play.rng.exhausted()
