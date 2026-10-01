"""Source B248 examples through an approved build and actual patient HP."""

from dataclasses import replace
from pathlib import Path

import pytest
from test_abilities import resources, world
from test_actions import campaign
from test_statistics import gurps_draft, profile_compiler, profile_package

from wayfarer.engine.character.compiler import CharacterCompiler, Purchase
from wayfarer.engine.character.power import CharacterProposal, PowerPolicy, PowerReviewer
from wayfarer.engine.rules.catalog import PackagePin, RulesCatalog
from wayfarer.engine.rules.checks import RecordedDice
from wayfarer.engine.rules.magic.healing import BINDINGS
from wayfarer.engine.rules.magic.healing import package as healing_package
from wayfarer.engine.simulation.action_engine.engine import ActionEngine
from wayfarer.engine.simulation.actions import ActionRules, ActorSetup
from wayfarer.engine.simulation.magic.backfires import backfires
from wayfarer.engine.simulation.magic.bindings import SpellChannel, SpellRules
from wayfarer.engine.simulation.magic.colleges import dispatch_college_spell
from wayfarer.engine.simulation.magic.spells import PROFILE, SpellCommand, latest
from wayfarer.engine.simulation.resource_engine import ResourceEngine
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.async_sqlite import AsyncSQLiteStore


def fixture(tmp_path: Path, spell: str, *, fp: int = 100, physician: bool = False):
    from wayfarer.engine.rules.skills.mundane.medicine import definitions as medical_definitions

    physician_definition = next(d for d in medical_definitions() if d.id == "skill:physician")
    package = profile_package(
        PROFILE, *healing_package().definitions, *((physician_definition,) if physician else ())
    )
    base = profile_compiler(
        PROFILE, package=profile_package(PROFILE, *healing_package().definitions)
    )
    compiler = CharacterCompiler(
        RulesCatalog((package,)),
        replace(base.rules, packages=(PackagePin(package.id, package.version, package.digest),)),
        replace(base.policy, allow_supernatural=True, technology_level=8),
        statistics_profile=PROFILE,
    )
    keys = ("lend-energy", "lend-vitality", "minor-healing", "major-healing", "great-healing")
    draft = gurps_draft(
        Purchase(definition_id="trait:magery-0"),
        Purchase(definition_id="trait:magery", amount=3),
        *(Purchase(definition_id="spell:" + k, amount=4) for k in keys),
        *(
            (Purchase(definition_id="skill:physician", amount=24, technology_level=8),)
            if physician
            else ()
        ),
    )
    rules = ActionRules(
        id="healing",
        version=1,
        spells=SpellRules(
            id="healing",
            version=1,
            execution_version=2,
            channels=(
                SpellChannel(
                    id=spell, actor_id="a", target_id="b", location_id="room", spell_id=spell
                ),
            ),
        ),
    )
    engine = ActionEngine(
        PowerReviewer(compiler, PowerPolicy(id="power", version=1), frozenset({"gm"})),
        ResourceEngine(world(), RulesCatalog((package,)), compiler.rules, compiler.policy, ()),
        rules,
    )
    play = PlayService(
        AsyncSQLiteStore(tmp_path / "healing.sqlite", 10), engine, rng=RecordedDice([])
    )
    state = play.initial_state(
        campaign(engine),
        world(),
        resources(),
        (
            ActorSetup(actor_id="a", proposal=CharacterProposal(draft=draft)),
            ActorSetup(actor_id="b", proposal=CharacterProposal(draft=gurps_draft())),
        ),
    )
    state = state.model_copy(
        update={
            "resources": state.resources.model_copy(
                update={
                    "pools": tuple(
                        p.model_copy(update={"current": 1})
                        if p.id == "hp:b"
                        else p.model_copy(update={"current": fp, "maximum": fp})
                        if p.id == "fp:a"
                        else p
                        for p in state.resources.pools
                    )
                }
            )
        }
    )
    return play.rules_context, state


def cast(runtime, state, spell, *, energy=1, cast_id="cast", dice=(3, 3, 3)):
    start = SpellCommand(
        id=cast_id + ":start",
        actor_id="a",
        expected_revision=state.resources.revision,
        kind="start",
        spell_id=spell,
        cast_id=cast_id,
        channel_id=spell,
        energy=energy,
    )
    state, _ = dispatch_college_spell(
        replace(runtime, rng=RecordedDice([])), state, start, BINDINGS, authorized_actor_id="a"
    )
    effect = latest(state.resources)[cast_id]
    # Skill below 10 takes two seconds; each additional second requires concentration.
    for second in range(1, effect.ready_at - effect.started_at):
        state = state.model_copy(
            update={
                "resources": state.resources.model_copy(
                    update={"game_time": effect.started_at + second}
                )
            }
        )
        step = start.model_copy(
            update={
                "id": cast_id + f":second{second}",
                "kind": "concentrate",
                "expected_revision": state.resources.revision,
            }
        )
        state, _ = dispatch_college_spell(runtime, state, step, BINDINGS, authorized_actor_id="a")
    state = state.model_copy(
        update={"resources": state.resources.model_copy(update={"game_time": effect.ready_at})}
    )
    complete = start.model_copy(
        update={
            "id": cast_id + ":complete",
            "kind": "complete",
            "expected_revision": state.resources.revision,
        }
    )
    runtime = replace(runtime, rng=RecordedDice(list(dice)))
    changed, result = dispatch_college_spell(
        runtime, state, complete, BINDINGS, authorized_actor_id="a"
    )
    assert dispatch_college_spell(
        replace(runtime, rng=RecordedDice([])), changed, complete, BINDINGS, authorized_actor_id="a"
    ) == (changed, result)
    return changed, result


@pytest.mark.parametrize(
    "spell,energy,healed,cost",
    [("minor-healing", 3, 3, 3), ("major-healing", 4, 8, 4), ("great-healing", 1, 9, 20)],
)
def test_approved_healing_restores_patient_hp(tmp_path, spell, energy, healed, cost):
    runtime, state = fixture(tmp_path, spell)
    changed, result = cast(runtime, state, spell, energy=energy)
    assert result.hp_restored == healed and result.energy_spent == cost
    assert next(p.current for p in changed.resources.pools if p.id == "hp:b") == 1 + healed
    assert next(p.current for p in changed.resources.pools if p.id == "hp:a") == 10
    assert latest(changed.resources)["cast"].phase == "ended"


def test_repeated_attempts_penalty_and_patient_daily_limit(tmp_path):
    runtime, state = fixture(tmp_path, "minor-healing")
    first, _ = cast(runtime, state, "minor-healing")
    second, _ = cast(runtime, first, "minor-healing", cast_id="second")
    assert latest(second.resources)["second"].skill == latest(first.resources)["cast"].skill - 3
    runtime, state = fixture(tmp_path, "great-healing")
    first, _ = cast(runtime, state, "great-healing")
    with pytest.raises(ConflictError, match="already had"):
        cast(runtime, first, "great-healing", cast_id="second")


def test_critical_failure_records_patient_harm_gate_and_no_healing(tmp_path):
    runtime, state = fixture(tmp_path, "minor-healing")
    changed, result = cast(runtime, state, "minor-healing", dice=(6, 6, 6))
    assert result.outcome == "critical-failure" and result.hp_restored == 0
    pending = backfires(changed.resources)[0]
    assert pending.pending and pending.target_id == "b" and pending.row == 0
    with pytest.raises(ConflictError, match="backfire"):
        cast(runtime, changed, "minor-healing", cast_id="second")


def test_source_energy_limit_is_atomic(tmp_path):
    runtime, state = fixture(tmp_path, "minor-healing")
    with pytest.raises(ValidationError, match="energy exceeds"):
        cast(runtime, state, "minor-healing", energy=4)
    assert state.resources.revision == 0 and not state.resources.receipts


def test_patient_harm_resolves_through_authored_consequence(tmp_path):
    from wayfarer.engine.simulation.magic.backfire_transitions import ResolveSpellBackfire, resolve
    from wayfarer.engine.simulation.magic.bindings import BackfireAlternative

    runtime, state = fixture(tmp_path, "minor-healing")
    damaged, _ = cast(runtime, state, "minor-healing", dice=(6, 6, 6))
    pending = backfires(damaged.resources)[0]
    choice = BackfireAlternative(
        id="worsen-wound",
        spell_id="minor-healing",
        rows=(0,),
        effect="damage",
        target_ids=("b",),
        damage_dice=0,
        damage_add=1,
        damage_type="cr",
        reason="Campaign-authorized B248 aggravation of the patient's wound",
    )
    spells = runtime.rules.spells
    assert spells is not None
    runtime = replace(
        runtime,
        rules=runtime.rules.model_copy(
            update={"spells": spells.model_copy(update={"backfire_alternatives": (choice,)})}
        ),
        rng=RecordedDice([]),
    )
    command = ResolveSpellBackfire(
        id="resolve",
        actor_id="a",
        expected_revision=damaged.revision,
        backfire_id=pending.id,
        alternative_id=choice.id,
    )
    resolved, consequence = resolve(runtime, damaged, command)
    assert not consequence.pending
    assert next(p.current for p in resolved.resources.pools if p.id == "hp:b") == 0
    assert next(p.current for p in resolved.resources.pools if p.id == "hp:a") == 10


def test_physician_exception_and_day_reset(tmp_path):
    from wayfarer.engine.simulation.magic.healing_effects import attempts, mitigates_failure

    runtime, state = fixture(tmp_path, "minor-healing")
    first, _ = cast(runtime, state, "minor-healing")
    effect = latest(first.resources)["cast"]
    assert not mitigates_failure(first.resources, effect, 15)
    assert mitigates_failure(state.resources, effect, 15)
    assert not mitigates_failure(state.resources, effect, 14)
    tomorrow = first.resources.model_copy(update={"game_time": 86400})
    assert attempts(tomorrow, "minor-healing", "a", "b") == 0


def test_approved_physician_mitigates_first_healing_critical_failure(tmp_path):
    runtime, state = fixture(tmp_path, "minor-healing", physician=True)
    changed, result = cast(runtime, state, "minor-healing", dice=(6, 6, 6))
    assert result.outcome == "failed" and result.energy_spent == 1
    assert not backfires(changed.resources) and result.hp_restored == 0
    repeated, result = cast(runtime, changed, "minor-healing", cast_id="second", dice=(6, 6, 6))
    assert result.outcome == "critical-failure" and backfires(repeated.resources)[0].pending


def test_great_healing_takes_minute_and_failed_try_blocks_today(tmp_path):
    runtime, state = fixture(tmp_path, "great-healing")
    failed, result = cast(runtime, state, "great-healing", dice=(5, 5, 5))
    effect = latest(failed.resources)["cast"]
    assert effect.ready_at - effect.started_at == 60
    assert result.outcome == "failed" and result.energy_spent == 1
    with pytest.raises(ConflictError, match="already had"):
        cast(runtime, failed, "great-healing", cast_id="second")
