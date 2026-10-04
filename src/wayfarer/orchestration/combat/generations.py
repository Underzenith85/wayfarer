"""Capture private combat feature generations from exact recorded inputs."""

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.orchestration.replay_inputs import recorded_command
from wayfarer.orchestration.sessions import Store
from wayfarer.persistence.command_inputs import replay_payload
from wayfarer.persistence.events import CommandInput, payload_digest

KEY = "combat_protocol_features"
KNOWN = frozenset(
    {
        "grenade-fuse",
        "maneuver-budget",
        "acrobatic-trait-bonuses",
        "acrobatic-reaction-attributes",
        "ground-dive-step",
        "secondary-object-blasts",
        "missile-interposition",
        "paralyze-buckler-drop",
        "rooted-dodge-health-trait-composition",
        "rooted-dodge-haste-composition",
        "hand-melee-spell-contact",
    }
)
ACTIVE = KNOWN


def features(record: CommandInput) -> frozenset[str]:
    if record.text is None:
        return frozenset()
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded combat input does not match its digest")
    payload = validation.mapping(replay_payload(record.text))
    if payload.get("operation") not in ("combat", "combat-random-unarmed"):
        return frozenset()
    raw = payload.get(KEY, [])
    if not isinstance(raw, list) or any(not isinstance(item, str) for item in raw):
        raise ValidationError("Invalid recorded combat feature generation")
    selected = frozenset(validation.string(item) for item in raw)
    if len(selected) != len(raw) or not selected <= KNOWN:
        raise ValidationError("Unsupported recorded combat feature generation")
    return selected


async def capture(store: Store, cid: str, command_id: str) -> frozenset[str]:
    prior = recorded_command.get()
    record = (
        CommandInput(prior.payload_hash, prior.command_input)
        if prior is not None
        else await store.command_input(cid, command_id)
    )
    return ACTIVE if record is None else features(record)
