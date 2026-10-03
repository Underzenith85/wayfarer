"""The combined named Step keeps its source through actual Wait and Will−3."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_great_haste_named_step import prepare_named
from test_power_maintenance_lifecycle import change

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.combat.maneuvers import WaitTrigger
from wayfarer.engine.simulation.combat.unarmed.records import UnarmedReaction
from wayfarer.engine.simulation.hex_geometry import Hex
from wayfarer.engine.simulation.magic.great_haste_step_state import (
    CastingStep,
    NamedStepCastGreatHaste,
    distractions,
    leases,
)
from wayfarer.engine.simulation.magic.spell_state import latest
from wayfarer.engine.world import Fact
from wayfarer.errors import ConflictError
from wayfarer.orchestration.combat import (
    ChooseDefense,
    CombatService,
    ResumeInterruptedTurn,
    TakeCombatTurn,
    TakeUnarmedTurn,
)
from wayfarer.orchestration.great_haste import GreatHasteService
from wayfarer.orchestration.play import PlayService


async def paused_named_step(
    path: Path, backend: str, *, continuing: bool = True, seeded: bool = False
) -> tuple[str, PlayService, NamedStepCastGreatHaste]:
    cid, play = await prepare_named(path, backend, blocked=False)
    await change(
        cid,
        play,
        "wait-knowledge",
        lambda state: state.model_copy(
            update={
                "world": replace(
                    state.world,
                    facts=state.world.facts + (Fact("caster-known", "a", "present", "dock"),),
                ).learn("b", "caster-known"),
            }
        ),
    )
    for index in range(2):
        state = play._load(await play.store.read(cid))
        if continuing:
            await GreatHasteService(play).execute(
                cid,
                NamedStepCastGreatHaste(
                    id="initial-named-" + str(index),
                    actor_id="a",
                    expected_revision=state.revision,
                    operation="start" if index == 0 else "concentrate",
                    channel_id="great-haste",
                    cast_id="named",
                    known_fact_id="named-subject",
                    step=CastingStep(hex_facing=0),
                ),
                principal_id="alice",
            )
        else:
            await CombatService(play).execute(
                cid,
                TakeCombatTurn(
                    id="initial-idle-" + str(index),
                    actor_id="a",
                    expected_revision=state.revision,
                    encounter_id="fight",
                    maneuver="do_nothing",
                ),
                principal_id="a",
            )
    for index in range(2):
        state = play._load(await play.store.read(cid))
        await CombatService(play).execute(
            cid,
            TakeCombatTurn(
                id="other-" + str(index),
                actor_id="b",
                expected_revision=state.revision,
                encounter_id="fight",
                maneuver="do_nothing" if index == 0 else "wait",
                wait_trigger=WaitTrigger(
                    actor_id="a",
                    action="move",
                    reaction="attack",
                    reaction_target_id="a",
                    unarmed=UnarmedReaction(action="kick"),
                )
                if index
                else None,
            ),
            principal_id="b",
        )
    state = play._load(await play.store.read(cid))
    command = NamedStepCastGreatHaste(
        id="paused-named",
        actor_id="a",
        expected_revision=state.revision,
        operation="concentrate" if continuing else "start",
        channel_id="great-haste",
        cast_id="named",
        known_fact_id="named-subject",
        step=CastingStep(hex_path=(Hex(q=1, r=0),)),
    )
    if seeded:
        import secrets

        play.rng, play.seeds = secrets, lambda: "00" * 32
    result = await GreatHasteService(play).execute(cid, command, principal_id="alice")
    assert result.outcome == "paused"
    state = play._load(await play.store.read(cid))
    if continuing:
        assert latest(state.resources)["named"].concentration_seconds == 2
    else:
        assert "named" not in latest(state.resources)
    assert next(p.position for p in state.encounters[0].participants if p.actor_id == "a") == Hex(
        q=1, r=0
    )
    assert leases(state.resources)[command.id].named_origin_json
    return cid, play, command


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("will_success", [False, True])
async def test_named_step_actual_wait_defense_uses_one_will_minus_three(
    tmp_path: Path, backend: str, will_success: bool
) -> None:
    cid, play, command = await paused_named_step(tmp_path, backend)
    state = play._load(await play.store.read(cid))
    combat = CombatService(play)
    await combat.execute(
        cid,
        TakeUnarmedTurn(
            id="reaction",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            target_id="a",
            action="kick",
        ),
        principal_id="b",
    )
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice([1, 2, 3, 1, 2, 3] + ([1, 1, 1] if will_success else [6, 6, 6]))
    await combat.execute(
        cid,
        ChooseDefense(
            id="defend",
            actor_id="a",
            expected_revision=state.revision,
            encounter_id="fight",
            defense="dodge",
        ),
        principal_id="a",
    )
    assert play.rng.exhausted()
    state = play._load(await play.store.read(cid))
    if will_success:
        check = distractions(state.resources)[command.id].check
        assert check.effective_target == 9 and check.dice == (1, 1, 1)
    play.rng = RecordedDice([1, 1, 3] if will_success else [])
    resume = ResumeInterruptedTurn(
        id="resume",
        actor_id="a",
        expected_revision=state.revision,
        encounter_id="fight",
    )
    result = await combat.execute(cid, resume, principal_id="a")
    final = await play.store.read(cid)
    state = play._load(final)
    effect = latest(state.resources)["named"]
    assert effect.phase == ("active" if will_success else "ended")
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == (
        6 if will_success else 10
    )
    assert play.rng.exhausted()
    assert await combat.execute(cid, resume, principal_id="a") == result
    assert await play.store.read(cid) == final == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("continuing", [False, True])
async def test_named_step_lease_refuses_changed_source_knowledge_after_movement(
    tmp_path: Path, backend: str, continuing: bool
) -> None:
    cid, play, _ = await paused_named_step(tmp_path, backend, continuing=continuing)
    state = play._load(await play.store.read(cid))
    combat = CombatService(play)
    await combat.execute(
        cid,
        TakeCombatTurn(
            id="decline",
            actor_id="b",
            expected_revision=state.revision,
            encounter_id="fight",
            maneuver="do_nothing",
        ),
        principal_id="b",
    )

    def renamed(state: PlayState) -> PlayState:
        return state.model_copy(
            update={
                "world": replace(
                    state.world,
                    entities=tuple(
                        replace(e, name="New subject name") if e.id == "b" else e
                        for e in state.world.entities
                    ),
                    facts=tuple(
                        replace(f, value="New subject name") if f.id == "named-subject" else f
                        for f in state.world.facts
                    ),
                )
            }
        )

    await change(cid, play, "rename-fact", renamed)
    before, history = await play.store.read(cid), await play.store.history(cid)
    state = play._load(before)
    play.rng = RecordedDice([])
    with pytest.raises(ConflictError, match="subject"):
        await combat.execute(
            cid,
            ResumeInterruptedTurn(
                id="changed-resume",
                actor_id="a",
                expected_revision=state.revision,
                encounter_id="fight",
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before and await play.store.history(cid) == history
    assert play.rng.exhausted()
