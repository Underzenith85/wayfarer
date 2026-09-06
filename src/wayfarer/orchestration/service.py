"""Application orchestration: validate, interpret, commit, then narrate."""
import copy
import os
import uuid
from pathlib import Path
from typing import cast

from wayfarer.character import builder
from wayfarer.models import Action, Campaign, Character
from wayfarer.orchestration import llm
from wayfarer.persistence.sqlite import SQLiteStore
from wayfarer.rules import catalog
from wayfarer.simulation.resolution import resolve
from wayfarer.simulation.scenario import scenario, validate_scenario

DB = Path(os.getenv('WAYFARER_DB', 'data/wayfarer.sqlite3'))


def create(c: Character, s: dict[str, str]) -> dict[str, object]:
    verdict = builder.validate(c)
    if not verdict['valid']:
        raise ValueError('; '.join(verdict['errors']))
    validate_scenario(s)
    cid = str(uuid.uuid4())
    state: Campaign = {'id': cid, 'revision': 0, 'rules': catalog.VERSION, 'character': c, 'scenario': s,
        'hp': c['attributes']['ST'], 'fp': c['attributes']['HT'], 'minutes': 0,
        'location': s['location'], 'inventory': ['Travel clothes', 'Rations', 'Waterskin'],
        'discoveries': [], 'flags': [], 'complete': False,
        'messages': [{'role': 'gm', 'text': s['premise']}]}
    SQLiteStore(DB).insert(state)
    return public(state)


def read(cid: str) -> Campaign:
    return SQLiteStore(DB).read(cid)


def public(state: Campaign) -> dict[str, object]:
    result = cast(dict[str, object], copy.deepcopy(state))
    visible_scenario = cast(dict[str, str], result['scenario'])
    visible_scenario.pop('secret', None)
    visible_scenario.pop('clue', None)
    result['validation'] = builder.validate(state['character'])
    return result


def listing() -> list[dict[str, str]]:
    return SQLiteStore(DB).listing()


def interpret(text: str, state: Campaign) -> Action:
    if llm.enabled():
        proposal = llm.generate('Classify intent into one action. Hypothetical questions are ask. Never infer success. Unsupported actions are ask.',
            {'input': text, 'scene': public(state)}, llm.ACTION_SCHEMA)
        return cast(Action, proposal['action'])
    t = text.lower().strip()
    if '?' in t or t.startswith(('could ', 'can ', 'would ')):
        return 'ask'
    words: dict[Action, tuple[str, ...]] = {
        'observe': ('look', 'search', 'observe', 'inspect'),
        'talk': ('talk', 'ask iven', 'persuade', 'speak'),
        'sneak': ('sneak', 'slip', 'customs', 'follow'), 'rest': ('rest', 'sleep')}
    for action, terms in words.items():
        if any(w in t for w in terms):
            return action
    return 'ask'


def turn(cid: str, request_id: str, revision: int, text: str) -> dict[str, object]:
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
        raise ValueError('A request ID is required')
    if type(revision) is not int or not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError('Invalid turn')
    store = SQLiteStore(DB)
    if store.duplicate(cid, request_id, text):
        return public(store.read(cid))
    initial = store.read(cid)
    if initial['revision'] != revision:
        raise ValueError('Campaign changed. Refresh before retrying.')
    action = interpret(text, initial)
    if action not in ('observe', 'talk', 'sneak', 'rest', 'ask'):
        raise ValueError('Unsupported action proposal')
    state, event = store.commit_turn(cid, request_id, revision, text, lambda s: resolve(s, action, text))
    if event is not None and llm.enabled():
        try:
            narration = llm.generate('Narrate only the committed outcome in 2 short atmospheric sentences. Do not add facts, rewards, actions or secrets. For questions explain available actions.',
                {'outcome': event['outcome'], 'location': state['location'], 'player': text}, llm.NARRATION_SCHEMA)['text']
            if not isinstance(narration, str) or len(narration) > 4000:
                raise ValueError('Invalid narration')
            store.save_narration(cid, state['revision'], narration)
        except Exception:
            pass  # Preserved demo fallback; typed errors/observability are tracked in #5.
    return public(store.read(cid))
