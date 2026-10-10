import uvicorn

from .app import create_app
from .config import PORT

if __name__ == '__main__':
    # Single worker: JSON storage and per-session locks aren't distributed locks.
    uvicorn.run(create_app(), host='127.0.0.1', port=PORT, access_log=False)
