"""Scene policy component proofs; played clinical care is covered by HTTP acceptance.

The fixture below constructs a RecoveryTask explicitly over a pure injury reducer
result. It does not claim a played combat, bandaging, or First Aid begin workflow.
"""

import hashlib
from pathlib import Path

import pytest
from pydantic import ValidationError as SchemaError
from support.runtime import build_play, seed_campaign
from test_actions import campaign
from test_scenes import configured

from wayfarer.adventures.roadwatch import adventure, engine
from wayfarer.contracts import Campaign
from wayfarer.engine.rules.types.recovery import RecoveryTask
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build
from wayfarer.engine.simulation.campaign.scenes import Scene, SceneRules
from wayfarer.engine.simulation.health.injury import Wound, apply_injury
from wayfarer.engine.simulation.health.medical.commands import CareContext, FinishRecovery
from wayfarer.engine.simulation.health.medical.recovery import apply_recovery
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.orchestration.scenes import SceneService, TravelScene


class NoDraw:
    def __init__(self) -> None:
        self.calls = 0

    def randbelow(self, upper: int) -> int:
        self.calls += 1
        raise AssertionError("Travel interruption must precede any new dice")


def component(
    path: Path, *, enabled: bool, backend: str = "sqlite"
) -> tuple[Campaign, PlayService, PlayState, NoDraw]:
    graph = adventure()
    assert graph.scenes is not None
    base = engine()
    rules = base.rules.model_copy(
        update={
            "scenes": graph.scenes.model_copy(
                update={"version": 2 if enabled else 1, "interrupt_recovery_on_travel": enabled}
            )
        }
    )
    configured_engine = ActionEngine(base.reviewer, base.resources, rules)
    rng = NoDraw()
    play = build_play(path, configured_engine, backend=backend, rng=rng)
    initial = campaign(configured_engine)
    state = play.initial_state(initial, graph.world, graph.resources, graph.actors)
    patient = build(play.rules_context, state, "a").statistics
    assert patient is not None
    resources, wound = apply_injury(
        state.resources,
        Wound(
            id="component-wound",
            actor_id="a",
            expected_revision=state.resources.revision,
            basic_damage=4,
            resistance=0,
            damage_type="cr",
        ),
        ht=patient.ht,
        rng=rng,
        system=True,
    )
    assert wound.injury == 4
    task = RecoveryTask(
        id="component-first-aid",
        actor_id="b",
        target_id="a",
        profile_id="gurps-basic-set-4e-2004",
        kind="first-aid",
        start=0,
        due=900,
        wound_id="component-wound",
        technology_level=3,
        skill=12,
    )
    resources = resources.model_copy(update={"recovery_tasks": (task,), "game_time": 100})
    return (
        initial,
        play,
        state.model_copy(update={"resources": resources, "revision": resources.revision}),
        rng,
    )


def travel(state: PlayState, actor: str, *, exit_id: str | None = None) -> TravelScene:
    return TravelScene(
        id="component-travel",
        actor_id=actor,
        expected_revision=state.revision,
        exit_id=exit_id or ("road-to-post" if actor == "brigand" else "post-to-road"),
    )


def test_default_false_omits_new_field_and_preserves_legacy_scene_configuration() -> None:
    minimal = SceneRules(
        id="legacy-scenes",
        version=1,
        scenes=(Scene(id="s", version=1, location_id="dock", title="Dock"),),
    )
    expected = (
        '{"id":"legacy-scenes","version":1,"scenes":[{"id":"s","version":1,'
        '"location_id":"dock","title":"Dock","exits":[],"obstacles":[]}],'
        '"discoveries":[],"triggers":[]}'
    )
    assert minimal.model_dump_json() == expected
    assert (
        minimal.model_copy(update={"interrupt_recovery_on_travel": False}).model_dump_json()
        == expected
    )
    assert SceneRules.model_validate_json(expected).model_dump_json() == expected
    old, _ = configured()
    assert old.rules.scenes is not None
    assert hashlib.sha256(old.rules.scenes.model_dump_json().encode()).hexdigest() == (
        "bb11638126ec52a2fccfab3bb3b0f3bdbd89b1bfee18609cbd48a93abd3e6299"
    )
    assert hashlib.sha256(old.rules.model_dump_json().encode()).hexdigest() == (
        "2e79e30cc29e4c711d205d62801186105766f27c55c7db90217b3b81ddec1f7a"
    )
    assert old.digest == "456fe4d0987d39c92ffe34fe8fc9aa07320a2abfbc7fe8d8d85d580291e68361"


def test_true_is_explicitly_serialized_schema_visible_and_changes_configuration_digest() -> None:
    old, _ = configured()
    assert old.rules.scenes is not None
    changed = old.rules.scenes.model_copy(update={"interrupt_recovery_on_travel": True})
    assert changed.model_dump(mode="json")["interrupt_recovery_on_travel"] is True
    assert SceneRules.model_validate_json(changed.model_dump_json()) == changed
    schema = SceneRules.model_json_schema()
    assert schema["properties"]["interrupt_recovery_on_travel"]["type"] == "boolean"
    assert "interrupt_recovery_on_travel" not in schema["required"]
    opted_in = ActionEngine(
        old.reviewer, old.resources, old.rules.model_copy(update={"scenes": changed})
    )
    assert opted_in.digest != old.digest
    for invalid in (None, 1, "true"):
        with pytest.raises(SchemaError):
            SceneRules.model_validate(
                changed.model_dump() | {"interrupt_recovery_on_travel": invalid}
            )


@pytest.mark.parametrize("enabled", [False, True])
@pytest.mark.parametrize("actor", ["a", "b", "brigand"])
def test_travel_interrupts_patient_or_caregiver_only_when_explicitly_enabled(
    tmp_path: Path, enabled: bool, actor: str
) -> None:
    _, play, before, rng = component(tmp_path, enabled=enabled)
    after = SceneService(play).reduce(before, travel(before, actor))
    task = after.resources.recovery_tasks[0]
    interrupted = enabled and actor in {"a", "b"}
    assert task.status == ("interrupted" if interrupted else "pending")
    assert task.interrupted_at == (100 if interrupted else None)
    assert after.resources.game_time == 110
    assert before.resources.recovery_tasks[0].status == "pending"
    assert rng.calls == 0


@pytest.mark.parametrize("actor", ["a", "b"])
def test_finishing_the_interrupted_component_first_aid_never_heals(
    tmp_path: Path, actor: str
) -> None:
    _, play, before, rng = component(tmp_path, enabled=True)
    moved = SceneService(play).reduce(before, travel(before, actor))
    hp_before = next(pool.current for pool in moved.resources.pools if pool.id == "hp:a")
    assert hp_before == 7
    finished, result = apply_recovery(
        moved.resources,
        FinishRecovery(
            id="component-finish",
            actor_id="b",
            expected_revision=moved.resources.revision,
            task_id="component-first-aid",
        ),
        CareContext("gurps-basic-set-4e-2004", ht=10, skill=12, technology_level=3),
        rng=rng,
        system=True,
    )
    assert result.status == "interrupted" and result.hp_recovered == 0
    assert result.check is None and result.healing_die is None
    assert next(pool.current for pool in finished.pools if pool.id == "hp:a") == hp_before
    assert rng.calls == 0


@pytest.mark.parametrize("actor", ["a", "b"])
def test_earned_care_at_due_time_must_be_settled_before_travel(tmp_path: Path, actor: str) -> None:
    _, play, before, rng = component(tmp_path, enabled=True)
    due = before.model_copy(
        update={"resources": before.resources.model_copy(update={"game_time": 900})}
    )
    with pytest.raises(ConflictError, match="Settle earned recovery"):
        SceneService(play).reduce(due, travel(due, actor))
    assert due.resources.recovery_tasks[0].status == "pending"
    assert due.resources.recovery_tasks[0].interrupted_at is None
    assert rng.calls == 0


@pytest.mark.parametrize("backend", ["sqlite", "postgres"])
async def test_scene_refusal_and_cached_retry_keep_component_care_state_atomic(
    tmp_path: Path, backend: str
) -> None:
    initial, play, state, rng = component(tmp_path, enabled=True, backend=backend)
    play.commit(initial, state)
    await seed_campaign(play.store, initial)
    cid = initial["id"]
    before = await play.store.read(cid)
    history, events = await play.store.history(cid), await play.store.stream(cid)
    with pytest.raises(ValidationError, match="Unknown exit"):
        await SceneService(play).execute(
            cid, travel(state, "a", exit_id="missing"), principal_id="a"
        )
    assert await play.store.read(cid) == before
    assert await play.store.history(cid) == history
    assert await play.store.stream(cid) == events
    assert await play.store.command_input(cid, "component-travel") is None
    command = travel(state, "a")
    first = await SceneService(play).execute(cid, command, principal_id="a")
    accepted = await play.store.read(cid)
    accepted_history, accepted_events = await play.store.history(cid), await play.store.stream(cid)
    # The actor has already left the command's source scene; retry must use the
    # accepted receipt instead of reevaluating its route or interrupting again.
    assert await SceneService(play).execute(cid, command, principal_id="a") == first
    assert await play.store.read(cid) == accepted
    assert await play.store.history(cid) == accepted_history
    assert await play.store.stream(cid) == accepted_events
    assert play._load(accepted).resources.recovery_tasks[0].interrupted_at == 100
    assert rng.calls == 0
