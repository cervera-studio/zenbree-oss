"""The key a Home welcome card carries for a stored onboarding segment, and that ``POST /v1/chat`` takes back. A
standard-library leaf, so ``agent`` reads the key's shape without importing ``onboarding``."""

import hashlib
import json

SEGMENT_KEY = r'^[0-9a-f]{16}$'


def segment_key(raw: object) -> str:
    """A stored segment's key: its content, so a key read before the segments were proposed again finds none."""
    canonical = json.dumps(raw, sort_keys=True, separators=(',', ':'), ensure_ascii=False)
    return hashlib.sha256(canonical.encode()).hexdigest()[:16]
