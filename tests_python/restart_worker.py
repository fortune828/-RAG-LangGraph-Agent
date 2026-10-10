"""Subprocess fixture for a real SQLite close/reopen, not an in-memory restart."""
import json
import os
import sys
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from fastapi.testclient import TestClient
from backend.app import create_app
from backend.domain import calculate_hexagram
from backend.knowledge import retrieve_exact
from backend.llm import ModelError
from backend.store import Store
from tests_python.test_migration import answer, response, tool_call

folder, phase = Path(sys.argv[1]), sys.argv[2]
if phase in ('fail', 'crash', 'invalid'):
    identity = dict(sid=str(uuid4()), token=str(uuid4()), tid=str(uuid4()))
    (folder / 'identity.json').write_text(json.dumps(identity))
    store = Store(folder)
    s = store.create(identity['sid'], identity['token'], '原始问题')
    s['cast'].update(calculate_hexagram([7] * 6))
    store.save(s)
else:
    identity = json.loads((folder / 'identity.json').read_text())

async def model(messages, tools=None):
    if phase == 'resume':
        assert tools is None, 'Completed routing/tool nodes must not run again'
        assert not any(m['role'] == 'tool' for m in messages)
        assert any(e['id'] == 'zy-1-line-2' for e in json.loads(messages[1]['content'])['参考原文'])
        return response(answer(retrieve_exact(dict(hexagramId=1, sections=['line'], lineIndices=[2]))['evidence'][0]))
    if tools:
        return response({'ready': True}) if any(m['role'] == 'tool' for m in messages) else response(calls=[tool_call()])
    if phase == 'invalid':
        return response({'invalid': 'repeated-schema-failure'})
    if phase == 'crash':
        saved = json.loads((folder / f"{identity['sid']}.json").read_text())
        saved.pop('tokenHash')
        (folder / 'failed.json').write_text(json.dumps(saved))
        os._exit(17)  # abrupt process death, no API failure handler or saver cleanup
    raise ModelError('MODEL_TIMEOUT_OR_NETWORK')

with TestClient(create_app(folder, model)) as client:
    r = client.post(f"/api/sessions/{identity['sid']}/turns", headers={'Authorization': 'Bearer ' + identity['token']},
                    json=dict(requestId=identity['tid'], question='请给出本卦第二爻原文', retry=phase == 'resume'))
    assert r.status_code == 200, r.text
    s = r.json()
    assert s['turns'][0]['status'] == ('failed' if phase in ('fail', 'invalid') else 'completed'), s['turns'][0].get('error')
    if phase == 'invalid':
        assert s['turns'][0]['error'] == 'ANSWER_VALIDATION_FAILED'
    (folder / ('failed.json' if phase in ('fail', 'invalid') else 'resumed.json')).write_text(json.dumps(s))
