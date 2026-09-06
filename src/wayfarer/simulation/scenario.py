"""Fixed demo scenario and validation."""
def scenario():
    return {'title': 'The Lantern at Blackwater', 'premise': 'A courier vanished before the last ferry. A blue lantern still burns at the abandoned customs house.',
            'location': 'Blackwater docks', 'contact': 'Iven, the ferryman', 'clue': 'A torn ferry manifest points to the customs house.',
            'secret': 'The courier is sheltering a witness inside the customs house.', 'objective': 'Find the missing courier'}


def validate_scenario(s):
    if not isinstance(s, dict) or set(s) != set(scenario()):
        raise ValueError('Scenario must have exactly the required fields')
    if any(not isinstance(v, str) or not 1 <= len(v) <= 2000 for v in s.values()):
        raise ValueError('Scenario fields must contain 1–2000 characters')


