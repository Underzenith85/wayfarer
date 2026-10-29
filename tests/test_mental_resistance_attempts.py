"""B348 current trusted mental provider, not an invented campaign/player host."""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict
from typing import cast

import pytest
from support.quick_contest_evidence import record_quick_contest
from test_mental_spirit_traits import approved, channel, command, world
from trait_support import options

from wayfarer.engine.character.compiler import Purchase
from wayfarer.engine.rules.gurps_checks import ResistanceTrace
from wayfarer.engine.rules.resistance_attempts import FailedResistanceAttempt
from wayfarer.engine.simulation.resources import Pool, ResourceState
from wayfarer.engine.simulation.traits import mental_spirit as provider
from wayfarer.engine.simulation.traits.mental_control import control_grants
from wayfarer.engine.simulation.traits.mental_spirit import (
    CurrentMentalChannel,
    MentalChannel,
    apply_current_mental_use,
    apply_mental_use,
    history,
    replay_mental_use,
)
from wayfarer.engine.simulation.traits.neutralization import suppressions
from wayfarer.errors import ConflictError, ValidationError

# Observed independently on frozen primary d766, before this provider changes.
LEGACY_EVENT = "01c766ac143209224105d14911661974d55163a7ad750438f7ff8ab5d7cfe48d"
LEGACY_CHANNEL_SCHEMA = "971cdfe12586e1923c32160c55a8d59a7a9fe208034f3ed3565a1ff8b9d48036"


def current(**changes: object) -> CurrentMentalChannel:
    return CurrentMentalChannel.model_validate(channel().model_dump() | changes)


def seed() -> ResourceState:
    return ResourceState(pools=(Pool(id="fp:a", current=10, maximum=10),))


class EvidenceDice:
    """Count attempted entropy, even if an unexpected draw would fail."""

    def __init__(self) -> None:
        self.calls = 0

    def randbelow(self, exclusive_upper_bound: int, /) -> int:
        self.calls += 1
        raise AssertionError("Authored mental totals do not draw contest dice")


def evidence_channel(resources: ResourceState) -> CurrentMentalChannel:
    origin = history(resources)[-1].resistance_origin
    assert origin is not None and origin.prepared_channel_json is not None
    raw = CurrentMentalChannel.model_validate_json(origin.channel_json)
    prepared = CurrentMentalChannel.model_validate_json(origin.prepared_channel_json)
    # These cases have no concentration, control-count, or wary adjustment.
    assert prepared == raw and origin.control_sources == ()
    assert origin.at == resources.game_time == resources.events[-1].at == 0
    return raw


def paired_inputs(raw: CurrentMentalChannel, trace: ResistanceTrace) -> dict[str, object]:
    # Receipt dice are canonical scoring decompositions of authored totals.
    assert trace.attacker.base_target == raw.actor_score
    assert trace.resister.base_target == raw.resistance_score
    assert not trace.attacker.modifiers and not trace.resister.modifiers
    return {
        "actor_base": raw.actor_score,
        "actor_total": raw.actor_roll,
        "actor_adapter_dice": list(trace.attacker.dice),
        "resister_base": raw.resistance_score,
        "resister_total": raw.resistance_roll,
        "resister_adapter_dice": list(trace.resister.dice),
        "fatigue_cost": raw.fatigue_cost,
        "supernatural_attack": raw.supernatural_attack,
        "target_living_or_sapient": raw.target_living_or_sapient,
    }


@pytest.mark.parametrize(
    "definition,kind",
    [
        ("advantage:mind-reading", "read"),
        ("advantage:mind-probe", "probe"),
        ("advantage:mind-control", "influence"),
        ("advantage:possession", "possession"),
        ("advantage:neutralize", "neutralize"),
        ("advantage:dominance", "influence"),
        ("advantage:terror", "terror"),
    ],
)
def test_admitted_current_channels_allow_failed_attack_without_subject_attempt(
    definition: str, kind: str, record_property: Callable[[str, object], None]
) -> None:
    purchase = (
        Purchase(definition_id=definition, trait=options(penalty=2))
        if kind == "terror"
        else Purchase(definition_id=definition)
    )
    build, compiler = approved(purchase)
    authored = current(
        definition_id=definition,
        kind=kind,
        actor_score=8,
        actor_roll=12,
        resistance_score=10,
        resistance_roll=None,
        fatigue_cost=0,
        touching_target=True,
    )
    use = command().model_copy(update={"definition_id": definition})
    rng = EvidenceDice()
    resources, unchanged, outcome = apply_current_mental_use(
        seed(),
        world(),
        use,
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
        rng=rng,
    )
    assert outcome.outcome == "resisted" and isinstance(outcome.resistance, FailedResistanceAttempt)
    assert outcome.resistance.attacker.total == 12 and outcome.resistance.attacker.margin == -4
    assert outcome.resistance.declared_resister.base_target == 10
    assert not outcome.resistance.affected and not hasattr(outcome.resistance, "resister")
    assert unchanged == world() and not outcome.revealed_fact_ids
    assert (
        not resources.active_effect_ids
        and not control_grants(resources)
        and not suppressions(resources)
    )
    event = resources.events[-1]
    saved = history(ResourceState.model_validate_json(resources.model_dump_json()))[-1]
    assert (
        saved.resistance_origin
        and saved.resistance_origin.channel_json == authored.model_dump_json()
    )
    assert replay_mental_use(seed(), world(), use, build, compiler.definitions, event) == (
        resources,
        unchanged,
        outcome,
    )
    # An accepted exact retry needs no fresh authored context or admission.
    assert apply_current_mental_use(
        resources,
        unchanged,
        use,
        build,
        compiler.definitions,
        (),
        authorized_actor_id="a",
        system=True,
    ) == (resources, unchanged, outcome)

    raw = evidence_channel(resources)
    trace = outcome.resistance
    assert trace.declared_attacker.base_target == raw.actor_score
    assert trace.declared_resister.base_target == raw.resistance_score
    assert not trace.attacker.modifiers and not trace.declared_resister.modifiers
    assert raw == authored and resources.pools == seed().pools and rng.calls == 0
    assert compiler.statistics_profile == "gurps-basic-set-4e-2004"
    record_property("b348_runtime_profile", compiler.statistics_profile)
    record_property("b348_runtime_rules", json.dumps(asdict(build.rules), sort_keys=True))
    record_quick_contest(
        "basic-b348-r7-mental-" + definition.removeprefix("advantage:"),
        {
            "definition_id": raw.definition_id,
            "kind": raw.kind,
            "actor_base": raw.actor_score,
            "actor_total": raw.actor_roll,
            "adapter_decomposition": list(trace.attacker.dice),
            "resister_base": raw.resistance_score,
            "resister_total": raw.resistance_roll,
            "fatigue_cost": raw.fatigue_cost,
            "touching_target": raw.touching_target,
            "supernatural_attack": raw.supernatural_attack,
            "target_living_or_sapient": raw.target_living_or_sapient,
        },
        {
            "attacker_margin": trace.attacker.margin,
            "subject_attempted": hasattr(trace, "resister"),
            "affected": trace.affected,
            "new_contest_rng_draws": rng.calls,
            "revealed_fact_ids": list(outcome.revealed_fact_ids),
            "control_grants": len(control_grants(resources)),
            "suppressions": len(suppressions(resources)),
            "world_unchanged": unchanged == world(),
            "rule_of_16": trace.rule_of_16,
        },
        record_property,
    )


def test_optional_actual_defender_attempt_cannot_make_failed_attack_affect_subject(
    record_property: Callable[[str, object], None],
) -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    rng = EvidenceDice()
    resources, unchanged, outcome = apply_current_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (current(actor_score=8, actor_roll=12, resistance_score=10, resistance_roll=17),),
        authorized_actor_id="a",
        system=True,
        rng=rng,
    )
    assert isinstance(outcome.resistance, ResistanceTrace)
    assert outcome.resistance.contest.winner == "attacker"
    assert outcome.resistance.contest.victory_margin == 3
    assert not outcome.resistance.affected and outcome.outcome == "resisted"
    assert resources.pools[0].current == 8 and unchanged == world()
    assert not outcome.revealed_fact_ids

    raw = evidence_channel(resources)
    assert rng.calls == 0 and not resources.active_effect_ids
    assert not control_grants(resources) and not suppressions(resources)
    assert compiler.statistics_profile == "gurps-basic-set-4e-2004"
    record_property("b348_runtime_profile", compiler.statistics_profile)
    record_property("b348_runtime_rules", json.dumps(asdict(build.rules), sort_keys=True))
    record_quick_contest(
        "basic-b348-r7-mental-reading-optional-subject",
        paired_inputs(raw, outcome.resistance),
        {
            "signed_margins": [
                outcome.resistance.attacker.margin,
                outcome.resistance.resister.margin,
            ],
            "ordinary_nested_winner": outcome.resistance.contest.winner,
            "ordinary_victory_margin": outcome.resistance.contest.victory_margin,
            "affected": outcome.resistance.affected,
            "subject_attempted": isinstance(outcome.resistance, ResistanceTrace),
            "fp_after": resources.pools[0].current,
            "world_unchanged": unchanged == world(),
            "rule_of_16": outcome.resistance.rule_of_16,
        },
        record_property,
    )


@pytest.mark.parametrize("score,roll,affected", [(15, 12, False), (10, 12, True)])
def test_resistance_tie_and_success_have_actual_two_rolls(
    score: int, roll: int, affected: bool, record_property: Callable[[str, object], None]
) -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    rng = EvidenceDice()
    resources, changed, outcome = apply_current_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (current(actor_score=12, actor_roll=9, resistance_score=score, resistance_roll=roll),),
        authorized_actor_id="a",
        system=True,
        rng=rng,
    )
    assert (
        isinstance(outcome.resistance, ResistanceTrace) and outcome.resistance.affected == affected
    )
    assert outcome.resistance.attacker.total == 9 and outcome.resistance.resister.total == 12
    assert ("a", "surface") in changed.knowledge if affected else changed == world()
    assert resources.pools[0].current == 8

    raw = evidence_channel(resources)
    assert rng.calls == 0 and not resources.active_effect_ids
    assert not control_grants(resources) and not suppressions(resources)
    assert outcome.revealed_fact_ids == (("surface",) if affected else ())
    assert compiler.statistics_profile == "gurps-basic-set-4e-2004"
    record_property("b348_runtime_profile", compiler.statistics_profile)
    record_property("b348_runtime_rules", json.dumps(asdict(build.rules), sort_keys=True))
    record_quick_contest(
        f"basic-b348-r{9 if affected else 8}-mental-reading",
        paired_inputs(raw, outcome.resistance),
        {
            "attacker_margin": outcome.resistance.attacker.margin,
            "resister_margin": outcome.resistance.resister.margin,
            "victory_margin": outcome.resistance.contest.victory_margin,
            "affected": outcome.resistance.affected,
            "fp_after": resources.pools[0].current,
            "learned_surface_fact": ("a", "surface") in changed.knowledge,
            "rule_of_16": outcome.resistance.rule_of_16,
        },
        record_property,
    )


@pytest.mark.parametrize("resistance", [1, 0, -5])
def test_mandatory_resistance_has_no_voluntary_three_floor(resistance: int) -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    _, _, outcome = apply_current_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (current(actor_score=3, actor_roll=3, resistance_score=resistance, resistance_roll=12),),
        authorized_actor_id="a",
        system=True,
    )
    assert isinstance(outcome.resistance, ResistanceTrace) and outcome.resistance.affected
    assert outcome.resistance.resister.effective_target == resistance


@pytest.mark.parametrize(
    "actor_score,actor_roll,message", [(2, 3, "at least 3"), (12, 9, "actual subject dice")]
)
def test_current_attack_floor_and_missing_successful_resistance_precede_fatigue(
    monkeypatch: pytest.MonkeyPatch, actor_score: int, actor_roll: int, message: str
) -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    calls = []

    def forbidden(*_args: object) -> tuple[Pool, ...]:
        calls.append("fatigue")
        raise AssertionError("FP is later than current score admission")

    monkeypatch.setattr(provider, "_spent_fatigue", forbidden)
    before = seed()
    with pytest.raises(ValidationError, match=message):
        apply_current_mental_use(
            before,
            world(),
            command(),
            build,
            compiler.definitions,
            (current(actor_score=actor_score, actor_roll=actor_roll, resistance_roll=None),),
            authorized_actor_id="a",
            system=True,
        )
    assert calls == [] and before == seed()


def test_rule16_failure_retains_uncapped_declarations_without_subject_dice() -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    _, _, outcome = apply_current_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (current(actor_score=30, actor_roll=17, resistance_score=10, resistance_roll=None),),
        authorized_actor_id="a",
        system=True,
    )
    assert isinstance(outcome.resistance, FailedResistanceAttempt)
    assert outcome.resistance.rule_of_16
    assert outcome.resistance.declared_attacker.base_target == 30
    assert outcome.resistance.attacker.effective_target == 16
    assert [m.value for m in outcome.resistance.attacker.modifiers] == [-14]


def test_original_possession_wary_context_is_captured_before_adjustment() -> None:
    build, compiler = approved(Purchase(definition_id="advantage:possession"))
    authored = current(
        definition_id="advantage:possession",
        kind="possession",
        fatigue_cost=0,
        touching_target=True,
        target_wary=True,
        resistance_score=12,
        actor_score=12,
        actor_roll=9,
        resistance_roll=18,
    )
    use = command().model_copy(update={"definition_id": "advantage:possession"})
    result = apply_current_mental_use(
        seed(),
        world(),
        use,
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert isinstance(result[2].resistance, ResistanceTrace)
    assert result[2].resistance.resister.base_target == 17
    saved = history(result[0])[-1].resistance_origin
    assert (
        saved
        and CurrentMentalChannel.model_validate_json(saved.channel_json).resistance_score == 12
    )
    assert (
        replay_mental_use(seed(), world(), use, build, compiler.definitions, result[0].events[-1])
        == result
    )


def test_actual_active_control_penalty_is_not_clamped_before_current_floor(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-control"))
    authored = current(
        definition_id="advantage:mind-control",
        kind="influence",
        fatigue_cost=0,
        actor_score=3,
        actor_roll=3,
        resistance_score=10,
        resistance_roll=12,
    )
    use = command().model_copy(update={"definition_id": "advantage:mind-control"})
    resources, changed, outcome = apply_current_mental_use(
        seed(),
        world(),
        use,
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "successful" and len(control_grants(resources)) == 1

    def forbidden(*_args: object) -> tuple[Pool, ...]:
        raise AssertionError("Derived score2 must refuse before FP")

    monkeypatch.setattr(provider, "_spent_fatigue", forbidden)
    with pytest.raises(ValidationError, match="at least 3"):
        apply_current_mental_use(
            resources,
            changed,
            use.model_copy(update={"id": "second", "expected_revision": 1}),
            build,
            compiler.definitions,
            (authored,),
            authorized_actor_id="a",
            system=True,
        )
    assert len(control_grants(resources)) == 1


def test_legacy_provider_schema_event_bytes_and_explicit_replay_stay_exact() -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    result = apply_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (channel(),),
        authorized_actor_id="a",
        system=True,
    )
    event = result[0].events[-1]
    assert hashlib.sha256(event.kind.encode()).hexdigest() == LEGACY_EVENT
    assert "resistance_origin" not in event.kind
    assert (
        hashlib.sha256(
            json.dumps(MentalChannel.model_json_schema(), sort_keys=True).encode()
        ).hexdigest()
        == LEGACY_CHANNEL_SCHEMA
    )
    assert (
        replay_mental_use(
            seed(),
            world(),
            command(),
            build,
            compiler.definitions,
            event,
            historical_channels=(channel(),),
        )
        == result
    )
    with pytest.raises(ValidationError, match="authored context"):
        replay_mental_use(seed(), world(), command(), build, compiler.definitions, event)
    with pytest.raises(ValidationError, match="requires a resistance roll"):
        apply_mental_use(
            seed(),
            world(),
            command(),
            build,
            compiler.definitions,
            (channel(actor_roll=12, resistance_roll=None),),
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ValidationError, match="cannot accept a current"):
        apply_mental_use(
            seed(),
            world(),
            command(),
            build,
            compiler.definitions,
            (current(),),
            authorized_actor_id="a",
            system=True,
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "version-bool",
        "version-float",
        "version-future",
        "digest",
        "actor",
        "target",
        "build",
        "duplicate",
    ],
)
def test_current_recorded_provider_provenance_fails_closed(tamper: str) -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    resources, _, _ = apply_current_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (current(actor_score=8, actor_roll=12, resistance_roll=None),),
        authorized_actor_id="a",
        system=True,
    )
    event = resources.events[-1]
    payload = json.loads(event.kind)
    origin = payload["resistance_origin"]
    if tamper.startswith("version"):
        origin["generation"] = {"version-bool": True, "version-float": 1.0, "version-future": 2}[
            tamper
        ]
    elif tamper == "digest":
        origin["command_digest"] = "0" * 64
    elif tamper == "actor":
        raw = json.loads(origin["command_json"])
        raw["actor_id"] = "b"
        origin["command_json"] = json.dumps(raw)
        origin["command_digest"] = hashlib.sha256(origin["command_json"].encode()).hexdigest()
    elif tamper == "target":
        raw = json.loads(origin["channel_json"])
        raw["target_id"] = "a"
        origin["channel_json"] = json.dumps(raw)
    elif tamper == "build":
        origin["build_revision"] = "0" * 64
    else:
        with pytest.raises(ValidationError, match="Duplicate"):
            history(resources.model_copy(update={"events": resources.events + (event,)}))
        return
    forged = event.model_copy(update={"kind": json.dumps(payload)})
    with pytest.raises(ValidationError):
        replay_mental_use(seed(), world(), command(), build, compiler.definitions, forged)


def test_current_retry_preserves_command_identity_before_new_context() -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    resources, changed, _ = apply_current_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (current(actor_score=8, actor_roll=12, resistance_roll=None),),
        authorized_actor_id="a",
        system=True,
    )
    with pytest.raises(ConflictError):
        apply_current_mental_use(
            resources,
            changed,
            command().model_copy(update={"expected_revision": 1}),
            build,
            compiler.definitions,
            (),
            authorized_actor_id="a",
            system=True,
        )


def test_current_neutralize_critical_failure_retains_real_cooldown_entropy() -> None:
    from wayfarer.engine.rules.checks import RecordedDice
    from wayfarer.engine.simulation.traits.neutralization import neutralize_crippled

    build, compiler = approved(Purchase(definition_id="advantage:neutralize"))
    authored = current(
        definition_id="advantage:neutralize",
        kind="neutralize",
        fatigue_cost=0,
        touching_target=True,
        actor_score=12,
        actor_roll=18,
        resistance_score=10,
        resistance_roll=None,
    )
    use = command().model_copy(update={"definition_id": "advantage:neutralize"})
    rng = RecordedDice((4,))
    result = apply_current_mental_use(
        seed(),
        world(),
        use,
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
        rng=rng,
    )
    assert rng.exhausted() and neutralize_crippled(result[0], "a")
    assert result[2].outcome == "resisted" and isinstance(
        result[2].resistance, FailedResistanceAttempt
    )
    assert not suppressions(result[0])
    assert (
        replay_mental_use(
            seed(),
            world(),
            use,
            build,
            compiler.definitions,
            result[0].events[-1],
            rng=RecordedDice((4,)),
        )
        == result
    )


def test_current_context_authority_and_ambiguous_selection_are_not_player_flags() -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    authored = current(actor_score=8, actor_roll=12, resistance_roll=None)
    with pytest.raises(ValidationError, match="authority"):
        apply_current_mental_use(
            seed(),
            world(),
            command(),
            build,
            compiler.definitions,
            (authored,),
            authorized_actor_id="a",
        )
    with pytest.raises(ValidationError, match="Ambiguous"):
        apply_current_mental_use(
            seed(),
            world(),
            command(),
            build,
            compiler.definitions,
            (authored, authored),
            authorized_actor_id="a",
            system=True,
        )
    with pytest.raises(ValidationError, match="explicit current context"):
        apply_current_mental_use(
            seed(),
            world(),
            command(),
            build,
            compiler.definitions,
            cast(tuple[CurrentMentalChannel, ...], (channel(),)),
            authorized_actor_id="a",
            system=True,
        )


@pytest.mark.parametrize("tamper", ["low-attack", "rule16-scope", "future-revision"])
def test_current_history_rejects_legacy_scoring_or_false_producer_metadata(tamper: str) -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    resources, _, _ = apply_current_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (current(actor_score=12, actor_roll=9, resistance_score=10, resistance_roll=12),),
        authorized_actor_id="a",
        system=True,
    )
    event = resources.events[-1]
    payload = json.loads(event.kind)
    origin = payload["resistance_origin"]
    if tamper == "low-attack":
        legacy = apply_mental_use(
            seed(),
            world(),
            command(),
            build,
            compiler.definitions,
            (channel(actor_score=2, actor_roll=3, resistance_score=10, resistance_roll=12),),
            authorized_actor_id="a",
            system=True,
        )
        assert isinstance(legacy[2].resistance, ResistanceTrace) and legacy[2].resistance.affected
        payload["outcome"] = legacy[2].model_dump(mode="json")
        origin["channel_json"] = current(
            actor_score=2, actor_roll=3, resistance_score=10, resistance_roll=12
        ).model_dump_json()
    elif tamper == "rule16-scope":
        origin["channel_json"] = current(
            actor_score=12,
            actor_roll=9,
            resistance_score=10,
            resistance_roll=12,
            supernatural_attack=False,
        ).model_dump_json()
        origin["prepared_channel_json"] = origin["channel_json"]
    else:
        raw = json.loads(origin["command_json"])
        raw["expected_revision"] = 999
        origin["command_json"] = json.dumps(raw)
        origin["command_digest"] = hashlib.sha256(origin["command_json"].encode()).hexdigest()
    forged = event.model_copy(update={"kind": json.dumps(payload)})
    with pytest.raises(ValidationError):
        history(resources.model_copy(update={"events": (forged,)}))


@pytest.mark.parametrize(
    "tamper", [None, "basis-digest", "derived-score", "self-basis", "future-basis", "missing-basis"]
)
def test_control_basis_is_native_compact_and_old_retry_survives_later_grants(
    tamper: str | None,
) -> None:
    from dataclasses import replace

    build, compiler = approved(Purchase(definition_id="advantage:mind-control"))
    authored = current(
        definition_id="advantage:mind-control",
        kind="influence",
        fatigue_cost=0,
        actor_score=5,
        actor_roll=3,
        resistance_score=10,
        resistance_roll=12,
    )
    first_command = command().model_copy(update={"definition_id": "advantage:mind-control"})
    first = apply_current_mental_use(
        seed(),
        world(),
        first_command,
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    second_command = first_command.model_copy(
        update={"id": "another-control", "expected_revision": 1}
    )
    second = apply_current_mental_use(
        first[0],
        first[1],
        second_command,
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    actual = history(second[0])[-1]
    assert actual.resistance_origin and len(actual.resistance_origin.control_sources) == 1
    prepared = CurrentMentalChannel.model_validate_json(
        actual.resistance_origin.prepared_channel_json or ""
    )
    assert prepared.actor_score == 4 and authored.actor_score == 5
    assert isinstance(actual.outcome.resistance, ResistanceTrace)
    assert actual.outcome.resistance.attacker.effective_target == 4
    assert "kind" not in actual.resistance_origin.control_sources[0].model_dump()
    event = second[0].events[-1]
    if tamper is not None:
        payload = json.loads(event.kind)
        origin = payload["resistance_origin"]
        if tamper == "basis-digest":
            origin["control_sources"][0]["event_digest"] = "0" * 64
        elif tamper == "missing-basis":
            del origin["control_sources"]
        elif tamper in {"self-basis", "future-basis"}:
            from wayfarer.engine.simulation.traits.mental_resistance_origin import control_source

            if tamper == "future-basis":
                later = apply_current_mental_use(
                    second[0],
                    second[1],
                    second_command.model_copy(
                        update={"id": "later-control", "expected_revision": 2}
                    ),
                    build,
                    compiler.definitions,
                    (authored,),
                    authorized_actor_id="a",
                    system=True,
                )
                second = later
                source_id = "mental-control:later-control"
            else:
                source_id = "mental-control:another-control"
            source = next(e for e in second[0].events if e.id == source_id)
            origin["control_sources"] = [control_source(source).model_dump(mode="json")]
        else:
            raw = json.loads(origin["prepared_channel_json"])
            raw["actor_score"] = 5
            origin["prepared_channel_json"] = json.dumps(raw)
        changed = event.model_copy(update={"kind": json.dumps(payload)})
        corrupted = second[0].model_copy(
            update={"events": tuple(changed if e.id == event.id else e for e in second[0].events)}
        )
        with pytest.raises(ValidationError):
            history(corrupted)
        return
    assert (
        replay_mental_use(first[0], first[1], second_command, build, compiler.definitions, event)
        == second
    )
    # The original committed result uses its captured basis, not the later grant
    # or a fresh build/context query. The deliberately different build is unused.
    assert apply_current_mental_use(
        second[0],
        second[1],
        first_command,
        replace(build, revision="0" * 64),
        {},
        (),
        authorized_actor_id="a",
        system=True,
    ) == (second[0], second[1], first[2])


def test_recorded_replay_cannot_substitute_an_already_applied_state() -> None:
    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    result = apply_current_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (current(actor_score=8, actor_roll=12, resistance_roll=None),),
        authorized_actor_id="a",
        system=True,
    )
    with pytest.raises(ValidationError, match="pre-command state"):
        replay_mental_use(
            result[0], result[1], command(), build, compiler.definitions, result[0].events[-1]
        )


@pytest.mark.parametrize(
    "tamper",
    [
        "duplicate-generation",
        "duplicate-origin",
        "origin-null",
        "origin-zero",
        "origin-false",
        "generation-zero",
        "generation-future",
        "generation-missing",
        "prepared-null",
        "prepared-missing",
        "duplicate-prepared",
        "duplicate-control-sources",
        "duplicate-command-json",
        "duplicate-channel-json",
        "duplicate-prepared-json",
        "attempt-missing",
    ],
)
def test_cached_current_two_roll_event_rejects_ambiguous_or_missing_private_metadata(
    tamper: str,
) -> None:
    from wayfarer.engine.rules.checks import RecordedDice

    class AuditedDice(RecordedDice):
        def __init__(self) -> None:
            super().__init__(())
            self.calls = 0

        def randbelow(self, exclusive_upper_bound: int, /) -> int:
            self.calls += 1
            return super().randbelow(exclusive_upper_bound)

    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    resources, changed, outcome = apply_current_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (current(actor_score=12, actor_roll=9, resistance_score=15, resistance_roll=12),),
        authorized_actor_id="a",
        system=True,
    )
    assert isinstance(outcome.resistance, ResistanceTrace) and not outcome.resistance.affected
    event = resources.events[-1]
    data = json.loads(event.kind)
    origin = data["resistance_origin"]
    if tamper == "duplicate-generation":
        text = event.kind.replace('"generation":1', '"generation":true,"generation":1', 1)
    elif tamper == "duplicate-origin":
        text = '{"resistance_origin":null,' + event.kind[1:]
    elif tamper in {"origin-null", "origin-zero", "origin-false"}:
        data["resistance_origin"] = {"origin-null": None, "origin-zero": 0, "origin-false": False}[
            tamper
        ]
        text = json.dumps(data)
    elif tamper in {"generation-zero", "generation-future"}:
        origin["generation"] = 0 if tamper == "generation-zero" else 2
        text = json.dumps(data)
    elif tamper == "generation-missing":
        del origin["generation"]
        text = json.dumps(data)
    elif tamper in {"prepared-null", "prepared-missing"}:
        if tamper == "prepared-null":
            origin["prepared_channel_json"] = None
        else:
            del origin["prepared_channel_json"]
        text = json.dumps(data)
    elif tamper == "duplicate-prepared":
        text = event.kind.replace(
            '"prepared_channel_json":', '"prepared_channel_json":null,"prepared_channel_json":', 1
        )
    elif tamper == "duplicate-control-sources":
        origin["control_sources"] = []
        text = json.dumps(data).replace(
            '"control_sources": []', '"control_sources": null,"control_sources": []', 1
        )
    elif tamper in {"duplicate-command-json", "duplicate-channel-json", "duplicate-prepared-json"}:
        key = {
            "duplicate-command-json": "command_json",
            "duplicate-channel-json": "channel_json",
            "duplicate-prepared-json": "prepared_channel_json",
        }[tamper]
        prefix = '{"actor_id":"wrong",' if key == "command_json" else '{"actor_score":2,'
        origin[key] = prefix + origin[key][1:]
        if key == "command_json":
            origin["command_digest"] = hashlib.sha256(origin[key].encode()).hexdigest()
        text = json.dumps(data)
    else:
        del data["outcome"]["resistance"]
        text = json.dumps(data)
    assert text != event.kind
    forged = event.model_copy(update={"kind": text})
    corrupted = resources.model_copy(update={"events": resources.events[:-1] + (forged,)})
    rng = AuditedDice()
    before = corrupted.model_dump_json(), changed
    with pytest.raises(ValidationError):
        apply_current_mental_use(
            corrupted,
            changed,
            command(),
            build,
            compiler.definitions,
            (),
            authorized_actor_id="a",
            system=True,
            rng=rng,
        )
    assert rng.calls == 0 and (corrupted.model_dump_json(), changed) == before


def test_actual_historical_absence_remains_a_valid_zero_draw_cached_retry() -> None:
    from wayfarer.engine.rules.checks import RecordedDice

    build, compiler = approved(Purchase(definition_id="advantage:mind-reading"))
    resources, changed, outcome = apply_mental_use(
        seed(),
        world(),
        command(),
        build,
        compiler.definitions,
        (channel(),),
        authorized_actor_id="a",
        system=True,
    )
    assert "resistance_origin" not in resources.events[-1].kind
    assert hashlib.sha256(resources.events[-1].kind.encode()).hexdigest() == LEGACY_EVENT
    assert apply_current_mental_use(
        resources,
        changed,
        command(),
        build,
        compiler.definitions,
        (),
        authorized_actor_id="a",
        system=True,
        rng=RecordedDice(()),
    ) == (resources, changed, outcome)


def test_current_generic_declared_supernatural_resistance_obeys_rule16() -> None:
    """Generic supplied numeric context; not a claim that stock Medium attacks."""
    build, compiler = approved(Purchase(definition_id="advantage:medium"))
    authored = current(
        definition_id="advantage:medium",
        kind="spirit",
        actor_score=30,
        actor_roll=15,
        resistance_score=10,
        resistance_roll=3,
        fatigue_cost=0,
    )
    use = command().model_copy(update={"definition_id": "advantage:medium"})
    resources, changed, outcome = apply_current_mental_use(
        seed(),
        world(),
        use,
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert isinstance(outcome.resistance, ResistanceTrace)
    assert (
        outcome.resistance.rule_of_16,
        outcome.resistance.attacker.effective_target,
        outcome.outcome,
        outcome.revealed_fact_ids,
    ) == (True, 16, "resisted", ())
    assert outcome.resistance.attacker.margin == 1 and outcome.resistance.resister.margin == 7
    assert (
        outcome.resistance.contest.winner == "resister"
        and outcome.resistance.contest.victory_margin == 6
    )
    assert changed == world() and resources.pools == seed().pools
    assert replay_mental_use(
        seed(), world(), use, build, compiler.definitions, resources.events[-1]
    ) == (resources, changed, outcome)


@pytest.mark.parametrize("scope", ["ordinary-attack", "nonliving-nonsapient", "no-resistance"])
def test_current_generic_rule16_requires_all_three_declared_conditions(scope: str) -> None:
    build, compiler = approved(Purchase(definition_id="advantage:medium"))
    changes: dict[str, object] = {
        "definition_id": "advantage:medium",
        "kind": "spirit",
        "actor_score": 30,
        "actor_roll": 15,
        "resistance_score": 10,
        "resistance_roll": 3,
        "fatigue_cost": 0,
    }
    if scope == "ordinary-attack":
        changes["supernatural_attack"] = False
    elif scope == "nonliving-nonsapient":
        changes["target_living_or_sapient"] = False
    else:
        changes.update(resistance_score=None, resistance_roll=None)
    authored = current(**changes)
    use = command().model_copy(update={"definition_id": "advantage:medium"})
    resources, changed, outcome = apply_current_mental_use(
        seed(),
        world(),
        use,
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert outcome.outcome == "successful" and outcome.revealed_fact_ids == ("surface",)
    if scope == "no-resistance":
        assert outcome.resistance is None
    else:
        assert isinstance(outcome.resistance, ResistanceTrace) and not outcome.resistance.rule_of_16
        assert outcome.resistance.attacker.effective_target == 30
        assert outcome.resistance.contest.victory_margin == 8
    assert replay_mental_use(
        seed(), world(), use, build, compiler.definitions, resources.events[-1]
    ) == (resources, changed, outcome)


@pytest.mark.parametrize("resistance", [0, -5])
def test_current_generic_zero_or_negative_resistance_still_selects_cap(resistance: int) -> None:
    build, compiler = approved(Purchase(definition_id="advantage:medium"))
    authored = current(
        definition_id="advantage:medium",
        kind="spirit",
        actor_score=30,
        actor_roll=15,
        resistance_score=resistance,
        resistance_roll=12,
        fatigue_cost=0,
    )
    _, _, outcome = apply_current_mental_use(
        seed(),
        world(),
        command().model_copy(update={"definition_id": "advantage:medium"}),
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert isinstance(outcome.resistance, ResistanceTrace) and outcome.resistance.rule_of_16
    assert outcome.resistance.attacker.effective_target == 16
    assert outcome.resistance.resister.effective_target == resistance


def test_current_generic_scope_remains_true_without_numerical_reduction() -> None:
    build, compiler = approved(Purchase(definition_id="advantage:medium"))
    authored = current(
        definition_id="advantage:medium",
        kind="spirit",
        actor_score=12,
        actor_roll=9,
        resistance_score=10,
        resistance_roll=12,
        fatigue_cost=0,
    )
    _, _, outcome = apply_current_mental_use(
        seed(),
        world(),
        command().model_copy(update={"definition_id": "advantage:medium"}),
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert isinstance(outcome.resistance, ResistanceTrace) and outcome.resistance.rule_of_16
    assert outcome.resistance.attacker.effective_target == 12
    assert outcome.resistance.attacker.modifiers == ()


def test_historical_generic_scope_does_not_reinterpret_old_whitelist() -> None:
    build, compiler = approved(Purchase(definition_id="advantage:medium"))
    authored = channel(
        definition_id="advantage:medium",
        kind="spirit",
        actor_score=30,
        actor_roll=15,
        resistance_score=10,
        resistance_roll=3,
        fatigue_cost=0,
    )
    use = command().model_copy(update={"definition_id": "advantage:medium"})
    resources, changed, outcome = apply_mental_use(
        seed(),
        world(),
        use,
        build,
        compiler.definitions,
        (authored,),
        authorized_actor_id="a",
        system=True,
    )
    assert isinstance(outcome.resistance, ResistanceTrace) and not outcome.resistance.rule_of_16
    assert outcome.resistance.attacker.effective_target == 30 and outcome.outcome == "successful"
    assert "resistance_origin" not in resources.events[-1].kind
    assert replay_mental_use(
        seed(),
        world(),
        use,
        build,
        compiler.definitions,
        resources.events[-1],
        historical_channels=(authored,),
    ) == (resources, changed, outcome)
