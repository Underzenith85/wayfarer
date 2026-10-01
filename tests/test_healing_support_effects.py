"""B248 support spell consequences, independent of generic casting receipts."""

from pathlib import Path

import pytest
from test_healing_spell_effects import cast, fixture

from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.simulation.magic.healing_support import expire_vitality, loans
from wayfarer.engine.simulation.magic.spells import latest
from wayfarer.engine.simulation.resources import Advance


def test_lend_energy_pays_full_selected_cost_and_restores_actual_fp(tmp_path: Path) -> None:
    runtime, state = fixture(tmp_path, "lend-energy", spell_points=12)
    patient = next(p for p in state.resources.pools if p.id == "fp:b")
    assert patient.fatigue
    patient = patient.model_copy(
        update={"current": 3, "fatigue": patient.fatigue.model_copy(update={"power": 7})}
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        patient if p.id == patient.id else p for p in state.resources.pools
                    )
                }
            )
        }
    )
    changed, result = cast(runtime, state, "lend-energy", energy=5)
    assert result.fp_restored == 5 and result.energy_spent == 5
    restored = next(p for p in changed.resources.pools if p.id == "fp:b")
    assert restored.current == 8 and restored.fatigue and restored.fatigue.power == 2
    assert latest(changed.resources)["cast"].phase == "ended"


def test_lent_vitality_expires_at_hour_on_actual_resource_clock(tmp_path: Path) -> None:
    runtime, state = fixture(tmp_path, "lend-vitality")
    changed, result = cast(runtime, state, "lend-vitality", energy=5)
    assert result.hp_restored == 5 and result.energy_spent == 5
    loan = loans(changed.resources)[0]
    assert loan.amount == 5 and loan.due == changed.resources.game_time + 3600
    before = runtime.resources.apply(
        changed.resources,
        Advance(
            id="before", actor_id="a", expected_revision=changed.resources.revision, to=loan.due - 1
        ),
        rng=RecordedDice([]),
        system=True,
    )
    assert next(p.current for p in before.pools if p.id == "hp:b") == 6
    ended = runtime.resources.apply(
        before,
        Advance(id="expiry", actor_id="a", expected_revision=before.revision, to=loan.due),
        rng=RecordedDice([]),
        system=True,
    )
    assert next(p.current for p in ended.pools if p.id == "hp:b") == 1
    assert loans(ended)[0].expired
    assert expire_vitality(ended, loan.due + 1) == ended


def test_lent_vitality_recast_replaces_borrowed_hp(tmp_path: Path) -> None:
    runtime, state = fixture(tmp_path, "lend-vitality")
    first, _ = cast(runtime, state, "lend-vitality", energy=5)
    second, result = cast(runtime, first, "lend-vitality", energy=2, cast_id="second")
    assert result.hp_restored == 0
    assert next(p.current for p in second.resources.pools if p.id == "hp:b") == 6
    assert [loan.amount for loan in loans(second.resources) if not loan.expired] == [5, 2]
    assert (
        next(p.current for p in expire_vitality(second.resources, 3602).pools if p.id == "hp:b")
        == 1
    )


@pytest.mark.parametrize(
    "points,mana,seconds,expected",
    [(12, "normal", 300, 1), (32, "normal", 120, 1), (32, "low", 120, 0), (32, "none", 120, 0)],
)
async def test_approved_recover_energy_uses_passive_rest_and_actual_fp(
    tmp_path: Path, points: int, mana: str, seconds: int, expected: int
) -> None:
    from typing import Literal
    from typing import cast as typed_cast

    from support.runtime import seed_campaign
    from test_actions import campaign

    from wayfarer.engine.simulation.action_engine.engine import ActionEngine
    from wayfarer.engine.simulation.health.medical.commands import BeginRecovery
    from wayfarer.orchestration.medical import CareEnvironment, MedicalService
    from wayfarer.orchestration.play import PlayService
    from wayfarer.persistence.async_sqlite import AsyncSQLiteStore

    runtime, state = fixture(tmp_path, "lend-energy", recover_points=points)
    engine = ActionEngine(runtime.reviewer, runtime.resources, runtime.rules)
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "recover.sqlite", 10), engine, rng=RecordedDice([])
    )
    fp = next(p for p in state.resources.pools if p.id == "fp:a")
    assert fp.fatigue
    fp = fp.model_copy(
        update={"current": fp.maximum - 5, "fatigue": fp.fatigue.model_copy(update={"power": 5})}
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={"pools": tuple(fp if p.id == fp.id else p for p in state.resources.pools)}
            )
        }
    )
    initial = campaign(engine)
    initial["id"] = state.campaign_id
    initial["play_json"] = state.model_dump_json()
    await seed_campaign(play.store, initial)
    env = CareEnvironment(
        mana=typed_cast(Literal["none", "low", "normal", "high", "very-high"], mana)
    )
    service = MedicalService(play, lambda _play, _state, _actor: env)
    command = BeginRecovery(
        id="rest", actor_id="a", expected_revision=0, kind="rest", target_id="a", seconds=seconds
    )
    result = await service.execute(initial["id"], command, principal_id="a")
    assert result.status == "pending" and result.check is None
    resting = play._load(await play.store.read(initial["id"]))
    task = resting.resources.recovery_tasks[0]
    assert task.power_interval == (seconds if mana == "normal" else 600)
    changed = runtime.resources.apply(
        resting.resources,
        Advance(
            id="elapsed", actor_id="a", expected_revision=resting.resources.revision, to=seconds
        ),
        system=True,
        rng=RecordedDice([]),
    )
    assert next(p.current for p in changed.pools if p.id == "fp:a") == fp.current + expected
    assert changed.game_time == seconds


@pytest.mark.parametrize(
    "spells,magery,legal",
    [
        (("lend-energy",), 1, True),
        (("lend-energy",), 0, False),
        (("lend-vitality",), 1, False),
        (("lend-energy", "lend-vitality"), 1, True),
        (("recover-energy",), 1, False),
        (("lend-energy", "recover-energy"), 1, True),
    ],
)
def test_support_construction_requires_source_training(
    spells: tuple[str, ...], magery: int, legal: bool
) -> None:
    from test_spell_construction import compile_spells

    from wayfarer.engine.rules.magic.healing import package

    result = compile_spells(
        (package(),), tuple(("spell:" + spell, 4) for spell in spells), magery=magery
    )
    assert result.legal is legal


def test_lend_vitality_cannot_maintain_and_early_cancellation_returns_hp(tmp_path: Path) -> None:
    from wayfarer.engine.rules.magic.healing import BINDINGS
    from wayfarer.engine.simulation.magic.colleges import dispatch_college_spell
    from wayfarer.engine.simulation.magic.spells import SpellCommand
    from wayfarer.errors import ConflictError

    runtime, state = fixture(tmp_path, "lend-vitality")
    borrowed, _ = cast(runtime, state, "lend-vitality", energy=5)
    effect = latest(borrowed.resources)["cast"]
    assert effect.expires_at
    expiry = borrowed.model_copy(
        update={"resources": borrowed.resources.model_copy(update={"game_time": effect.expires_at})}
    )
    command = SpellCommand(
        id="maintain",
        actor_id="a",
        expected_revision=expiry.resources.revision,
        kind="maintain",
        spell_id="lend-vitality",
        cast_id="cast",
        channel_id="lend-vitality",
    )
    with pytest.raises(ConflictError, match="Maintenance"):
        dispatch_college_spell(runtime, expiry, command, BINDINGS, authorized_actor_id="a")
    command = command.model_copy(
        update={"id": "cancel", "kind": "cancel", "expected_revision": borrowed.resources.revision}
    )
    cancelled, result = dispatch_college_spell(
        runtime, borrowed, command, BINDINGS, authorized_actor_id="a"
    )
    assert result.energy_spent == 1
    assert next(p.current for p in cancelled.resources.pools if p.id == "hp:b") == 1
