"""Private command metadata with digest-checked, byte-exact intent identity."""

import json

from wayfarer import validation
from wayfarer.errors import ValidationError
from wayfarer.persistence.ability_resistance_inputs import KEY as ABILITY_RESISTANCE_KEY
from wayfarer.persistence.ability_resistance_inputs import intent as ability_resistance_intent
from wayfarer.persistence.ability_resistance_inputs import validate_keys as validate_ability_keys
from wayfarer.persistence.events import CommandInput, payload_digest
from wayfarer.persistence.hazard_care_inputs import KEY as HAZARD_CARE_KEY
from wayfarer.persistence.hazard_care_inputs import intent as hazard_care_intent
from wayfarer.persistence.hazard_care_inputs import validate_keys as validate_hazard_care_keys
from wayfarer.persistence.healing_physician_inputs import KEY as HEALING_PHYSICIAN_KEY
from wayfarer.persistence.healing_physician_inputs import intent as physician_intent
from wayfarer.persistence.healing_physician_inputs import validate_keys as validate_physician_keys
from wayfarer.persistence.medical_bandage_inputs import KEY as MEDICAL_BANDAGE_KEY
from wayfarer.persistence.medical_bandage_inputs import intent as medical_bandage_intent
from wayfarer.persistence.medical_bandage_inputs import validate_keys as validate_bandage_keys
from wayfarer.persistence.medical_minimum_inputs import KEY as MEDICAL_MINIMUM_KEY
from wayfarer.persistence.medical_minimum_inputs import intent as medical_minimum_intent
from wayfarer.persistence.medical_minimum_inputs import validate_keys as validate_medical_keys

KEY = "symptom_attribute_generation"
WRAPPED_INPUT = "symptom_command_input"
ORIGINAL_INPUT = "symptom_original_input"
DEFENSE_POSTURE_KEY = "defense_posture_generation"
RANGED_TARGET_POSTURE_KEY = "ranged_target_posture_generation"
DEFAULT_KNOWLEDGE_KEY = "default_knowledge_generation"
REACTION_KEY = "reaction_semantics_generation"
REACTION_ORIGINAL = "reaction_original_input"
REACTION_WRAPPED = "reaction_command_input"
ARMOURY_OPERATING_KEY = "operating_tools_generation"
ARMOURY_OPERATING_ORIGINAL = "operating_tools_original_input"


def object_input(text: str) -> dict[str, object] | None:
    try:
        decoded = validation.decode(text)
    except json.JSONDecodeError:
        return None
    return validation.mapping(decoded) if isinstance(decoded, dict) else None


def canonical(payload: dict[str, object]) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def reaction_metadata(payload: dict[str, object] | None) -> bool:
    if payload is None:
        return False
    if REACTION_KEY not in payload:
        if REACTION_ORIGINAL in payload or REACTION_WRAPPED in payload:
            raise ValidationError("Reaction generation metadata requires its version")
        return False
    if type(payload[REACTION_KEY]) is not int or payload[REACTION_KEY] not in (1, 2, 3, 4):
        raise ValidationError("Unsupported recorded reaction generation")
    if REACTION_WRAPPED in payload:
        if set(payload) != {REACTION_KEY, REACTION_WRAPPED} or not isinstance(
            payload[REACTION_WRAPPED], str
        ):
            raise ValidationError("Invalid recorded reaction input wrapper")
        if object_input(validation.string(payload[REACTION_WRAPPED])) is not None:
            raise ValidationError("Reaction wrapper cannot hide an object input")
    else:
        raw = payload.get(REACTION_ORIGINAL)
        source = object_input(raw) if isinstance(raw, str) else None
        intended = {
            key: value
            for key, value in payload.items()
            if key not in {REACTION_KEY, REACTION_ORIGINAL}
        }
        if (
            source is None
            or {REACTION_KEY, REACTION_ORIGINAL, REACTION_WRAPPED}.intersection(source)
            or canonical(source) != canonical(intended)
        ):
            raise ValidationError("Invalid recorded reaction original input")
    return True


def metadata(payload: dict[str, object] | None) -> bool:
    if payload is None:
        return False
    if DEFENSE_POSTURE_KEY in payload:
        if KEY not in payload:
            raise ValidationError("Defense posture generation requires command metadata")
        if type(payload[DEFENSE_POSTURE_KEY]) is not int or payload[DEFENSE_POSTURE_KEY] != 1:
            raise ValidationError("Unsupported recorded defense posture generation")
    if RANGED_TARGET_POSTURE_KEY in payload:
        if KEY not in payload:
            raise ValidationError("Ranged target posture generation requires command metadata")
        if (
            type(payload[RANGED_TARGET_POSTURE_KEY]) is not int
            or payload[RANGED_TARGET_POSTURE_KEY] != 1
        ):
            raise ValidationError("Unsupported recorded ranged target posture generation")
    if DEFAULT_KNOWLEDGE_KEY in payload:
        if KEY not in payload:
            raise ValidationError("Default knowledge generation requires command metadata")
        if type(payload[DEFAULT_KNOWLEDGE_KEY]) is not int or payload[
            DEFAULT_KNOWLEDGE_KEY
        ] not in (1, 2):
            raise ValidationError("Unsupported recorded default knowledge generation")
    if KEY not in payload:
        return False
    generation = payload[KEY]
    if type(generation) is not int or generation not in (1, 2, 3):
        raise ValidationError("Unsupported recorded Symptoms attribute generation")
    if WRAPPED_INPUT in payload:
        expected = {KEY, WRAPPED_INPUT}
        if DEFENSE_POSTURE_KEY in payload:
            expected.add(DEFENSE_POSTURE_KEY)
        if RANGED_TARGET_POSTURE_KEY in payload:
            expected.add(RANGED_TARGET_POSTURE_KEY)
        if DEFAULT_KNOWLEDGE_KEY in payload:
            expected.add(DEFAULT_KNOWLEDGE_KEY)
        if set(payload) != expected or not isinstance(payload[WRAPPED_INPUT], str):
            raise ValidationError("Invalid recorded Symptoms input wrapper")
    if ORIGINAL_INPUT in payload:
        original = payload[ORIGINAL_INPUT]
        decoded = object_input(original) if isinstance(original, str) else None
        intended = {
            key: value
            for key, value in payload.items()
            if key
            not in {
                KEY,
                ORIGINAL_INPUT,
                DEFENSE_POSTURE_KEY,
                RANGED_TARGET_POSTURE_KEY,
                DEFAULT_KNOWLEDGE_KEY,
            }
        }
        if decoded is None or canonical(decoded) != canonical(intended):
            raise ValidationError("Invalid recorded Symptoms original input")
    return True


def generation(record: CommandInput) -> bool:
    return generation_version(record) > 0


def generation_version(record: CommandInput) -> int:
    if record.text is None:
        return 0
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded command input does not match its digest")
    payload = object_input(record.text)
    return validation.integer(payload[KEY]) if metadata(payload) and payload is not None else 0


def defense_posture_generation(record: CommandInput) -> bool:
    if record.text is None:
        return False
    if payload_digest({"input": record.text}) != record.payload_hash:
        raise ValidationError("Recorded command input does not match its digest")
    payload = object_input(record.text)
    return bool(metadata(payload) and payload is not None and DEFENSE_POSTURE_KEY in payload)


def stamp_defense_postures(text: str) -> str:
    """Add one independent mechanic generation without rewriting caller bytes."""
    payload = object_input(text)
    if not metadata(payload) or payload is None:
        raise ValidationError("Defense posture generation requires command metadata")
    payload[DEFENSE_POSTURE_KEY] = 1
    return canonical(payload)


def ranged_target_posture_generation(record: CommandInput) -> bool:
    generation_version(record)
    payload = object_input(record.text) if record.text is not None else None
    return bool(metadata(payload) and payload is not None and RANGED_TARGET_POSTURE_KEY in payload)


def stamp_ranged_target_postures(text: str) -> str:
    payload = object_input(text)
    if not metadata(payload) or payload is None:
        raise ValidationError("Ranged target posture generation requires command metadata")
    payload[RANGED_TARGET_POSTURE_KEY] = 1
    return canonical(payload)


def default_knowledge_generation(record: CommandInput) -> bool:
    return default_knowledge_version(record) > 0


def default_knowledge_version(record: CommandInput) -> int:
    generation_version(record)
    payload = object_input(record.text) if record.text is not None else None
    return (
        validation.integer(payload[DEFAULT_KNOWLEDGE_KEY])
        if metadata(payload) and payload is not None and DEFAULT_KNOWLEDGE_KEY in payload
        else 0
    )


def stamp_default_knowledge(text: str, *, version: int = 1) -> str:
    if type(version) is not int or version not in (1, 2):
        raise ValidationError("Unsupported recorded default knowledge generation")
    payload = object_input(text)
    if not metadata(payload) or payload is None:
        raise ValidationError("Default knowledge generation requires command metadata")
    payload[DEFAULT_KNOWLEDGE_KEY] = version
    return canonical(payload)


def stamp(text: str, *, retain_original: bool = True, version: int = 1) -> str:
    if type(version) is not int or version not in (1, 2, 3):
        raise ValidationError("Unsupported recorded Symptoms attribute generation")
    payload = object_input(text)
    if metadata(payload):
        return text
    if payload is None:
        payload = {WRAPPED_INPUT: text}
    elif retain_original:
        payload[ORIGINAL_INPUT] = text
    payload[KEY] = version
    return canonical(payload)


def original_input(text: str) -> str:
    payload = object_input(text)
    if not metadata(payload):
        return text
    assert payload is not None
    if WRAPPED_INPUT in payload:
        return validation.string(payload[WRAPPED_INPUT])
    if ORIGINAL_INPUT in payload:
        return validation.string(payload[ORIGINAL_INPUT])
    # The first metadata-bearing version kept only this canonical object. Keep
    # its exact encoded identity; do not accept arbitrary whitespace changes.
    return canonical(
        {
            key: value
            for key, value in payload.items()
            if key
            not in {KEY, DEFENSE_POSTURE_KEY, RANGED_TARGET_POSTURE_KEY, DEFAULT_KNOWLEDGE_KEY}
        }
    )


def same_input(record: CommandInput, requested: str) -> bool:
    """Verify stored bytes before removing only validated private metadata."""
    validate_armoury_keys(requested)
    validate_hazard_care_keys(requested)
    validate_medical_keys(requested)
    validate_bandage_keys(requested)
    validate_physician_keys(requested)
    validate_ability_keys(requested)
    if record.text is not None:
        validate_armoury_keys(record.text)
        validate_hazard_care_keys(record.text)
        validate_medical_keys(record.text)
        validate_bandage_keys(record.text)
        validate_physician_keys(record.text)
        validate_ability_keys(record.text)
    recorded_generation = generation(record)
    requested_input = medical_bandage_intent(
        ability_resistance_intent(
            hazard_care_intent(
                medical_minimum_intent(
                    physician_intent(
                        armoury_intent(great_haste_intent(combat_intent(intent_input(requested))))
                    )
                )
            )
        )
    )
    recorded_input = (
        medical_bandage_intent(
            ability_resistance_intent(
                hazard_care_intent(
                    medical_minimum_intent(
                        physician_intent(
                            armoury_intent(
                                great_haste_intent(combat_intent(intent_input(record.text)))
                            )
                        )
                    )
                )
            )
        )
        if record.text is not None
        else None
    )
    if payload_digest({"input": requested}) == record.payload_hash:
        return True
    armoury_payload = object_input(intent_input(record.text)) if record.text is not None else None
    recorded_armoury = bool(
        armoury_payload is not None and ARMOURY_OPERATING_KEY in armoury_payload
    )
    recorded_care = bool(armoury_payload is not None and HAZARD_CARE_KEY in armoury_payload)
    recorded_medical = bool(armoury_payload is not None and MEDICAL_MINIMUM_KEY in armoury_payload)
    recorded_bandage = bool(armoury_payload is not None and MEDICAL_BANDAGE_KEY in armoury_payload)
    recorded_physician = bool(
        armoury_payload is not None and HEALING_PHYSICIAN_KEY in armoury_payload
    )
    recorded_ability = bool(
        armoury_payload is not None and ABILITY_RESISTANCE_KEY in armoury_payload
    )
    if record.text is None or not (
        recorded_generation
        or reaction_metadata(object_input(record.text))
        or recorded_armoury
        or recorded_care
        or recorded_medical
        or recorded_bandage
        or recorded_physician
        or recorded_ability
    ):
        return False
    return recorded_input == requested_input


def combat_intent(text: str) -> str:
    """Remove validated features only from the host's canonical combat envelope."""
    payload = object_input(text)
    key = "combat_protocol_features"
    task_key = "task_combat_protocol_features"
    if payload is not None and task_key in payload:
        raw_task = payload[task_key]
        command = payload.get("command")
        response = command.get("response") if isinstance(command, dict) else None
        fragment = isinstance(command, dict) and command.get("kind") in (
            "prepare-opponent-fragment",
            "choose-opponent-fragment",
            "amend-fragment-responses",
        )
        defense = (
            isinstance(command, dict)
            and command.get("kind") in ("choose-opponent-attack", "prepare-owner-damage")
            and isinstance(response, dict)
            and response.get("kind") == "choose_defense"
        )
        begin = isinstance(command, dict) and command.get("kind") == "begin-opponent-attack"
        if (
            key in payload
            or payload.get("operation") != "task-host"
            or not isinstance(command, dict)
            or not (fragment or defense or begin)
            or not isinstance(raw_task, list)
            or any(not isinstance(item, str) for item in raw_task)
            or len(set(raw_task)) != len(raw_task)
            or not set(raw_task)
            <= (
                {"ground-dive-step", "secondary-object-blasts"}
                if fragment
                else {
                    "missile-interposition",
                    "karate-kicking-technique",
                    "staff-form-contact",
                    "hand-melee-striking-skills",
                    "melee-attack-postures",
                    "voluntary-attack-minimum",
                    "regular-contest-extreme-scores",
                }
                if begin
                else {
                    "grenade-fuse",
                    "missile-interposition",
                    "acrobatic-trait-bonuses",
                    "acrobatic-reaction-attributes",
                    "acrobatic-condition-checks",
                    "acrobatic-skill-minimum",
                    "interposition-current-step",
                    "staff-heavy-bare-parry",
                    "retreat-postures",
                    "hand-melee-striking-skills",
                    "melee-attack-postures",
                    "voluntary-attack-minimum",
                    "karate-kicking-technique",
                    "staff-form-contact",
                    "regular-contest-extreme-scores",
                }
            )
            or text != canonical(payload)
        ):
            raise ValidationError("Invalid recorded task combat feature generation")
        return canonical({name: value for name, value in payload.items() if name != task_key})
    if payload is None or key not in payload:
        return text
    raw = payload[key]
    if (
        payload.get("operation") not in {"combat", "combat-random-unarmed", "composed-defense"}
        or not isinstance(raw, list)
        or any(not isinstance(item, str) for item in raw)
        or len(set(raw)) != len(raw)
        or not set(raw)
        <= {
            "grenade-fuse",
            "maneuver-budget",
            "acrobatic-trait-bonuses",
            "acrobatic-reaction-attributes",
            "acrobatic-condition-checks",
            "acrobatic-skill-minimum",
            "interposition-current-step",
            "staff-heavy-bare-parry",
            "retreat-postures",
            "hand-melee-striking-skills",
            "melee-attack-postures",
            "voluntary-attack-minimum",
            "ground-dive-step",
            "secondary-object-blasts",
            "missile-interposition",
            "paralyze-buckler-drop",
            "rooted-dodge-health-trait-composition",
            "rooted-dodge-haste-composition",
            "hand-melee-spell-contact",
            "karate-kicking-technique",
            "staff-form-contact",
            "regular-contest-extreme-scores",
            "repair-operating-tools",
        }
        or (
            payload.get("operation") == "composed-defense"
            and not set(raw)
            <= {
                "acrobatic-skill-minimum",
                "acrobatic-trait-bonuses",
                "acrobatic-condition-checks",
                "acrobatic-reaction-attributes",
            }
        )
        or text != canonical(payload)
    ):
        raise ValidationError("Invalid recorded combat feature generation")
    return canonical({name: value for name, value in payload.items() if name != key})


def intent_input(text: str) -> str:
    """Recover exact caller bytes through the validated, ordered private layers."""
    raw = original_input(text)
    payload = object_input(raw)
    if not reaction_metadata(payload):
        return raw
    assert payload is not None
    return validation.string(
        payload[REACTION_WRAPPED] if REACTION_WRAPPED in payload else payload[REACTION_ORIGINAL]
    )


def replay_payload(text: str) -> object:
    raw = intent_input(text)
    try:
        return validation.decode(raw)
    except json.JSONDecodeError:
        return raw


def great_haste_intent(text: str) -> str:
    """Recover exact private host bytes only through validated generation metadata."""
    payload = object_input(text)
    key, original = "great_haste_combat_generation", "great_haste_original_input"
    if payload is None or not {key, original}.intersection(payload):
        return text
    raw = payload.get(original)
    source = object_input(raw) if isinstance(raw, str) else None
    intended = {name: value for name, value in payload.items() if name not in {key, original}}
    if (
        payload.get("operation") != "great-haste"
        or type(payload.get(key)) is not int
        or payload[key] != 1
        or source is None
        or {key, original}.intersection(source)
        or canonical(source) != canonical(intended)
    ):
        raise ValidationError("Invalid recorded Great Haste combat generation")
    return validation.string(raw)


def armoury_intent(text: str) -> str:
    """Strip only authenticated Armoury policy, retaining exact public input bytes."""
    validate_armoury_keys(text)
    payload = object_input(text)
    key, original = ARMOURY_OPERATING_KEY, ARMOURY_OPERATING_ORIGINAL
    if payload is None or not {key, original}.intersection(payload):
        return text
    raw = payload.get(original)
    source = object_input(raw) if isinstance(raw, str) else None
    intended = {name: value for name, value in payload.items() if name not in {key, original}}
    if (
        type(payload.get(key)) is not int
        or payload.get(key) != 1
        or payload.get("operation") != "armoury"
        or type(payload.get("generation")) is not int
        or payload.get("generation") != 1
        or set(payload) != {"operation", "generation", "principal_id", "command", key, original}
        or source is None
        or {key, original}.intersection(source)
        or canonical(source) != canonical(intended)
        or text != canonical(payload)
    ):
        raise ValidationError("Invalid recorded Armoury operating-tool generation")
    return validation.string(raw)


def _armoury_pairs(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in {ARMOURY_OPERATING_KEY, ARMOURY_OPERATING_ORIGINAL} and key in result:
            raise ValidationError("Duplicate recorded Armoury operating-tool generation metadata")
        result[key] = value
    return result


def validate_armoury_keys(text: str) -> None:
    """Reject duplicate private keys before any outer input wrapper is removed."""
    if ARMOURY_OPERATING_KEY not in text and ARMOURY_OPERATING_ORIGINAL not in text:
        return
    try:
        json.loads(text, object_pairs_hook=_armoury_pairs)
    except json.JSONDecodeError:
        return
