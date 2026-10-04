"""Authenticated secret Information casting and GM reporting transaction."""

import json

from wayfarer import validation
from wayfarer.contracts import Campaign, CommandReceipt
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.magic.identify_spell_admission import (
    AcceptedSpellProducer,
    current_subject,
    observed_spells,
)
from wayfarer.engine.simulation.magic.identify_spell_state import (
    ADAPTER,
    CastIdentifySpell,
    DirectorIdentifySpellResult,
    IdentifySpellCommand,
    IdentifySpellResult,
    ObserveIdentifySpellSubject,
    ReportIdentifySpell,
    identifier,
    secret_result,
    secrets,
)
from wayfarer.engine.simulation.magic.identify_spell_transitions import apply
from wayfarer.engine.simulation.magic.spell_state import event_id, parse_event
from wayfarer.engine.simulation.magic.spells import RuntimeSpellCommand
from wayfarer.errors import ConflictError
from wayfarer.orchestration.membership import member_for
from wayfarer.orchestration.pipeline import CommandPlan, Controls, Seats, Trusted, submit
from wayfarer.orchestration.play import PlayService
from wayfarer.persistence.events import CommandRecord


class IdentifySpellService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    def plan(
        self,
        play: PlayService,
        state: PlayState,
        command: IdentifySpellCommand,
        *,
        principal_id: str,
        producers: tuple[AcceptedSpellProducer, ...] = (),
    ) -> CommandPlan[IdentifySpellResult]:
        member = member_for(state, principal_id)
        trusted = isinstance(command, (ObserveIdentifySpellSubject, ReportIdentifySpell))
        payload = json.dumps(
            {
                "operation": "identify-spell",
                "principal_id": principal_id,
                "command": command.model_dump(mode="json"),
                "producer_generation": 1,
                "producer_evidence": [p.model_dump(mode="json") for p in producers],
            },
            sort_keys=True,
        )

        def resolve(campaign: Campaign) -> CommandReceipt:
            before = play._load(campaign)
            updated, result = apply(play.rules_context, before, command, producers)
            revision = before.revision + 1
            updated = updated.model_copy(
                update={
                    "revision": revision,
                    "resources": updated.resources.model_copy(update={"revision": revision}),
                }
            )
            updated = play.checkpoint(updated, before=before)
            play.commit(campaign, updated)
            return CommandReceipt(action="resource", outcome="identify-spell:" + result.outcome)

        async def outcome(campaign: Campaign) -> IdentifySpellResult:
            resources = play._load(campaign).resources
            value = IdentifySpellResult.model_validate_json(
                next(e.kind for e in resources.events if e.id == identifier("receipt", command.id))
            )
            if (
                member.role == "gm"
                and principal_id in play.engine.reviewer.gm_ids
                and hasattr(command, "cast_id")
            ):
                return DirectorIdentifySpellResult(
                    **value.model_dump(), secret=secret_result(resources, command.cast_id)
                )
            return value

        return CommandPlan(
            command_id=command.id,
            expected_revision=command.expected_revision,
            payload=payload,
            resolve=resolve,
            actor_id=principal_id,
            outcome=outcome,
            control=(Seats(state), Trusted(play.engine.reviewer.gm_ids))
            if trusted
            else (Controls(member, command.actor_id, state=state),),
            rng=play.rng,
        )

    async def execute(
        self,
        cid: str,
        value: object,
        *,
        principal_id: str,
        replay_producers: tuple[AcceptedSpellProducer, ...] | None = None,
    ) -> IdentifySpellResult:
        command = ADAPTER.validate_python(value)
        campaign = await self.play.store.read(cid)
        play = self.play.for_campaign(campaign)
        current = play._load(campaign)
        if isinstance(command, CastIdentifySpell):
            Controls(member_for(current, principal_id), command.actor_id, state=current)(
                principal_id
            )
        producers = replay_producers
        if producers is None:
            producers = ()
            if isinstance(command, CastIdentifySpell):
                original = play._load(await play.store.replay(cid, command.expected_revision))
                if any(
                    s.actor_id == command.actor_id
                    and s.at // 86400 == original.resources.game_time // 86400
                    for s in secrets(original.resources)
                ):
                    raise ConflictError("Caster already attempted Identify Spell today")
                physical = current_subject(original, command.subject_id, command.actor_id)
                accepted = _producers(
                    [
                        r
                        for r in await play.store.history(cid)
                        if r.resulting_revision <= command.expected_revision
                    ]
                )
                end = original.model_copy(
                    update={
                        "resources": original.resources.model_copy(
                            update={"game_time": original.resources.game_time + 1}
                        )
                    }
                )
                relevant = {s.event_id for s in observed_spells(end, physical, accepted)}
                producers = tuple(p for p in accepted if p.event_id in relevant)
        return await submit(
            play,
            cid,
            self.plan(
                play, play._load(campaign), command, principal_id=principal_id, producers=producers
            ),
            principal_id=principal_id,
        )


def _producers(records: list[CommandRecord]) -> tuple[AcceptedSpellProducer, ...]:
    result: list[AcceptedSpellProducer] = []
    for record in records:
        if record.command_input is None:
            continue
        data = validation.mapping(validation.decode(record.command_input))
        if data.get("operation") not in {"haste", "water", "spell-lifecycle", "lock-spell"}:
            continue
        raw = validation.mapping(data["command"])
        if raw.get("kind") not in {"start", "complete"}:
            continue
        command = RuntimeSpellCommand.model_validate(raw)
        if command.kind not in {"start", "complete"}:
            continue
        if command.id != record.command_id or data.get("principal_id") != record.actor_id:
            raise ValueError("Accepted spell command identity mismatch")
        state = PlayState.model_validate_json(record.state_after["play_json"])
        event = next(
            (e for e in state.resources.events if e.id == event_id(command.id, command.spell_id)),
            None,
        )
        if event is None:
            continue
        effect = parse_event(event).effect
        if (effect.actor_id, effect.cast_id, effect.spell_id) != (
            command.actor_id,
            command.cast_id,
            command.spell_id,
        ):
            raise ValueError("Accepted spell event identity mismatch")
        result.append(
            AcceptedSpellProducer(
                command_id=command.id,
                actor_id=command.actor_id,
                cast_id=command.cast_id,
                spell_id=command.spell_id,
                event_id=event.id,
                event_json=event.kind,
                event_at=event.at,
                event_target_id=event.target_id,
                kind=command.kind,
            )
        )
    return tuple(result)
