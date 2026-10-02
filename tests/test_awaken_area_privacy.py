"""B239/B248 empty Area casts and private per-subject waking checks."""

import secrets
from dataclasses import replace
from pathlib import Path

import pytest
from support.runtime import build_play, played, seed_campaign
from test_actions import campaign
from test_awaken_consciousness import area_state
from test_combat_sensory_authority import change

from scripts.replay_fixtures import FixtureExecutor
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.protocols import AreaSelection
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.combat.battlefield import Battlefield, GridPoint
from wayfarer.engine.simulation.combat.encounter import Encounter
from wayfarer.engine.simulation.combat.profiles import CombatRules
from wayfarer.engine.simulation.combat.spatial import SquareSpatialContext
from wayfarer.engine.simulation.magic.casting_targeting import awaken_checks
from wayfarer.engine.simulation.magic.spells import SpellCommand, SpellEvent, event_id, latest
from wayfarer.engine.world import Fact
from wayfarer.errors import ValidationError
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import submit
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.spells import SpellService
from wayfarer.persistence.replay import verify_commands


def placement(encounter: Encounter, occupied: bool) -> Encounter:
    context = encounter.spatial
    assert isinstance(context, SquareSpatialContext)
    point = GridPoint(x=2 if occupied else 4, y=1)
    return encounter.model_copy(
        update={
            "participants": tuple(
                p.model_copy(
                    update={"initiative": 10, **({"position": point} if p.actor_id == "b" else {})}
                )
                for p in encounter.participants
            ),
            "spatial_context": context.model_copy(
                update={
                    "placements": tuple(
                        p.model_copy(update={"position": point}) if p.actor_id == "b" else p
                        for p in context.placements
                    )
                }
            ),
        }
    )


async def prepare(
    path: Path, backend: str, *, occupied: bool, seeded: bool = False
) -> tuple[str, PlayService, SpellCommand]:
    runtime, state, command = area_state(path)
    assert runtime.rules.spells is not None
    rules = runtime.rules.model_copy(
        update={
            "combat": CombatRules(
                id="combat",
                version=1,
                battlefields=(Battlefield(id="room", location_id="room", width=5, height=5),),
            ),
            "spells": runtime.rules.spells.model_copy(
                update={
                    "channels": tuple(
                        c.model_copy(
                            update={
                                "target_id": "room",
                                "area": AreaSelection(center=(2, 1), cells=((2, 1),)),
                            }
                        )
                        for c in runtime.rules.spells.channels
                    )
                }
            ),
        }
    )
    engine = ActionEngine(runtime.reviewer, runtime.resources, rules)
    state = state.model_copy(
        update={
            "configuration_digest": engine.digest,
            "world": replace(state.world, facts=(), knowledge=()),
            "actors": tuple(
                a.model_copy(update={"conditions": ("unconscious",)}) if a.actor_id == "b" else a
                for a in state.actors
            ),
            "encounters": tuple(placement(e, occupied) for e in state.encounters),
        }
    )
    play = build_play(path, engine, backend=backend, rng=secrets if seeded else RecordedDice(()))
    initial = campaign(engine)
    initial["id"], initial["play_json"] = state.campaign_id, state.model_dump_json()
    await seed_campaign(play.store, initial)
    return state.campaign_id, play, command.model_copy(update={"radius": 1})


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
@pytest.mark.parametrize("occupied", [False, True])
async def test_empty_and_hidden_occupied_awaken_surface_have_same_cast_admission(
    tmp_path: Path, backend: str, occupied: bool
) -> None:
    cid, play, command = await prepare(tmp_path, backend, occupied=occupied)
    dice = RecordedDice((3, 3, 3, 3, 3, 3) if occupied else (3, 3, 3))
    play.rng = dice
    result = await SpellService(play).execute(cid, command, principal_id="a")
    after = await play.store.read(cid)
    state = play._load(after)
    assert result.outcome == "active" and result.energy_spent == 1 and len(result.checks) == 1
    assert result.checks[0].effective_target == 12
    assert next(p.current for p in state.resources.pools if p.id == "fp:a") == 29
    assert latest(state.resources)[command.cast_id].phase == "ended"
    assert next(a.conditions for a in state.actors if a.actor_id == "b") == (
        () if occupied else ("unconscious",)
    )
    assert "b" not in {e.id for e in state.world.perspective("a").entities}
    assert awaken_checks(state.resources, command.cast_id) == (("b",) if occupied else ())
    recorded = SpellEvent.model_validate_json(
        next(e.kind for e in state.resources.events if e.id == event_id(command.id))
    ).result
    assert len(recorded.checks) == (2 if occupied else 1) and dice.exhausted()
    assert await SpellService(play).execute(cid, command, principal_id="a") == result
    assert await play.store.read(cid) == after == await play.store.replay(cid)


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_awaken_exact_retry_uses_current_visibility_and_retains_private_history(
    tmp_path: Path, backend: str
) -> None:
    cid, play, command = await prepare(tmp_path, backend, occupied=True, seeded=True)
    initial = (await play.store.history(cid))[0].state_after
    play.seeds = lambda: format(1, "064x")  # Caster9 succeeds at12, hidden HT9 succeeds at13.
    result = await SpellService(play).execute(cid, command, principal_id="a")
    after = await play.store.read(cid)
    assert len(result.checks) == 1
    records = await played(play.store, cid)
    final, checks = await verify_commands(
        initial,
        records,
        await play.store.stream(cid),
        configuration_digest=play._load(initial).configuration_digest,
        execute=FixtureExecutor(play.engine, tmp_path / "reexecuted"),
    )
    assert all(c.folded and c.reexecuted for c in checks) and final == after
    fact = Fact("seen", "b", "visible", "yes")
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={"world": replace(s.world, facts=(fact,), knowledge=(("a", "seen"),))}
        ),
    )
    observed = await SpellService(play).execute(cid, command, principal_id="a")
    assert len(observed.checks) == 2 and observed.checks[1].effective_target == 13
    await change(
        play,
        cid,
        lambda s: s.model_copy(update={"world": replace(s.world, facts=(), knowledge=())}),
    )
    before_retry = await play.store.read(cid)
    assert await SpellService(play).execute(cid, command, principal_id="a") == result
    assert await play.store.read(cid) == before_retry
    assert play._load(before_retry).resources.pools == play._load(after).resources.pools


async def test_legacy_empty_awaken_refusal_and_old_gm_seat_authority_are_independent(
    tmp_path: Path,
) -> None:
    from test_item_area_targeting import prepare as fire

    cid, play, command = await prepare(tmp_path / "empty", "sqlite", occupied=False)
    state = play._load(await play.store.read(cid))
    # This old explicit location-only input has no participant center and stays refused.
    before = await play.store.read(cid)
    with pytest.raises(ValidationError):
        await submit(
            play,
            cid,
            SpellService(play).plan(
                play,
                member_for(state, "a"),
                command,
                principal_id="a",
                state=state,
                area_targeting=False,
            ),
            principal_id="a",
        )
    assert await play.store.read(cid) == before
    cid, play, command = await fire(tmp_path / "old", "sqlite", radius=1)
    state = play._load(await play.store.read(cid))
    play.rng = RecordedDice((3, 3, 3))
    await submit(
        play,
        cid,
        SpellService(play).plan(
            play,
            member_for(state, "gm"),
            command,
            principal_id="gm",
            state=state,
            area_targeting=False,
        ),
        principal_id="gm",
    )
    await change(
        play,
        cid,
        lambda s: s.model_copy(
            update={
                "members": tuple(
                    m.model_copy(update={"role": "spectator"}) if m.principal_id == "gm" else m
                    for m in s.members
                )
            }
        ),
    )
    with pytest.raises(ValidationError, match="director authority"):
        await SpellService(play).execute(cid, command, principal_id="gm")
