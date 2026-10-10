"""Local configuration. Secrets never enter graph state or API responses."""
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / '.env.local')
# Keep tracing local unless a future, explicitly configured integration is added.
os.environ['LANGSMITH_TRACING'] = 'false'
os.environ['LANGCHAIN_TRACING_V2'] = 'false'
MODEL = os.getenv('DEEPSEEK_MODEL', 'deepseek-flash')
DATA_DIR = Path(os.getenv('DATA_DIR', str(ROOT / 'runtime'))).resolve()
PORT = int(os.getenv('PORT', '3001'))

# Shared by the API adapter and evaluation budget accounting.
MAX_COMPLETION_TOKENS = 8192
