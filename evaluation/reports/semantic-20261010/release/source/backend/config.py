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

# Shared by the API adapter and evaluation budget accounting. Invalid settings
# fail at startup rather than reaching the provider as an accidental experiment.
THINKING = os.getenv('DEEPSEEK_THINKING', 'enabled')
REASONING_EFFORT = os.getenv('DEEPSEEK_REASONING_EFFORT', 'low')
MAX_COMPLETION_TOKENS = int(os.getenv('DEEPSEEK_MAX_TOKENS', '8192'))
if THINKING not in ('enabled', 'disabled'):
    raise ValueError('DEEPSEEK_THINKING must be enabled or disabled')
if REASONING_EFFORT not in ('low', 'high', 'max'):
    raise ValueError('DEEPSEEK_REASONING_EFFORT must be low, high or max')
if MAX_COMPLETION_TOKENS < 1:
    raise ValueError('DEEPSEEK_MAX_TOKENS must be positive')
