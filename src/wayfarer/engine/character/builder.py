"""Preserved demo character construction; full compiler is tracked separately."""

from wayfarer import validation
from wayfarer.engine.rules.catalog import ATTR_COST, BUDGET, SKILLS, TRAITS
from wayfarer.models import Character, ValidationResult


def validate(value: object) -> ValidationResult:
    try:
        c = validation.character(value)
    except ValueError as exc:
        return {"valid": False, "errors": [str(exc)], "spent": 0, "remaining": BUDGET, "levels": {}}
    errors: list[str] = []
    for text in (c["name"], c["concept"]):
        if not 1 <= len(text) <= 1000:
            errors.append("Name and concept must contain 1–1000 characters")
    attrs, skills, traits = c["attributes"], c["skills"], c["traits"]
    spent, disadvantage = 0, 0
    levels: dict[str, int] = {}
    for k, cost in ATTR_COST.items():
        v = attrs[k]
        if type(v) is not int or not 8 <= v <= 14:
            errors.append(f"{k} must be an integer from 8 to 14")
            continue
        delta = (v - 10) * cost
        spent += delta
        disadvantage += max(0, -delta)
    for name, points in skills.items():
        if name not in SKILLS or type(points) is not int or points not in (1, 2, 4, 8, 12, 16):
            errors.append(f"Invalid skill or point allocation: {name}")
            continue
        spent += points
        attr, base = SKILLS[name]
        if type(attrs[attr]) is not int:
            continue
        level = attrs[attr] + base + ({1: 0, 2: 1}.get(points, 2 + (points - 4) // 4))
        levels[name] = level
        if level > 16:
            errors.append(f"{name} exceeds the campaign skill ceiling of 16")
    if len(set(traits)) != len(traits):
        errors.append("Duplicate traits are forbidden")
    for trait in traits:
        if trait not in TRAITS:
            errors.append(f"Trait is not allowed: {trait}")
            continue
        spent += TRAITS[trait]
        disadvantage += max(0, -TRAITS[trait])
    if disadvantage > 25:
        errors.append("Disadvantages, including reduced attributes, exceed 25 points")
    if spent > BUDGET:
        errors.append(f"Character exceeds the {BUDGET}-point budget by {spent - BUDGET}")
    return {
        "valid": not errors,
        "errors": errors,
        "spent": spent,
        "remaining": BUDGET - spent,
        "levels": levels,
    }


def character() -> Character:
    return {
        "name": "Mira Voss",
        "concept": "A curious investigator with a debt to repay.",
        "attributes": {"ST": 10, "DX": 11, "IQ": 12, "HT": 11},
        "skills": {"Stealth": 4, "Observation": 4, "Diplomacy": 4, "Survival": 2},
        "traits": ["Curious", "Keen senses"],
    }
