import copy
import json
import os
import sqlite3
import uuid
from pathlib import Path
import llm
import rules

DB = Path(os.getenv('WAYFARER_DB', 'data/wayfarer.sqlite3'))


def connect():
    DB.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(DB, timeout=10)
    db.execute('CREATE TABLE IF NOT EXISTS campaigns (id TEXT PRIMARY KEY, state TEXT NOT NULL)')
    db.execute('CREATE TABLE IF NOT EXISTS events (campaign TEXT, request_id TEXT, payload TEXT, PRIMARY KEY(campaign, request_id))')
    return db


def scenario():
    return {'title': 'The Lantern at Blackwater', 'premise': 'A courier vanished before the last ferry. A blue lantern still burns at the abandoned customs house.',
            'location': 'Blackwater docks', 'contact': 'Iven, the ferryman', 'clue': 'A torn ferry manifest points to the customs house.',
            'secret': 'The courier is sheltering a witness inside the customs house.', 'objective': 'Find the missing courier'}


def validate_scenario(s):
    if not isinstance(s, dict) or set(s) != set(scenario()):
        raise ValueError('Scenario must have exactly the required fields')
    if any(not isinstance(v, str) or not 1 <= len(v) <= 2000 for v in s.values()):
        raise ValueError('Scenario fields must contain 1–2000 characters')


def create(c, s):
    verdict = rules.validate(c)
    if not verdict['valid']:
        raise ValueError('; '.join(verdict['errors']))
    validate_scenario(s)
    cid = str(uuid.uuid4())
    state = {'id': cid, 'revision': 0, 'rules': rules.VERSION, 'character': c, 'scenario': s,
             'hp': c['attributes']['ST'], 'fp': c['attributes']['HT'], 'minutes': 0,
             'location': s['location'], 'inventory': ['Travel clothes', 'Rations', 'Waterskin'],
             'discoveries': [], 'flags': [], 'complete': False,
             'messages': [{'role': 'gm', 'text': s['premise']}]}
    with connect() as db:
        db.execute('INSERT INTO campaigns VALUES (?,?)', (cid, json.dumps(state)))
    return public(state)


def read(cid):
    with connect() as db:
        row = db.execute('SELECT state FROM campaigns WHERE id=?', (cid,)).fetchone()
    if not row:
        raise ValueError('Campaign not found')
    return json.loads(row[0])


def public(state):
    s = copy.deepcopy(state)
    s['scenario'].pop('secret', None)
    s['scenario'].pop('clue', None)
    s['validation'] = rules.validate(s['character'])
    return s


def listing():
    with connect() as db:
        rows = db.execute('SELECT state FROM campaigns ORDER BY rowid DESC').fetchall()
    return [{'id': s['id'], 'title': s['scenario']['title'], 'name': s['character']['name']}
            for (raw,) in rows for s in [json.loads(raw)]]


def interpret(text, state):
    if llm.enabled():
        proposal = llm.generate('Classify intent into one action. Hypothetical questions are ask. Never infer success. Unsupported actions are ask.',
                                {'input': text, 'scene': public(state)}, llm.ACTION_SCHEMA)
        return proposal['action']
    t = text.lower().strip()
    if '?' in t or t.startswith(('could ', 'can ', 'would ')):
        return 'ask'
    for action, words in {'observe': ('look', 'search', 'observe', 'inspect'), 'talk': ('talk', 'ask iven', 'persuade', 'speak'),
                          'sneak': ('sneak', 'slip', 'customs', 'follow'), 'rest': ('rest', 'sleep')}.items():
        if any(w in t for w in words):
            return action
    return 'ask'


def turn(cid, request_id, revision, text):
    if not isinstance(request_id, str) or not 1 <= len(request_id) <= 100:
        raise ValueError('A request ID is required')
    if type(revision) is not int or not isinstance(text, str) or not 1 <= len(text.strip()) <= 2000:
        raise ValueError('Invalid turn')
    with connect() as db:
        old = db.execute('SELECT payload FROM events WHERE campaign=? AND request_id=?', (cid, request_id)).fetchone()
    if old:
        previous = json.loads(old[0])
        if previous['input'] != text:
            raise ValueError('Request ID already used for different input')
        return public(read(cid))
    initial = read(cid)
    if initial['revision'] != revision:
        raise ValueError('Campaign changed. Refresh before retrying.')
    action = interpret(text, initial)
    if action not in ('observe', 'talk', 'sneak', 'rest', 'ask'):
        raise ValueError('Unsupported action proposal')
    # Model calls happen outside the write lock. Revision is checked again below.
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        duplicate = db.execute('SELECT payload FROM events WHERE campaign=? AND request_id=?', (cid, request_id)).fetchone()
        if duplicate:
            if json.loads(duplicate[0])['input'] != text:
                raise ValueError('Request ID already used for different input')
            return public(json.loads(db.execute('SELECT state FROM campaigns WHERE id=?', (cid,)).fetchone()[0]))
        s = json.loads(db.execute('SELECT state FROM campaigns WHERE id=?', (cid,)).fetchone()[0])
        if s['revision'] != revision:
            raise ValueError('Campaign changed. Refresh before retrying.')
        result = None
        if action == 'ask':
            outcome = 'You can inspect the docks, talk to the ferryman, follow a discovered lead, or rest. Questions do not spend time.'
        elif s['complete']:
            outcome = 'The courier is safe. This prototype adventure is complete; start another campaign in the Scenario studio.'
        elif action == 'rest':
            s['fp'] = min(s['character']['attributes']['HT'], s['fp'] + 1)
            s['minutes'] += 30
            outcome = 'You rest for thirty minutes and recover up to one fatigue point.'
        elif action == 'sneak' and not s['discoveries']:
            outcome = 'You need a lead before approaching the courier. Inspect the docks or talk to the ferryman.'
        elif s['fp'] <= 0:
            outcome = 'You are exhausted. Rest before attempting another check.'
        else:
            skill = {'observe': 'Observation', 'talk': 'Diplomacy', 'sneak': 'Stealth'}[action]
            target = rules.validate(s['character'])['levels'].get(skill)
            if target is None:
                outcome = f'You have not trained {skill}. Untrained checks are outside this prototype ruleset.'
            else:
                result = rules.roll(target)
                s['minutes'] += 10
                if result['success']:
                    if action in ('observe', 'talk'):
                        clue = s['scenario']['clue']
                        if clue not in s['discoveries']:
                            s['discoveries'].append(clue)
                        if action == 'talk' and 'Ferryman trusts you' not in s['flags']:
                            s['flags'].append('Ferryman trusts you')
                        outcome = clue
                    else:
                        s['location'] = 'Customs house'
                        s['complete'] = True
                        s['discoveries'].append(s['scenario']['secret'])
                        s['inventory'].append('Courier’s letter')
                        outcome = s['scenario']['secret'] + ' You secure the courier’s letter. Objective complete.'
                else:
                    s['fp'] -= 1
                    outcome = 'The attempt fails. You lose one fatigue point and ten minutes pass. No new information is discovered.'
        event = {'input': text, 'action': action, 'outcome': outcome, 'roll': result}
        s['messages'].extend([{'role': 'player', 'text': text}, {'role': 'gm', 'text': outcome, 'roll': result, 'action': action}])
        s['revision'] += 1
        db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(s), cid))
        db.execute('INSERT INTO events VALUES (?,?,?)', (cid, request_id, json.dumps(event)))
    # Narration is separately persisted. Failure cannot undo or reroll mechanics.
    if llm.enabled():
        try:
            narration = llm.generate('Narrate only the committed outcome in 2 short atmospheric sentences. Do not add facts, rewards, actions or secrets. For questions explain available actions.',
                {'outcome': outcome, 'location': s['location'], 'player': text}, llm.NARRATION_SCHEMA)['text']
            if not isinstance(narration, str) or len(narration) > 4000:
                raise ValueError('Invalid narration')
            with connect() as db:
                db.execute('BEGIN IMMEDIATE')
                current = json.loads(db.execute('SELECT state FROM campaigns WHERE id=?', (cid,)).fetchone()[0])
                if current['revision'] == s['revision']:
                    current['messages'][-1]['flavor'] = narration
                    db.execute('UPDATE campaigns SET state=? WHERE id=?', (json.dumps(current), cid))
        except Exception:
            pass  # The authoritative outcome remains visible and persisted.
    return public(read(cid))
