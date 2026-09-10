"""Explicit migration of complete legacy contexts without repeating attack/table dice."""

from pathlib import Path

import pytest
from test_gurps_melee import attack, choice, setup

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.combat import CombatService, ContinueCriticalMiss
from wayfarer.rules.checks import RecordedDice
from wayfarer.simulation.critical import CriticalMiss
from wayfarer.simulation.mechanics.critical_continuation import Continuation
from wayfarer.simulation.mechanics.critical_limbs import CriticalLimbResult


@pytest.mark.parametrize("parry", [False, True])
async def test_explicit_migration_resolves_original_wounds_once(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    parry: bool,
) -> None:
    import wayfarer.simulation.mechanics.critical_limbs as limbs

    cid, play = await setup(tmp_path, "gurps-basic-set-4e-2004", human=True)
    await attack(cid, play)
    original = limbs.resolve_limb

    def old_adapter(*args, **kwargs):  # type: ignore[no-untyped-def]
        return (
            args[1],
            args[2],
            CriticalLimbResult.model_validate({"table_rolls": (kwargs["table"],)}),
        )

    monkeypatch.setattr(limbs, "resolve_limb", old_adapter)
    play.rng = RecordedDice([3, 3, 3, 6, 6, 6, 1, 2, 2] if parry else [6, 6, 6, 1, 2, 2])
    defense = choice("parry" if parry else "none").model_copy(
        update={"parry_mode_id": "swing" if parry else None}
    )
    await CombatService(play).execute(cid, defense, authenticated_actor_id="b")
    monkeypatch.setattr(limbs, "resolve_limb", original)
    state = play._load(await play.store.read(cid))
    event = next(e for e in state.resources.events if e.id.startswith("critical:"))
    saved = CriticalMiss.model_validate_json(event.kind)
    assert len(saved.weapons) == 1 and saved.table_total == 5
    resume = ContinueCriticalMiss(
        id="resume",
        actor_id="gm",
        encounter_id="fight",
        expected_revision=3,
        critical_id=saved.id,
        stage="resume",
    )
    play.rng = RecordedDice([])
    with pytest.raises(ConflictError, match="explicit migration"):
        await CombatService(play).execute(cid, resume, authenticated_actor_id="gm")
    with pytest.raises(ValidationError, match="GM authority"):
        await CombatService(play).execute(
            cid, resume.model_copy(update={"actor_id": "a"}), authenticated_actor_id="a"
        )
    migration = resume.model_copy(update={"id": "migrate", "stage": "migrate"})
    await CombatService(play).execute(cid, migration, authenticated_actor_id="gm")
    play.rng = RecordedDice([1, 1, 2, 2] if parry else [1, 1, 2])
    resume = resume.model_copy(update={"expected_revision": 4})
    result = await CombatService(play).execute(cid, resume, authenticated_actor_id="gm")
    state = play._load(await play.store.read(cid))
    record = Continuation.model_validate_json(
        next(
            e.kind
            for e in reversed(state.resources.events)
            if e.id.startswith("critical-continuation:")
        )
    )
    assert record.injury == 4 and record.incoming_injury == (4 if parry else 0)
    assert record.status == "resolved" and state.encounters[0].blocked_reason is None
    assert next(e for e in state.resources.events if e.id == event.id) == event
    play.rng = RecordedDice([])
    assert await CombatService(play).execute(cid, resume, authenticated_actor_id="gm") == result
