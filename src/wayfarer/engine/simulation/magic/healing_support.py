"""B248 lending on canonical pools; borrowed vitality expires on the shared clock."""

from wayfarer.engine.rules.types.hazard import blocked_fp, blocked_hp
from wayfarer.engine.simulation.health.healing import restore_hp
from wayfarer.engine.simulation.magic.spell_state import SpellEffect
from wayfarer.engine.simulation.resources import ResourceEvent, ResourceState
from wayfarer.errors import ValidationError
from wayfarer.models import Record

SUPPORT = frozenset({"lend-energy", "lend-vitality"})
PREFIX = "lent-vitality:"


class LentVitality(Record):
    cast_id: str
    target_id: str
    amount: int
    due: int
    expired: bool = False


def loans(state: ResourceState) -> tuple[LentVitality, ...]:
    found = {}
    for event in state.events:
        if event.id.startswith(PREFIX):
            loan = LentVitality.model_validate_json(event.kind)
            found[loan.cast_id] = loan
    return tuple(found.values())


def expire_vitality(
    state: ResourceState, at: int, *, cancel_cast_id: str | None = None
) -> ResourceState:
    old = loans(state)
    expired = tuple(
        loan
        for loan in old
        if not loan.expired and (loan.due <= at or loan.cast_id == cancel_cast_id)
    )
    expired_ids = {loan.cast_id for loan in expired}
    pools = {p.id: p for p in state.pools}
    for target_id in {loan.target_id for loan in expired}:
        before = max(
            (loan.amount for loan in old if loan.target_id == target_id and not loan.expired),
            default=0,
        )
        after = max(
            (
                loan.amount
                for loan in old
                if loan.target_id == target_id
                and not loan.expired
                and loan.cast_id not in expired_ids
            ),
            default=0,
        )
        hp = pools["hp:" + target_id]
        pools[hp.id] = hp.model_copy(update={"current": hp.current - before + after})
    events = tuple(
        ResourceEvent(
            id=PREFIX + loan.cast_id + ":end",
            at=min(at, loan.due),
            target_id=loan.target_id,
            kind=loan.model_copy(update={"expired": True}).model_dump_json(),
        )
        for loan in expired
    )
    return state.model_copy(
        update={"pools": tuple(pools.values()), "events": state.events + events}
    )


def apply_support(
    state: ResourceState, effect: SpellEffect, command_id: str
) -> tuple[ResourceState, int, int]:
    if effect.spell_id == "lend-energy":
        fp = next((p for p in state.pools if p.id == "fp:" + effect.target_id), None)
        if fp is None or fp.fatigue is None or fp.fatigue.profile_id != "gurps-basic-set-4e-2004":
            raise ValidationError("Lend Energy requires canonical patient FP")
        status = fp.fatigue
        eligible = max(
            0,
            fp.maximum
            - fp.current
            - status.starvation
            - status.dehydration
            - status.sleep
            - blocked_fp(state.illnesses, effect.target_id),
        )
        restored = min(effect.energy, eligible)
        current = fp.current + restored
        status = status.model_copy(
            update={
                "power": max(0, status.power - max(0, restored - max(0, eligible - status.power))),
                "collapsed": status.collapsed and current <= 0,
                "unconscious": status.unconscious and current <= 0,
            }
        )
        new = fp.model_copy(update={"current": current, "fatigue": status})
        return (
            state.model_copy(
                update={"pools": tuple(new if p.id == fp.id else p for p in state.pools)}
            ),
            0,
            restored,
        )
    # B237: only the most powerful active instance counts; temporary HP do not stack.
    hp = next(p for p in state.pools if p.id == "hp:" + effect.target_id)
    borrowed = max(
        (
            loan.amount
            for loan in loans(state)
            if loan.target_id == effect.target_id and not loan.expired
        ),
        default=0,
    )
    eligible = max(
        0,
        hp.maximum - hp.current + borrowed - blocked_hp(state.illnesses, effect.target_id, "magic"),
    )
    amount = min(effect.energy, eligible)
    increase = max(0, amount - borrowed)
    new, restored = restore_hp(state, hp, increase, kind="magic")
    loan = LentVitality(
        cast_id=effect.cast_id,
        target_id=effect.target_id,
        amount=amount,
        due=state.game_time + 3600,
    )
    return (
        state.model_copy(
            update={
                "pools": tuple(new if p.id == hp.id else p for p in state.pools),
                "events": state.events
                + (
                    ResourceEvent(
                        id=PREFIX + effect.cast_id,
                        at=state.game_time,
                        target_id=effect.target_id,
                        kind=loan.model_dump_json(),
                    ),
                ),
            }
        ),
        restored,
        0,
    )


def validate_support(state: ResourceState, spell_id: str, target_id: str) -> None:
    if spell_id != "lend-energy":
        return
    fp = next((p for p in state.pools if p.id == "fp:" + target_id), None)
    if fp is None or fp.fatigue is None or fp.fatigue.profile_id != "gurps-basic-set-4e-2004":
        raise ValidationError("Lend Energy requires canonical patient FP")
