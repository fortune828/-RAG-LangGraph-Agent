"""Locate a unique literal source span across script and punctuation differences.

This helper does not assess meaning, choose evidence, or change a claim. Callers
must retain the usual evidence-ID, attribution and answer validation checks.
"""
import unicodedata

from .knowledge import CHAR_MAP


def _indexed_text(text):
    normalized, positions = [], []
    for index, character in enumerate(text):
        if character.isspace() or unicodedata.category(character).startswith('P'):
            continue
        mapped = CHAR_MAP.get(character, character)
        normalized.extend(mapped)
        positions.extend([index] * len(mapped))
    return ''.join(normalized), positions


def locate_original_quote(source: str, quote: str) -> dict | None:
    """Return {quote, start, end} only for one contiguous source location.

    Only the corpus's existing traditional/simplified character map and removal
    of punctuation/whitespace participate. Numbers, symbols, negation, and word
    order remain significant. End is exclusive; positions index Python strings.
    Leading/trailing punctuation is excluded, internal source punctuation stays.
    """
    needle, _ = _indexed_text(quote)
    if len(needle) < 2:
        return None
    haystack, positions = _indexed_text(source)
    matches = set()
    offset = haystack.find(needle)
    while offset >= 0:
        start, end = positions[offset], positions[offset + len(needle) - 1] + 1
        # A character mapping must not create an apparent match to only part of
        # one original character; the entire actual span must normalize equally.
        if _indexed_text(source[start:end])[0] == needle:
            matches.add((start, end))
            if len(matches) > 1:
                return None
        offset = haystack.find(needle, offset + 1)
    if not matches:
        return None
    start, end = matches.pop()
    return dict(quote=source[start:end], start=start, end=end)
