"""Transactional adapter for the explicit combat encounter state machine."""

from __future__ import annotations

import hashlib
import json
from typing import Annotated, Literal

from pydantic import Field, TypeAdapter
from pydantic import ValidationError as SchemaError

from wayfarer.errors import ConflictError, ValidationError
from wayfarer.models import Campaign, Event
from wayfarer.orchestration.injury import resolve_injury
from wayfarer.orchestration.play import PlayService
from wayfarer.simulation.actions import PlayState
from wayfarer.simulation.adjudication import expire_rulings
from wayfarer.simulation.combat import (
    Combatant,
    CombatResult,
    Defense,
    Encounter,
    Facing,
    GridPoint,
    Maneuver,
    Placement,
    Posture,
)
from wayfarer.simulation.maneuvers import ATTACK_MANEUVERS, AttackOption, DefenseOption, WaitTrigger
from wayfarer.simulation.resources import Advance, Id, Record


class CombatCommand(Record):
    id: Id
    actor_id: Id
    expected_revision: int = Field(ge=0)


class StartEncounter(CombatCommand):
    kind: Literal["start_encounter"] = "start_encounter"
    encounter_id: Id
    battlefield_id: Id
    placements: tuple[Placement, ...] = Field(min_length=2)


class TakeCombatTurn(CombatCommand):
    kind: Literal["take_combat_turn"] = "take_combat_turn"
    encounter_id: Id
    maneuver: Maneuver
    destination: GridPoint | None = None
    facing: Facing | None = None
    posture: Posture | None = None
    item_id: str | None = None
    target_id: str | None = None
    mode_id: str | None = None
    attack_option: AttackOption | None = None
    defense_option: DefenseOption | None = None
    wait_trigger: WaitTrigger | None = None


class ResumeInterruptedTurn(CombatCommand):
    kind: Literal["resume_interrupted_turn"] = "resume_interrupted_turn"
    encounter_id: Id
    cancel: bool = False


class ChooseDefense(CombatCommand):
    kind: Literal["choose_defense"] = "choose_defense"
    encounter_id: Id
    defense: Defense
    item_id: str | None = None
    second_defense: Defense | None = None
    second_item_id: str | None = None


class JoinEncounter(CombatCommand):
    kind: Literal["join_encounter"] = "join_encounter"
    encounter_id: Id
    position: GridPoint
    facing: Facing = "north"


class EndEncounter(CombatCommand):
    kind: Literal["end_encounter"] = "end_encounter"
    encounter_id: Id
    reason: str = Field(min_length=1, max_length=1000)


TypedCombatCommand = Annotated[
    StartEncounter
    | TakeCombatTurn
    | ChooseDefense
    | JoinEncounter
    | EndEncounter
    | ResumeInterruptedTurn,
    Field(discriminator="kind"),
]
COMBAT_ADAPTER: TypeAdapter[TypedCombatCommand] = TypeAdapter(TypedCombatCommand)


class CombatService:
    def __init__(self, play: PlayService) -> None:
        self.play = play

    async def _recorded_result(self, cid: str, command_id: str) -> CombatResult:
        for entry in await self.play.store.history(cid):
            if entry.command_id == command_id:
                return CombatResult.model_validate_json(entry.event["outcome"])
        raise ValidationError("Missing committed combat receipt")

    @staticmethod
    def propose(value: object) -> TypedCombatCommand:
        try:
            return COMBAT_ADAPTER.validate_python(value)
        except SchemaError as exc:
            raise ValidationError("Invalid combat command") from exc

    @staticmethod
    def _encounter(state: PlayState, encounter_id: str) -> Encounter:
        encounter = next((e for e in state.encounters if e.id == encounter_id), None)
        if encounter is None:
            raise ValidationError("Unknown encounter")
        return encounter

    async def execute(
        self, cid: str, value: object, *, authenticated_actor_id: str
    ) -> CombatResult:
        command = self.propose(value)
        if command.actor_id != authenticated_actor_id:
            raise ValidationError("Combat command actor is not authorized")
        bound = self.play.for_campaign(await self.play.store.read(cid))
        if bound is not self.play:
            return await CombatService(bound).execute(
                cid, value, authenticated_actor_id=authenticated_actor_id
            )
        engine = self.play.engine.combat
        if engine is None:
            raise ValidationError("Campaign combat is not configured")
        if isinstance(command, (StartEncounter, EndEncounter)) and (
            command.actor_id not in self.play.engine.reviewer.gm_ids
        ):
            raise ValidationError("Encounter lifecycle requires GM authority")
        payload = json.dumps(
            {"operation": "combat", "command": command.model_dump(mode="json")},
            sort_keys=True,
            separators=(",", ":"),
        )
        duplicate = await self.play.store.duplicate(cid, command.id, payload)
        if duplicate is not None:
            return await self._recorded_result(cid, command.id)

        submitted_command = command

        def resolve(campaign: Campaign) -> Event:
            command = submitted_command
            state = self.play._load(campaign)
            initial_state = state
            resuming = False
            reaction = False
            if isinstance(command, ResumeInterruptedTurn):
                paused = self._encounter(state, command.encounter_id)
                interrupt = paused.wait_interrupt
                if (
                    interrupt is None
                    or not interrupt.ready
                    or interrupt.actor_id != command.actor_id
                ):
                    raise ConflictError("No interrupted turn is ready for this actor")
                saved = TakeCombatTurn.model_validate_json(interrupt.command_json)
                if command.cancel:
                    saved = saved.model_copy(
                        update={
                            "maneuver": "do_nothing",
                            "destination": None,
                            "facing": None,
                            "posture": None,
                            "item_id": None,
                            "target_id": None,
                            "mode_id": None,
                            "attack_option": None,
                            "defense_option": None,
                            "wait_trigger": None,
                        }
                    )
                command = saved.model_copy(
                    update={"id": command.id, "expected_revision": command.expected_revision}
                )
                state = state.model_copy(
                    update={
                        "encounters": tuple(
                            e.model_copy(update={"wait_interrupt": None})
                            if e.id == paused.id
                            else e
                            for e in state.encounters
                        )
                    }
                )
                resuming = True
            elif isinstance(command, TakeCombatTurn):
                paused = self._encounter(state, command.encounter_id)
                reaction = (
                    paused.wait_interrupt is not None
                    and paused.wait_interrupt.waiter_id == command.actor_id
                )
                if (
                    reaction
                    and paused.wait_interrupt is not None
                    and command.maneuver != "do_nothing"
                    and command.mode_id != paused.wait_interrupt.declaration.mode_id
                ):
                    raise ValidationError("Wait reaction must use the declared weapon mode")
            from wayfarer.orchestration.recovery import guard

            guard(state, command.actor_id, command.kind)
            if engine.rules.gurps_equipment is not None:
                from wayfarer.rules.recovery_types import require_settled

                affected = {command.actor_id}
                if isinstance(command, StartEncounter):
                    affected.update(p.actor_id for p in command.placements)
                elif isinstance(command, TakeCombatTurn) and command.target_id is not None:
                    affected.add(command.target_id)
                elif isinstance(command, ChooseDefense):
                    pending = self._encounter(state, command.encounter_id).pending_defense
                    if pending is not None:
                        affected.update((pending.attacker_id, pending.defender_id))
                require_settled(
                    state.resources.recovery_tasks, frozenset(affected), state.resources.game_time
                )
            if command.expected_revision != state.revision:
                raise ConflictError("Play revision changed")
            resources = state.resources
            if isinstance(command, StartEncounter):
                if any(e.id == command.encounter_id for e in state.encounters):
                    raise ConflictError("Encounter ID already exists")
                actor_map = {actor.actor_id: actor for actor in state.actors}
                if len({p.actor_id for p in command.placements}) != len(command.placements) or any(
                    p.actor_id not in actor_map for p in command.placements
                ):
                    raise ValidationError("Encounter placements require unique play actors")
                initiatives: dict[str, int] = {}
                pools = {pool.id: pool for pool in state.resources.pools}
                for placement in command.placements:
                    actor = actor_map[placement.actor_id]
                    if engine.rules.gurps_equipment is not None:
                        from wayfarer.orchestration.gurps_melee import fatigue_ready

                        if not fatigue_ready(state, actor.actor_id):
                            raise ValidationError("Exhausted actor cannot start combat")
                    hp = pools.get(f"hp:{actor.actor_id}")
                    if (
                        actor.conditions
                        or hp is None
                        or (hp.injury.incapacitated if hp.injury else hp.current == 0)
                    ):
                        raise ValidationError("Incapacitated actor cannot start combat")
                    build, _ = self.play.engine.reviewer.activate(
                        actor.proposal,
                        actor.approval,
                        campaign_id=state.campaign_id,
                        actor_id=actor.actor_id,
                    )
                    values = {value.target: int(value.value) for value in build.sheet.values}
                    initiatives[actor.actor_id] = values["attribute:dx"]
                encounter = engine.start(
                    command.encounter_id,
                    command.battlefield_id,
                    command.placements,
                    initiatives,
                    state.world,
                    resources,
                    frozenset(actor_map),
                )
                encounters = state.encounters + (encounter,)
                result = CombatResult(
                    encounter_id=encounter.id,
                    code="combat.started",
                    round=encounter.round,
                    current_actor_id=encounter.current_actor_id,
                    available=engine.available(encounter, encounter.current_actor_id),
                )
            else:
                encounter = self._encounter(state, command.encounter_id)
                if isinstance(command, JoinEncounter):
                    from wayfarer.simulation.party import group_for

                    if encounter.status != "active" or encounter.pending_defense is not None:
                        raise ConflictError("Reinforcements join between resolved combat stages")
                    if command.actor_id in encounter.turn_order:
                        raise ConflictError("Actor already participates")
                    joining_actor = next(
                        (a for a in state.actors if a.actor_id == command.actor_id), None
                    )
                    if (
                        joining_actor is None
                        or joining_actor.conditions
                        or joining_actor.available_at > resources.game_time
                    ):
                        raise ValidationError("Reinforcement joining_actor is unavailable")
                    if (
                        next(
                            p.current
                            for p in resources.pools
                            if p.id == f"hp:{joining_actor.actor_id}"
                        )
                        == 0
                    ):
                        raise ValidationError("Reinforcement joining_actor is incapacitated")
                    source = group_for(state, command.actor_id)
                    target_group = group_for(state, encounter.current_actor_id)
                    if (
                        source.id == target_group.id
                        or source.scene_id != target_group.scene_id
                        or source.paused
                        or target_group.paused
                    ):
                        raise ValidationError(
                            "Reinforcements must arrive from another reachable subgroup"
                        )
                    if (
                        source.ready_through != resources.game_time
                        or target_group.ready_through != resources.game_time
                        or any(
                            q.group_id in (source.id, target_group.id) for q in state.party.queue
                        )
                    ):
                        raise ConflictError("Reinforcement arrival requires synchronized time")
                    build, _ = self.play.engine.reviewer.activate(
                        joining_actor.proposal,
                        joining_actor.approval,
                        campaign_id=cid,
                        actor_id=joining_actor.actor_id,
                    )
                    initiative = int(
                        next(v.value for v in build.sheet.values if v.target == "attribute:dx")
                    )
                    participant = Combatant(
                        actor_id=joining_actor.actor_id,
                        initiative=initiative,
                        position=command.position,
                        facing=command.facing,
                        reach=engine.rules.default_reach,
                        movement_allowance=engine.rules.movement_allowance,
                        ready_item_ids=tuple(
                            sorted(
                                i.id
                                for i in resources.items
                                if i.owner_id == joining_actor.actor_id and i.equipped and i.ready
                            )
                        ),
                    )
                    joined_participants = encounter.participants + (participant,)
                    order = tuple(
                        p.actor_id
                        for p in sorted(
                            joined_participants, key=lambda p: (-p.initiative, p.actor_id)
                        )
                    )
                    current_actor = encounter.current_actor_id
                    encounter = encounter.model_copy(
                        update={
                            "participants": joined_participants,
                            "turn_order": order,
                            "turn_index": order.index(current_actor),
                        }
                    )
                    remaining = tuple(a for a in source.actor_ids if a != joining_actor.actor_id)
                    groups = tuple(
                        g.model_copy(
                            update={
                                "actor_ids": g.actor_ids + (joining_actor.actor_id,),
                                "generation": g.generation + 1,
                            }
                        )
                        if g.id == target_group.id
                        else g.model_copy(
                            update={"actor_ids": remaining, "generation": g.generation + 1}
                        )
                        if g.id == source.id
                        else g
                        for g in state.party.groups
                        if g.id != source.id or remaining
                    )
                    state = state.model_copy(
                        update={"party": state.party.model_copy(update={"groups": groups})}
                    )
                    result = CombatResult(
                        encounter_id=encounter.id,
                        code="combat.reinforcement_arrived",
                        round=encounter.round,
                        current_actor_id=current_actor,
                    )
                elif isinstance(command, TakeCombatTurn):
                    from wayfarer.simulation.abilities import interrupt_concentration

                    resources = interrupt_concentration(resources, command.actor_id, command.id)
                    state = state.model_copy(update={"resources": resources})
                    actor = next(a for a in state.actors if a.actor_id == command.actor_id)
                    hp = next(p for p in resources.pools if p.id == f"hp:{actor.actor_id}")
                    if (
                        hp.injury.incapacitated if hp.injury else hp.current == 0
                    ) or actor.conditions:
                        raise ValidationError("Incapacitated actor cannot act")
                    if actor.available_at > resources.game_time and command.maneuver not in (
                        "wait",
                        "do_nothing",
                    ):
                        raise ValidationError("Actor is recovering from injury")
                    if command.maneuver == "attack" and engine.rules.attacks:
                        item = next((i for i in resources.items if i.id == command.item_id), None)
                        if item is None or not any(
                            p.definition_id == item.definition_id for p in engine.rules.attacks
                        ):
                            raise ValidationError("Unsupported combat weapon or attack mode")
                    if command.mode_id is not None and (
                        command.maneuver not in ATTACK_MANEUVERS | {"feint", "aim"}
                        or engine.rules.gurps_equipment is None
                    ):
                        raise ValidationError("Weapon mode requires GURPS attack dispatch")
                    if (
                        command.maneuver in ATTACK_MANEUVERS | {"feint"}
                        and engine.rules.gurps_equipment is not None
                    ):
                        from wayfarer.orchestration.gurps_melee import mode

                        selected_mode = mode(
                            self.play,
                            state,
                            command.actor_id,
                            command.item_id or "",
                            command.mode_id,
                        )
                        encounter = engine._replace(
                            encounter,
                            next(
                                p for p in encounter.participants if p.actor_id == command.actor_id
                            ).model_copy(update={"reach": max(selected_mode.reach)}),
                        )
                    if engine.rules.gurps_equipment is not None:
                        from wayfarer.orchestration.gurps_melee import (
                            exertion,
                            injury_turn,
                            movement,
                        )

                        participant = next(
                            p for p in encounter.participants if p.actor_id == command.actor_id
                        )
                        forced = participant.forced_do_nothing
                        if not forced and not (hp.injury and hp.injury.stunned):
                            preview, _, preview_result = engine.take_turn(
                                encounter,
                                actor_id=command.actor_id,
                                maneuver=command.maneuver,
                                resources=resources,
                                destination=command.destination,
                                facing=command.facing,
                                posture=command.posture,
                                item_id=command.item_id,
                                target_id=command.target_id,
                                command_id=command.id,
                                attack_option=command.attack_option,
                                defense_option=command.defense_option,
                                wait_trigger=command.wait_trigger,
                                command_json=command.model_dump_json(),
                            )
                            if preview.pending_defense is not None:
                                from wayfarer.orchestration.gurps_melee import prepare_attack

                                prepare_attack(self.play, state, preview, command.mode_id)
                            if (
                                command.maneuver == "aim"
                                and preview_result.code != "combat.wait_triggered"
                            ):
                                from wayfarer.orchestration.gurps_maneuvers import observe

                                observe(self.play, state, preview, command)

                        if not resuming and not reaction:
                            state = injury_turn(
                                self.play,
                                state,
                                command.actor_id,
                                command.id,
                                start=True,
                                do_nothing=command.maneuver == "do_nothing"
                                or forced
                                or bool(hp.injury and hp.injury.stunned),
                            )
                        resources = state.resources
                        started_hp = next(p for p in resources.pools if p.id == hp.id)
                        assert started_hp.injury is not None
                        allowed = not (
                            started_hp.injury.incapacitated or started_hp.injury.stunned or forced
                        )
                        if allowed and command.maneuver != "do_nothing" and not resuming:
                            state, allowed = exertion(
                                self.play, state, command.actor_id, command.id
                            )
                            resources = state.resources
                        encounter = engine._replace(
                            encounter,
                            next(
                                p for p in encounter.participants if p.actor_id == command.actor_id
                            ).model_copy(
                                update={
                                    "movement_allowance": movement(
                                        self.play, state, command.actor_id
                                    )
                                    if allowed
                                    else 0
                                }
                            ),
                        )
                        if not allowed:
                            command_for_turn = command.model_copy(
                                update={
                                    "maneuver": "do_nothing",
                                    "item_id": None,
                                    "mode_id": None,
                                    "target_id": None,
                                    "destination": None,
                                    "facing": None,
                                    "posture": None,
                                    "attack_option": None,
                                    "defense_option": None,
                                    "wait_trigger": None,
                                }
                            )
                        else:
                            command_for_turn = command
                    else:
                        command_for_turn = command
                    encounter, resources, result = engine.take_turn(
                        encounter,
                        actor_id=command.actor_id,
                        maneuver=command_for_turn.maneuver,
                        resources=resources,
                        destination=command_for_turn.destination,
                        facing=command_for_turn.facing,
                        posture=command_for_turn.posture,
                        item_id=command_for_turn.item_id,
                        target_id=command_for_turn.target_id,
                        command_id=command.id,
                        attack_option=command_for_turn.attack_option,
                        defense_option=command_for_turn.defense_option,
                        wait_trigger=command_for_turn.wait_trigger,
                        command_json=command_for_turn.model_dump_json(),
                    )
                    if (
                        engine.rules.gurps_equipment is not None
                        and result.code != "combat.wait_triggered"
                    ):
                        acted = next(
                            p for p in encounter.participants if p.actor_id == command.actor_id
                        )
                        encounter = engine._replace(
                            encounter, acted.model_copy(update={"forced_do_nothing": False})
                        )
                    if (
                        command_for_turn.maneuver in ("aim", "feint")
                        or command_for_turn.attack_option == "feint"
                    ) and result.code != "combat.wait_triggered":
                        from wayfarer.orchestration.gurps_maneuvers import observe

                        encounter = observe(self.play, state, encounter, command_for_turn)
                    if (
                        command_for_turn.maneuver in ATTACK_MANEUVERS
                        and engine.rules.gurps_equipment is not None
                        and result.code != "combat.wait_triggered"
                    ):
                        from wayfarer.orchestration.gurps_melee import prepare_attack

                        encounter = prepare_attack(self.play, state, encounter, command.mode_id)
                        assert encounter.pending_defense is not None
                        result = result.model_copy(
                            update={"available": encounter.pending_defense.allowed}
                        )
                    elif (
                        engine.rules.gurps_equipment is not None
                        and not reaction
                        and result.code != "combat.wait_triggered"
                    ):
                        state = injury_turn(
                            self.play,
                            state.model_copy(update={"resources": resources}),
                            command.actor_id,
                            command.id,
                            start=False,
                            do_nothing=command_for_turn.maneuver == "do_nothing",
                        )
                        resources = state.resources
                elif isinstance(command, ChooseDefense):
                    from wayfarer.simulation.abilities import interrupt_concentration

                    if command.defense != "none":
                        resources = interrupt_concentration(
                            resources, command.actor_id, command.id, distraction=True
                        )
                        state = state.model_copy(update={"resources": resources})
                    previous = encounter
                    selected_defense = command.defense
                    if engine.rules.gurps_equipment is not None:
                        from wayfarer.orchestration.gurps_melee import exertion, resolve_melee

                        pending = encounter.pending_defense
                        if (
                            pending is None
                            or pending.defender_id != command.actor_id
                            or command.defense not in pending.allowed
                        ):
                            raise ValidationError("Defense is not available to this actor")
                        from wayfarer.orchestration.gurps_melee import validate_defense_choices

                        validate_defense_choices(
                            self.play,
                            state,
                            encounter,
                            selected_defense,
                            command.item_id,
                            command.second_defense,
                            command.second_item_id,
                        )
                        if selected_defense != "none":
                            state, allowed = exertion(
                                self.play, state, command.actor_id, command.id
                            )
                            if not allowed:
                                selected_defense = "none"
                        state, encounter, injury = resolve_melee(
                            self.play,
                            state,
                            encounter,
                            selected_defense,
                            command.item_id,
                            second_defense=command.second_defense
                            if selected_defense != "none"
                            else None,
                            second_item_id=command.second_item_id
                            if selected_defense != "none"
                            else None,
                        )
                        from wayfarer.orchestration.gurps_melee import injury_turn

                        attacker = next(
                            p for p in encounter.participants if p.actor_id == pending.attacker_id
                        )
                        if not attacker.maneuver_state.attacks_remaining and (
                            encounter.wait_interrupt is None
                            or not encounter.wait_interrupt.reacting
                        ):
                            state = injury_turn(
                                self.play,
                                state,
                                pending.attacker_id,
                                pending.id,
                                start=False,
                                do_nothing=False,
                            )
                        resources = state.resources
                    elif (
                        command.item_id is not None
                        or command.second_defense is not None
                        or command.second_item_id is not None
                    ):
                        raise ValidationError("Defense equipment selection requires GURPS dispatch")
                    encounter, result = engine.choose_defense(
                        encounter, actor_id=command.actor_id, selected=selected_defense
                    )
                    if (
                        encounter.pending_defense is not None
                        and engine.rules.gurps_equipment is not None
                    ):
                        from wayfarer.orchestration.gurps_melee import prepare_attack

                        encounter = prepare_attack(
                            self.play, state, encounter, encounter.pending_defense.mode_id
                        )
                    if engine.rules.attacks or engine.rules.gurps_equipment is not None:
                        if engine.rules.gurps_equipment is None:
                            state, injury = resolve_injury(
                                self.play, state, previous, command.defense
                            )
                        resources = state.resources
                        encounter = encounter.model_copy(
                            update={"wounds": encounter.wounds + (injury,)}
                        )
                        alive = {
                            p.id.removeprefix("hp:")
                            for p in resources.pools
                            if p.id.startswith("hp:")
                            and (not p.injury.incapacitated if p.injury else p.current > 0)
                        }
                        if len(alive.intersection(encounter.turn_order)) < 2:
                            encounter = encounter.model_copy(
                                update={
                                    "status": "completed",
                                    "completion_reason": "incapacitation",
                                    "pending_defense": None,
                                    "wait_interrupt": None,
                                }
                            )
                        else:
                            while encounter.current_actor_id not in alive:
                                encounter = engine._advance(encounter)
                        result = result.model_copy(
                            update={
                                "code": "combat.resolved",
                                "injury": injury,
                                "round": encounter.round,
                                "current_actor_id": encounter.current_actor_id,
                                "available": engine.available(
                                    encounter, encounter.current_actor_id
                                ),
                            }
                        )
                else:
                    if (
                        encounter.status != "active"
                        or encounter.pending_defense is not None
                        or encounter.blocked_reason
                    ):
                        raise ConflictError("Encounter cannot end during a pending defense")
                    encounter = encounter.model_copy(
                        update={"status": "completed", "completion_reason": command.reason}
                    )
                    result = CombatResult(
                        encounter_id=encounter.id,
                        code="combat.completed",
                        round=encounter.round,
                        current_actor_id=encounter.current_actor_id,
                    )
                encounters = tuple(
                    encounter if e.id == encounter.id else e for e in state.encounters
                )
            if (
                engine.rules.gurps_equipment is not None
                and encounter.pending_defense is None
                and encounter.status == "active"
            ):
                from wayfarer.orchestration.gurps_melee import fatigue_ready

                conscious = {
                    p.id.removeprefix("hp:")
                    for p in resources.pools
                    if p.id.startswith("hp:")
                    and p.injury is not None
                    and not p.injury.incapacitated
                    and fatigue_ready(
                        state.model_copy(update={"resources": resources}), p.id.removeprefix("hp:")
                    )
                }
                if len(conscious.intersection(encounter.turn_order)) < 2:
                    encounter = encounter.model_copy(
                        update={"status": "completed", "completion_reason": "incapacitation"}
                    )
                else:
                    while encounter.current_actor_id not in conscious:
                        encounter = engine._advance(encounter)
                encounters = tuple(encounter if e.id == encounter.id else e for e in encounters)
                result = result.model_copy(
                    update={
                        "round": encounter.round,
                        "current_actor_id": encounter.current_actor_id,
                        "available": engine.available(encounter, encounter.current_actor_id),
                    }
                )
            # A full combat round contributes one original-subset tick to its subgroup.
            party = state.party
            if party.groups:
                participants = set(encounter.turn_order)
                involved = tuple(g for g in party.groups if set(g.actor_ids) & participants)
                if len(involved) != 1 or not participants <= set(involved[0].actor_ids):
                    raise ValidationError("Combat participants must share one subgroup")
                group = involved[0]
                if group.paused or any(q.group_id == group.id for q in party.queue):
                    raise ConflictError("Combat subgroup is paused or has pending activity")
                if group.ready_through > resources.game_time:
                    raise ConflictError("Combat waits at the shared-time barrier")
                prior = next((e for e in state.encounters if e.id == encounter.id), None)
                ticks = max(0, encounter.round - prior.round) if prior is not None else 0
                party = party.model_copy(
                    update={
                        "groups": tuple(
                            g.model_copy(update={"ready_through": g.ready_through + ticks})
                            if g.id == group.id
                            else g
                            for g in party.groups
                        )
                    }
                )
            elif (
                engine.rules.attacks
                or engine.rules.gurps_equipment is not None
                or self.play.engine.rules.abilities
            ):
                prior = next((e for e in state.encounters if e.id == encounter.id), None)
                ticks = max(0, encounter.round - prior.round) if prior is not None else 0
                if ticks:
                    resources = self.play.engine.resources.apply(
                        resources,
                        Advance(
                            id="combat-time:" + hashlib.sha256(command.id.encode()).hexdigest()
                            if engine.rules.gurps_equipment
                            else f"{command.id}:round-time",
                            actor_id=encounter.current_actor_id,
                            expected_revision=resources.revision,
                            to=resources.game_time + ticks,
                        ),
                        system=True,
                    )
            revision = state.revision + 1
            resources = resources.model_copy(update={"revision": revision})
            updated = state.model_copy(
                update={
                    "revision": revision,
                    "party": party,
                    "resources": resources,
                    "encounters": encounters,
                    "last_combat_result": result,
                    "rulings": expire_rulings(state.rulings, revision, resources.game_time),
                }
            )
            if updated.party.groups:
                from wayfarer.orchestration.party import PartyService

                updated = PartyService(self.play).flush(updated)
            if (
                isinstance(command, ChooseDefense)
                and engine.rules.attacks
                and encounter.status == "completed"
            ):
                world = updated.world
                for consequence in engine.rules.consequences:
                    if (
                        consequence.battlefield_id == encounter.battlefield_id
                        and previous.pending_defense is not None
                        and consequence.defeated_actor_id == previous.pending_defense.defender_id
                        and injury.incapacitated
                    ):
                        for recipient in consequence.recipient_actor_ids:
                            for fact in consequence.fact_ids:
                                world = world.learn(recipient, fact)
                updated = updated.model_copy(update={"world": world})
            updated = self.play.checkpoint(updated, before=initial_state)
            self.play.engine.validate(updated)
            campaign["revision"], campaign["play_json"] = revision, updated.model_dump_json()
            return Event(
                input=payload, action="combat", outcome=result.model_dump_json(), roll=None
            )

        committed = await self.play.store.commit_turn(
            cid,
            command.id,
            command.expected_revision,
            payload,
            resolve,
            actor_id=command.actor_id,
        )
        if committed["kind"] == "replayed":
            return await self._recorded_result(cid, command.id)
        result = self.play._load(committed["state"]).last_combat_result
        if result is None:
            raise ValidationError("Missing committed combat result")
        return result
