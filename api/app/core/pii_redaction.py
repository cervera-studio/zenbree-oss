"""National-ID redaction for scraped content before it reaches the LLM (#817).

Policy (approved on #817): scraped web content passed to the model has its
**national-identifier class** of PII stripped by default (Italian codice
fiscale, US SSN, and the like), while emails and phone numbers are kept
(they are B2B contact data central to the product, and the contact-finding
tools go through Apollo, not the scrape path). National IDs are never
legitimately needed in a scraped page handed to the model and are the
GDPR high-sensitivity class the EU review targets, so redaction is uniform
by type (no per-tool allowlist needed).

Pure functions, no I/O. The shapes are distinctive enough that false
positives on ordinary prose are near-zero:

- Italian codice fiscale: 6 letters, 2 digits, 1 letter, 2 digits, 1
  letter, 3 digits, 1 letter (16 chars, fixed letter/digit positions).
- US SSN: NNN-NN-NNNN (dashed form only; bare 9-digit runs are left alone
  to avoid clobbering phone numbers, order ids, and counts).
"""

from __future__ import annotations

import re

# Fixed-position 16-char shape; matched case-insensitively because scraped
# HTML may lower-case it, but the letter/digit layout keeps it precise.
_CODICE_FISCALE_RE = re.compile(
    r'\b[A-Za-z]{6}\d{2}[A-Za-z]\d{2}[A-Za-z]\d{3}[A-Za-z]\b',
)
# Dashed US SSN only. A bare \d{9} floor would swallow phone numbers and ids.
_SSN_RE = re.compile(r'\b\d{3}-\d{2}-\d{4}\b')

_PATTERNS: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ('codice_fiscale', _CODICE_FISCALE_RE, '[REDACTED_CODICE_FISCALE]'),
    ('ssn', _SSN_RE, '[REDACTED_SSN]'),
)


def redact_national_ids(text: str) -> tuple[str, dict[str, int]]:
    """Redact national-ID PII from ``text``.

    Returns the redacted text plus a per-type count of what was removed
    (only non-zero types are included, so an empty dict means nothing was
    redacted). Order-stable and idempotent: re-running on already-redacted
    text is a no-op because the placeholders do not match the patterns.
    """
    if not text:
        return text, {}
    counts: dict[str, int] = {}
    for label, pattern, placeholder in _PATTERNS:
        n = 0

        def _sub(_m: re.Match[str], _p: str = placeholder) -> str:
            nonlocal n
            n += 1
            return _p

        text = pattern.sub(_sub, text)
        if n:
            counts[label] = n
    return text, counts
