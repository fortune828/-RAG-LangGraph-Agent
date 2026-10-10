"""Single-process, atomic JSON business storage compatible with the old server."""
import copy
import hashlib
import hmac
import json
import os
from pathlib import Path
from uuid import UUID, uuid4

from .domain import now, sample_cast
from .knowledge import CURRENT_RELEASE, LEGACY_RELEASE


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.cache = {}

    def path(self, sid):
        if str(UUID(sid)) != sid.lower():
            raise ValueError('INVALID_SESSION_ID')
        return self.directory / f'{sid}.json'

    @staticmethod
    def token_hash(token):
        return hashlib.sha256(token.encode()).hexdigest()

    def authorize(self, sid, token):
        path = self.path(sid)
        return path.exists() and hmac.compare_digest(json.loads(path.read_text())['tokenHash'], self.token_hash(token))

    def get(self, sid):
        if sid not in self.cache:
            path = self.path(sid)
            if not path.exists():
                return None
            data = json.loads(path.read_text())
            data.pop('tokenHash')
            data.setdefault('releaseId', LEGACY_RELEASE)
            for turn in data['turns']:
                if turn['status'] == 'running':
                    turn.update(status='failed', error='SERVER_RESTARTED')
            self.cache[sid] = data
            self.save(data)
        return copy.deepcopy(self.cache[sid])

    def create(self, sid, token, question):
        if self.path(sid).exists():
            if not self.authorize(sid, token):
                raise ValueError('SESSION_ACCESS_DENIED')
            session = self.get(sid)
            if session['question'] != question:
                raise ValueError('IDEMPOTENCY_CONFLICT')
            return session
        session = dict(id=sid, question=question, cast=sample_cast(), turns=[], createdAt=now(), releaseId=CURRENT_RELEASE)
        self.save(session, self.token_hash(token))
        return session

    def save(self, session, token_hash=None):
        path = self.path(session['id'])
        token_hash = token_hash or json.loads(path.read_text())['tokenHash']
        tmp = path.with_suffix(f'.{uuid4()}.tmp')
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(fd, 'w') as stream:
                json.dump(dict(session, tokenHash=token_hash), stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(tmp, path)
        finally:
            tmp.unlink(missing_ok=True)
        self.cache[session['id']] = copy.deepcopy(session)

    def delete(self, sid):
        self.path(sid).unlink()
        self.cache.pop(sid, None)

    def log(self, session, turn):
        # Do not log prompts, user text, tool arguments, credentials or answers.
        row = dict(sessionId=session['id'], turnId=turn['id'], status=turn['status'],
                   promptVersion=turn.get('promptVersion'), graphVersion=turn.get('graphVersion'),
                   corpusVersion=session['releaseId'], model=turn['model'], durationMs=turn.get('durationMs'),
                   trace=[{k: v for k, v in e.items() if k != 'args'} for e in turn['trace']])
        fd = os.open(self.directory / 'traces.jsonl', os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, 'a') as stream:
            stream.write(json.dumps(row, ensure_ascii=False) + '\n')
