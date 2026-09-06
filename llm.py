"""Optional server-side Responses API adapter. No credentials sent to the browser."""
import json
import os
import urllib.request


def enabled():
    return bool(os.getenv('OPENAI_API_KEY') and os.getenv('OPENAI_MODEL'))


def generate(instructions, context, schema):
    payload = {'model': os.environ['OPENAI_MODEL'], 'store': False,
               'instructions': instructions,
               'input': json.dumps(context),
               'text': {'format': {'type': 'json_schema', 'name': 'proposal', 'strict': True, 'schema': schema}}}
    request = urllib.request.Request('https://api.openai.com/v1/responses',
        data=json.dumps(payload).encode(), headers={'Authorization': 'Bearer ' + os.environ['OPENAI_API_KEY'], 'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=45) as response:
        result = json.load(response)
    if result.get('status') != 'completed':
        raise ValueError('Model response did not complete; no changes were applied')
    for item in result.get('output', []):
        for content in item.get('content', []):
            if content.get('type') == 'output_text':
                return json.loads(content['text'])
    raise ValueError('Model declined or returned no structured proposal')


def obj(properties):
    return {'type': 'object', 'properties': properties, 'required': list(properties), 'additionalProperties': False}

STRING = {'type': 'string'}
CHARACTER_SCHEMA = obj({'name': STRING, 'concept': STRING,
    'attributes': obj({k: {'type': 'integer'} for k in ['ST', 'DX', 'IQ', 'HT']}),
    'skills': obj({k: {'type': 'integer'} for k in ['Stealth', 'Observation', 'Diplomacy', 'Survival']}),
    'traits': {'type': 'array', 'items': STRING}})
SCENARIO_SCHEMA = obj({k: STRING for k in ['title', 'premise', 'location', 'contact', 'clue', 'secret', 'objective']})
ACTION_SCHEMA = obj({'action': {'type': 'string', 'enum': ['observe', 'talk', 'sneak', 'rest', 'ask']}})
NARRATION_SCHEMA = obj({'text': STRING})
