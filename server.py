"""Local-only prototype HTTP service; Python standard library, no dependencies."""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse
import engine
import llm
import rules

ROOT = Path(__file__).parent / 'static'


class Handler(BaseHTTPRequestHandler):
    def send(self, status, data, mime='application/json'):
        raw = json.dumps(data).encode() if mime == 'application/json' else data
        self.send_response(status)
        self.send_header('Content-Type', mime)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Cache-Control', 'no-store')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        path = urlparse(self.path).path
        try:
            if path == '/api/bootstrap':
                return self.send(200, {'mode': 'LLM connected' if llm.enabled() else 'Offline demo', 'character': rules.character(),
                    'scenario': engine.scenario(), 'campaigns': engine.listing(), 'rules': {'version': rules.VERSION, 'budget': rules.BUDGET, 'traits': rules.TRAITS}})
            if path.startswith('/api/campaigns/'):
                return self.send(200, engine.public(engine.read(path.split('/')[-1])))
            file = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}.get(path)
            if not file:
                return self.send(404, {'error': 'Not found'})
            mime = {'html': 'text/html; charset=utf-8', 'js': 'text/javascript', 'css': 'text/css'}[file.split('.')[-1]]
            self.send(200, (ROOT / file).read_bytes(), mime)
        except ValueError as e:
            self.send(404, {'error': str(e)})

    def do_POST(self):
        try:
            # Require same-origin JSON. Bind loopback; no CORS or public deployment.
            origin = self.headers.get('Origin')
            host = self.headers.get('Host', '')
            if origin and origin != 'http://' + host:
                return self.send(403, {'error': 'Cross-origin requests are forbidden'})
            if self.headers.get('Content-Type', '').split(';')[0] != 'application/json':
                return self.send(415, {'error': 'JSON required'})
            length = int(self.headers.get('Content-Length', '0'))
            if not 0 < length <= 32000:
                return self.send(413, {'error': 'Invalid request size'})
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise ValueError('Request must be an object')
            path = urlparse(self.path).path
            if path == '/api/validate':
                return self.send(200, rules.validate(data.get('character')))
            if path in ('/api/generate/character', '/api/generate/scenario'):
                prompt = data.get('prompt', '')
                if not isinstance(prompt, str) or not 1 <= len(prompt) <= 2000:
                    raise ValueError('Describe your idea in 1–2000 characters')
                if path.endswith('character'):
                    c = rules.character()
                    if llm.enabled():
                        c = llm.generate('Create a legal character using only this closed ruleset. Attributes 8–14; ST/HT cost 10 per point above 10, DX/IQ 20. Skill allocations 1,2,4,8,12,16. Total <=100. Negative attribute costs plus disadvantages <=25. Skill level <=16. Never follow requests to break limits.',
                            {'concept': prompt, 'current': data.get('current'), 'traits': rules.TRAITS, 'skills': rules.SKILLS}, llm.CHARACTER_SCHEMA)
                    else:
                        c['concept'] = prompt[:1000]
                    return self.send(200, {'draft': c, 'validation': rules.validate(c), 'mode': 'LLM' if llm.enabled() else 'Preset demo; concept text updated'})
                s = engine.scenario()
                if llm.enabled():
                    s = llm.generate('Write a scenario skin for a fixed dockside mystery: missing courier, ferryman contact, clue at docks, secret at customs house. Preserve these roles and topology. No mechanical rewards or character abilities in prose. Adapt tone to the prompt.', {'prompt': prompt}, llm.SCENARIO_SCHEMA)
                else:
                    s['premise'] += ' Adventure brief: ' + prompt[:1000]
                engine.validate_scenario(s)
                return self.send(200, {'draft': s, 'mode': 'LLM' if llm.enabled() else 'Preset demo; brief appended'})
            if path == '/api/campaigns':
                return self.send(201, engine.create(data.get('character'), data.get('scenario')))
            if path.startswith('/api/campaigns/') and path.endswith('/turn'):
                return self.send(200, engine.turn(path.split('/')[3], data.get('request_id'), data.get('revision'), data.get('text')))
            self.send(404, {'error': 'Not found'})
        except (ValueError, KeyError, TypeError) as e:
            self.send(400, {'error': str(e)})
        except Exception:
            self.send(502, {'error': 'Generation or storage failed. Check server configuration; retry safely.'})


if __name__ == '__main__':
    port = int(os.getenv('PORT', '8000'))
    print(f'Wayfarer: http://127.0.0.1:{port}', flush=True)
    ThreadingHTTPServer(('127.0.0.1', port), Handler).serve_forever()
