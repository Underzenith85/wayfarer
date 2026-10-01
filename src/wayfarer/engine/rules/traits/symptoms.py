"""Named executable Symptoms construction (B35-36, B109); other effects remain unsupported."""

from wayfarer.engine.rules.traits.modifiers import ModifierSelection
from wayfarer.engine.rules.types.symptoms import SymptomSpec
from wayfarer.errors import ValidationError

SYMPTOMS = "modifier:enhancement:symptoms"


def symptom_spec(selection: ModifierSelection) -> SymptomSpec:
    parameters = selection.parameters
    if parameters is None or parameters.symptom is None or parameters.symptom_threshold is None:
        raise ValidationError("Symptoms requires a named effect and threshold")
    numerator, denominator = {"one-third": (1, 3), "one-half": (1, 2), "two-thirds": (2, 3)}[
        parameters.symptom_threshold
    ]
    name = parameters.symptom
    if name in {"blindness", "coughing"}:
        return SymptomSpec.model_validate(
            {"kind": name, "numerator": numerator, "denominator": denominator}
        )
    parts = name.split(":")
    if (
        len(parts) != 3
        or parts[0] != "attribute-penalty"
        or parts[1] not in {"st", "dx", "iq", "ht"}
        or not parts[2].isdigit()
    ):
        raise ValidationError("Symptoms effect has no executable typed consumer")
    return SymptomSpec.model_validate(
        {
            "kind": "attribute-penalty",
            "attribute": parts[1],
            "level": int(parts[2]),
            "numerator": numerator,
            "denominator": denominator,
        }
    )


def symptom_percentage(spec: SymptomSpec) -> int:
    base = (
        50
        if spec.kind == "blindness"
        else 20
        if spec.kind == "coughing"
        else (5 if spec.attribute in {"st", "ht"} else 10) * spec.level
    )
    multiplier = (
        3 if spec.denominator == 3 and spec.numerator == 1 else 2 if spec.denominator == 2 else 1
    )
    return base * multiplier
