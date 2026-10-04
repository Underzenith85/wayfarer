"""Current physical admission and immutable actual spell-producer selection."""

from wayfarer import validation
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.actors import build, fatigue_ready
from wayfarer.engine.simulation.campaign.party import synchronous
from wayfarer.engine.simulation.health.recovery_guard import guard
from wayfarer.engine.simulation.magic.analyze_magic_state import secrets as analysis_secrets
from wayfarer.engine.simulation.magic.backfires import forgotten, require_settled
from wayfarer.engine.simulation.magic.concentration import require_no_held_melee
from wayfarer.engine.simulation.magic.haste_state import environments
from wayfarer.engine.simulation.magic.identify_spell_state import (
    IdentifySubject,
    IdentifySubjectObservation,
    ObservedSpell,
    observations,
    secrets,
)
from wayfarer.engine.simulation.magic.rituals import require_ordinary_ritual
from wayfarer.engine.simulation.magic.spell_state import (
    PREFIX as SPELL_PREFIX,
)
from wayfarer.engine.simulation.magic.spell_state import (
    RUNTIME_PREFIX,
    active_spells,
    latest,
    parse_event,
)
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Id, Record


class AcceptedSpellProducer(Record):
    command_id: Id
    actor_id: Id
    cast_id: Id
    spell_id: Id
    event_id: Id
    event_json: str
    event_at: int
    event_target_id: str
    kind: str


def observation(
    state: PlayState, claim: IdentifySubject, command_id: str
) -> IdentifySubjectObservation:
    caster = next((e for e in state.world.entities if e.id == claim.caster_id), None)
    target = next((e for e in state.world.entities if e.id == claim.subject_id), None)
    actors = {a.actor_id for a in state.actors}
    if caster is None or target is None or target.id not in actors or caster.id not in actors:
        raise ValidationError("Bounded identification requires actual actor subjects")
    if caster.location_id is None or caster.location_id != target.location_id:
        raise ConflictError("Identify Spell touching observation is not currently co-located")
    if len({u.spell_id for u in claim.unfamiliar}) != len(claim.unfamiliar):
        raise ValidationError("Duplicate unfamiliar spell observation")
    return IdentifySubjectObservation(
        command_id=command_id, subject=claim, location_id=caster.location_id
    )


def current_subject(state: PlayState, subject_id: str, actor_id: str) -> IdentifySubjectObservation:
    found = next((o for o in observations(state.resources) if o.subject.id == subject_id), None)
    if found is None or found.subject.caster_id != actor_id:
        raise ConflictError("Identify Spell physical observation is unavailable to caster")
    current = observation(state, found.subject, found.command_id)
    if current != found:
        raise ConflictError("Identify Spell physical subject observation changed")
    return found


def ready(runtime: RulesContext, state: PlayState, actor_id: str) -> int:
    if runtime.reviewer.compiler.statistics_profile != "gurps-basic-set-4e-2004":
        raise ValidationError("Identify Spell requires the Basic Set profile")
    guard(state, actor_id, "identify_spell_cast")
    synchronous(state, actor_id)
    require_settled(state.resources, actor_id)
    require_no_held_melee(state.resources, actor_id)
    if forgotten(state.resources, actor_id, "identify-spell"):
        raise ConflictError("Identify Spell is currently forgotten")
    if len(state.party.groups) > 1 or any(e.status == "active" for e in state.encounters):
        raise ConflictError("Bounded Identify Spell requires a synchronous noncombat party")
    if any(e.actor_id == actor_id for e in active_spells(state.resources)):
        raise ConflictError("Bounded Identify Spell does not admit existing spells on")
    actor = next(a for a in state.actors if a.actor_id == actor_id)
    if (
        actor.available_at > state.resources.game_time
        or actor.conditions
        or not fatigue_ready(state, actor_id)
    ):
        raise ConflictError("Identify Spell caster is unavailable")
    hp = next(p for p in state.resources.pools if p.id == "hp:" + actor_id)
    if hp.current <= 1 or hp.injury is None or hp.injury.incapacitated or hp.injury.stunned:
        raise ConflictError("Bounded Identify Spell requires a healthy conscious caster")
    if hp.injury.shock:
        raise ConflictError("Bounded Identify Spell does not admit temporal injury shock")
    fp = next(p for p in state.resources.pools if p.id == "fp:" + actor_id)
    if fp.current < 2:
        raise ConflictError("Identify Spell requires its full two FP before dice")
    compiled = build(runtime, state, actor_id)
    skill = next(
        (v.value for v in compiled.sheet.values if v.target == "spell:identify-spell"), None
    )
    purchased = any(
        p.definition_id == "spell:identify-spell" and p.amount > 0 for p in compiled.purchases
    )
    if not purchased or skill is None or not 10 <= skill <= 14:
        raise ValidationError("Bounded Identify Spell requires purchased skill 10 through 14")
    caster = next(e for e in state.world.entities if e.id == actor_id)
    if environments(state.resources).get(caster.location_id or "") != "normal":
        raise ValidationError("Identify Spell requires authenticated normal mana")
    if any(
        s.actor_id == actor_id and s.at // 86400 == state.resources.game_time // 86400
        for s in secrets(state.resources)
    ):
        raise ConflictError("Caster already attempted Identify Spell today")
    require_ordinary_ritual(runtime, state, actor_id, compiled, int(skill))
    return int(skill)


def _private_spell_producers(state: PlayState, target: str) -> None:
    latest_casts: dict[tuple[str, str], tuple[str, str, int]] = {}
    detected: set[tuple[str, int]] = set()
    for event in state.resources.events:
        if event.id.startswith("detect-magic:finding:"):
            detected.add((event.target_id, event.at))
        prefix = next(
            (p for p in ("analyze-magic:", "detect-magic:") if event.id.startswith(p + "cast:")),
            None,
        )
        if prefix is not None:
            data = validation.mapping(validation.decode(event.kind))
            actor, cast, status = data.get("actor_id"), data.get("cast_id"), data.get("status")
            if (
                not isinstance(actor, str)
                or not isinstance(cast, str)
                or not isinstance(status, str)
            ):
                raise ConflictError("Unsupported Information casting identity is invalid")
            latest_casts[(prefix, cast)] = (actor, status, event.at)
    pending = any(
        actor == target and status in {"casting", "ready"}
        for actor, status, _ in latest_casts.values()
    )
    recent_detect = any(
        prefix == "detect-magic:"
        and actor == target
        and status == "rolled"
        and (actor, at) in detected
        and 0 <= state.resources.game_time - at <= 5
        for (prefix, _), (actor, status, at) in latest_casts.items()
    )
    recent_analysis = any(
        s.actor_id == target
        and s.check.outcome.succeeded
        and 0 <= state.resources.game_time - s.at <= 5
        for s in analysis_secrets(state.resources)
    )
    recent_identify = any(
        (s.actor_id == target or s.subject_id == target)
        and s.check.outcome.succeeded
        and 0 <= state.resources.game_time - s.at <= 5
        for s in secrets(state.resources)
    )
    if pending or recent_detect or recent_analysis or recent_identify:
        raise ConflictError(
            "Bounded Identify Spell has a relevant unsupported private spell producer"
        )
    projects = {p.id: p for p in state.resources.enchantment_projects}
    if any(
        p.status == "active" and p.active_work is not None and target in p.active_work.enchanter_ids
        for p in projects.values()
    ):
        raise ConflictError("Bounded Identify Spell has a relevant unsupported Enchant producer")
    for event in state.resources.events:
        if (
            not event.id.startswith("enchantment:")
            or not 0 <= state.resources.game_time - event.at <= 5
        ):
            continue
        project = projects.get(event.target_id)
        if project is None or target not in project.enchanter_ids:
            continue
        result = validation.mapping(validation.decode(event.kind))
        if result.get("status") == "completed" and result.get("check") is not None:
            raise ConflictError(
                "Bounded Identify Spell has a relevant unsupported Enchant producer"
            )


def _complete_inventory(
    state: PlayState, target: str, producers: tuple[AcceptedSpellProducer, ...]
) -> None:
    _private_spell_producers(state, target)
    accepted = {p.event_id for p in producers}
    starts = {(p.actor_id, p.cast_id, p.spell_id) for p in producers if p.kind == "start"}
    current = latest(state.resources)
    for event in state.resources.events:
        if not event.id.startswith((SPELL_PREFIX, RUNTIME_PREFIX)):
            continue
        record = parse_event(event)
        effect = record.effect
        if effect.actor_id.startswith("item:") or (
            effect.actor_id != target
            and (effect.spell_id == "fireball" or effect.target_id != target)
        ):
            continue
        casting = effect.phase == "casting" and current.get(effect.cast_id) == effect
        if record.result.outcome == "resisted" and 0 <= state.resources.game_time - event.at <= 5:
            raise ConflictError("Bounded Identify Spell does not admit recent resisted casts")
        completed = (
            record.result.outcome == "active"
            and bool(record.result.checks)
            and record.result.checks[0].outcome.succeeded
            and 0 <= state.resources.game_time - event.at <= 5
        )
        covered_casting = casting and (effect.actor_id, effect.cast_id, effect.spell_id) in starts
        if ((casting and not covered_casting) or completed) and event.id not in accepted:
            raise ConflictError("Identify Spell has a relevant unsupported casting producer")


def observed_spells(
    state: PlayState,
    subject: IdentifySubjectObservation,
    producers: tuple[AcceptedSpellProducer, ...],
) -> tuple[ObservedSpell, ...]:
    _complete_inventory(state, subject.subject.subject_id, producers)
    current = latest(state.resources)
    actual = {e.id: e for e in state.resources.events}
    found: dict[str, ObservedSpell] = {}
    unfamiliar = {u.spell_id: u.description for u in subject.subject.unfamiliar}
    for producer in producers:
        event = actual.get(producer.event_id)
        if event is None or (event.kind, event.at, event.target_id) != (
            producer.event_json,
            producer.event_at,
            producer.event_target_id,
        ):
            raise ConflictError("Accepted spell producer no longer matches immutable event")
        record = parse_event(event)
        effect = record.effect
        if (effect.actor_id, effect.cast_id, effect.spell_id) != (
            producer.actor_id,
            producer.cast_id,
            producer.spell_id,
        ):
            raise ConflictError("Accepted producer effect identity mismatch")
        target = subject.subject.subject_id
        if effect.actor_id != target and (
            effect.spell_id == "fireball" or effect.target_id != target
        ):
            continue
        if effect.actor_id.startswith("item:"):
            continue
        casting = (
            producer.kind == "start"
            and current.get(effect.cast_id) is not None
            and current[effect.cast_id].phase == "casting"
        )
        completed = (
            producer.kind == "complete"
            and record.result.outcome == "active"
            and bool(record.result.checks)
            and record.result.checks[0].outcome.succeeded
            and 0 <= state.resources.game_time - event.at <= 5
        )
        if not casting and not completed:
            continue
        found[effect.cast_id] = ObservedSpell(
            event_id=event.id,
            cast_id=effect.cast_id,
            spell_id=effect.spell_id,
            caster_id=effect.actor_id,
            subject_id=effect.target_id,
            at=event.at,
            status="casting" if casting else "completed",
            description=unfamiliar.get(effect.spell_id, effect.spell_id.replace("-", " ").title()),
        )
    return tuple(found[k] for k in sorted(found))
