"""Preserved demo character construction; full compiler is tracked separately."""
from wayfarer.rules.catalog import ATTR_COST, BUDGET, SKILLS, TRAITS

def validate(c):
    errors = []
    if not isinstance(c, dict):
        return {'valid': False, 'errors': ['Character must be an object'], 'spent': 0, 'remaining': BUDGET, 'levels': {}}
    if set(c) - {'name', 'concept', 'attributes', 'skills', 'traits'}:
        errors.append('Unknown character fields are forbidden')
    for key in ('name', 'concept'):
        if not isinstance(c.get(key), str) or not 1 <= len(c[key]) <= 1000:
            errors.append(f'{key} must contain 1–1000 characters')
    attrs, skills, traits = c.get('attributes'), c.get('skills'), c.get('traits')
    if not isinstance(attrs, dict) or set(attrs) != set(ATTR_COST):
        errors.append('Exactly ST, DX, IQ and HT are required')
        attrs = {k: 10 for k in ATTR_COST}
    spent, disadvantage, levels = 0, 0, {}
    for k, cost in ATTR_COST.items():
        v = attrs[k]
        if type(v) is not int or not 8 <= v <= 14:
            errors.append(f'{k} must be an integer from 8 to 14')
            continue
        delta = (v - 10) * cost
        spent += delta
        disadvantage += max(0, -delta)
    if not isinstance(skills, dict):
        errors.append('Skills must be an object'); skills = {}
    for name, points in skills.items():
        if name not in SKILLS or type(points) is not int or points not in (1, 2, 4, 8, 12, 16):
            errors.append(f'Invalid skill or point allocation: {name}'); continue
        spent += points
        attr, base = SKILLS[name]
        if type(attrs[attr]) is not int:
            continue
        level = attrs[attr] + base + ({1: 0, 2: 1}.get(points, 2 + (points - 4) // 4))
        levels[name] = level
        if level > 16:
            errors.append(f'{name} exceeds the campaign skill ceiling of 16')
    if not isinstance(traits, list) or any(not isinstance(t, str) for t in traits):
        errors.append('Traits must be a list of names'); traits = []
    if len(set(traits)) != len(traits):
        errors.append('Duplicate traits are forbidden')
    for trait in traits:
        if trait not in TRAITS:
            errors.append(f'Trait is not allowed: {trait}'); continue
        spent += TRAITS[trait]
        disadvantage += max(0, -TRAITS[trait])
    if disadvantage > 25:
        errors.append('Disadvantages, including reduced attributes, exceed 25 points')
    if spent > BUDGET:
        errors.append(f'Character exceeds the {BUDGET}-point budget by {spent - BUDGET}')
    return {'valid': not errors, 'errors': errors, 'spent': spent, 'remaining': BUDGET-spent, 'levels': levels}

def character():
    return {'name': 'Mira Voss', 'concept': 'A curious investigator with a debt to repay.',
            'attributes': {'ST': 10, 'DX': 11, 'IQ': 12, 'HT': 11},
            'skills': {'Stealth': 4, 'Observation': 4, 'Diplomacy': 4, 'Survival': 2},
            'traits': ['Curious', 'Keen senses']}
