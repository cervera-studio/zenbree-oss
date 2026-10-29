"""Asking a provider for JSON, instead of asking the prose nicely.

Two grades, weakest first. ``json_object`` makes the reply a JSON object:
no preamble, no ``` fence, no Python literal. ``json_schema`` also constrains
the types inside it, so a field declared boolean cannot come back as the
string ``"false"``.

Which grade a provider actually honours is declared per provider
(:attr:`app.llm.base.LLMProvider.json_output_modes`) from that
provider's own documentation. This module is the vocabulary the call sites
use, plus the local net for the part the wire did not enforce.
"""

from __future__ import annotations

import json

JSON_OBJECT = 'json_object'
JSON_SCHEMA = 'json_schema'


class JSONOutputError(Exception):
    """A reply that does not match the schema the call site declared.

    ``response`` is the reply that failed, attached by whoever raised this if
    they were holding it. The provider generated those tokens and will
    invoice them whatever shape they came back in, and the frame that meters
    a call is not the frame that still has the response, so it travels on the
    exception or it is not metered at all.
    """

    response: object | None = None


def json_object() -> dict:
    """Ask for a JSON object, without constraining what is in it."""
    return {'type': JSON_OBJECT}


def json_schema(name: str, schema: dict, *, strict: bool = True) -> dict:
    """Ask for a JSON object shaped like ``schema``.

    ``name`` is the label the provider echoes back; it is required by the
    OpenAI-shaped field even though nothing on our side reads it.
    """
    return {
        'type': JSON_SCHEMA,
        'json_schema': {'name': name, 'schema': schema, 'strict': strict},
    }


# The JSON Schema subset we send. A keyword outside this table is not an
# error, it is a keyword this checker has no opinion on: the provider that
# honours the schema natively may well enforce it, and refusing a reply over
# a rule we chose not to model would reject good answers.
_TYPE_CHECKS = {
    'object': lambda v: isinstance(v, dict),
    'array': lambda v: isinstance(v, list),
    'string': lambda v: isinstance(v, str),
    'boolean': lambda v: isinstance(v, bool),
    # ``bool`` is an ``int`` in Python and a JSON true is not a JSON number,
    # so the two numeric arms have to say so explicitly or every boolean
    # would satisfy a field declared integer.
    'integer': lambda v: isinstance(v, int) and not isinstance(v, bool),
    'number': lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
    'null': lambda v: v is None,
}


def _enum_holds(value: object, allowed: list) -> bool:
    # ``in`` compares with ``==``, and ``True == 1`` in Python, so a plain
    # membership test lets a JSON true satisfy ``enum: [1, 2]`` and the number
    # 1 satisfy ``enum: [true]``. Same reason the numeric arms above have to
    # exclude ``bool`` explicitly: a JSON true is not a JSON number.
    return any(isinstance(value, bool) == isinstance(option, bool) and value == option for option in allowed)


def _fail(path: str, expected: object, value: object) -> None:
    raise JSONOutputError(f'{path}: expected {expected}, got {type(value).__name__}')


def check(value: object, schema: dict, path: str = '$') -> None:
    """Hold ``value`` to ``schema``, or raise :class:`JSONOutputError`.

    Only the declared keys are judged. Extra keys pass unless the schema says
    ``additionalProperties: false``, which is the same rule the provider
    applies when it enforces the schema itself, so a reply reads the same way
    whether the constraint landed on the wire or here.
    """
    declared = schema.get('type')
    types = declared if isinstance(declared, list) else [declared]
    checks = [_TYPE_CHECKS[t] for t in types if t in _TYPE_CHECKS]
    if checks and not any(check_fn(value) for check_fn in checks):
        _fail(path, ' or '.join(str(t) for t in types), value)

    allowed = schema.get('enum')
    if isinstance(allowed, list) and not _enum_holds(value, allowed):
        raise JSONOutputError(f'{path}: {value!r} is not one of {allowed}')

    if isinstance(value, dict):
        properties = schema.get('properties')
        properties = properties if isinstance(properties, dict) else {}
        for name in schema.get('required') or []:
            if name not in value:
                raise JSONOutputError(f'{path}: missing required "{name}"')
        if schema.get('additionalProperties') is False:
            extra = sorted(set(value) - set(properties))
            if extra:
                raise JSONOutputError(f'{path}: unexpected {extra}')
        for name, sub in properties.items():
            if name in value and isinstance(sub, dict):
                check(value[name], sub, f'{path}.{name}')

    if isinstance(value, list):
        items = schema.get('items')
        if isinstance(items, dict):
            for i, item in enumerate(value):
                check(item, items, f'{path}[{i}]')


def check_reply(text: str, schema: dict) -> None:
    """Hold a reply's text to ``schema``.

    A reply that is not JSON at all is left alone. That case only reaches
    here from a provider that was sent no ``response_format`` to begin with,
    a fallback that predates the field, and turning its prose into an
    exception would take away the tolerant reader that is the whole reason
    such a provider is still in the chain. What this rejects is the reply
    that IS JSON and contradicts what was asked for, which no reader can
    tell apart from an answer.

    A bare JSON scalar counts as prose for the same reason. ``false`` and
    ``"n/a"`` parse, but only a provider under no constraint can answer a
    call that asked for an object with one, so judging them would make the
    tolerant path fatal for exactly the providers it exists to protect,
    and on the wording of the reply rather than on anything the caller did.
    """
    try:
        value = json.loads(text)
    except ValueError:
        return
    if not isinstance(value, (dict, list)):
        return
    check(value, schema)
