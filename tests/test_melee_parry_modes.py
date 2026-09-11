"""B376/B556: declared weapon modes determine Parry and critical self-wounds."""

from pathlib import Path

import pytest
from test_gurps_melee import attack, choice, setup

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.critical import CriticalMiss
from wayfarer.simulation.gurps_equipment import Damage, MeleeMode, Parry
from wayfarer.simulation.mechanics.critical_limbs import CriticalLimbResult

MODES = (
    MeleeMode(
        id="swing",
        skill_id="skill:broadsword",
        minimum_st=10,
        damage=Damage(basis="swing", adds=1, damage_type="cut"),
        reach=(1,),
        parry=Parry(),
    ),
    MeleeMode(
        id="jab",
        skill_id="skill:broadsword",
        minimum_st=10,
        damage=Damage(basis="fixed", dice=1, damage_type="cr"),
        reach=(1,),
        parry=Parry(modifier=-1),
    ),
)


async def test_selected_parry_mode_survives_pending_restart_and_critical_self_hit(
    tmp_path: Path,
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True, melee_modes=MODES)
    await attack(cid, play)
    assert isinstance(play.store, AsyncSQLiteStore)
    play = PlayService(
        AsyncSQLiteStore(play.store.path),
        play.engine,
        rng=RecordedDice([3, 3, 3, 6, 6, 6, 2, 2, 2, 1, 1, 4, 1]),
    )
    command = choice("parry").model_copy(update={"item_id": "sword-b", "parry_mode_id": "jab"})
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert result.injury and result.injury.defense
    assert result.injury.defense.effective_target == 9  # skill 13: 6+3-1+shield DB 1.
    assert result.injury.adjudication_required is None
    state = play._load(await play.store.read(cid))
    saved = CriticalLimbResult.model_validate_json(
        next(e.kind for e in state.resources.events if e.id.startswith("critical-limb:"))
    )
    assert saved.resolved and saved.damage_dice == (4,)
    assert saved.injury == 2  # Row 6 halves 4 crushing, not swing+1 cutting.
    assert saved.location == "right-arm"
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 5
    # The original incoming swing still hits for floor((1+1)*1.5)=3.
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, command, authenticated_actor_id="b") == result
    with pytest.raises(ConflictError):
        await CombatService(play).execute(
            cid, command.model_copy(update={"parry_mode_id": "swing"}), authenticated_actor_id="b"
        )


@pytest.mark.parametrize("selected", [None, "jab"])
async def test_blocked_critical_context_keeps_declared_mode(
    tmp_path: Path, selected: str | None
) -> None:
    # Explicit anatomy is absent, so the recorded consequence remains blocked.
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", melee_modes=MODES)
    await attack(cid, play)
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 2, 2, 2])
    command = choice("parry").model_copy(update={"parry_mode_id": selected})
    result = await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert result.injury and result.injury.adjudication_required == "basic-critical-miss:6:defender"
    state = play._load(await play.store.read(cid))
    record = CriticalMiss.model_validate_json(
        next(
            e.kind
            for e in state.resources.events
            if e.kind.startswith('{"kind":"basic-critical-miss-v1"')
        )
    )
    assert tuple(w.mode.id for w in record.weapons) == (("jab",) if selected else ("swing", "jab"))
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()


@pytest.mark.parametrize("defense,mode_id", [("parry", "unknown"), ("dodge", "jab")])
async def test_invalid_parry_mode_rejects_before_dice(
    tmp_path: Path, defense: str, mode_id: str
) -> None:
    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True, melee_modes=MODES)
    await attack(cid, play)
    before = await play.store.read(cid)
    play.rng = RecordedDice([])
    command = choice().model_copy(update={"defense": defense, "parry_mode_id": mode_id})
    with pytest.raises(ValidationError):
        await CombatService(play).execute(cid, command, authenticated_actor_id="b")
    assert await play.store.read(cid) == before


async def test_second_parry_uses_its_own_damage_mode(tmp_path: Path) -> None:
    from test_gurps_maneuvers import defend, turn

    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True, melee_modes=MODES)
    await turn(cid, play, "a", "do_nothing")
    await turn(cid, play, "b", "all_out_defense", defense_option="double")
    await turn(cid, play, "a", "attack", item_id="sword-a", target_id="b", mode_id="swing")
    play.rng = RecordedDice([3, 3, 3, 4, 4, 4, 6, 6, 6, 2, 2, 2, 1, 1, 4, 1])
    result = await defend(
        cid,
        play,
        "b",
        "dodge",
        second_defense="parry",
        second_item_id="sword-b",
        second_parry_mode_id="jab",
    )
    assert result.injury and result.injury.second_defense
    assert result.injury.second_defense.effective_target == 9
    assert result.injury.adjudication_required is None
    state = play._load(await play.store.read(cid))
    saved = CriticalLimbResult.model_validate_json(
        next(e.kind for e in state.resources.events if e.id.startswith("critical-limb:"))
    )
    assert saved.injury == 2 and saved.location == "right-arm"
    assert next(p.current for p in state.resources.pools if p.id == "hp:b") == 5
    assert isinstance(play.rng, RecordedDice) and play.rng.exhausted()
