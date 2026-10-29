import json
import re

# The closing quote must be in the text: an escape pair (\") is consumed whole, so it never ends the match.
_SUMMARY = re.compile(r'"summary"\s*:\s*("(?:[^"\\]|\\.)*")')


def completed_summary(text: str) -> str | None:
    match = _SUMMARY.search(text)
    if not match:
        return None
    try:
        summary = json.loads(match.group(1))
    except ValueError:
        return None
    return summary.strip()[:1200] or None
