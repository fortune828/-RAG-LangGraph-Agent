"""FastAPI preserves /api/sessions and /turns for the existing React UI."""
import asyncio
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse
from uuid import UUID

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from .config import DATA_DIR, MODEL, ROOT
from .domain import now
from .graph import GRAPH_VERSION, build_graph, initial_state
from .knowledge import CURRENT_RELEASE, coverage, load_release
from .llm import ModelError, deepseek_chat
from .models import CreateRequest, TurnRequest
from .prompts import PROMPT_VERSION
from .store import Store


def create_app(directory=DATA_DIR, chat=deepseek_chat):
    store = Store(directory)
    busy, requests = set(), {}
    checkpoint_path = Path(directory) / 'checkpoints.sqlite'

    @asynccontextmanager
    async def lifespan(app):
        # Pre-create privately; SQLite WAL files inherit the database permissions.
        fd = os.open(checkpoint_path, os.O_CREAT | os.O_WRONLY, 0o600)
        os.close(fd)
        async with AsyncSqliteSaver.from_conn_string(str(checkpoint_path)) as saver:
            await saver.setup()
            app.state.saver = saver
            app.state.graph = build_graph(saver, chat)
            yield

    app = FastAPI(title='Yijing Python Agent', lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.store = store

    @app.middleware('http')
    async def boundaries(request, call_next):
        if not request.url.path.startswith('/api'):
            return await call_next(request)
        origin = request.headers.get('origin')
        if origin and urlparse(origin).netloc != request.headers.get('host'):
            return JSONResponse({'error': 'ORIGIN_DENIED'}, status_code=403)
        if request.method not in ('GET', 'DELETE'):
            size = 0
            chunks = []
            async for chunk in request.stream():
                size += len(chunk)
                if size > 16384:
                    return JSONResponse({'error': 'INVALID_REQUEST'}, status_code=400)
                chunks.append(chunk)
            request._body = b''.join(chunks)
        response = await call_next(request)
        response.headers.update({'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff'})
        return response

    @app.exception_handler(RequestValidationError)
    async def invalid_request(_request, _exc):
        return JSONResponse({'error': 'INVALID_REQUEST'}, status_code=400)

    @app.exception_handler(ValueError)
    async def bad_value(_request, exc):
        code = str(exc)
        statuses = {'SESSION_ACCESS_DENIED': 404, 'IDEMPOTENCY_CONFLICT': 409,
                    'SESSION_BUSY': 409, 'SESSION_LIMIT': 409, 'SERVER_BUSY': 503, 'RATE_LIMITED': 429}
        return JSONResponse({'error': code if code in statuses else 'INVALID_REQUEST'}, status_code=statuses.get(code, 400))

    def token(request):
        value = request.headers.get('authorization', '').removeprefix('Bearer ')
        try:
            UUID(value)
        except ValueError:
            raise ValueError('SESSION_ACCESS_DENIED') from None
        return value

    def access(sid, request):
        try:
            if store.authorize(sid, token(request)):
                return store.get(sid)
        except (ValueError, KeyError):
            pass
        raise ValueError('SESSION_ACCESS_DENIED')

    def rate_limit(request):
        key = request.client.host if request.client else 'local'
        moment = time.monotonic()
        recent = [t for t in requests.get(key, []) if moment - t < 600]
        if len(recent) >= 40:
            raise ValueError('RATE_LIMITED')
        requests[key] = recent + [moment]

    @app.get('/api/health')
    async def health():
        return dict(status='ok', model=MODEL, configured=bool(os.getenv('DEEPSEEK_API_KEY')),
                    corpusVersion=CURRENT_RELEASE, evidenceCount=len(load_release()[0]),
                    knowledgeCoverage=coverage(), backend='python', orchestrator='langgraph',
                    graphVersion=GRAPH_VERSION, promptVersion=PROMPT_VERSION)

    @app.post('/api/sessions', status_code=201)
    async def create(body: CreateRequest, request: Request):
        rate_limit(request)
        return store.create(str(body.requestId), token(request), body.question)

    @app.get('/api/sessions/{sid}')
    async def get(sid: str, request: Request):
        return access(sid, request)

    @app.delete('/api/sessions/{sid}', status_code=204)
    async def delete(sid: str, request: Request):
        session = access(sid, request)
        if sid in busy:
            raise ValueError('SESSION_BUSY')
        busy.add(sid)
        try:
            for turn in session['turns']:
                if turn.get('checkpointThreadId'):
                    await app.state.saver.adelete_thread(turn['checkpointThreadId'])
            store.delete(sid)
            return Response(status_code=204)
        finally:
            busy.discard(sid)

    @app.post('/api/sessions/{sid}/turns')
    async def turn(sid: str, body: TurnRequest, request: Request):
        session = access(sid, request)
        rate_limit(request)
        turn_id = str(body.requestId)
        current = next((t for t in session['turns'] if t['id'] == turn_id), None)
        if current and current['question'] != body.question:
            raise ValueError('IDEMPOTENCY_CONFLICT')
        if current and (current['status'] != 'failed' or not body.retry):
            return JSONResponse(session, status_code=202 if current['status'] == 'running' else 200)
        if sid in busy:
            raise ValueError('SESSION_BUSY')
        if len(busy) >= 2:
            raise ValueError('SERVER_BUSY')
        if len(session['turns']) >= 60 and not current:
            raise ValueError('SESSION_LIMIT')
        busy.add(sid)
        started = time.monotonic()
        graph = app.state.graph
        try:
            if current is None:
                mode = 'followup' if any(t['status'] == 'completed' for t in session['turns']) or body.question != session['question'] else 'initial'
                current = dict(id=turn_id, question=body.question, mode=mode, createdAt=now(), evidence=[], trace=[])
                session['turns'].append(current)
            # Persist thread identity before invoking the graph, so a process crash
            # can be recovered without a new cast or a second business turn.
            thread_id = current.get('checkpointThreadId', f'{GRAPH_VERSION}:{sid}:{turn_id}')
            current.update(status='running', model=MODEL, releaseId=session['releaseId'], checkpointThreadId=thread_id,
                           promptVersion=PROMPT_VERSION, graphVersion=GRAPH_VERSION)
            current.pop('error', None)
            current.pop('answer', None)
            store.save(session)
            config = dict(configurable={'thread_id': thread_id}, recursion_limit=24)
            snapshot = await graph.aget_state(config)
            if snapshot.values and (snapshot.values.get('graph_version') != GRAPH_VERSION or
                                    snapshot.values.get('prompt_version') != PROMPT_VERSION):
                raise ValueError('CHECKPOINT_VERSION_MISMATCH')
            if snapshot.values and not snapshot.next and snapshot.values.get('errors') and body.retry:
                # The previous graph reached END with an invalid answer. Resume
                # at generation using its evidence, not the completed fallback.
                # as_node selects the existing prepare -> generate edge; prepare
                # itself (and all completed tools) is not re-executed.
                await graph.aupdate_state(config, dict(answer=None, errors=[], repairs=0, next_step='generate',
                    trace=snapshot.values['trace'] + [dict(step='retry', status='requested', code='REGENERATE_ANSWER')]),
                    as_node='prepare')
                snapshot = await graph.aget_state(config)
            if snapshot.values and not snapshot.next:
                result = snapshot.values  # Graph committed before a process died saving the business record.
            else:
                inputs = None if snapshot.values else initial_state(session, body.question, current['mode'])
                result = await asyncio.wait_for(graph.ainvoke(inputs, config, durability='sync'), timeout=150)
            if result.get('errors'):
                raise ValueError('ANSWER_VALIDATION_FAILED')
            current.update(status='completed', answer=result['answer'], evidence=result['evidence'], trace=result['trace'])
        except Exception as exc:
            known = {'RELEASE_UNAVAILABLE', 'CORPUS_HASH_MISMATCH', 'REQUIRED_EVIDENCE_BUDGET_EXCEEDED',
                     'TOOL_BATCH_TOO_LARGE', 'CHECKPOINT_VERSION_MISMATCH', 'ANSWER_VALIDATION_FAILED'}
            code = str(exc) if isinstance(exc, ModelError) or str(exc) in known else 'MODEL_TIMEOUT_OR_NETWORK' if isinstance(exc, asyncio.TimeoutError) else 'AGENT_EXECUTION_FAILED'
            snapshot = await graph.aget_state(dict(configurable={'thread_id': current['checkpointThreadId']}))
            current.update(status='failed', error=code, evidence=snapshot.values.get('evidence', []),
                           trace=snapshot.values.get('trace', []) + [dict(step='execution', status='error', code=code)])
        finally:
            busy.discard(sid)
        current['durationMs'] = round((time.monotonic() - started) * 1000)
        store.save(session)
        store.log(session, current)
        return session

    @app.api_route('/api/{rest:path}', methods=['GET', 'POST', 'DELETE', 'PUT', 'PATCH'])
    async def not_found(rest):
        return JSONResponse({'error': 'NOT_FOUND'}, status_code=404)

    if (ROOT / 'dist/index.html').exists():
        app.mount('/', StaticFiles(directory=ROOT / 'dist', html=True), name='frontend')
    return app
