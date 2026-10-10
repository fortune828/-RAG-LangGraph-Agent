"""Test-only fake model behind a real FastAPI server. Not a product mode."""
import json
import os

from backend.app import create_app
from backend.knowledge import retrieve_exact, load_release
from backend.config import PORT
from tests_python.test_migration import answer, response, tool_call
import uvicorn


async def model(messages, tools=None):
    context = json.loads(messages[1]['content'])
    number = context['cast']['original']['number'] if tools else next(e['hexagramId'] for e in load_release()[0] if e['id'] == context['参考原文'][0]['id'])
    if tools and not any(m['role'] == 'tool' for m in messages):
        return response(calls=[tool_call(h=number)])
    if tools:
        return response({'ready': True})
    target = '第二爻' in context['当前问题']
    e = retrieve_exact(dict(hexagramId=number, sections=['line'], lineIndices=[2]))['evidence'][0] if target else context['参考原文'][0]
    return response(answer(e))


if __name__ == '__main__':
    uvicorn.run(create_app(os.environ['DATA_DIR'], model), host='127.0.0.1', port=PORT, access_log=False)
