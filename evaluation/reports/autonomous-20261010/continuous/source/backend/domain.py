"""Pure casting rules; never call an LLM here. Lines are bottom-to-top."""
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

HEXAGRAMS = json.loads((Path(__file__).parent / 'hexagrams.json').read_text())


def now():
    return datetime.now(timezone.utc).isoformat()


def calculate_hexagram(lines):
    if len(lines) != 6 or any(type(v) is not int or v not in (6, 7, 8, 9) for v in lines):
        raise ValueError('INVALID_CAST')
    original = ''.join(str(v % 2) for v in lines)
    changed = ''.join('1' if v in (6, 7) else '0' for v in lines)
    return dict(lines=list(lines), original=HEXAGRAMS[original], changed=HEXAGRAMS[changed],
                originalBinary=original, changedBinary=changed,
                changingIndices=[i for i, v in enumerate(lines) if v in (6, 9)])


def sample_cast(next_bit=None):
    # Injection is a Python test seam, never an API input.
    next_bit = next_bit or (lambda: secrets.randbelow(2))
    coins = [[next_bit() for _ in range(3)] for _ in range(6)]
    if any(type(v) is not int or v not in (0, 1) for row in coins for v in row):
        raise ValueError('INVALID_RANDOM_BIT')
    return dict(**calculate_hexagram([6 + sum(row) for row in coins]),
                id=str(uuid4()), method='three-coins', algorithmVersion='coins-py-v1',
                readingPolicyVersion='all-moving-lines-v1', coins=coins, createdAt=now())
