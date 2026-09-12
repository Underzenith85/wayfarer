"""Derive private spell roll context exclusively from approved, pinned builds."""

from typing import Literal

from pydantic import Field

from wayfarer.engine.rules.gurps_magic import definitions, magery_level
from wayfarer.engine.rules.magic_protocols import MagicItemBinding, effective_item_power
from wayfarer.engine.simulation.actions import PlayState
from wayfarer.engine.simulation.rules_context import RulesContext
from wayfarer.engine.simulation.spells import PROFILE, SPELLS, SpellCommand, SpellContext
from wayfarer.errors import ValidationError
from wayfarer.models import Id, Record


class SpellEnvironment(Record):
    """Authored world facts only; never skill, Magery, HT or approval claims.

    This private adapter remains unavailable to player payloads until concrete
    effects and their world/combat channels have executable consumers.
    """

    target_id: Id
    mana: Literal["none", "low", "normal", "high", "very-high"] = "normal"
    distance: int = Field(default=0, ge=0, le=10000)
    radius: int = Field(default=1, ge=1, le=100)
    energy: int = Field(default=1, ge=1, le=100)
    magic_item: MagicItemBinding | None = None


def approved_context(
    runtime: RulesContext, state: PlayState, command: SpellCommand, environment: SpellEnvironment
) -> SpellContext:
    compiler = runtime.reviewer.compiler
    if compiler.statistics_profile != PROFILE:
        raise ValidationError("Spellcasting requires the exact Basic Set profile")
    spell_key = "spell:" + command.spell_id
    expected = {d.id: d for d in definitions(2)}
    permitted = tuple(next(d for d in definitions(v) if d.id == spell_key) for v in (1, 2))
    if compiler.definitions.get(spell_key) not in permitted:
        raise ValidationError("Spell is not bound to the pinned learning catalog")
    actor = next((a for a in state.actors if a.actor_id == command.actor_id), None)
    if actor is None or actor.approval is None:
        raise ValidationError("Caster requires an approved build")
    build, _ = runtime.reviewer.activate(
        actor.proposal, actor.approval, campaign_id=state.campaign_id, actor_id=actor.actor_id
    )
    purchases = {p.definition_id: p.amount for p in build.purchases}
    if spell_key not in purchases and environment.magic_item is None:
        raise ValidationError("Spell was not purchased and approved")
    values = {v.target: int(v.value) for v in build.sheet.values}
    learned = tuple(
        k.removeprefix("spell:") for k in purchases if k in expected and k.startswith("spell:")
    )
    skill = values.get(spell_key)
    item_reduction = 0
    if environment.magic_item is not None:
        item = environment.magic_item
        if item.spell_id != command.spell_id:
            raise ValidationError("Magic item does not carry the selected spell")
        power = effective_item_power(item.power, environment.mana)
        if power is None or power < 15:
            raise ValidationError("Magic item has insufficient Power in this mana level")
        if item.requires_magery and magery_level(purchases) < 0:
            raise ValidationError("Magic item requires Magery")
        skill = power
        learned = tuple(
            dict.fromkeys((*learned, command.spell_id, *SPELLS[command.spell_id].prerequisites))
        )
        item_reduction = item.power_reduction
    if skill is None:
        raise ValidationError("Spell has no compiled skill")
    target_ht = 10
    if SPELLS[command.spell_id].kind == "resisted":
        target = next((a for a in state.actors if a.actor_id == environment.target_id), None)
        if target is None or target.approval is None:
            raise ValidationError("Resisted spell requires an approved target build")
        target_build, _ = runtime.reviewer.activate(
            target.proposal,
            target.approval,
            campaign_id=state.campaign_id,
            actor_id=target.actor_id,
        )
        target_ht = next(
            int(v.value) for v in target_build.sheet.values if v.target == "attribute:ht"
        )
    return SpellContext(
        profile_id=PROFILE,
        build_revision=build.revision,
        skill=skill,
        magery=magery_level(purchases),
        learned=learned,
        ht=values["attribute:ht"],
        will=values["secondary:will"],
        iq=values["attribute:iq"],
        target_ht=target_ht,
        unavailable=bool(actor.conditions) or actor.available_at > state.resources.game_time,
        item_power_reduction=item_reduction,
        **environment.model_dump(exclude={"magic_item"}),
    )
