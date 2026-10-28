"""Only ``normalise_domain`` from the earlier implementation ``app/people/entity_match.py`` (the rest is chat-side)."""


def normalise_domain(domain: str) -> str:
    """Strip scheme / path / port / query / fragment / leading ``www.``.

    ``"https://www.acme.com/path?utm=x#frag"`` → ``"acme.com"``: Apollo's domain
    endpoint rejects URLs, and its ``primary_domain`` is always the bare form.
    Only the exact ``www.`` label is dropped: ``careers.acme.com`` stays.
    """
    if not domain:
        return ''
    raw = domain.strip().lower().rstrip('.')
    if '://' in raw:
        raw = raw.split('://', 1)[1]
    for sep in ('/', '?', '#'):
        raw = raw.split(sep, 1)[0]
    raw = raw.split(':', 1)[0]
    if raw.startswith('www.'):
        raw = raw[4:]
    return raw
