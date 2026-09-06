"""Exercise an installed wheel from a temporary cwd, without checkout imports.

Usage: python scripts/smoke_installed.py /absolute/path/to/installed/wayfarer
"""
import json
import os
from pathlib import Path
import queue
import subprocess
import sys
import tempfile
import threading
import urllib.request


def main() -> None:
    executable = str(Path(sys.argv[1]).resolve())
    with tempfile.TemporaryDirectory() as directory:
        env = {key: value for key, value in os.environ.items()
               if key not in {'PYTHONPATH', 'OPENAI_API_KEY', 'OPENAI_MODEL'}}
        process = subprocess.Popen([executable, '--port', '0', '--db', str(Path(directory) / 'campaigns.sqlite3')],
                                   cwd=directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        output: queue.Queue[str] = queue.Queue()
        assert process.stdout is not None
        reader = threading.Thread(target=lambda: output.put(process.stdout.readline()), daemon=True)
        reader.start()
        try:
            line = output.get(timeout=15).strip()
            assert line.startswith('Wayfarer: http://127.0.0.1:'), line
            base = line.removeprefix('Wayfarer: ')

            def get(path: str) -> bytes:
                with urllib.request.urlopen(base + path, timeout=5) as response:
                    return response.read()

            def post(path: str, data: dict) -> dict:
                request = urllib.request.Request(base + path, data=json.dumps(data).encode(),
                                                 headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=5) as response:
                    return json.load(response)

            assert b'WAYFARER' in get('/')
            assert get('/app.js') and get('/style.css')
            bootstrap = json.loads(get('/api/bootstrap'))
            campaign = post('/api/campaigns', {'character':bootstrap['character'], 'scenario':bootstrap['scenario']})
            path = '/api/campaigns/' + campaign['id']
            command = {'request_id':'wheel-smoke','revision':0,'text':'Rest'}
            first = post(path + '/turn', command)
            assert first['minutes'] == 30 and first['revision'] == 1
            assert post(path + '/turn', command) == first
            assert json.loads(get(path)) == first
            assert 'secret' not in first['scenario']
            print('Installed wheel smoke passed: assets, create, turn, retry, reload, secrets')
        finally:
            process.terminate()
            try:
                process.communicate(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.communicate()


if __name__ == '__main__':
    main()
